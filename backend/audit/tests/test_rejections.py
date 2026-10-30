"""`audit.services.record_rejection`: authenticated attempts refused with an API error code.

A rejection changed nothing, so it has no states; it stores the error code and the sanitized
request parameters (AUD-1, AUD-4, INV-6). It is listed together with mutations by `list_events`.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from audit.services import (
    AuditEventDTO,
    SensitiveDataRejected,
    list_events,
    record,
    record_rejection,
)
from audit.tests.conftest import ACTOR_ID, IDEMPOTENCY_KEY, REQUEST_ID, make_context
from audit.tests.test_services import FORBIDDEN_KEYS

ENTITY_ID = UUID("1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b")
ATTEMPT = {
    "amount_minor": 5_000,
    "merchant": {"name": "Corner Shop", "mcc": "5411"},
    "tags": ["online", None, True],
}


def _reject(**overrides) -> AuditEventDTO:
    kwargs = {
        "context": make_context(),
        "action": "card.purchase",
        "entity_type": "credit_account",
        "entity_id": ENTITY_ID,
        "error_code": "INSUFFICIENT_CREDIT",
        "attempt": ATTEMPT,
    } | overrides
    return record_rejection(**kwargs)


# --- stored fields ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_record_rejection_stores_every_field_exactly(stored_event):
    """Fails if a refused attempt loses its who, what, which request, error code or parameters
    on the way to the row, or is stored with states or as a success (AUD-1, INV-6)."""
    start = timezone.now()

    event = _reject()

    stored = stored_event(event.id)
    assert start <= stored[0] <= timezone.now()
    assert stored[1:] == (
        ACTOR_ID,
        "operator",
        "card.purchase",
        "credit_account",
        ENTITY_ID,
        None,
        None,
        REQUEST_ID,
        IDEMPOTENCY_KEY,
        "rejected",
        "INSUFFICIENT_CREDIT",
        ATTEMPT,
    )


@pytest.mark.django_db
def test_record_rejection_returns_the_event_as_stored(stored_event):
    """Fails if the returned DTO differs from the row or is a model instance (DEP-3)."""
    event = _reject()

    assert event == AuditEventDTO(
        id=event.id,
        occurred_at=stored_event(event.id)[0],
        actor_id=ACTOR_ID,
        actor_role="operator",
        action="card.purchase",
        entity_type="credit_account",
        entity_id=ENTITY_ID,
        before_state=None,
        after_state=None,
        request_id=REQUEST_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        outcome="rejected",
        error_code="INSUFFICIENT_CREDIT",
        attempt=ATTEMPT,
    )


@pytest.mark.django_db
def test_record_rejection_without_entity_or_parameters_stores_nulls(stored_event):
    """Fails if an attempt on an entity that does not exist yet (e.g. an opening refused before
    any deposit) or with no parameters is refused or stored with invented values (AUD-1)."""
    customer_id = uuid4()
    context = make_context(
        actor_id=customer_id, actor_role="customer", request_id=None, idempotency_key=None
    )

    event = _reject(
        context=context,
        action="collateral_deposit.request_opening",
        entity_type="collateral_deposit",
        entity_id=None,
        error_code="FORBIDDEN",
        attempt={},
    )

    assert stored_event(event.id)[1:] == (
        customer_id,
        "customer",
        "collateral_deposit.request_opening",
        "collateral_deposit",
        None,
        None,
        None,
        None,
        None,
        "rejected",
        "FORBIDDEN",
        {},
    )


@pytest.mark.django_db
def test_record_rejection_accepts_an_error_code_of_exactly_64_characters(stored_event):
    """Fails if the error code length limit is off by one against the 64-character column."""
    error_code = "A" * 63 + "_"

    event = _reject(error_code=error_code)

    assert stored_event(event.id)[11] == error_code


@pytest.mark.django_db
def test_record_rejection_is_not_affected_by_later_changes_to_the_callers_attempt(stored_event):
    """Fails if the event keeps a reference to the caller's dict, so mutating the request data
    after recording would rewrite the returned audit record (AUD-1)."""
    attempt = {"amount_minor": 5_000, "merchant": {"mcc": "5411"}}
    event = _reject(attempt=attempt)

    attempt["merchant"]["mcc"] = "0000"

    assert event.attempt == {"amount_minor": 5_000, "merchant": {"mcc": "5411"}}
    assert stored_event(event.id)[12] == {"amount_minor": 5_000, "merchant": {"mcc": "5411"}}


# --- argument validation ------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"actor_role": "admin"}, id="role-unknown"),
        pytest.param({"actor_role": None}, id="role-none"),
        pytest.param({"actor_id": str(ACTOR_ID)}, id="actor-id-string"),
        pytest.param({"request_id": str(REQUEST_ID)}, id="request-id-string"),
        pytest.param({"idempotency_key": str(IDEMPOTENCY_KEY)}, id="idempotency-key-string"),
    ],
)
def test_record_rejection_rejects_an_invalid_context(event_count, overrides):
    """Fails if a refused attempt can be attributed to an unknown role or to a non-UUID
    identifier, making its "who" and "which request" unreliable (AUD-1)."""
    with pytest.raises(ValueError, match="^context\\."):
        _reject(context=make_context(**overrides))

    assert event_count() == 0


@pytest.mark.django_db
def test_record_rejection_rejects_a_context_that_is_not_an_audit_context(event_count):
    """Fails if a plain dict is accepted as context, bypassing its field validation (AUD-1)."""
    with pytest.raises(ValueError, match="^context must be an AuditContext, got dict$"):
        _reject(context={"actor_id": ACTOR_ID, "actor_role": "customer"})

    assert event_count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"action": "card"}, "^action must match", id="action-no-verb"),
        pytest.param({"action": "Card.purchase"}, "^action must match", id="action-uppercase"),
        pytest.param({"action": "card.purchase\n"}, "^action must match", id="action-newline"),
        pytest.param({"action": None}, "^action must match", id="action-none"),
        pytest.param({"entity_type": "account"}, "^entity_type must be one of", id="type-unknown"),
        pytest.param({"entity_type": None}, "^entity_type must be one of", id="type-none"),
        pytest.param({"entity_id": str(ENTITY_ID)}, "^entity_id must be a UUID", id="id-string"),
        pytest.param({"entity_id": ENTITY_ID.int}, "^entity_id must be a UUID", id="id-int"),
        pytest.param({"error_code": None}, "^error_code must match", id="code-none"),
        pytest.param({"error_code": ""}, "^error_code must match", id="code-empty"),
        pytest.param(
            {"error_code": "insufficient_credit"}, "^error_code must match", id="code-lowercase"
        ),
        pytest.param({"error_code": "Not_Found"}, "^error_code must match", id="code-mixed-case"),
        pytest.param({"error_code": "NOT-FOUND"}, "^error_code must match", id="code-hyphen"),
        pytest.param({"error_code": "NOT FOUND"}, "^error_code must match", id="code-space"),
        pytest.param({"error_code": "CODE1"}, "^error_code must match", id="code-digit"),
        pytest.param({"error_code": "CRÉDITO"}, "^error_code must match", id="code-non-ascii"),
        pytest.param({"error_code": "FORBIDDEN\n"}, "^error_code must match", id="code-newline"),
        pytest.param({"error_code": "A" * 65}, "^error_code must match", id="code-65-chars"),
        pytest.param({"error_code": b"FORBIDDEN"}, "^error_code must match", id="code-bytes"),
        pytest.param({"attempt": None}, "^attempt must be a dict, got NoneType$", id="no-attempt"),
        pytest.param({"attempt": []}, "^attempt must be a dict, got list$", id="attempt-list"),
        pytest.param({"attempt": "a=1"}, "^attempt must be a dict, got str$", id="attempt-str"),
        pytest.param(
            {"attempt": {"amount": 1.5}},
            "^attempt.amount has a value of type float",
            id="attempt-float",
        ),
        pytest.param(
            {"attempt": {"amount": Decimal("50.00")}},
            "^attempt.amount has a value of type Decimal",
            id="attempt-decimal",
        ),
        pytest.param(
            {"attempt": {"account_id": uuid4()}},
            "^attempt.account_id has a value of type UUID",
            id="attempt-uuid",
        ),
        pytest.param(
            {"attempt": {"a": [{"b": 0.1}]}},
            "^attempt.a\\[0\\].b has a value of type float",
            id="attempt-deep-float",
        ),
        pytest.param({"attempt": {1: "x"}}, "^attempt has a non-string key 1", id="int-key"),
        pytest.param({"attempt": {"n": "a\x00b"}}, "^attempt.n contains a NUL", id="nul-value"),
    ],
)
def test_record_rejection_rejects_an_invalid_argument(event_count, overrides, message):
    """Fails if a refused attempt can be written with a malformed action, unknown entity type,
    non-UUID entity id, missing or malformed error code, or an attempt that is not a dict of
    JSON-native values (float money included) — nothing may be written then (AUD-1,
    BR-MONEY-3)."""
    with pytest.raises(ValueError, match=message):
        _reject(**overrides)

    assert event_count() == 0


@pytest.mark.django_db(transaction=True)
def test_record_rejection_refuses_to_run_outside_a_transaction(event_count):
    """Fails if a rejection can be written in autocommit, outside the transaction the caller
    chose for it (e.g. the idempotent request's own, so a replay adds nothing) (AUD-2, MOD-4)."""
    with pytest.raises(RuntimeError, match="^record_rejection must run inside transaction.atomic$"):
        _reject()

    assert event_count() == 0


@pytest.mark.django_db
def test_rejection_of_a_rolled_back_savepoint_does_not_exist(stored_event, event_count):
    """Fails if a rejection recorded in a block that rolls back persists, so a discarded request
    (or a replay whose transaction is undone) would still leave a trail (AUD-2)."""

    class _Discarded(Exception):
        pass

    with pytest.raises(_Discarded), transaction.atomic():
        _reject()
        raise _Discarded

    assert event_count() == 0


# --- AUD-4: no secrets in attempts --------------------------------------------------------------

PLACEMENTS = {
    "top": lambda key: {"amount_minor": 5_000, key: "s3cr3t-value"},
    "nested": lambda key: {"card": {"last4": "4242", key: "s3cr3t-value"}},
    "in-list": lambda key: {"cards": [{"id": "c1"}, {key: "s3cr3t-value"}]},
    "key-of-dict-value": lambda key: {key: {"v": 1}},
}

EXPECTED_PATHS = {
    "top": "attempt.{key}",
    "nested": "attempt.card.{key}",
    "in-list": "attempt.cards[1].{key}",
    "key-of-dict-value": "attempt.{key}",
}


@pytest.mark.django_db
@pytest.mark.parametrize("placement", PLACEMENTS.keys())
@pytest.mark.parametrize("key", FORBIDDEN_KEYS)
def test_record_rejection_rejects_an_attempt_with_a_sensitive_key(event_count, key, placement):
    """Fails if a PAN, token, signature, secret or password sent with a refused request can
    reach the audit log at any depth of the attempt, under any letter case (AUD-4,
    SEC-CARD-3)."""
    with pytest.raises(SensitiveDataRejected) as excinfo:
        _reject(attempt=PLACEMENTS[placement](key))

    assert excinfo.value.path == EXPECTED_PATHS[placement].format(key=key)
    assert "s3cr3t-value" not in str(excinfo.value)
    assert event_count() == 0


# --- list_events: both outcomes -----------------------------------------------------------------


@pytest.mark.django_db
def test_list_events_returns_mutations_and_rejections_in_order():
    """Fails if `list_events` drops rejected attempts, or the mutations, of an entity or a
    request, so the INV-6 trail would only show half of what happened."""
    opened = record(
        context=make_context(),
        action="credit_account.opened",
        entity_type="credit_account",
        entity_id=ENTITY_ID,
        before=None,
        after={"status": "ACTIVE"},
    )
    refused = _reject()
    unknown_entity = _reject(entity_id=None, error_code="FORBIDDEN", attempt={})

    assert list_events(entity_id=ENTITY_ID) == [opened, refused]
    assert list_events(request_id=REQUEST_ID) == [opened, refused, unknown_entity]
