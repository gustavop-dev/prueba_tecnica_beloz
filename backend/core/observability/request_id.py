"""Request id (OBS-2): taken from a valid incoming `X-Request-ID`, otherwise generated."""

import uuid

HEADER = "X-Request-ID"
META_KEY = "HTTP_X_REQUEST_ID"


def _incoming_or_new(raw: str | None) -> str:
    try:
        return str(uuid.UUID(raw or ""))
    except ValueError:
        return str(uuid.uuid4())


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = _incoming_or_new(request.META.get(META_KEY))
        response = self.get_response(request)
        response[HEADER] = request.request_id
        return response
