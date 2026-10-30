import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.auth.principals import Principal, Role, resolve, validate_configuration
from core.tests.conftest import CUSTOMER_A, PROVIDER, TOKENS

VALID_ID = "11111111-1111-4111-8111-111111111111"


def test_resolve_maps_a_token_to_its_principal_and_role():
    """Fails if a configured token does not resolve to its principal id and role (SEC-AUTH-3)."""
    assert resolve(TOKENS["customer_a"][2]) == Principal(id=CUSTOMER_A, role=Role.CUSTOMER)
    assert resolve(TOKENS["provider"][2]) == Principal(id=PROVIDER, role=Role.PAYMENT_PROVIDER)


@pytest.mark.parametrize("token", ["", "unknown-token-123456", TOKENS["customer_a"][2] + "x"])
def test_resolve_returns_none_for_unknown_tokens(token):
    """Fails if a token that is not configured resolves to a principal (SEC-AUTH-4)."""
    assert resolve(token) is None


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "customer:not-a-uuid:long-enough-token-1",
        f"admin:{VALID_ID}:long-enough-token-1",
        f"customer:{VALID_ID}:short",
        f"customer:{VALID_ID}",
        f"customer:{VALID_ID}:long-enough-token-1,payment_provider:{uuid.uuid4()}:long-enough-token-1",
    ],
    ids=["empty", "bad-uuid", "unknown-role", "short-token", "missing-token", "duplicate-token"],
)
def test_malformed_token_configuration_fails_closed(settings, raw):
    """Fails if a malformed AUTH_TOKENS value is accepted instead of stopping startup (SEC-SECRET-3)."""
    settings.AUTH_TOKENS = raw

    with pytest.raises(ImproperlyConfigured, match="AUTH_TOKENS"):
        validate_configuration()


def test_well_formed_token_configuration_is_accepted(settings):
    """Fails if a valid AUTH_TOKENS value is rejected at startup."""
    settings.AUTH_TOKENS = f" customer:{VALID_ID}:long-enough-token-1 , "

    validate_configuration()

    assert resolve("long-enough-token-1") == Principal(id=uuid.UUID(VALID_ID), role=Role.CUSTOMER)
