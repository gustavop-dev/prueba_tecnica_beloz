"""Per-request correlation data (OBS-2, AUD-1), shared with logs and audit events.

Stored in a `ContextVar`, so each request — and each thread — sees only its own values. Outside an
HTTP request (management commands, direct flow calls) every value is `None`.
"""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: uuid.UUID | None = None
    idempotency_key: uuid.UUID | None = None


_NO_REQUEST = RequestContext()
_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def current() -> RequestContext:
    return _current.get() or _NO_REQUEST


def start(request_id: uuid.UUID):
    """Begin a request; returns the token `end` needs."""
    return _current.set(RequestContext(request_id=request_id))


def end(token) -> None:
    _current.reset(token)


def bind_idempotency_key(key: uuid.UUID) -> None:
    _current.set(replace(current(), idempotency_key=key))
