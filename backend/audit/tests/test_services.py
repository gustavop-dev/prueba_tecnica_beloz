"""`audit.services`: recording (AUD-1, AUD-2, AUD-4) and listing audit events (INV-6)."""

from datetime import datetime
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
)
from audit.tests.conftest import ACTOR_ID, IDEMPOTENCY_KEY, REQUEST_ID, make_context

ENTITY_ID = UUID("3c2b1a09-8f7e-4d6c-b5a4-938271605f4e")
BEFORE = {
    "status": "ACTIVE",
    "outstanding_minor": 0,
    "flags": [True, None],
    "card": {"last4": "4242"},
}
AFTER = {"status": "ACTIVE", "outstanding_minor": 1_500, "flags": [], "card": {"last4": "4242"}}


def _record(**overrides) -> AuditEventDTO:
    kwargs = {
        "context": make_context(),
        "action": "credit_account.purchase_authorized",
        "entity_type": "credit_account",
        "entity_id": ENTITY_ID,
        "before": BEFORE,
        "after": AFTER,
    } | overrides
    return record(**kwargs)


# --- record: stored fields ----------------------------------------------------------------------


@pytest.mark.django_db
def test_record_stores_every_field_exactly(stored_event):
    """Fails if any audited field (who, what, when, which request) is dropped or altered on its
    way to the row, losing part of the INV-6 trail, or if a mutation is not stored as a success
    without error code or attempt (AUD-1)."""
    start = timezone.now()

    event = _record()

    stored = stored_event(event.id)
    assert start <= stored[0] <= timezone.now()
    assert stored[1:] == (
        ACTOR_ID,
        "operator",
        "credit_account.purchase_authorized",
        "credit_account",
        ENTITY_ID,
        BEFORE,
        AFTER,
        REQUEST_ID,
        IDEMPOTENCY_KEY,
        "succeeded",
        None,
        None,
    )


@pytest.mark.django_db
def test_record_returns_the_event_as_stored(stored_event):
    """Fails if the returned DTO differs from the row, e.g. a timestamp that was not persisted
    or states that are not the stored ones (DEP-3)."""
    event = _record()

    assert event == AuditEventDTO(
        id=event.id,
        occurred_at=stored_event(event.id)[0],
        actor_id=ACTOR_ID,
        actor_role="operator",
        action="credit_account.purchase_authorized",
        entity_type="credit_account",
        entity_id=ENTITY_ID,
        before_state=BEFORE,
        after_state=AFTER,
        request_id=REQUEST_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        outcome="succeeded",
        error_code=None,
        attempt=None,
    )
    assert isinstance(event.id, UUID)


@pytest.mark.django_db
def test_record_of_a_creation_outside_http_stores_nulls(stored_event):
    """Fails if a creation (`before=None`) recorded outside a request (no request id, no key)
    is rejected or stored with invented values (AUD-1, OBS-2)."""
    provider_id = uuid4()
    context = make_context(
        actor_id=provider_id, actor_role="payment_provider", request_id=None, idempotency_key=None
    )

    event = _record(
        context=context,
        action="deposit.requested",
        entity_type="collateral_deposit",
        before=None,
        after={"status": "PENDING", "amount_minor": 50_000},
    )

    assert stored_event(event.id)[1:] == (
        provider_id,
        "payment_provider",
        "deposit.requested",
        "collateral_deposit",
        ENTITY_ID,
        None,
        {"status": "PENDING", "amount_minor": 50_000},
        None,
        None,
        "succeeded",
        None,
        None,
    )


@pytest.mark.django_db
def test_record_is_not_affected_by_later_changes_to_the_callers_states(stored_event):
    """Fails if the event keeps a reference to the caller's dict, so mutating the entity state
    after recording would rewrite the returned audit snapshot (AUD-1)."""
    after = {"status": "ACTIVE", "card": {"last4": "4242"}}
    event = _record(after=after)

    after["card"]["last4"] = "0000"

    assert event.after_state == {"status": "ACTIVE", "card": {"last4": "4242"}}
    assert stored_event(event.id)[7] == {"status": "ACTIVE", "card": {"last4": "4242"}}


# --- record: argument validation ----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"actor_role": "admin"}, id="role-unknown"),
        pytest.param({"actor_role": "Customer"}, id="role-wrong-case"),
        pytest.param({"actor_role": ""}, id="role-empty"),
        pytest.param({"actor_role": None}, id="role-none"),
        pytest.param({"actor_id": str(ACTOR_ID)}, id="actor-id-string"),
        pytest.param({"actor_id": None}, id="actor-id-none"),
        pytest.param({"request_id": str(REQUEST_ID)}, id="request-id-string"),
        pytest.param({"idempotency_key": str(IDEMPOTENCY_KEY)}, id="idempotency-key-string"),
    ],
)
def test_record_rejects_an_invalid_context(event_count, overrides):
    """Fails if an event can be attributed to an unknown role or to an identifier that is not
    a UUID, making the "who" and "which request" of INV-6 unreliable (AUD-1)."""
    with pytest.raises(ValueError, match="^context\\."):
        _record(context=make_context(**overrides))

    assert event_count() == 0


