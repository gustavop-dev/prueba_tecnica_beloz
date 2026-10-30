"""Database guards on idempotency records, exercised with raw SQL that bypasses Python.

IDEM-4: unique `(principal_id, endpoint, key)`. CHECKs: 32-byte request hash, HTTP status range,
response stored all at once. IDEM-7: a record only commits completed (deferred trigger). IDEM-6 /
IDEM-12: completed records are immutable and no record is ever deleted or truncated.
"""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from idempotency.services import claim
from idempotency.tests.conftest import ENDPOINT, HASH_200, HASH_500, RECORD_TABLE

APP_ROLE = "idempotency_test_app_role"

IN_FLIGHT = {"status": None, "body": None, "completed": False}


def _violated_constraint(excinfo) -> str:
    return excinfo.value.__cause__.diag.constraint_name


def _record_count(raw_sql) -> int:
    return raw_sql(f"SELECT count(*) FROM {RECORD_TABLE}")[0][0]  # noqa: S608


def _claim_without_completing() -> None:
    claim(principal_id=uuid4(), endpoint=ENDPOINT, key=uuid4(), request_hash=HASH_500)


@pytest.fixture
def completed_record_id(raw_sql, insert_raw_record):
    """A completed record whose deferred IDEM-7 check already ran, as after a commit.

    Firing it now matters for TRUNCATE, which PostgreSQL refuses while the table has pending
    trigger events.
    """
    record_id = insert_raw_record()
    raw_sql("SET CONSTRAINTS ALL IMMEDIATE")
    return record_id


@pytest.fixture
def non_superuser_role(raw_sql) -> str:
    """A non-superuser role with full table privileges, like the production app role.

    It holds the TRUNCATE privilege, so a rejection can only come from the trigger. Created inside
    the test transaction, so it is rolled back with it.
    """
    raw_sql(f"CREATE ROLE {APP_ROLE} NOSUPERUSER NOLOGIN")
    raw_sql(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    raw_sql(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    return APP_ROLE


# --- IDEM-4 and CHECK constraints -----------------------------------------------------------


@pytest.mark.django_db
def test_duplicate_principal_endpoint_and_key_is_rejected(raw_sql, insert_raw_record):
    """Fails if two records can share `(principal_id, endpoint, key)`: two requests with the same
    key would both run and the operation would be applied twice (IDEM-4, INV-4)."""
    principal_id, key = uuid4(), uuid4()
    insert_raw_record(principal_id=principal_id, key=key)

    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        insert_raw_record(principal_id=principal_id, key=key, request_hash=HASH_200)

    assert _violated_constraint(excinfo) == "idempotency_record_unique_key"
    assert _record_count(raw_sql) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        pytest.param(
            {"request_hash": b""}, "idempotency_record_request_hash_is_sha256", id="hash-empty"
        ),
        pytest.param(
            {"request_hash": HASH_500[:31]},
            "idempotency_record_request_hash_is_sha256",
            id="hash-31-bytes",
        ),
        pytest.param(
            {"request_hash": HASH_500 + b"\x00"},
            "idempotency_record_request_hash_is_sha256",
            id="hash-33-bytes",
        ),
        pytest.param({"status": 99}, "idempotency_record_response_status_is_http", id="status-99"),
        pytest.param(
            {"status": 600}, "idempotency_record_response_status_is_http", id="status-600"
        ),
        pytest.param(
            {"completed": False}, "idempotency_record_completed_atomically", id="no-completed-at"
        ),
        pytest.param(
            {"status": None}, "idempotency_record_completed_atomically", id="no-response-status"
        ),
        pytest.param(
            {"body": None}, "idempotency_record_completed_atomically", id="no-response-body"
        ),
    ],
)
def test_record_violating_a_check_constraint_is_rejected(
    raw_sql, insert_raw_record, columns, constraint
):
    """Fails if a record can store a hash that is not a SHA-256 digest, an impossible HTTP status
    or a partial response, any of which would make replays wrong (IDEM-5, IDEM-6)."""
    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        insert_raw_record(**columns)

    assert _violated_constraint(excinfo) == constraint
    assert _record_count(raw_sql) == 0


# --- IDEM-7: completed at commit ------------------------------------------------------------


@pytest.mark.django_db
def test_in_flight_record_is_rejected_by_the_deferred_trigger(raw_sql, insert_raw_record):
    """Fails if a record without its response can pass the commit-time check (IDEM-7)."""
    with (
        pytest.raises(IntegrityError, match="IDEM-7: idempotency record .* without its response"),
        transaction.atomic(),
    ):
        insert_raw_record(**IN_FLIGHT)
        raw_sql("SET CONSTRAINTS ALL IMMEDIATE")

    assert _record_count(raw_sql) == 0


@pytest.mark.django_db
def test_record_completed_later_in_the_same_transaction_passes_the_deferred_trigger(
    raw_sql, insert_raw_record, stored_record
):
    """Fails if IDEM-7 runs per statement instead of at commit, rejecting the claim that `claim`
    inserts before the operation runs and `complete` fills in afterwards (IDEM-7, IDEM-10)."""
    record_id = insert_raw_record(**IN_FLIGHT)
    raw_sql(
        f"UPDATE {RECORD_TABLE} SET response_status = 201, "  # noqa: S608
        'response_body = \'{"id": "a"}\', completed_at = now() WHERE id = %s',
        (record_id,),
    )

    raw_sql("SET CONSTRAINTS ALL IMMEDIATE")

    assert stored_record(record_id)[4:] == (201, {"id": "a"}, True)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "write_in_flight_record",
    [
        pytest.param("raw-sql", id="raw-sql"),
        pytest.param("service", id="claim-without-complete"),
    ],
)
def test_in_flight_record_is_rejected_at_commit_and_nothing_persists(
    raw_sql, insert_raw_record, write_in_flight_record
):
    """Fails if a record can commit without its response: other requests with the same key would
    then see a half-done record forever instead of waiting or replaying (IDEM-7)."""
    writers = {
        "raw-sql": lambda: insert_raw_record(**IN_FLIGHT),
        "service": _claim_without_completing,
    }

    with (
        pytest.raises(IntegrityError, match="IDEM-7: idempotency record .* without its response"),
        transaction.atomic(),
    ):
        writers[write_in_flight_record]()

    assert _record_count(raw_sql) == 0


