"""Database guards on audit events, exercised with raw SQL that bypasses Python.

CHECKs: actor role, action format, entity type, states are JSON objects (`after_state` never
null). AUD-3 (INV-6): the table is append-only; update, delete and (non-superuser) truncate are
rejected.
"""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from audit.tests.conftest import EVENT_TABLE


def _violated_constraint(excinfo) -> str:
    return excinfo.value.__cause__.diag.constraint_name


@pytest.fixture
def event_id(insert_raw_event):
    return insert_raw_event(before_state='{"status": "ACTIVE"}')


# --- CHECK constraints --------------------------------------------------------------------------


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
    ],
)
def test_event_violating_a_check_constraint_is_rejected(
    insert_raw_event, event_count, columns, constraint
):
    """Fails if a row with an unknown role or entity type, a malformed action or a state that is
    not a JSON object can be stored by bypassing the service (AUD-1, INV-6)."""
    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        insert_raw_event(**columns)

    assert _violated_constraint(excinfo) == constraint
    assert event_count() == 0


@pytest.mark.django_db
def test_event_without_after_state_is_rejected(insert_raw_event, event_count):
    """Fails if an event can be stored with no after state at all (SQL NULL) (AUD-1)."""
    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        insert_raw_event(after_state=None)

    assert excinfo.value.__cause__.diag.column_name == "after_state"
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
    )


# --- AUD-3: append-only -------------------------------------------------------------------------

MUTATIONS = {
    "update-after-state": f"UPDATE {EVENT_TABLE} SET after_state = '{{}}' WHERE id = %s",  # noqa: S608
    "update-actor": f"UPDATE {EVENT_TABLE} SET actor_id = gen_random_uuid() WHERE id = %s",  # noqa: S608
    "update-occurred-at": f"UPDATE {EVENT_TABLE} "  # noqa: S608
    "SET occurred_at = occurred_at - interval '1 day' WHERE id = %s",
    "update-noop": f"UPDATE {EVENT_TABLE} SET action = action WHERE id = %s",  # noqa: S608
    "delete": f"DELETE FROM {EVENT_TABLE} WHERE id = %s",  # noqa: S608
}


@pytest.mark.django_db
@pytest.mark.parametrize("sql", MUTATIONS.values(), ids=MUTATIONS.keys())
def test_update_and_delete_of_an_event_are_rejected(raw_sql, stored_event, event_id, sql):
    """Fails if an audit event can be rewritten or erased, even with raw SQL by a role that holds
    the privileges, so the trail of a mutation could be hidden (AUD-3, INV-6)."""
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
