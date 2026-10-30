import uuid

import pytest
from django.db import transaction

from cards import services as cards
from core.tests.conftest import (
    DEPOSIT_MINOR,
    close,
    error_code,
    purchase,
    repay,
)

pytestmark = pytest.mark.django_db


def _detail(customer, account):
    return customer.get(f"/accounts/{account.account_id}").json()


def _entries(customer, account):
    return customer.get(f"/accounts/{account.account_id}/ledger").json()["entries"]


def _spend(customer, account, amount_minor):
    """Leave `amount_minor` of debt on the account (no purchase for zero)."""
    if amount_minor:
        assert purchase(customer, account.card_id, amount_minor).status_code == 201


# Purchases ------------------------------------------------------------------------------------


def test_purchase_is_approved_and_recorded_as_debt_immediately(customer_a, account_a):
    """Fails if an approved purchase does not raise the debt in the same operation (BR-PUR-1)."""
    response = purchase(customer_a, account_a.card_id, 30_000)

    assert response.status_code == 201
    assert response.json()["status"] == "APPROVED"
    assert response.json()["outstanding_minor"] == 30_000
    assert response.json()["available_credit_minor"] == DEPOSIT_MINOR - 30_000
    assert _detail(customer_a, account_a)["outstanding_minor"] == 30_000


def test_purchase_is_booked_as_receivable_against_merchant_payable(customer_a, account_a):
    """Fails if a purchase is not booked Dr customer_receivable / Cr merchant_payable (ledger.md §5)."""
    authorization_id = purchase(customer_a, account_a.card_id, 30_000).json()["authorization_id"]

    entry = _entries(customer_a, account_a)[-1]

    assert entry["kind"] == "PURCHASE"
    assert entry["reference_id"] == authorization_id
    assert entry["lines"] == [
        {"ledger_account": "CUSTOMER_RECEIVABLE", "amount_minor": 30_000},
        {"ledger_account": "MERCHANT_PAYABLE", "amount_minor": -30_000},
    ]


def test_purchase_of_exactly_the_available_credit_is_approved(customer_a, account_a):
    """Fails if spending exactly the available credit is rejected (BR-LIM-5, BR-PUR-2)."""
    response = purchase(customer_a, account_a.card_id, DEPOSIT_MINOR)

    assert response.status_code == 201
    assert response.json()["available_credit_minor"] == 0


def test_purchase_above_the_available_credit_is_rejected_in_full(customer_a, account_a):
    """Fails if a purchase over the available credit is approved, even partially (BR-PUR-2, INV-1)."""
    purchase(customer_a, account_a.card_id, 60_000)

    response = purchase(customer_a, account_a.card_id, 40_001)

    assert response.status_code == 422
    assert error_code(response) == "INSUFFICIENT_CREDIT"
    assert _detail(customer_a, account_a)["outstanding_minor"] == 60_000
    assert len(_entries(customer_a, account_a)) == 2


def test_purchase_with_a_frozen_card_is_rejected(customer_a, account_a):
    """Fails if a card that is not ACTIVE can authorize a purchase (BR-PUR-2, TEST-SM-3)."""
    with transaction.atomic():
        cards.freeze_card(uuid.UUID(account_a.card_id))

    response = purchase(customer_a, account_a.card_id, 1_000)

    assert response.status_code == 409
    assert error_code(response) == "CARD_NOT_ACTIVE"
    assert _detail(customer_a, account_a)["outstanding_minor"] == 0


def test_purchase_with_an_unknown_card_is_not_found(customer_a):
    """Fails if a purchase on a card that does not exist is not answered with 404."""
    response = purchase(customer_a, str(uuid.uuid4()), 1_000)

    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


# Repayments -----------------------------------------------------------------------------------


def test_repayment_reduces_the_outstanding_debt(customer_a, account_a):
    """Fails if a repayment does not reduce the debt by its amount (BR-PAY-1)."""
    purchase(customer_a, account_a.card_id, 30_000)

    response = repay(customer_a, account_a.account_id, 10_000)

    assert response.status_code == 201
    assert response.json()["outstanding_minor"] == 20_000
    assert response.json()["available_credit_minor"] == DEPOSIT_MINOR - 20_000


def test_repayment_is_booked_as_cash_against_customer_receivable(customer_a, account_a):
    """Fails if a repayment is not booked Dr cash / Cr customer_receivable (ledger.md §5)."""
    purchase(customer_a, account_a.card_id, 30_000)
    repayment_id = repay(customer_a, account_a.account_id, 10_000).json()["repayment_id"]

    entry = _entries(customer_a, account_a)[-1]

    assert entry["kind"] == "REPAYMENT"
    assert entry["reference_id"] == repayment_id
    assert entry["lines"] == [
        {"ledger_account": "CASH", "amount_minor": 10_000},
        {"ledger_account": "CUSTOMER_RECEIVABLE", "amount_minor": -10_000},
    ]


