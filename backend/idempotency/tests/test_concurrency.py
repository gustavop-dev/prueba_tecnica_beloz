"""`claim` under real concurrent transactions (IDEM-10, CON-4, INV-4).

Each competitor runs in its own thread, connection and transaction, like a second HTTP request.
The test thread claims a key and keeps its transaction open; the competitor's claim of the same
key must wait on the unique index (PostgreSQL makes it wait on the first transaction's id) and
only then see the outcome: the committed response, or a free key after a rollback. A claim that
looked before inserting, or did not insert on the unique constraint, would not wait: it would run
the operation a second time or fail with a unique violation.
"""

import threading
import time
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction

from idempotency.services import (
    ClaimResult,
    StoredResponse,
    claim,
    complete,
    request_fingerprint,
)
from idempotency.tests.conftest import ENDPOINT, RECORD_TABLE

PRINCIPAL = UUID("6f1c2a4e-1b7d-4c39-9a52-3d8e0f7a1c01")
REQUEST = {"amount_minor": 500}
OTHER_REQUEST = {"amount_minor": 200}
FIRST_RESPONSE = StoredResponse(status_code=201, body={"repayment": "first"})
COMPETITOR_RESPONSE = StoredResponse(status_code=201, body={"repayment": "competitor"})

BLOCKED_WAIT_SECONDS = 5
FINISH_WAIT_SECONDS = 10
POLL_SECONDS = 0.02
CONCURRENT_REQUESTS = 6

# Lock requests from other backends that wait on the transaction of the calling connection.
_WAITERS_ON_MY_TRANSACTION_SQL = """
SELECT count(*) FROM pg_locks waiting
WHERE NOT waiting.granted
  AND waiting.locktype = 'transactionid'
  AND waiting.pid <> pg_backend_pid()
  AND waiting.transactionid = (
      SELECT mine.transactionid FROM pg_locks mine
      WHERE mine.pid = pg_backend_pid() AND mine.locktype = 'transactionid' AND mine.granted
  )
"""


# --- Helpers --------------------------------------------------------------------------------


def _claim(key: UUID, request: dict) -> ClaimResult:
    return claim(
        principal_id=PRINCIPAL,
        endpoint=ENDPOINT,
        key=key,
        request_hash=request_fingerprint(request),
    )


def _request_in_own_transaction(key: UUID, request: dict, outcome: list) -> None:
    """One idempotent request: claim, run (here: answer COMPETITOR_RESPONSE) and complete.

    Records the `ClaimResult`, or the exception class name, in `outcome`.
    """
    try:
        with transaction.atomic():
            result = _claim(key, request)
            if result.record_id is not None:
                complete(
                    result.record_id,
                    status_code=COMPETITOR_RESPONSE.status_code,
                    body=COMPETITOR_RESPONSE.body,
                )
        outcome.append(result)
    except Exception as exc:  # noqa: BLE001 - the test asserts on whatever ended the request
        outcome.append(type(exc).__name__)
    finally:
        connection.close()


def _start_competitor(key: UUID, request: dict) -> tuple[threading.Thread, list]:
    outcome: list = []
    thread = threading.Thread(
        target=_request_in_own_transaction, args=(key, request, outcome), daemon=True
    )
    thread.start()
    return thread, outcome


def _wait_for_a_waiter_on_my_transaction(thread: threading.Thread) -> bool:
    """Poll `pg_locks` until another backend waits on this connection's transaction.

    Returns False when the competitor finishes or the timeout expires without ever waiting.
    """
    deadline = time.monotonic() + BLOCKED_WAIT_SECONDS
    with connection.cursor() as cursor:
        while time.monotonic() < deadline and thread.is_alive():
            cursor.execute(_WAITERS_ON_MY_TRANSACTION_SQL)
            if cursor.fetchone()[0] > 0:
                return True
            time.sleep(POLL_SECONDS)
    return False


def _concurrent_requests(key: UUID, count: int) -> list:
    """Run `count` requests with the same key and body at once, behind a barrier."""
    barrier = threading.Barrier(count)
    outcomes: list = []

    def request() -> None:
        barrier.wait()
        _request_in_own_transaction(key, REQUEST, outcomes)

    threads = [threading.Thread(target=request, daemon=True) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=FINISH_WAIT_SECONDS)
    return outcomes


def _split_claims_and_replays(outcomes: list) -> tuple[list[UUID], list]:
    claimed = [o.record_id for o in outcomes if isinstance(o, ClaimResult) and o.record_id]
    others = [o for o in outcomes if not (isinstance(o, ClaimResult) and o.record_id)]
    return claimed, others


