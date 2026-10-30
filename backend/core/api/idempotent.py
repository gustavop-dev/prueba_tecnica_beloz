"""Idempotent POST handling (docs/idempotency.md, ADR 0002, INV-4).

Order for every POST: authentication and permission (DRF) → `Idempotency-Key` check → one
transaction that claims the key **first** (IDEM-10), runs the view, stores the response and commits
both together (IDEM-7). Business rejections (< 500) are stored and replayed like successes
(IDEM-11); technical failures (>= 500, including LOCK_TIMEOUT) roll everything back, claim
included, so the client can retry with the same key.
"""

import functools
import uuid

from rest_framework.response import Response

from core.api import rejections
from core.api.errors import ApiError, render
from core.db import money_transaction
from core.observability import context
from idempotency import services as idempotency

HEADER = "Idempotency-Key"
META_KEY = "HTTP_IDEMPOTENCY_KEY"
REPLAY_HEADER = "Idempotent-Replayed"


class _RollBack(Exception):
    """Abort the transaction (claim included) and still answer with `response`."""

    def __init__(self, response: Response):
        self.response = response


def idempotent(view_method):
    @functools.wraps(view_method)
    def wrapper(view, request, *args, **kwargs):
        key = _idempotency_key(request)
        context.bind_idempotency_key(key)
        endpoint = f"{request.method} {request.path}"
        request_hash = idempotency.request_fingerprint(request.data)
        try:
            with money_transaction():
                claim = idempotency.claim(
                    principal_id=request.user.id,
                    endpoint=endpoint,
                    key=key,
                    request_hash=request_hash,
                )
                if claim.replay is not None:
                    return Response(
                        claim.replay.body,
                        status=claim.replay.status_code,
                        headers={REPLAY_HEADER: "true"},
                    )
                response = _run(view_method, view, request, *args, **kwargs)
                if response.status_code >= 500:
                    raise _RollBack(response)
                if response.status_code >= 400:
                    # Stored with the response: a replay returns it without a second event.
                    rejections.record_in_current_transaction(
                        request, response.data["error"]["code"]
                    )
                idempotency.complete(
                    claim.record_id, status_code=response.status_code, body=response.data
                )
                return response
        except _RollBack as rollback:
            return rollback.response

    return wrapper


def _idempotency_key(request) -> uuid.UUID:
    raw = request.META.get(META_KEY)
    if not raw:
        raise ApiError(400, "IDEMPOTENCY_KEY_MISSING", f"The {HEADER} header is required.")
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ApiError(400, "IDEMPOTENCY_KEY_INVALID", f"{HEADER} must be a UUID.") from None


def _run(view_method, view, request, *args, **kwargs) -> Response:
    """Run the view and turn any exception into the response the client would receive."""
    try:
        return view_method(view, request, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — every outcome must become a storable response
        # `render`, not the DRF handler: the wrapper records the rejection in its transaction.
        return render(exc, {"request": request, "view": view})
