import uuid

import pytest

from core.tests.conftest import (
    DEPOSIT_MINOR,
    error_code,
    report_deposit,
    request_opening,
)

pytestmark = pytest.mark.django_db


def test_opening_request_creates_a_pending_deposit_and_reserves_the_account_id(customer_a):
    """Fails if requesting an opening does not create a PENDING deposit with a reserved id (BR-DEP-1)."""
    body = request_opening(customer_a, 25_000)

    assert body["deposit"]["status"] == "PENDING"
    assert body["deposit"]["amount_minor"] == 25_000
    assert body["deposit"]["currency"] == "USD"
    assert uuid.UUID(body["account_id"])


def test_account_does_not_exist_before_the_deposit_is_confirmed(customer_a):
    """Fails if an account becomes visible while its deposit is still pending (BR-ACC-1, BR-ACC-4)."""
    body = request_opening(customer_a)

    response = customer_a.get(f"/accounts/{body['account_id']}")

    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


def test_confirmed_deposit_opens_account_with_limit_and_active_card(customer_a, provider):
    """Fails if confirmation does not open the account, set the limit and activate the card together (BR-DEP-3)."""
    opening = request_opening(customer_a)

    report = report_deposit(provider, opening["deposit"]["id"])
    detail = customer_a.get(f"/accounts/{opening['account_id']}").json()

    assert report.status_code == 200
    assert report.json()["deposit"]["status"] == "CONFIRMED"
    assert report.json()["account_id"] == opening["account_id"]
    assert detail["status"] == "ACTIVE"
    assert detail["card"]["status"] == "ACTIVE"
    assert len(detail["card"]["last4"]) == 4
    assert detail["deposit"]["status"] == "CONFIRMED"


def test_limit_equals_the_confirmed_deposit_with_ratio_one(customer_a, provider):
    """Fails if the credit limit is not deposit × ratio with ratio 1.0 (BR-LIM-1, BR-LIM-4)."""
    opening = request_opening(customer_a, 123_456)
    report_deposit(provider, opening["deposit"]["id"])

    detail = customer_a.get(f"/accounts/{opening['account_id']}").json()

    assert detail["collateral_ratio_bps"] == 10_000
    assert detail["credit_limit_minor"] == 123_456
    assert detail["outstanding_minor"] == 0
    assert detail["available_credit_minor"] == 123_456


def test_confirmed_deposit_is_recorded_as_cash_against_customer_collateral(customer_a, provider):
    """Fails if the deposit is not booked Dr cash / Cr customer_collateral (ledger.md §5)."""
    opening = request_opening(customer_a)
    report_deposit(provider, opening["deposit"]["id"])

    entries = customer_a.get(f"/accounts/{opening['account_id']}/ledger").json()["entries"]

    assert [entry["kind"] for entry in entries] == ["DEPOSIT_CONFIRMED"]
    assert entries[0]["reference_id"] == opening["deposit"]["id"]
    assert entries[0]["lines"] == [
        {"ledger_account": "CASH", "amount_minor": DEPOSIT_MINOR},
        {"ledger_account": "CUSTOMER_COLLATERAL", "amount_minor": -DEPOSIT_MINOR},
    ]


def test_failed_deposit_is_marked_failed_and_opens_nothing(customer_a, provider):
    """Fails if a failed deposit creates an account, card or ledger entry (BR-DEP-4)."""
    opening = request_opening(customer_a)

    report = report_deposit(provider, opening["deposit"]["id"], outcome="failed")
    account = customer_a.get(f"/accounts/{opening['account_id']}")

    assert report.status_code == 200
    assert report.json() == {
        "deposit": {
            "id": opening["deposit"]["id"],
            "status": "FAILED",
            "amount_minor": DEPOSIT_MINOR,
            "applied_minor": None,
            "refunded_minor": None,
            "currency": "USD",
        },
        "account_id": None,
    }
    assert account.status_code == 404


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("succeeded", "succeeded"),
        ("succeeded", "failed"),
        ("failed", "succeeded"),
        ("failed", "failed"),
    ],
)
def test_only_a_pending_deposit_can_be_reported(customer_a, provider, first, second):
    """Fails if an already processed deposit accepts another report (BR-DEP-6, IDEM-15)."""
    opening = request_opening(customer_a)
    report_deposit(provider, opening["deposit"]["id"], outcome=first)

    response = report_deposit(provider, opening["deposit"]["id"], outcome=second)

    assert response.status_code == 409
    assert error_code(response) == "DEPOSIT_NOT_PENDING"


def test_second_confirmation_does_not_book_the_deposit_twice(customer_a, provider):
    """Fails if a repeated confirmation records the deposit again in the ledger (BR-ACC-2)."""
    opening = request_opening(customer_a)
    report_deposit(provider, opening["deposit"]["id"])
    report_deposit(provider, opening["deposit"]["id"])

    entries = customer_a.get(f"/accounts/{opening['account_id']}/ledger").json()["entries"]

    assert len(entries) == 1


def test_report_for_an_unknown_deposit_is_not_found(provider):
    """Fails if the webhook accepts a deposit id that does not exist."""
    response = report_deposit(provider, str(uuid.uuid4()))

    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"outcome": "succeeded"}, "deposit_id"),
        ({"deposit_id": "not-a-uuid", "outcome": "succeeded"}, "deposit_id"),
        ({"deposit_id": str(uuid.uuid4()), "outcome": "maybe"}, "outcome"),
        (
            {"deposit_id": str(uuid.uuid4()), "outcome": "succeeded", "amount_minor": 1},
            "amount_minor",
        ),
    ],
    ids=["missing-id", "malformed-id", "unknown-outcome", "unknown-field"],
)
def test_webhook_rejects_invalid_reports(provider, body, field):
    """Fails if a malformed provider report is accepted (SEC-INPUT-1..4)."""
    response = provider.post("/webhooks/payments", body)

    assert response.status_code == 400
    assert error_code(response) == "VALIDATION_ERROR"
    assert field in response.json()["error"]["details"]
