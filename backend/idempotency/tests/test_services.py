"""`claim` and `complete`: the idempotency behavior table (idempotency.md §3, IDEM-4..8, IDEM-11)."""

from uuid import UUID, uuid4

import pytest

from idempotency.services import (
    ClaimResult,
    KeyReusedWithDifferentRequest,
    StoredResponse,
    claim,
    complete,
)
from idempotency.tests.conftest import ENDPOINT, HASH_200, HASH_500, RECORD_TABLE

PRINCIPAL = UUID("6f1c2a4e-1b7d-4c39-9a52-3d8e0f7a1c01")
OTHER_PRINCIPAL = UUID("6f1c2a4e-1b7d-4c39-9a52-3d8e0f7a1c02")
KEY = UUID("0b7a6f0e-3f55-4d0c-8a8e-6d1f4c2b9e10")
OTHER_KEY = UUID("0b7a6f0e-3f55-4d0c-8a8e-6d1f4c2b9e11")
OTHER_ENDPOINT = "POST /accounts/6f1c2a4e-1b7d-4c39-9a52-3d8e0f7a1c99/repayments"


def _claim(*, principal_id=PRINCIPAL, endpoint=ENDPOINT, key=KEY, request_hash=HASH_500):
    return claim(principal_id=principal_id, endpoint=endpoint, key=key, request_hash=request_hash)


# How the claimed record id is passed to `complete` (None: as returned by `claim`).
_RECORD_ID_VARIANTS = {None: lambda record_id: record_id, "as-string": str}


def _record_count(raw_sql) -> int:
    return raw_sql(f"SELECT count(*) FROM {RECORD_TABLE}")[0][0]  # noqa: S608


# --- Behavior table -----------------------------------------------------------------------


@pytest.mark.django_db
def test_claim_of_a_new_key_inserts_an_in_flight_record(stored_record, discarded_savepoint):
    """Fails if a new key does not claim (the operation would never run) or the record does not
    store principal, endpoint, key and request hash with no response yet (IDEM-4, IDEM-5, IDEM-8)."""
    with discarded_savepoint():
        result = _claim()
        stored = stored_record(result.record_id)

    assert result.replay is None
    assert isinstance(result.record_id, UUID)
    assert stored == (PRINCIPAL, ENDPOINT, KEY, HASH_500, None, None, False)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        pytest.param(201, {"id": "p-1", "amount_minor": 500, "status": "APPROVED"}, id="created"),
        pytest.param(
            422, {"error": {"code": "INSUFFICIENT_CREDIT"}}, id="business-rejection-IDEM-11"
        ),
        pytest.param(200, [{"merchant": "Café"}, 7, True, None], id="list-and-unicode"),
        pytest.param(204, None, id="no-body"),
    ],
)
def test_completed_key_with_the_same_request_replays_the_stored_response(
    raw_sql, status_code, body
):
    """Fails if a retry with the same key and body runs again or gets a response other than the
    stored one, including stored business rejections (IDEM-6, IDEM-11, INV-4)."""
    first = _claim()
    complete(first.record_id, status_code=status_code, body=body)

    retry = _claim()

    assert retry == ClaimResult(
        record_id=None, replay=StoredResponse(status_code=status_code, body=body)
    )
    assert _record_count(raw_sql) == 1


@pytest.mark.django_db
def test_completed_key_with_a_different_request_is_rejected(stored_record):
    """Fails if a key reused for a different body replays the first response or runs the new
    operation, e.g. a payment of 200 answered with the receipt of a payment of 500 (ADR 0002)."""
    first = _claim(request_hash=HASH_500)
    complete(first.record_id, status_code=201, body={"amount_minor": 500})

    with pytest.raises(KeyReusedWithDifferentRequest) as excinfo:
        _claim(request_hash=HASH_200)

    assert (excinfo.value.principal_id, excinfo.value.endpoint, excinfo.value.key) == (
        PRINCIPAL,
        ENDPOINT,
        KEY,
    )
    assert stored_record(first.record_id) == (
        PRINCIPAL,
        ENDPOINT,
        KEY,
        HASH_500,
        201,
        {"amount_minor": 500},
        True,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scope",
    [
        pytest.param({"principal_id": OTHER_PRINCIPAL}, id="other-principal-IDEM-4"),
        pytest.param({"endpoint": OTHER_ENDPOINT}, id="other-endpoint-IDEM-8"),
        pytest.param({"key": OTHER_KEY}, id="other-key"),
    ],
)
def test_same_key_in_another_scope_is_a_new_claim(raw_sql, discarded_savepoint, scope):
    """Fails if records are not scoped by principal, endpoint and key: another customer's key or
    the same body on another account (path) would replay a foreign response (IDEM-4, IDEM-8)."""
    first = _claim()
    complete(first.record_id, status_code=201, body={"id": "p-1"})

    with discarded_savepoint():
        second = _claim(**scope)
        records = _record_count(raw_sql)

    assert second.replay is None
    assert second.record_id not in (None, first.record_id)
    assert records == 2