@pytest.mark.django_db
def test_record_rejects_a_context_that_is_not_an_audit_context(event_count):
    """Fails if a plain dict is accepted as context, bypassing its field validation (AUD-1)."""
    context = {"actor_id": ACTOR_ID, "actor_role": "operator", "request_id": None}

    with pytest.raises(ValueError, match="^context must be an AuditContext, got dict$"):
        _record(context=context)

    assert event_count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "action",
    [
        pytest.param("deposit", id="no-verb"),
        pytest.param("deposit.", id="empty-verb"),
        pytest.param(".confirmed", id="empty-entity"),
        pytest.param("deposit.confirmed.twice", id="three-parts"),
        pytest.param("Deposit.confirmed", id="uppercase"),
        pytest.param("deposit-x.confirmed", id="hyphen"),
        pytest.param("deposit 2.confirmed", id="space-and-digit"),
        pytest.param("deposit.confirmed\n", id="trailing-newline"),
        pytest.param("a." + "b" * 63, id="65-chars"),
        pytest.param("", id="empty"),
        pytest.param(None, id="none"),
        pytest.param(b"deposit.confirmed", id="bytes"),
    ],
)
def test_record_rejects_a_malformed_action(event_count, action):
    """Fails if an action outside `<entity>.<verb>` (lowercase, underscores, <= 64 chars) is
    accepted, so events could not be grouped or searched by action (AUD-1)."""
    with pytest.raises(ValueError, match="^action must match"):
        _record(action=action)

    assert event_count() == 0


@pytest.mark.django_db
def test_record_accepts_an_action_of_exactly_64_characters(stored_event):
    """Fails if the action length limit is off by one against the 64-character column (AUD-1)."""
    action = "a." + "b" * 62

    event = _record(action=action)

    assert stored_event(event.id)[3] == action


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"entity_type": "account"}, "^entity_type must be one of", id="type-unknown"),
        pytest.param({"entity_type": "CARD"}, "^entity_type must be one of", id="type-uppercase"),
        pytest.param({"entity_type": None}, "^entity_type must be one of", id="type-none"),
        pytest.param({"entity_id": str(ENTITY_ID)}, "^entity_id must be a UUID", id="id-string"),
        pytest.param({"entity_id": None}, "^entity_id must be a UUID", id="id-none"),
        pytest.param({"entity_id": ENTITY_ID.int}, "^entity_id must be a UUID", id="id-int"),
    ],
)
def test_record_rejects_an_invalid_entity(event_count, overrides, message):
    """Fails if an event can point to an unknown kind of entity or a non-UUID id, so the "what
    it was done to" of INV-6 could not be resolved (AUD-1)."""
    with pytest.raises(ValueError, match=message):
        _record(**overrides)

    assert event_count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"after": None}, "^after must be a dict, got NoneType$", id="after-missing"),
        pytest.param({"after": []}, "^after must be a dict, got list$", id="after-list"),
        pytest.param({"after": "ACTIVE"}, "^after must be a dict, got str$", id="after-string"),
        pytest.param({"before": []}, "^before must be a dict, got list$", id="before-list"),
        pytest.param({"before": "x"}, "^before must be a dict, got str$", id="before-string"),
        pytest.param({"before": 0}, "^before must be a dict, got int$", id="before-int"),
    ],
)
def test_record_rejects_states_that_are_not_dicts(event_count, overrides, message):
    """Fails if an event can be written without an after state, or with a state that is not an
    object, losing the before/after snapshot of the mutation (AUD-1)."""
    with pytest.raises(ValueError, match=message):
        _record(**overrides)

    assert event_count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("after", "message"),
    [
        pytest.param({"amount": 1.5}, "^after.amount has a value of type float", id="float"),
        pytest.param(
            {"amount": Decimal("1.50")}, "^after.amount has a value of type Decimal", id="decimal"
        ),
        pytest.param({"id": uuid4()}, "^after.id has a value of type UUID", id="uuid"),
        pytest.param(
            {"at": datetime.fromisoformat("2026-10-30T12:00:00+00:00")},
            "^after.at has a value of type datetime",
            id="datetime",
        ),
        pytest.param({"ids": ("a", "b")}, "^after.ids has a value of type tuple", id="tuple"),
        pytest.param({"ids": {"a"}}, "^after.ids has a value of type set", id="set"),
        pytest.param({"raw": b"x"}, "^after.raw has a value of type bytes", id="bytes"),
        pytest.param({"a": [{"b": 0.1}]}, "^after.a\\[0\\].b has a value of type float", id="deep"),
        pytest.param({1: "x"}, "^after has a non-string key 1", id="int-key"),
        pytest.param({"note": "a\x00b"}, "^after.note contains a NUL", id="nul-value"),
        pytest.param({"a\x00": "b"}, "^after key contains a NUL", id="nul-key"),
        pytest.param({"note": "\ud800"}, "^after.note is not valid UTF-8", id="lone-surrogate"),
    ],
)
def test_record_rejects_states_that_are_not_json_compatible(event_count, after, message):
    """Fails if a state that PostgreSQL would reject or silently change (float money, UUID,
    tuple read back as list, int key read back as str, NUL) reaches the database (AUD-1,
    BR-MONEY-3)."""
    with pytest.raises(ValueError, match=message):
        _record(after=after)

    assert event_count() == 0


