"""Database guards on audit events, exercised with raw SQL that bypasses Python.

CHECKs: actor role, action format, entity type, outcome, error code format, states and attempts
are JSON objects; a success is a mutation (entity, after state, no error code or attempt) and a
rejection changed nothing (no states, an error code and an attempt). AUD-3 (INV-6): the table is
append-only whatever the outcome; update, delete and (non-superuser) truncate are rejected.
"""

from uuid import UUID, uuid4

import pytest
from django.db import DataError, IntegrityError, transaction

from audit.tests.conftest import EVENT_TABLE, REJECTED_COLUMNS

ENTITY_ID = UUID("7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5d")


def _violated_constraint(excinfo) -> str:
    return excinfo.value.__cause__.diag.constraint_name


def _rejected(**overrides) -> dict:
    return REJECTED_COLUMNS | overrides


@pytest.fixture
def event_id(insert_raw_event):
    return insert_raw_event(before_state='{"status": "ACTIVE"}')


# --- CHECK constraints --------------------------------------------------------------------------

SUCCEEDED_IS_MUTATION = "audit_auditevent_succeeded_is_mutation"
REJECTED_CHANGED_NOTHING = "audit_auditevent_rejected_changed_nothing"
ERROR_CODE_FORMAT = "audit_auditevent_error_code_format"
ATTEMPT_IS_OBJECT = "audit_auditevent_attempt_is_object"


# Each row violates exactly one constraint, so the reported name proves which CHECK caught it.
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        pytest.param({"actor_role": "admin"}, "audit_auditevent_actor_role_valid", id="role"),
        pytest.param({"actor_role": "CUSTOMER"}, "audit_auditevent_actor_role_valid", id="role-uc"),
        pytest.param({"action": "deposit"}, "audit_auditevent_action_format", id="no-verb"),
        pytest.param({"action": "Deposit.ok"}, "audit_auditevent_action_format", id="uppercase"),
        pytest.param({"action": "a.b.c"}, "audit_auditevent_action_format", id="three-parts"),
        pytest.param({"action": "a.b\n"}, "audit_auditevent_action_format", id="trailing-nl"),
        pytest.param({"action": "a1.b"}, "audit_auditevent_action_format", id="digit"),
        pytest.param({"entity_type": "account"}, "audit_auditevent_entity_type_valid", id="type"),
        pytest.param(
            {"after_state": "null"}, "audit_auditevent_after_state_is_object", id="after-json-null"
        ),
        pytest.param(
            {"after_state": "[]"}, "audit_auditevent_after_state_is_object", id="after-array"
        ),
        pytest.param(
            {"after_state": '"x"'}, "audit_auditevent_after_state_is_object", id="after-string"
        ),
        pytest.param(
            {"before_state": "null"},
            "audit_auditevent_before_state_is_object",
            id="before-json-null",
        ),
        pytest.param(
            {"before_state": "[1]"}, "audit_auditevent_before_state_is_object", id="before-array"
        ),
        # Outcome values.
        pytest.param({"outcome": "failed"}, "audit_auditevent_outcome_valid", id="outcome-unknown"),
        pytest.param({"outcome": "SUCCEEDED"}, "audit_auditevent_outcome_valid", id="outcome-uc"),
        pytest.param({"outcome": ""}, "audit_auditevent_outcome_valid", id="outcome-empty"),
        # A success is a mutation.
        pytest.param({"entity_id": None}, SUCCEEDED_IS_MUTATION, id="success-without-entity"),
        pytest.param({"after_state": None}, SUCCEEDED_IS_MUTATION, id="success-without-after"),
        pytest.param(
            {"error_code": "INSUFFICIENT_CREDIT"}, SUCCEEDED_IS_MUTATION, id="success-with-code"
        ),
        pytest.param({"attempt": "{}"}, SUCCEEDED_IS_MUTATION, id="success-with-attempt"),
        # A rejection changed nothing.
        pytest.param(
            _rejected(before_state='{"status": "ACTIVE"}'),
            REJECTED_CHANGED_NOTHING,
            id="rejection-with-before",
        ),
        pytest.param(
            _rejected(after_state='{"status": "ACTIVE"}'),
            REJECTED_CHANGED_NOTHING,
            id="rejection-with-after",
        ),
        pytest.param(
            _rejected(error_code=None), REJECTED_CHANGED_NOTHING, id="rejection-without-code"
        ),
        pytest.param(
            _rejected(attempt=None), REJECTED_CHANGED_NOTHING, id="rejection-without-attempt"
        ),
        # Error code format (any outcome; only rejections may carry one).
        pytest.param(
            _rejected(error_code="insufficient_credit"), ERROR_CODE_FORMAT, id="code-lowercase"
        ),
        pytest.param(_rejected(error_code="NOT-FOUND"), ERROR_CODE_FORMAT, id="code-hyphen"),
        pytest.param(_rejected(error_code="CODE1"), ERROR_CODE_FORMAT, id="code-digit"),
        pytest.param(_rejected(error_code="NOT FOUND"), ERROR_CODE_FORMAT, id="code-space"),
        pytest.param(_rejected(error_code="CODE\n"), ERROR_CODE_FORMAT, id="code-trailing-nl"),
        pytest.param(_rejected(error_code=""), ERROR_CODE_FORMAT, id="code-empty"),
        pytest.param(_rejected(error_code="CRÉDITO"), ERROR_CODE_FORMAT, id="code-non-ascii"),
        # Attempts are JSON objects.
        pytest.param(_rejected(attempt="[]"), ATTEMPT_IS_OBJECT, id="attempt-array"),
        pytest.param(_rejected(attempt='"x"'), ATTEMPT_IS_OBJECT, id="attempt-string"),
        pytest.param(_rejected(attempt="5000"), ATTEMPT_IS_OBJECT, id="attempt-number"),
        pytest.param(_rejected(attempt="null"), ATTEMPT_IS_OBJECT, id="attempt-json-null"),
    ],
)
def test_event_violating_a_check_constraint_is_rejected(
    insert_raw_event, event_count, columns, constraint
):
    """Fails if a row with an unknown role, entity type or outcome, a malformed action or error
    code, a JSON column that is not an object, a "success" that mutated nothing identifiable or
    a "rejection" that claims to have changed state can be stored by bypassing the service
    (AUD-1, INV-6)."""
    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        insert_raw_event(**columns)

    assert _violated_constraint(excinfo) == constraint
    assert event_count() == 0