@pytest.mark.django_db
def test_response_without_body_is_stored_as_json_null(raw_sql):
    """Fails if a `None` body is stored as SQL NULL: the record would look in flight and the
    IDEM-7 trigger would reject the commit of a valid `204` response (IDEM-6, IDEM-7)."""
    claimed = _claim()

    complete(claimed.record_id, status_code=204, body=None)

    assert raw_sql(
        f"SELECT response_body IS NULL, response_body::text FROM {RECORD_TABLE} WHERE id = %s",  # noqa: S608
        (claimed.record_id,),
    ) == [(False, "null")]


# --- Misuse -------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("call", "function_name"),
    [
        pytest.param(_claim, "claim", id="claim"),
        pytest.param(
            lambda: complete(uuid4(), status_code=201, body={}), "complete", id="complete"
        ),
    ],
)
def test_claim_and_complete_require_an_open_transaction(raw_sql, call, function_name):
    """Fails if the record can be written in autocommit, outside the transaction of the effect,
    so an effect could commit without its record or the reverse (IDEM-7, MOD-4)."""
    with pytest.raises(RuntimeError, match=f"{function_name} must run inside transaction.atomic"):
        call()

    assert _record_count(raw_sql) == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"principal_id": str(PRINCIPAL)}, id="principal-as-string"),
        pytest.param({"key": str(KEY)}, id="key-as-string"),
        pytest.param({"endpoint": ""}, id="empty-endpoint"),
        pytest.param({"endpoint": "   "}, id="blank-endpoint"),
        pytest.param({"endpoint": "POST /" + "a" * 250}, id="endpoint-over-255"),
        pytest.param({"request_hash": HASH_500[:31]}, id="hash-31-bytes"),
        pytest.param({"request_hash": HASH_500 + b"\x00"}, id="hash-33-bytes"),
        pytest.param({"request_hash": HASH_500.hex()}, id="hash-as-hex-string"),
    ],
)
def test_claim_rejects_invalid_arguments(raw_sql, arguments):
    """Fails if `claim` accepts a malformed id, endpoint or hash, which would store a record no
    retry can ever match or that silently collides with another (IDEM-2, IDEM-5, IDEM-8)."""
    with pytest.raises(ValueError, match="must be"):
        _claim(**arguments)

    assert _record_count(raw_sql) == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("record_id", "status_code", "body"),
    [
        pytest.param(None, 99, {}, id="status-99"),
        pytest.param(None, 600, {}, id="status-600"),
        pytest.param(None, True, {}, id="status-bool"),
        pytest.param(None, "201", {}, id="status-string"),
        pytest.param(None, 201, {"ids": {1, 2}}, id="body-not-json"),
        pytest.param("as-string", 201, {}, id="record-id-as-string"),
    ],
)
def test_complete_rejects_invalid_arguments(
    stored_record, discarded_savepoint, record_id, status_code, body
):
    """Fails if `complete` stores a response no HTTP client could receive or that cannot be
    replayed as JSON, instead of rejecting it before writing (IDEM-6)."""
    with discarded_savepoint():
        claimed = _claim()
        with pytest.raises(ValueError, match="must be"):
            complete(
                _RECORD_ID_VARIANTS[record_id](claimed.record_id),
                status_code=status_code,
                body=body,
            )
        stored = stored_record(claimed.record_id)

    assert stored == (PRINCIPAL, ENDPOINT, KEY, HASH_500, None, None, False)


@pytest.mark.django_db
def test_complete_twice_is_rejected_and_keeps_the_first_response(stored_record):
    """Fails if a completed record can be overwritten, so a retry would replay a response other
    than the one first returned (IDEM-6)."""
    claimed = _claim()
    complete(claimed.record_id, status_code=201, body={"id": "p-1"})

    with pytest.raises(RuntimeError, match="is already completed"):
        complete(claimed.record_id, status_code=500, body={"error": "late"})

    assert stored_record(claimed.record_id)[4:] == (201, {"id": "p-1"}, True)


@pytest.mark.django_db
def test_complete_of_an_unclaimed_record_is_rejected(raw_sql):
    """Fails if completing an id that was never claimed silently succeeds, hiding a core bug that
    would leave the real record without its response (IDEM-7)."""
    with pytest.raises(RuntimeError, match="was not claimed in this transaction"):
        complete(uuid4(), status_code=201, body={})

    assert _record_count(raw_sql) == 0


@pytest.mark.django_db
def test_claiming_the_same_key_twice_in_one_transaction_is_rejected(discarded_savepoint):
    """Fails if a second claim of an in-flight key in the same transaction returns a replay with
    no response, or claims it again, instead of signalling the programming error (IDEM-10)."""
    with discarded_savepoint():
        _claim()
        with pytest.raises(RuntimeError, match="already claimed in this transaction"):
            _claim()