@pytest.mark.django_db
def test_record_rejects_a_cyclic_state(event_count):
    """Fails if a self-referencing state recurses without bound instead of being rejected."""
    after: dict = {"status": "ACTIVE"}
    after["self"] = after

    with pytest.raises(ValueError, match="nests deeper than 16 levels"):
        _record(after=after)

    assert event_count() == 0


@pytest.mark.django_db(transaction=True)
def test_record_refuses_to_run_outside_a_transaction(event_count):
    """Fails if an event can be written in its own autocommit transaction, detached from the
    mutation it describes, so one could persist without the other (AUD-2, MOD-4)."""
    with pytest.raises(RuntimeError, match="^record must run inside transaction.atomic$"):
        _record()

    assert event_count() == 0


# --- AUD-4: no secrets in audit states ----------------------------------------------------------

FORBIDDEN_KEYS = [
    "token",
    "card_token",
    "accessToken",
    "pan",
    "PAN",
    "masked_pan",
    "card_number",
    "Card_Number",
    "signature",
    "webhook_signature",
    "secret",
    "client_secret",
    "password",
    "db_password",
]

PLACEMENTS = {
    "after-top": lambda key: {"after": {"status": "ACTIVE", key: "s3cr3t-value"}},
    "before-top": lambda key: {"before": {"status": "ACTIVE", key: "s3cr3t-value"}},
    "after-nested": lambda key: {"after": {"card": {"last4": "4242", key: "s3cr3t-value"}}},
    "after-in-list": lambda key: {"after": {"cards": [{"id": "c1"}, {key: "s3cr3t-value"}]}},
    "before-key-of-dict-value": lambda key: {"before": {key: {"v": 1}}},
}

EXPECTED_PATHS = {
    "after-top": "after.{key}",
    "before-top": "before.{key}",
    "after-nested": "after.card.{key}",
    "after-in-list": "after.cards[1].{key}",
    "before-key-of-dict-value": "before.{key}",
}


@pytest.mark.django_db
@pytest.mark.parametrize("placement", PLACEMENTS.keys())
@pytest.mark.parametrize("key", FORBIDDEN_KEYS)
def test_record_rejects_a_state_with_a_sensitive_key(event_count, key, placement):
    """Fails if a PAN, token, signature, secret or password can reach the audit log at any depth
    of either state, under any letter case (AUD-4, SEC-CARD-3)."""
    with pytest.raises(SensitiveDataRejected) as excinfo:
        _record(**PLACEMENTS[placement](key))

    assert excinfo.value.path == EXPECTED_PATHS[placement].format(key=key)
    assert "s3cr3t-value" not in str(excinfo.value)
    assert event_count() == 0


@pytest.mark.django_db
def test_record_accepts_the_last_four_digits_of_a_card(stored_event):
    """Fails if AUD-4 also rejects the non-sensitive card data the system is allowed to keep
    (last 4 digits and internal id, SEC-CARD-2)."""
    after = {"card_id": "c1", "last4": "4242", "status": "ACTIVE"}

    event = _record(entity_type="card", action="card.issued", before=None, after=after)

    assert stored_event(event.id)[7] == after


# --- AUD-2: same transaction as the mutation ----------------------------------------------------


class _MutationFailed(Exception):
    pass


def _record_then_fail() -> UUID:
    event_id = None
    try:
        with transaction.atomic():
            event_id = _record().id
            raise _MutationFailed
    except _MutationFailed:
        return event_id


@pytest.mark.django_db(transaction=True)
def test_event_of_a_rolled_back_transaction_does_not_exist(stored_event):
    """Fails if the audit event survives a rollback of the mutation it describes, recording
    something that never happened (AUD-2)."""
    event_id = _record_then_fail()

    assert isinstance(event_id, UUID)
    assert stored_event(event_id) is None