def test_repayment_of_the_full_debt_leaves_it_at_zero(customer_a, account_a):
    """Fails if paying exactly the outstanding debt is rejected (BR-PAY-1)."""
    purchase(customer_a, account_a.card_id, 30_000)

    response = repay(customer_a, account_a.account_id, 30_000)

    assert response.status_code == 201
    assert response.json()["outstanding_minor"] == 0


def test_repayment_above_the_debt_is_rejected(customer_a, account_a):
    """Fails if a payment larger than the debt is accepted, creating a credit balance (BR-PAY-2)."""
    purchase(customer_a, account_a.card_id, 30_000)

    response = repay(customer_a, account_a.account_id, 30_001)

    assert response.status_code == 422
    assert error_code(response) == "PAYMENT_EXCEEDS_DEBT"
    assert _detail(customer_a, account_a)["outstanding_minor"] == 30_000


def test_any_repayment_is_rejected_when_there_is_no_debt(customer_a, account_a):
    """Fails if a payment is accepted while the debt is zero (BR-PAY-3)."""
    response = repay(customer_a, account_a.account_id, 1)

    assert response.status_code == 422
    assert error_code(response) == "PAYMENT_EXCEEDS_DEBT"


# Closure --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("debt", "deposit_status", "refund"),
    [
        (0, "RELEASED", DEPOSIT_MINOR),
        (30_000, "PARTIALLY_APPLIED", DEPOSIT_MINOR - 30_000),
        (DEPOSIT_MINOR, "APPLIED", 0),
    ],
    ids=["no-debt", "partial-debt", "debt-equals-deposit"],
)
def test_closure_applies_collateral_to_debt_and_refunds_the_rest(
    customer_a, account_a, debt, deposit_status, refund
):
    """Fails if closure does not refund deposit − debt and settle the deposit accordingly (BR-CLO-1..4)."""
    _spend(customer_a, account_a, debt)

    response = close(customer_a, account_a.account_id)

    assert response.status_code == 200
    assert response.json()["debt_at_closure_minor"] == debt
    assert response.json()["refund_minor"] == refund
    assert response.json()["deposit"]["status"] == deposit_status
    assert response.json()["deposit"]["applied_minor"] == debt
    assert response.json()["deposit"]["refunded_minor"] == refund


def test_full_refund_happens_only_with_zero_debt(customer_a, account_a):
    """Fails if the full deposit is refunded while debt existed (BR-CLO-2, the 'only if' direction)."""
    purchase(customer_a, account_a.card_id, 1)

    response = close(customer_a, account_a.account_id)

    assert response.json()["refund_minor"] == DEPOSIT_MINOR - 1
    assert response.json()["deposit"]["status"] == "PARTIALLY_APPLIED"


def test_closure_leaves_debt_at_zero_and_closes_account_and_card(customer_a, account_a):
    """Fails if after closure the debt is not zero or the account or card stay open (BR-CLO-5)."""
    purchase(customer_a, account_a.card_id, 30_000)
    close(customer_a, account_a.account_id)

    detail = _detail(customer_a, account_a)

    assert detail["status"] == "CLOSED"
    assert detail["outstanding_minor"] == 0
    assert detail["card"]["status"] == "CLOSED"
    assert detail["closed_at"] is not None


def test_closure_is_booked_in_one_entry_with_application_and_refund(customer_a, account_a):
    """Fails if closure is not one entry applying collateral to receivable and refunding cash (ledger.md §5)."""
    purchase(customer_a, account_a.card_id, 30_000)
    close(customer_a, account_a.account_id)

    entry = _entries(customer_a, account_a)[-1]

    assert entry["kind"] == "CLOSURE"
    assert sorted(
        entry["lines"], key=lambda line: (line["ledger_account"], line["amount_minor"])
    ) == [
        {"ledger_account": "CASH", "amount_minor": -70_000},
        {"ledger_account": "CUSTOMER_COLLATERAL", "amount_minor": 30_000},
        {"ledger_account": "CUSTOMER_COLLATERAL", "amount_minor": 70_000},
        {"ledger_account": "CUSTOMER_RECEIVABLE", "amount_minor": -30_000},
    ]


@pytest.mark.parametrize("operation", ["purchase", "repay", "close"])
def test_no_operation_is_allowed_on_a_closed_account(customer_a, account_a, operation):
    """Fails if a closed account accepts a purchase, payment or second closure (BR-CLO-6, TEST-SM-2)."""
    close(customer_a, account_a.account_id)
    calls = {
        "purchase": lambda: purchase(customer_a, account_a.card_id, 1),
        "repay": lambda: repay(customer_a, account_a.account_id, 1),
        "close": lambda: close(customer_a, account_a.account_id),
    }

    response = calls[operation]()

    assert response.status_code == 409
    assert error_code(response) == "ACCOUNT_CLOSED"
