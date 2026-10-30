import hashlib
import json
from collections.abc import Callable
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction

RECORD_TABLE = "idempotency_idempotencyrecord"
ENDPOINT = "POST /accounts/6f1c2a4e-1b7d-4c39-9a52-3d8e0f7a1c01/repayments"

# Fixed 32-byte request hashes (SHA-256 digests of two different bodies, ADR 0002 example).
HASH_500 = hashlib.sha256(b'{"amount_minor":500}').digest()
HASH_200 = hashlib.sha256(b'{"amount_minor":200}').digest()


@pytest.fixture
def raw_sql() -> Callable[..., list[tuple] | None]:
    """Run raw SQL, bypassing Python, and return all rows (or None for statements)."""

    def run(sql: str, params: tuple = ()) -> list[tuple] | None:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.description else None

    return run


@pytest.fixture
def discarded_savepoint():
    """A savepoint that is always rolled back, for records that must never reach a commit.

    Django's TestCase teardown runs `check_constraints()`, which fires the deferred IDEM-7
    trigger: a record left in flight in the test transaction would fail the teardown.
    """

    @contextmanager
    def savepoint():
        with transaction.atomic():
            yield
            transaction.set_rollback(True)

    return savepoint


@pytest.fixture
def insert_raw_record(raw_sql) -> Callable[..., UUID]:
    """Insert a record with raw SQL, completed unless `status` is None. Returns its id.

    The deferred IDEM-7 trigger only fires at commit or on `SET CONSTRAINTS ALL IMMEDIATE`.
    """

    def insert(
        *,
        principal_id: UUID | None = None,
        endpoint: str = ENDPOINT,
        key: UUID | None = None,
        request_hash: bytes = HASH_500,
        status: int | None = 201,
        body: str | None = '{"id": "a"}',
        completed: bool = True,
    ) -> UUID:
        record_id = uuid4()
        raw_sql(
            f"INSERT INTO {RECORD_TABLE} (id, principal_id, endpoint, key, request_hash, "  # noqa: S608
            "response_status, response_body, created_at, completed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now(), "
            "CASE WHEN %s THEN now() END)",
            (
                record_id,
                principal_id or uuid4(),
                endpoint,
                key or uuid4(),
                request_hash,
                status,
                body,
                completed,
            ),
        )
        return record_id

    return insert


@pytest.fixture
def stored_record(raw_sql) -> Callable[[UUID], tuple | None]:
    """Read a record's persisted columns with raw SQL, or None when it does not exist.

    Returns `(principal_id, endpoint, key, request_hash, response_status, response_body,
    completed)`, with the JSON body decoded (SQL NULL and JSON null both read as None).
    """

    def read(record_id: UUID) -> tuple | None:
        rows = raw_sql(
            f"SELECT principal_id, endpoint, key, request_hash, response_status, "  # noqa: S608
            f"COALESCE(response_body::text, 'null'), completed_at IS NOT NULL "
            f"FROM {RECORD_TABLE} WHERE id = %s",
            (record_id,),
        )
        if not rows:
            return None
        principal_id, endpoint, key, request_hash, status, body, completed = rows[0]
        return (
            principal_id,
            endpoint,
            key,
            bytes(request_hash),
            status,
            json.loads(body),
            completed,
        )

    return read