@pytest.mark.django_db(transaction=True)
def test_event_of_a_committed_transaction_persists(stored_event):
    """Fails if the event is not committed together with the mutation (AUD-2)."""
    with transaction.atomic():
        event_id = _record().id

    assert stored_event(event_id)[3] == "credit_account.purchase_authorized"


@pytest.mark.django_db
def test_event_of_a_rolled_back_savepoint_does_not_exist(stored_event):
    """Fails if an event recorded in a nested block that rolls back persists in the outer
    transaction, as when one step of a flow fails and is undone (AUD-2)."""
    kept = _record().id
    discarded = _record_then_fail()

    assert stored_event(discarded) is None
    assert stored_event(kept)[3] == "credit_account.purchase_authorized"


# --- list_events --------------------------------------------------------------------------------

T0 = "2026-10-30T12:00:00+00:00"
T1 = "2026-10-30T12:00:01+00:00"
T2 = "2026-10-30T12:00:02+00:00"
LOW_ID = UUID("00000000-0000-4000-8000-000000000001")
HIGH_ID = UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")


@pytest.fixture
def listed_events(insert_raw_event) -> dict[str, UUID]:
    """Events of two entities and two requests, inserted out of chronological order.

    `a-late` and `a-tie-high` share T2 and `a-tie-high` has the highest id, so ordering by id
    alone, by insertion or by time alone would all give a different order.
    """
    entity_a, entity_b, other_request = (
        UUID("aaaaaaaa-0000-4000-8000-000000000000"),
        UUID("bbbbbbbb-0000-4000-8000-000000000000"),
        UUID("cccccccc-0000-4000-8000-000000000000"),
    )
    ids = {
        "a-tie-high": insert_raw_event(id=HIGH_ID, entity_id=entity_a, occurred_at=T2),
        "a-early": insert_raw_event(entity_id=entity_a, occurred_at=T0),
        "a-tie-low": insert_raw_event(id=LOW_ID, entity_id=entity_a, occurred_at=T2),
        "a-other-request": insert_raw_event(
            entity_id=entity_a, occurred_at=T1, request_id=other_request
        ),
        "b": insert_raw_event(entity_id=entity_b, occurred_at=T1),
        "b-no-request": insert_raw_event(entity_id=entity_b, occurred_at=T0, request_id=None),
    }
    return ids | {"entity_a": entity_a, "entity_b": entity_b, "other_request": other_request}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        pytest.param(
            lambda ids: {"entity_id": ids["entity_a"]},
            ["a-early", "a-other-request", "a-tie-low", "a-tie-high"],
            id="by-entity",
        ),
        pytest.param(
            lambda ids: {"entity_id": ids["entity_b"]},
            ["b-no-request", "b"],
            id="by-other-entity",
        ),
        pytest.param(
            lambda ids: {"request_id": REQUEST_ID},
            ["a-early", "b", "a-tie-low", "a-tie-high"],
            id="by-request",
        ),
        pytest.param(
            lambda ids: {"entity_id": ids["entity_a"], "request_id": ids["other_request"]},
            ["a-other-request"],
            id="by-entity-and-request",
        ),
        pytest.param(lambda ids: {"entity_id": uuid4()}, [], id="unknown-entity"),
    ],
)
def test_list_events_filters_and_orders_by_time_then_id(listed_events, filters, expected):
    """Fails if `list_events` leaks events of another entity or request, ignores one of two
    filters, or returns them in any order other than `occurred_at` then `id` (INV-6)."""
    events = list_events(**filters(listed_events))

    assert [event.id for event in events] == [listed_events[name] for name in expected]


@pytest.mark.django_db
def test_list_events_returns_the_recorded_events():
    """Fails if `list_events` returns model instances or drops fields instead of the DTOs that
    `record` produced (DEP-3)."""
    first = _record(before=None, action="credit_account.opened")
    second = _record()

    assert list_events(entity_id=ENTITY_ID) == [first, second]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("filters", "message"),
    [
        pytest.param({}, "^list_events requires entity_id, request_id or both$", id="no-filter"),
        pytest.param(
            {"entity_id": None, "request_id": None},
            "^list_events requires entity_id, request_id or both$",
            id="both-none",
        ),
        pytest.param({"entity_id": str(ENTITY_ID)}, "^entity_id must be a UUID", id="entity-str"),
        pytest.param({"request_id": "req-1"}, "^request_id must be a UUID", id="request-str"),
    ],
)
def test_list_events_rejects_missing_or_invalid_filters(filters, message):
    """Fails if the whole audit log can be listed without a filter, or a string filter silently
    matches nothing instead of signalling the programming error."""
    with pytest.raises(ValueError, match=message):
        list_events(**filters)
