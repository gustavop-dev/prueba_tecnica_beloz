"""Principals and the static token registry (SEC-AUTH-1..5).

Tokens are configured in the `AUTH_TOKENS` environment variable as comma-separated
`role:principal_id:token` entries. The rest of the code only sees the resolved `Principal`.
"""

import hmac
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MIN_TOKEN_LENGTH = 16


class Role(StrEnum):
    CUSTOMER = "customer"
    OPERATOR = "operator"
    PAYMENT_PROVIDER = "payment_provider"


@dataclass(frozen=True, slots=True)
class Principal:
    id: UUID
    role: Role

    # DRF reads `request.user.is_authenticated`.
    is_authenticated = True


@dataclass(frozen=True, slots=True)
class _Entry:
    token: bytes
    principal: Principal


@lru_cache(maxsize=4)
def _parse(raw: str) -> tuple[_Entry, ...]:
    entries = []
    for position, item in enumerate(filter(None, (part.strip() for part in raw.split(","))), 1):
        fields = item.split(":", 2)
        if len(fields) != 3:
            raise ImproperlyConfigured(
                f"AUTH_TOKENS entry {position} must be role:principal_id:token."
            )
        role, principal_id, token = (field.strip() for field in fields)
        try:
            principal = Principal(id=UUID(principal_id), role=Role(role))
        except ValueError as exc:
            raise ImproperlyConfigured(f"AUTH_TOKENS entry {position} is invalid: {exc}") from None
        if len(token) < MIN_TOKEN_LENGTH:
            raise ImproperlyConfigured(
                f"AUTH_TOKENS entry {position}: tokens need at least {MIN_TOKEN_LENGTH} characters."
            )
        entries.append(_Entry(token=token.encode(), principal=principal))
    if not entries:
        raise ImproperlyConfigured("AUTH_TOKENS has no entries.")
    if len({entry.token for entry in entries}) != len(entries):
        raise ImproperlyConfigured("AUTH_TOKENS contains a duplicated token.")
    return tuple(entries)


def validate_configuration() -> None:
    """Fail closed at startup when the token configuration is missing or malformed."""
    _parse(settings.AUTH_TOKENS)


def resolve(token: str) -> Principal | None:
    """Return the principal for `token`, comparing against every entry in constant time."""
    candidate = token.encode()
    match = None
    for entry in _parse(settings.AUTH_TOKENS):
        if hmac.compare_digest(entry.token, candidate):
            match = entry.principal
    return match
