"""Audit events of the flows (docs/audit.md §1, INV-6)."""

import uuid
from unittest import mock

import pytest

from audit import services as audit
from core.tests.conftest import (
    CUSTOMER_A,
    DEPOSIT_MINOR,
    PROVIDER,
    close,
    purchase,
    repay,
    report_deposit,
    request_opening,
)

pytestmark = pytest.mark.django_db

KEY = "99999999-8888-4777-8666-555555555555"


def _events(response):
    return audit.list_events(request_id=uuid.UUID(response["X-Request-ID"]))


def _summary(events):
    return [(event.action, event.entity_type, event.actor_role) for event in events]


def test_opening_request_records_who_what_when_and_which_request(customer_a):
    """Fails if a mutation is not audited with actor, role, entity, states, request id and key (AUD-1, INV-6)."""
    response = customer_a.post(
        "/accounts", {"deposit_amount_minor": 25_000}, HTTP_IDEMPOTENCY_KEY=KEY
    )

    (event,) = _events(response)
    assert event.action == "deposit.requested"
    assert event.actor_id == CUSTOMER_A
    assert event.actor_role == "customer"
    assert event.entity_type == "collateral_deposit"
    assert str(event.entity_id) == response.json()["deposit"]["id"]
    assert event.before_state is None
    assert event.after_state["status"] == "PENDING"
    assert event.after_state["amount_minor"] == 25_000
    assert event.idempotency_key == uuid.UUID(KEY)
    assert event.occurred_at is not None


def test_confirmation_records_deposit_account_and_card_as_the_provider(customer_a, provider):
    """Fails if opening an account does not audit each created or changed entity (AUD-1)."""
    opening = request_opening(customer_a)

    response = report_deposit(provider, opening["deposit"]["id"])

    events = _events(response)
    assert _summary(events) == [
        ("deposit.confirmed", "collateral_deposit", "payment_provider"),
        ("credit_account.opened", "credit_account", "payment_provider"),
        ("card.issued", "card", "payment_provider"),
    ]
    assert {event.actor_id for event in events} == {PROVIDER}
    assert events[0].before_state["status"] == "PENDING"
    assert events[0].after_state["status"] == "CONFIRMED"


def test_failed_deposit_is_audited(customer_a, provider):
    """Fails if a failed deposit leaves no audit trail (AUD-1)."""
    opening = request_opening(customer_a)

    response = report_deposit(provider, opening["deposit"]["id"], outcome="failed")

    assert _summary(_events(response)) == [
        ("deposit.failed", "collateral_deposit", "payment_provider")
    ]


def test_purchase_and_repayment_record_the_debt_before_and_after(customer_a, account_a):
    """Fails if money operations are not audited with the debt before and after (AUD-1)."""
    bought = purchase(customer_a, account_a.card_id, 30_000)
    repaid = repay(customer_a, account_a.account_id, 10_000)

    (purchase_event,) = _events(bought)
    (repayment_event,) = _events(repaid)
    assert purchase_event.action == "credit_account.purchase_authorized"
    assert (
        purchase_event.before_state["outstanding_minor"],
        purchase_event.after_state["outstanding_minor"],
    ) == (0, 30_000)
    assert repayment_event.action == "credit_account.repayment_applied"
    assert (
        repayment_event.before_state["outstanding_minor"],
        repayment_event.after_state["outstanding_minor"],
    ) == (30_000, 20_000)


def test_closure_records_account_deposit_and_card(customer_a, account_a):
    """Fails if closure does not audit the account, the settled deposit and the closed card (AUD-1)."""
    purchase(customer_a, account_a.card_id, 30_000)

    response = close(customer_a, account_a.account_id)

    events = _events(response)
    assert _summary(events) == [
        ("credit_account.closed", "credit_account", "customer"),
        ("deposit.settled", "collateral_deposit", "customer"),
        ("card.closed", "card", "customer"),
    ]
    assert events[1].after_state == {
        "status": "PARTIALLY_APPLIED",
        "amount_minor": DEPOSIT_MINOR,
        "applied_minor": 30_000,
        "refunded_minor": DEPOSIT_MINOR - 30_000,
    }


def test_card_events_never_carry_the_token_or_a_card_number(customer_a, provider):
    """Fails if a card audit state holds anything beyond status and last 4 digits (AUD-4, SEC-CARD-3)."""
    opening = request_opening(customer_a)
    response = report_deposit(provider, opening["deposit"]["id"])

    card_event = next(event for event in _events(response) if event.entity_type == "card")

    assert set(card_event.after_state) == {"status", "last4"}


def test_business_rejection_is_not_audited(customer_a, account_a):
    """Fails if a rejected operation, which changed nothing, produces an audit event (AUD-1)."""
    response = purchase(customer_a, account_a.card_id, DEPOSIT_MINOR + 1)

    assert response.status_code == 422
    assert _events(response) == []


def test_replayed_request_is_not_audited_again(customer_a, account_a):
    """Fails if an idempotent replay records the mutation a second time (AUD-1, INV-4)."""
    url = f"/cards/{account_a.card_id}/authorizations"
    customer_a.post(url, {"amount_minor": 1_000}, HTTP_IDEMPOTENCY_KEY=KEY)

    replay = customer_a.post(url, {"amount_minor": 1_000}, HTTP_IDEMPOTENCY_KEY=KEY)

    assert replay["Idempotent-Replayed"] == "true"
    assert _events(replay) == []
    assert len(audit.list_events(entity_id=uuid.UUID(account_a.account_id))) == 2


def test_rolled_back_operation_leaves_no_audit_event(customer_a, account_a):
    """Fails if an audit event survives the rollback of its mutation (AUD-2)."""
    with mock.patch("core.flows.operations.ledger.post_purchase", side_effect=RuntimeError("down")):
        response = purchase(customer_a, account_a.card_id, 1_000)

    assert response.status_code == 500
    assert _events(response) == []
