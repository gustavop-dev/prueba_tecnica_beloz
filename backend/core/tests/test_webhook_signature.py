"""Provider webhook signatures (docs/security.md §5, docs/testing.md §5)."""

import json
import uuid
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured

from collateral import services as collateral
from core.auth import webhook
from core.tests.conftest import WEBHOOK_SECRET, WEBHOOK_URL, Api, error_code, request_opening

pytestmark = pytest.mark.django_db

NOW = 1_800_000_000
TOLERANCE = 300


def _send(
    body: dict,
    *,
    secret=WEBHOOK_SECRET,
    timestamp=NOW,
    raw: bytes | None = None,
    signed_raw: bytes | None = None,
):
    """POST `raw` (default: the JSON of `body`) signed over `signed_raw` (default: the same bytes)."""
    raw = json.dumps(body).encode() if raw is None else raw
    signature = webhook.sign(secret, timestamp, raw if signed_raw is None else signed_raw)
    with mock.patch("core.auth.webhook.now", return_value=NOW):
        return Api().client.post(
            WEBHOOK_URL,
            raw,
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            HTTP_X_WEBHOOK_SIGNATURE=signature,
        )


@pytest.fixture
def report(customer_a):
    opening = request_opening(customer_a)
    return {"deposit_id": opening["deposit"]["id"], "outcome": "succeeded"}


def _deposit_status(report):
    return collateral.get_deposit(uuid.UUID(report["deposit_id"])).status


def test_correctly_signed_report_is_accepted(report):
    """Fails if a report signed with the shared secret is rejected (SEC-HOOK-1, SEC-HOOK-2)."""
    response = _send(report)

    assert response.status_code == 200
    assert _deposit_status(report) == "CONFIRMED"


def test_report_signed_with_another_secret_is_rejected(report):
    """Fails if a signature made with a different secret is accepted (TEST-SEC-1, SEC-HOOK-2)."""
    response = _send(report, secret="an-attacker-guessed-secret-0123456789")

    assert response.status_code == 401
    assert error_code(response) == "INVALID_SIGNATURE"
    assert _deposit_status(report) == "PENDING"


def test_body_altered_after_signing_is_rejected(report):
    """Fails if a body changed after signing (e.g. succeeded → failed) passes verification (TEST-SEC-2)."""
    original = json.dumps(report).encode()
    tampered = json.dumps({**report, "outcome": "failed"}).encode()

    response = _send(report, raw=tampered, signed_raw=original)

    assert response.status_code == 401
    assert error_code(response) == "INVALID_SIGNATURE"
    assert _deposit_status(report) == "PENDING"


@pytest.mark.parametrize(
    "header",
    ["", "garbage", "t=abc,v1=00", f"t={NOW}", f"v1={'0' * 64}", f"t={NOW},v1={'0' * 64}"],
    ids=["empty", "garbage", "bad-timestamp", "no-signature", "no-timestamp", "wrong-digest"],
)
def test_missing_or_malformed_signature_header_is_rejected(report, header):
    """Fails if a malformed signature header is accepted (TEST-SEC-1)."""
    with mock.patch("core.auth.webhook.now", return_value=NOW):
        response = Api().client.post(
            WEBHOOK_URL,
            json.dumps(report),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            HTTP_X_WEBHOOK_SIGNATURE=header,
        )

    assert response.status_code == 401
    assert error_code(response) == "INVALID_SIGNATURE"


@pytest.mark.parametrize(
    "offset",
    [-(TOLERANCE + 1), TOLERANCE + 1, -3_600],
    ids=["just-expired", "too-far-in-future", "an-hour-old"],
)
def test_timestamp_outside_the_window_is_rejected(report, offset):
    """Fails if a validly signed but stale or future-dated report is accepted (TEST-SEC-3, SEC-HOOK-3)."""
    response = _send(report, timestamp=NOW + offset)

    assert response.status_code == 401
    assert error_code(response) == "SIGNATURE_EXPIRED"
    assert _deposit_status(report) == "PENDING"


@pytest.mark.parametrize(
    "offset", [-TOLERANCE, TOLERANCE - 1, 0], ids=["oldest-allowed", "future-edge", "now"]
)
def test_timestamp_inside_the_window_is_accepted(report, offset):
    """Fails if a report just inside the 5-minute window is rejected (TEST-SEC-3)."""
    response = _send(report, timestamp=NOW + offset)

    assert response.status_code == 200


def test_a_captured_report_replayed_with_a_new_key_cannot_apply_twice(report):
    """Fails if resending a captured signed report inside the window confirms the deposit again (SEC-HOOK-4, IDEM-15)."""
    raw = json.dumps(report).encode()
    signature = webhook.sign(WEBHOOK_SECRET, NOW, raw)

    def send():
        with mock.patch("core.auth.webhook.now", return_value=NOW):
            return Api().client.post(
                WEBHOOK_URL,
                raw,
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
                HTTP_X_WEBHOOK_SIGNATURE=signature,
            )

    first, replayed = send(), send()

    assert first.status_code == 200
    assert replayed.status_code == 409
    assert error_code(replayed) == "DEPOSIT_NOT_PENDING"


def test_signature_comparison_is_constant_time(report):
    """Fails if the signature is compared with `==` instead of a constant-time check (SEC-AUTH-5)."""
    with mock.patch("core.auth.webhook.hmac.compare_digest", return_value=False) as compare:
        response = _send(report)

    assert compare.called
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("secret", "provider_id"),
    [("short-secret", str(uuid.uuid4())), (WEBHOOK_SECRET, "not-a-uuid")],
    ids=["short-secret", "bad-provider-id"],
)
def test_weak_webhook_configuration_fails_closed(settings, secret, provider_id):
    """Fails if a weak secret or malformed provider id lets the service start (SEC-SECRET-3)."""
    settings.WEBHOOK_SIGNING_SECRET = secret
    settings.PAYMENT_PROVIDER_ID = provider_id

    with pytest.raises(ImproperlyConfigured):
        webhook.validate_configuration()