@pytest.fixture
def raw_sql_rows(raw_sql) -> Callable[[], list[tuple]]:
    """Every stored record as `(id, response_status, response_body as JSON text)`."""
    return lambda: raw_sql(
        f"SELECT id, response_status, response_body::text FROM {RECORD_TABLE}"  # noqa: S608
    )


# --- Same key while the first request is in flight ------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("competitor_request", "expected_outcome"),
    [
        pytest.param(
            REQUEST,
            ClaimResult(record_id=None, replay=FIRST_RESPONSE),
            id="same-request-replays",
        ),
        pytest.param(
            OTHER_REQUEST, "KeyReusedWithDifferentRequest", id="different-request-is-rejected"
        ),
    ],
)
def test_competing_claim_waits_for_the_first_transaction_then_sees_its_commit(
    raw_sql_rows, competitor_request, expected_outcome
):
    """Fails if a second request with an in-flight key does not wait on the unique index (IDEM-10):
    a check-then-insert claim would run the operation twice or end in a unique violation instead
    of replaying the committed response (INV-4, idempotency.md §3)."""
    key = uuid4()

    with transaction.atomic():
        first = _claim(key, REQUEST)
        thread, outcome = _start_competitor(key, competitor_request)
        competitor_waited_on_the_first_transaction = _wait_for_a_waiter_on_my_transaction(thread)
        outcome_while_first_in_flight = list(outcome)
        complete(first.record_id, status_code=FIRST_RESPONSE.status_code, body=FIRST_RESPONSE.body)
    thread.join(timeout=FINISH_WAIT_SECONDS)

    assert competitor_waited_on_the_first_transaction is True
    assert outcome_while_first_in_flight == []
    assert outcome == [expected_outcome]
    assert raw_sql_rows() == [(first.record_id, 201, '{"repayment": "first"}')]


@pytest.mark.django_db(transaction=True)
def test_competing_claim_takes_the_key_when_the_first_transaction_rolls_back(raw_sql_rows):
    """Fails if a request that failed technically (rolled back) leaves its key taken: the retry
    waiting behind it must claim the key and run the operation itself (IDEM-7, IDEM-10)."""
    key = uuid4()

    with transaction.atomic():
        first = _claim(key, REQUEST)
        thread, outcome = _start_competitor(key, REQUEST)
        competitor_waited_on_the_first_transaction = _wait_for_a_waiter_on_my_transaction(thread)
        transaction.set_rollback(True)
    thread.join(timeout=FINISH_WAIT_SECONDS)

    [competitor_result] = outcome
    assert competitor_waited_on_the_first_transaction is True
    assert competitor_result.replay is None
    assert competitor_result.record_id not in (None, first.record_id)
    assert raw_sql_rows() == [
        (competitor_result.record_id, 201, '{"repayment": "competitor"}'),
    ]


@pytest.mark.django_db(transaction=True)
def test_claim_of_another_key_does_not_wait_for_an_in_flight_key(raw_sql_rows):
    """Fails if the claim serializes unrelated requests (a table lock or a lock on the principal):
    only requests carrying the same key may wait on each other (IDEM-10, CON-4)."""
    key, other_key = uuid4(), uuid4()

    with transaction.atomic():
        _claim(key, REQUEST)
        thread, outcome = _start_competitor(other_key, REQUEST)
        thread.join(timeout=FINISH_WAIT_SECONDS)
        competitor_finished_while_first_in_flight = not thread.is_alive()
        transaction.set_rollback(True)

    [competitor_result] = outcome
    assert competitor_finished_while_first_in_flight is True
    assert competitor_result.replay is None
    assert raw_sql_rows() == [
        (competitor_result.record_id, 201, '{"repayment": "competitor"}'),
    ]


@pytest.mark.django_db(transaction=True)
def test_concurrent_requests_with_the_same_key_run_once_and_share_one_response(raw_sql_rows):
    """Fails if several simultaneous requests with the same key can each claim it, applying the
    operation more than once, or if any of them gets a response other than the stored one
    (INV-4, IDEM-10, TEST-IDEM-3 at service level)."""
    key = uuid4()

    outcomes = _concurrent_requests(key, CONCURRENT_REQUESTS)

    claimed, others = _split_claims_and_replays(outcomes)
    assert len(claimed) == 1
    assert others == [ClaimResult(record_id=None, replay=COMPETITOR_RESPONSE)] * (
        CONCURRENT_REQUESTS - 1
    )
    assert raw_sql_rows() == [(claimed[0], 201, '{"repayment": "competitor"}')]
