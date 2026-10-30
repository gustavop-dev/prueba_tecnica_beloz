"""Public API of the `idempotency` app: the only module other code may import (MOD-1).

The operations core wraps every idempotent `POST` in one transaction (IDEM-7):

    with transaction.atomic():
        result = claim(principal_id=..., endpoint=..., key=..., request_hash=...)  # first write
        if result.replay is not None:
            return result.replay                    # stored response, operation not executed
        response = run_the_operation()
        complete(result.record_id, status_code=..., body=...)

`claim` is an `INSERT ... ON CONFLICT DO NOTHING` on the IDEM-4 unique constraint. A concurrent
claim of the same key waits on the unique index until the first transaction ends, then either
replays its committed response or, if it rolled back, claims the key itself (IDEM-10).
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import JSONNull
from django.utils import timezone

from idempotency.exceptions import IdempotencyError, KeyReusedWithDifferentRequest
from idempotency.models import REQUEST_HASH_BYTES, UNIQUE_KEY_CONSTRAINT, IdempotencyRecord

__all__ = [
    "ClaimResult",
    "IdempotencyError",
    "KeyReusedWithDifferentRequest",
    "StoredResponse",
    "claim",
    "complete",
    "request_fingerprint",
]

_ENDPOINT_MAX_LENGTH = IdempotencyRecord._meta.get_field("endpoint").max_length

_CLAIM_SQL = (
    f"INSERT INTO {IdempotencyRecord._meta.db_table} "  # noqa: S608 - constants, no user input
    "(id, principal_id, endpoint, key, request_hash, created_at) "
    "VALUES (%s, %s, %s, %s, %s, %s) "
    f"ON CONFLICT ON CONSTRAINT {UNIQUE_KEY_CONSTRAINT} DO NOTHING "
    "RETURNING id"
)


# --- DTOs -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: Any  # JSON-compatible


@dataclass(frozen=True, slots=True)
class ClaimResult:
    # Set when this call claimed the key: the caller must execute the operation and `complete`.
    record_id: UUID | None
    # Set when the key was already completed with the same request: return it, do not execute.
    replay: StoredResponse | None


# --- Guards -----------------------------------------------------------------------------


def _require_atomic(function_name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{function_name} must run inside transaction.atomic")


def _require_uuid(name: str, value: object) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID, got {value!r}")


def _require_endpoint(endpoint: object) -> None:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError(f"endpoint must be a non-empty string, got {endpoint!r}")
    if len(endpoint) > _ENDPOINT_MAX_LENGTH:
        raise ValueError(f"endpoint must be at most {_ENDPOINT_MAX_LENGTH} characters")


def _require_request_hash(request_hash: object) -> None:
    if not isinstance(request_hash, bytes) or len(request_hash) != REQUEST_HASH_BYTES:
        raise ValueError(
            f"request_hash must be {REQUEST_HASH_BYTES} bytes (a SHA-256 digest), "
            f"got {request_hash!r}"
        )


def _require_status_code(status_code: object) -> None:
    if (
        not isinstance(status_code, int)
        or isinstance(status_code, bool)
        or not 100 <= status_code <= 599
    ):
        raise ValueError(f"status_code must be an int between 100 and 599, got {status_code!r}")


def _require_json_body(body: object) -> None:
    try:
        json.dumps(body, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"body must be JSON-compatible: {exc}") from exc


# --- Public API -------------------------------------------------------------------------


def request_fingerprint(body: Any) -> bytes:
    """SHA-256 of the canonical JSON of `body` (IDEM-5, IDEM-9, ADR 0002). Always 32 bytes.

    Keys are sorted and there is no insignificant whitespace, so the same logical request hashes
    the same however the client serialized it. Lone surrogates (valid `\\udXXX` JSON escapes) are
    encoded as-is instead of failing, keeping the encoding injective.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8", errors="surrogatepass")).digest()


def claim(*, principal_id: UUID, endpoint: str, key: UUID, request_hash: bytes) -> ClaimResult:
    """Claim `(principal_id, endpoint, key)` for this transaction, or return its stored response.

    Must be the first write of the caller's transaction (IDEM-10): a concurrent claim of the same
    key blocks on the unique index until this transaction ends, holding no other lock (CON-4).

    - New key: inserts the record and returns `ClaimResult(record_id=<id>, replay=None)`.
    - Completed with the same `request_hash`: returns `ClaimResult(None, StoredResponse(...))`.
    - Completed with a different `request_hash`: raises `KeyReusedWithDifferentRequest`.
    """
    _require_atomic("claim")
    _require_uuid("principal_id", principal_id)
    _require_endpoint(endpoint)
    _require_uuid("key", key)
    _require_request_hash(request_hash)

    with connection.cursor() as cursor:
        cursor.execute(
            _CLAIM_SQL, (uuid4(), principal_id, endpoint, key, request_hash, timezone.now())
        )
        inserted = cursor.fetchone()
    if inserted is not None:
        return ClaimResult(record_id=inserted[0], replay=None)

    # The conflicting record is committed (READ COMMITTED: this new statement sees it), or it
    # was inserted earlier in this same transaction.
    existing = IdempotencyRecord.objects.values(
        "request_hash", "response_status", "response_body"
    ).get(principal_id=principal_id, endpoint=endpoint, key=key)
    if bytes(existing["request_hash"]) != request_hash:
        raise KeyReusedWithDifferentRequest(principal_id=principal_id, endpoint=endpoint, key=key)
    if existing["response_status"] is None:
        raise RuntimeError(
            f"idempotency key {key} on {endpoint} was already claimed in this transaction "
            "and is not completed"
        )
    return ClaimResult(
        record_id=None,
        replay=StoredResponse(
            status_code=existing["response_status"], body=existing["response_body"]
        ),
    )


def complete(record_id: UUID, *, status_code: int, body: Any) -> None:
    """Store the response of a record claimed in this same transaction (IDEM-6, IDEM-7).

    `body` must be JSON-compatible; `None` is stored as JSON `null`. A record can be completed
    once: the database rejects any later change (IDEM-6 trigger).
    """
    _require_atomic("complete")
    _require_uuid("record_id", record_id)
    _require_status_code(status_code)
    _require_json_body(body)

    updated = IdempotencyRecord.objects.filter(id=record_id, response_status__isnull=True).update(
        response_status=status_code,
        response_body=JSONNull() if body is None else body,
        completed_at=timezone.now(),
    )
    if updated != 1:
        raise RuntimeError(
            f"idempotency record {record_id} was not claimed in this transaction "
            "or is already completed"
        )
