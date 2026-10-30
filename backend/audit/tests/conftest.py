import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from django.db import connection

from audit.services import AuditContext

EVENT_TABLE = "audit_auditevent"
APP_ROLE = "audit_test_app_role"

ACTOR_ID = UUID("0b6f3c1e-2d4a-4f8b-9c7e-1a2b3c4d5e6f")
REQUEST_ID = UUID("5d0e9a3b-7c21-4e6f-8a9b-0c1d2e3f4a5b")
IDEMPOTENCY_KEY = UUID("9f8e7d6c-5b4a-4321-8fed-cba987654321")


def make_context(**overrides) -> AuditContext:
    """An operator context inside an HTTP request with an idempotency key, unless overridden."""
    fields = {
        "actor_id": ACTOR_ID,
        "actor_role": "operator",
        "request_id": REQUEST_ID,
        "idempotency_key": IDEMPOTENCY_KEY,
    } | overrides
    return AuditContext(**fields)


@pytest.fixture
def raw_sql() -> Callable[..., list[tuple] | None]:
    """Run raw SQL, bypassing Python, and return all rows (or None for statements)."""

    def run(sql: str, params: tuple = ()) -> list[tuple] | None:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.description else None

    return run


@pytest.fixture
def event_count(raw_sql) -> Callable[[], int]:
    def count() -> int:
        return raw_sql(f"SELECT count(*) FROM {EVENT_TABLE}")[0][0]  # noqa: S608

    return count


@pytest.fixture
def stored_event(raw_sql) -> Callable[[UUID], tuple | None]:
    """Read an event's persisted columns with raw SQL, or None when it does not exist.

    Returns `(occurred_at, actor_id, actor_role, action, entity_type, entity_id, before_state,
    after_state, request_id, idempotency_key)` with the JSON states decoded (SQL NULL -> None).
    """

    def read(event_id: UUID) -> tuple | None:
        rows = raw_sql(
            "SELECT occurred_at, actor_id, actor_role, action, entity_type, entity_id, "  # noqa: S608
            "before_state::text, after_state::text, request_id, idempotency_key "
            f"FROM {EVENT_TABLE} WHERE id = %s",
            (event_id,),
        )
        if not rows:
            return None
        row = rows[0]
        before = None if row[6] is None else json.loads(row[6])
        return (*row[:6], before, json.loads(row[7]), *row[8:])

    return read


@pytest.fixture
def insert_raw_event(raw_sql) -> Callable[..., UUID]:
    """Insert a valid event with raw SQL (states as JSON text), overriding any column."""

    def insert(**overrides) -> UUID:
        columns = {
            "id": uuid4(),
            "occurred_at": datetime.fromisoformat("2026-10-30T12:00:00+00:00"),
            "actor_id": ACTOR_ID,
            "actor_role": "operator",
            "action": "card.issued",
            "entity_type": "card",
            "entity_id": uuid4(),
            "before_state": None,
            "after_state": '{"status": "ACTIVE"}',
            "request_id": REQUEST_ID,
            "idempotency_key": IDEMPOTENCY_KEY,
        } | overrides
        raw_sql(
            f"INSERT INTO {EVENT_TABLE} ({', '.join(columns)}) "  # noqa: S608
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)",
            tuple(columns.values()),
        )
        return columns["id"]

    return insert


@pytest.fixture
def non_superuser_role(raw_sql) -> str:
    """A non-superuser role with full table privileges, like the production app role.

    It holds the TRUNCATE privilege, so a rejection can only come from the AUD-3 trigger.
    Created inside the test transaction, so it is rolled back with it.
    """
    raw_sql(f"CREATE ROLE {APP_ROLE} NOSUPERUSER NOLOGIN")
    raw_sql(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    raw_sql(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    return APP_ROLE
