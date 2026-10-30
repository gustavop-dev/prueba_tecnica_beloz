"""Provider webhook signatures (SEC-HOOK-1..5).

The provider sends `X-Webhook-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>`, computed with the
shared secret over `"<t>.<raw body>"`. Acme recomputes it over the exact bytes received and compares
in constant time, which proves who sent the message and that it was not altered. The timestamp is
signed too, so a captured message cannot be replayed outside the 5-minute window; inside it,
idempotency and the deposit state machine neutralize the replay (SEC-HOOK-4).
"""

import hashlib
import hmac
import time
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from core.auth.principals import Principal, Role

HEADER = "X-Webhook-Signature"
META_KEY = "HTTP_X_WEBHOOK_SIGNATURE"
SCHEME = "v1"
MIN_SECRET_LENGTH = 32


class InvalidSignature(exceptions.AuthenticationFailed):
    default_code = "invalid_signature"


class SignatureExpired(exceptions.AuthenticationFailed):
    default_code = "signature_expired"


def now() -> int:
    return int(time.time())


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The header value a provider sends for `body` at `timestamp`."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},{SCHEME}={digest.hexdigest()}"


def validate_configuration() -> None:
    """Fail closed at startup on a weak secret or a malformed provider id (SEC-SECRET-3)."""
    if len(settings.WEBHOOK_SIGNING_SECRET) < MIN_SECRET_LENGTH:
        raise ImproperlyConfigured(
            f"WEBHOOK_SIGNING_SECRET needs at least {MIN_SECRET_LENGTH} characters."
        )
    try:
        uuid.UUID(settings.PAYMENT_PROVIDER_ID)
    except ValueError:
        raise ImproperlyConfigured("PAYMENT_PROVIDER_ID must be a UUID.") from None


def _parse(header: str) -> tuple[int, str]:
    fields = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    try:
        return int(fields["t"]), fields[SCHEME]
    except KeyError, ValueError:
        raise InvalidSignature() from None


class WebhookSignatureAuthentication(BaseAuthentication):
    """The only credential accepted by the webhook (SEC-HOOK-5): bearer tokens are ignored."""

    def authenticate(self, request):
        header = request.META.get(META_KEY, "")
        if not header:
            raise InvalidSignature()
        timestamp, received = _parse(header)
        expected = sign(settings.WEBHOOK_SIGNING_SECRET, timestamp, request.body)
        if not hmac.compare_digest(expected, f"t={timestamp},{SCHEME}={received}"):
            raise InvalidSignature()
        if abs(now() - timestamp) > settings.WEBHOOK_TOLERANCE_SECONDS:
            raise SignatureExpired()
        provider = Principal(id=uuid.UUID(settings.PAYMENT_PROVIDER_ID), role=Role.PAYMENT_PROVIDER)
        return provider, None

    def authenticate_header(self, request):
        return "Signature"
