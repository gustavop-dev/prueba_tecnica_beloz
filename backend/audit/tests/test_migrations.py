"""Migration `0003_rejected_attempts`, run forwards and backwards against real PostgreSQL.

Existing events become successes without any `UPDATE` (which the AUD-3 trigger forbids); the
migration reverses cleanly while the table holds only successes, and refuses to reverse once it
holds a rejection (which the old schema cannot store and AUD-3 forbids deleting).
"""

from datetime import datetime
from uuid import UUID

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

from audit.tests.conftest import ACTOR_ID, EVENT_TABLE, REJECTED_COLUMNS, REQUEST_ID

PREVIOUS = ("audit", "0002_event_guards")
CURRENT = ("audit", "0003_rejected_attempts")

OLD_EVENT_ID = UUID("6e5d4c3b-2a19-4807-b6a5-948372615041")
OLD_ENTITY_ID = UUID("0a1b2c3d-4e5f-4061-8728-394a5b6c7d8e")

OLD_CHECKS = {
    "audit_auditevent_actor_role_valid",
    "audit_auditevent_action_format",
    "audit_auditevent_entity_type_valid",
    "audit_auditevent_after_state_is_object",
    "audit_auditevent_before_state_is_object",
}
NEW_CHECKS = OLD_CHECKS | {
    "audit_auditevent_outcome_valid",
    "audit_auditevent_attempt_is_object",
    "audit_auditevent_error_code_format",
    "audit_auditevent_succeeded_is_mutation",
    "audit_auditevent_rejected_changed_nothing",
}
AUD_3_TRIGGERS = {"audit_auditevent_append_only", "audit_auditevent_no_truncate"}

# (is_nullable per column) for the columns the migration adds or relaxes.
OLD_COLUMNS = {"entity_id": "NO", "after_state": "NO"}
NEW_COLUMNS = {
    "entity_id": "YES",
    "after_state": "YES",
    "outcome": "NO",
    "error_code": "YES",
    "attempt": "YES",
}


def _migrate_to(target: tuple[str, str]) -> None:
    MigrationExecutor(connection).migrate([target])


@pytest.fixture
def migrate_to():
    """Migrate the audit app to a node; afterwards, always restore every app's latest schema."""
    yield _migrate_to
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.fixture
def schema(raw_sql):
    """The audit table's relevant columns (nullability), CHECK constraints and triggers."""

    def read() -> tuple[dict[str, str], set[str], set[str]]:
        columns = dict(
            raw_sql(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = ANY(%s)",
                (EVENT_TABLE, list(NEW_COLUMNS)),
            )
        )
        checks = {
            name
            for (name,) in raw_sql(
                "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c'",
                (EVENT_TABLE,),
            )
        }
        triggers = {
            name
            for (name,) in raw_sql(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal",
                (EVENT_TABLE,),
            )
        }
        return columns, checks, triggers

    return read


@pytest.mark.django_db(transaction=True)
def test_existing_events_become_successes(migrate_to, raw_sql, stored_event):
    """Fails if an event written before the migration is left without an outcome, is turned
    into a rejection, gains an error code or attempt, or has any recorded field altered —
    or if the backfill needs an UPDATE that AUD-3 rejects (INV-6, AUD-3)."""
    migrate_to(PREVIOUS)
    raw_sql(
        f"INSERT INTO {EVENT_TABLE} (id, occurred_at, actor_id, actor_role, action, "  # noqa: S608
        "entity_type, entity_id, before_state, after_state, request_id, idempotency_key) "
        "VALUES (%s, %s, %s, 'customer', 'card.frozen', 'card', %s, "
        """'{"status": "ACTIVE"}', '{"status": "FROZEN"}', %s, NULL)""",
        (
            OLD_EVENT_ID,
            datetime.fromisoformat("2026-10-01T09:30:00+00:00"),
            ACTOR_ID,
            OLD_ENTITY_ID,
            REQUEST_ID,
        ),
    )

    migrate_to(CURRENT)

    assert stored_event(OLD_EVENT_ID) == (
        datetime.fromisoformat("2026-10-01T09:30:00+00:00"),
        ACTOR_ID,
        "customer",
        "card.frozen",
        "card",
        OLD_ENTITY_ID,
        {"status": "ACTIVE"},
        {"status": "FROZEN"},
        REQUEST_ID,
        None,
        "succeeded",
        None,
        None,
    )


@pytest.mark.django_db(transaction=True)
def test_migration_reverses_to_the_previous_schema(migrate_to, insert_raw_event, schema):
    """Fails if reversing the migration with only successes stored fails, leaves the new columns
    or CHECKs behind, does not restore NOT NULL on `entity_id`/`after_state`, or drops the AUD-3
    triggers (AUD-3)."""
    insert_raw_event()

    migrate_to(PREVIOUS)

    assert schema() == (OLD_COLUMNS, OLD_CHECKS, AUD_3_TRIGGERS)


@pytest.mark.django_db(transaction=True)
def test_migration_reapplies_after_a_reversal(migrate_to, insert_raw_event, schema, stored_event):
    """Fails if the migration cannot be applied again after a reversal, or the round trip loses
    the stored events, a CHECK or an AUD-3 trigger (AUD-3, INV-6)."""
    event_id = insert_raw_event()
    migrate_to(PREVIOUS)

    migrate_to(CURRENT)

    assert schema() == (NEW_COLUMNS, NEW_CHECKS, AUD_3_TRIGGERS)
    assert stored_event(event_id)[10:] == ("succeeded", None, None)


@pytest.mark.django_db(transaction=True)
def test_reversal_is_refused_while_rejections_exist(
    migrate_to, insert_raw_event, schema, stored_event
):
    """Fails if reversing the migration with a rejection stored half-applies, silently turns the
    rejection into a success, or deletes it (AUD-3): it must refuse and change nothing."""
    event_id = insert_raw_event(**REJECTED_COLUMNS)

    with pytest.raises(IntegrityError, match="AUD-3: audit_auditevent holds rejected events"):
        migrate_to(PREVIOUS)

    assert CURRENT in MigrationRecorder(connection).applied_migrations()
    assert schema() == (NEW_COLUMNS, NEW_CHECKS, AUD_3_TRIGGERS)
    assert stored_event(event_id)[10:] == (
        "rejected",
        "INSUFFICIENT_CREDIT",
        {"amount_minor": 5000},
    )