# --- IDEM-6 / IDEM-12: immutable, never deleted ---------------------------------------------

COMPLETED_RECORD_UPDATES = {
    "response_status": "response_status = 500",
    "response_body": 'response_body = \'{"id": "b"}\'',
    "completed_at": "completed_at = completed_at + interval '1 second'",
    "request_hash": "request_hash = sha256('other')",
    "key": "key = gen_random_uuid()",
    "endpoint": "endpoint = 'POST /other'",
    "uncomplete": "response_status = NULL, response_body = NULL, completed_at = NULL",
}

IN_FLIGHT_IDENTITY_UPDATES = {
    "principal_id": "principal_id = gen_random_uuid()",
    "endpoint": "endpoint = 'POST /other'",
    "key": "key = gen_random_uuid()",
    "request_hash": "request_hash = sha256('other')",
    "created_at": "created_at = created_at - interval '1 day'",
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "assignment", COMPLETED_RECORD_UPDATES.values(), ids=COMPLETED_RECORD_UPDATES.keys()
)
def test_update_of_a_completed_record_is_rejected(
    raw_sql, stored_record, completed_record_id, assignment
):
    """Fails if a stored response or its request can be rewritten, so a retry would replay
    something other than what the first request returned (IDEM-6)."""
    before = stored_record(completed_record_id)

    with (
        pytest.raises(
            IntegrityError, match="IDEM-6: idempotency record .* completed and immutable"
        ),
        transaction.atomic(),
    ):
        raw_sql(
            f"UPDATE {RECORD_TABLE} SET {assignment} WHERE id = %s",  # noqa: S608
            (completed_record_id,),
        )

    assert stored_record(completed_record_id) == before


@pytest.mark.django_db
@pytest.mark.parametrize(
    "assignment", IN_FLIGHT_IDENTITY_UPDATES.values(), ids=IN_FLIGHT_IDENTITY_UPDATES.keys()
)
def test_identity_of_an_in_flight_record_cannot_change(
    raw_sql, insert_raw_record, stored_record, discarded_savepoint, assignment
):
    """Fails if completing a record can also move it to another principal, endpoint, key or
    request, detaching the stored response from the request that produced it (IDEM-4, IDEM-5)."""
    with discarded_savepoint():
        record_id = insert_raw_record(**IN_FLIGHT)
        before = stored_record(record_id)
        with (
            pytest.raises(IntegrityError, match="IDEM-4: the identity of idempotency record"),
            transaction.atomic(),
        ):
            raw_sql(
                f"UPDATE {RECORD_TABLE} SET {assignment} WHERE id = %s",  # noqa: S608
                (record_id,),
            )
        after = stored_record(record_id)

    assert after == before


@pytest.mark.django_db
def test_delete_of_a_completed_record_is_rejected(raw_sql, completed_record_id):
    """Fails if a record can be deleted, letting a retry with the same key run again (IDEM-12)."""
    with (
        pytest.raises(IntegrityError, match="IDEM-12: idempotency records never expire; DELETE"),
        transaction.atomic(),
    ):
        raw_sql(f"DELETE FROM {RECORD_TABLE} WHERE id = %s", (completed_record_id,))  # noqa: S608

    assert _record_count(raw_sql) == 1


@pytest.mark.django_db
def test_delete_of_an_in_flight_record_is_rejected(raw_sql, insert_raw_record, discarded_savepoint):
    """Fails if a claim can be deleted instead of rolled back, freeing the key for a second
    execution while the first transaction still runs the operation (IDEM-12, IDEM-10)."""
    with discarded_savepoint():
        record_id = insert_raw_record(**IN_FLIGHT)
        with (
            pytest.raises(IntegrityError, match="IDEM-12: idempotency records never expire"),
            transaction.atomic(),
        ):
            raw_sql(f"DELETE FROM {RECORD_TABLE} WHERE id = %s", (record_id,))  # noqa: S608
        remaining = _record_count(raw_sql)

    assert remaining == 1


@pytest.mark.django_db
def test_truncate_is_rejected_for_a_non_superuser_role(
    raw_sql, completed_record_id, non_superuser_role
):
    """Fails if the application (non-superuser) role can TRUNCATE the records (IDEM-12, LED-4
    pattern)."""
    with (
        pytest.raises(IntegrityError, match="IDEM-12: idempotency records never expire; TRUNCATE"),
        transaction.atomic(),
    ):
        raw_sql(f"SET LOCAL ROLE {non_superuser_role}")
        raw_sql(f"TRUNCATE {RECORD_TABLE}")

    assert _record_count(raw_sql) == 1


@pytest.mark.django_db
def test_truncate_is_allowed_for_a_superuser(raw_sql, completed_record_id):
    """Fails if the superuser exemption is lost, which would break Django's test flush (LED-4
    pattern)."""
    is_superuser = raw_sql("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")

    raw_sql(f"TRUNCATE {RECORD_TABLE}")

    assert is_superuser == [(True,)]
    assert _record_count(raw_sql) == 0
