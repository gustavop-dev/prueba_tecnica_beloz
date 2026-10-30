"""Request id (OBS-2) and the per-request log line (OBS-1).

The id is taken from a valid incoming `X-Request-ID`, otherwise generated, and returned in the
response header. Only method, path, status and duration are logged — never headers or bodies
(OBS-3).
"""

import logging
import time
import uuid

from core.observability import context

HEADER = "X-Request-ID"
META_KEY = "HTTP_X_REQUEST_ID"

logger = logging.getLogger("core.request")


def _incoming_or_new(raw: str | None) -> uuid.UUID:
    try:
        return uuid.UUID(raw or "")
    except ValueError:
        return uuid.uuid4()


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = _incoming_or_new(request.META.get(META_KEY))
        request.request_id = str(request_id)
        token = context.start(request_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response[HEADER] = request.request_id
            logger.info(
                "request.completed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return response
        finally:
            context.end(token)