@pytest.mark.django_db
def test_event_without_outcome_is_rejected(insert_raw_event, event_count):
    """Fails if a row written outside the service can leave its outcome unstated (SQL NULL or a
    silent database default) instead of declaring success or rejection (INV-6)."""
    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        insert_raw_event(outcome=None)

    assert excinfo.value.__cause__.diag.column_name == "outcome"
    assert event_count() == 0


@pytest.mark.django_db
def test_error_code_longer_than_64_characters_is_rejected(insert_raw_event, event_count):
    """Fails if the error code column silently accepts (or truncates) a code over 64 characters
    (AUD-1)."""
    with pytest.raises(DataError), transaction.atomic():
        insert_raw_event(**_rejected(error_code="A" * 65))

    assert event_count() == 0


@pytest.mark.django_db
def test_event_satisfying_every_check_is_stored(insert_raw_event, stored_event):
    """Fails if the CHECKs are stricter than the contract and reject a valid creation event
    (NULL `before_state`, no request) inserted directly (AUD-1)."""
    deposit_id = uuid4()

    event_id = insert_raw_event(
        actor_role="payment_provider",
        action="deposit.requested",
        entity_type="collateral_deposit",
        entity_id=deposit_id,
        request_id=None,
        idempotency_key=None,
    )

    assert stored_event(event_id)[2:] == (
        "payment_provider",
        "deposit.requested",
        "collateral_deposit",
        deposit_id,
        None,
        {"status": "ACTIVE"},
        None,
        None,
        "succeeded",
        None,
        None,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        pytest.param(
            {"entity_id": None, "attempt": "{}", "error_code": "FORBIDDEN"},
            (None, None, None, "rejected", "FORBIDDEN", {}),
            id="no-entity-empty-attempt",
        ),
        pytest.param(
            {"entity_id": ENTITY_ID, "error_code": "A" * 64},
            (ENTITY_ID, None, None, "rejected", "A" * 64, {"amount_minor": 5000}),
            id="entity-64-char-code",
        ),
        pytest.param(
            {"entity_id": ENTITY_ID, "error_code": "_", "attempt": '{"filters": {"limit": 10}}'},
            (ENTITY_ID, None, None, "rejected", "_", {"filters": {"limit": 10}}),
            id="underscore-code-nested-attempt",
        ),
    ],
)
def test_rejection_satisfying_every_check_is_stored(
    insert_raw_event, stored_event, columns, expected
):
    """Fails if the CHECKs reject a valid rejection: no entity, an empty attempt, a 64-character
    or single-underscore code, or a nested attempt object (AUD-1)."""
    event_id = insert_raw_event(**_rejected(**columns))

    stored = stored_event(event_id)
    assert (*stored[5:8], *stored[10:]) == expected


# --- AUD-3: append-only -------------------------------------------------------------------------

MUTATIONS = {
    "update-after-state": f"UPDATE {EVENT_TABLE} SET after_state = '{{}}' WHERE id = %s",  # noqa: S608
    "update-actor": f"UPDATE {EVENT_TABLE} SET actor_id = gen_random_uuid() WHERE id = %s",  # noqa: S608
    "update-occurred-at": f"UPDATE {EVENT_TABLE} "  # noqa: S608
    "SET occurred_at = occurred_at - interval '1 day' WHERE id = %s",
    "update-noop": f"UPDATE {EVENT_TABLE} SET action = action WHERE id = %s",  # noqa: S608
    "update-outcome": f"UPDATE {EVENT_TABLE} SET outcome = 'rejected' WHERE id = %s",  # noqa: S608
    "update-error-code": f"UPDATE {EVENT_TABLE} SET error_code = NULL WHERE id = %s",  # noqa: S608
    "update-attempt": f"UPDATE {EVENT_TABLE} SET attempt = '{{}}' WHERE id = %s",  # noqa: S608
    "delete": f"DELETE FROM {EVENT_TABLE} WHERE id = %s",  # noqa: S608
}

OUTCOME_ROWS = {
    "succeeded": {"before_state": '{"status": "ACTIVE"}'},
    "rejected": REJECTED_COLUMNS,
}


@pytest.mark.django_db
@pytest.mark.parametrize("row", OUTCOME_ROWS.values(), ids=OUTCOME_ROWS.keys())
@pytest.mark.parametrize("sql", MUTATIONS.values(), ids=MUTATIONS.keys())
def test_update_and_delete_of_an_event_are_rejected(
    raw_sql, insert_raw_event, stored_event, row, sql
):
    """Fails if an audit event of either outcome can be rewritten or erased, even with raw SQL by
    a role that holds the privileges, so the trail of a mutation or of a refused attempt could be
    hidden or a rejection turned into a success (AUD-3, INV-6)."""
    event_id = insert_raw_event(**row)
    before = stored_event(event_id)

    with (
        pytest.raises(
            IntegrityError, match="AUD-3: audit_auditevent is append-only; (UPDATE|DELETE)"
        ),
        transaction.atomic(),
    ):
        raw_sql(sql, (event_id,))

    assert stored_event(event_id) == before


@pytest.mark.django_db
def test_truncate_is_rejected_for_a_non_superuser_role(
    raw_sql, event_count, event_id, non_superuser_role
):
    """Fails if the application (non-superuser) role can TRUNCATE the audit log (AUD-3, LED-4
    pattern)."""
    with (
        pytest.raises(IntegrityError, match="AUD-3: audit_auditevent is append-only; TRUNCATE"),
        transaction.atomic(),
    ):
        raw_sql(f"SET LOCAL ROLE {non_superuser_role}")
        raw_sql(f"TRUNCATE {EVENT_TABLE}")

    assert event_count() == 1


@pytest.mark.django_db
def test_truncate_is_allowed_for_a_superuser(raw_sql, event_count, event_id):
    """Fails if the superuser exemption is lost, which would break Django's test flush (AUD-3,
    LED-4 pattern)."""
    is_superuser = raw_sql("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")

    raw_sql(f"TRUNCATE {EVENT_TABLE}")

    assert is_superuser == [(True,)]
    assert event_count() == 0
