"""Card freeze and unfreeze endpoints (state-machines.md §3, the optional add-on of business-rules.md §1)."""

import uuid

import pytest

from audit import services as audit
from core.reconciliation import find_mismatches
from core.tests.conftest import DEPOSIT_MINOR, close, error_code, purchase, repay

pytestmark = pytest.mark.django_db

KEY = "77777777-6666-4555-8444-333333333333"


def freeze(customer, card_id, **headers):
    return customer.post(f"/cards/{card_id}/freeze", **headers)


def unfreeze(customer, card_id, **headers):
    return customer.post(f"/cards/{card_id}/unfreeze", **headers)


def _card_status(customer, account):
    return customer.get(f"/accounts/{account.account_id}").json()["card"]["status"]


def test_freeze_makes_the_card_inactive(customer_a, account_a):
    """Fails if freezing does not move an ACTIVE card to INACTIVE (state-machines.md §3)."""
    response = freeze(customer_a, account_a.card_id)

    assert response.status_code == 200
    assert response.json()["account_id"] == account_a.account_id
    assert response.json()["card"]["id"] == account_a.card_id
    assert response.json()["card"]["status"] == "INACTIVE"
    assert _card_status(customer_a, account_a) == "INACTIVE"


def test_frozen_card_cannot_authorize_purchases(customer_a, account_a):
    """Fails if a frozen card still authorizes a purchase or moves the debt (BR-PUR-2, TEST-SM-3)."""
    freeze(customer_a, account_a.card_id)

    response = purchase(customer_a, account_a.card_id, 1_000)

    assert response.status_code == 409
    assert error_code(response) == "CARD_NOT_ACTIVE"
    assert customer_a.get(f"/accounts/{account_a.account_id}").json()["outstanding_minor"] == 0


def test_unfreeze_restores_purchases(customer_a, account_a):
    """Fails if an unfrozen card stays unable to authorize purchases (state-machines.md §3)."""
    freeze(customer_a, account_a.card_id)

    response = unfreeze(customer_a, account_a.card_id)
    bought = purchase(customer_a, account_a.card_id, 1_000)

    assert response.status_code == 200
    assert response.json()["card"]["status"] == "ACTIVE"
    assert bought.status_code == 201


def test_repayment_is_still_accepted_while_the_card_is_frozen(customer_a, account_a):
    """Fails if freezing the card blocks repaying the account's debt (BR-PAY-1)."""
    purchase(customer_a, account_a.card_id, 30_000)
    freeze(customer_a, account_a.card_id)

    response = repay(customer_a, account_a.account_id, 10_000)

    assert response.status_code == 201
    assert response.json()["outstanding_minor"] == 20_000


def test_account_with_a_frozen_card_can_be_closed(customer_a, account_a):
    """Fails if a frozen card blocks closure or is left INACTIVE after it (state-machines.md §3: INACTIVE → CLOSED)."""
    purchase(customer_a, account_a.card_id, 30_000)
    freeze(customer_a, account_a.card_id)

    response = close(customer_a, account_a.account_id)

    assert response.status_code == 200
    assert response.json()["card"]["status"] == "CLOSED"
    assert response.json()["refund_minor"] == DEPOSIT_MINOR - 30_000
    assert find_mismatches() == []


def test_freezing_a_frozen_card_is_rejected(customer_a, account_a):
    """Fails if INACTIVE → INACTIVE, which the state machine does not list, is accepted (INV-5, TEST-SM-1)."""
    freeze(customer_a, account_a.card_id)

    response = freeze(customer_a, account_a.card_id)

    assert response.status_code == 409
    assert error_code(response) == "INVALID_STATE_TRANSITION"
    assert _card_status(customer_a, account_a) == "INACTIVE"


def test_unfreezing_an_active_card_is_rejected(customer_a, account_a):
    """Fails if ACTIVE → ACTIVE, which the state machine does not list, is accepted (INV-5, TEST-SM-1)."""
    response = unfreeze(customer_a, account_a.card_id)

    assert response.status_code == 409
    assert error_code(response) == "INVALID_STATE_TRANSITION"
    assert _card_status(customer_a, account_a) == "ACTIVE"


@pytest.mark.parametrize("operation", ["freeze", "unfreeze"])
def test_card_of_a_closed_account_cannot_be_frozen_or_unfrozen(customer_a, account_a, operation):
    """Fails if the card of a closed account changes state (BR-CLO-6)."""
    close(customer_a, account_a.account_id)
    calls = {"freeze": freeze, "unfreeze": unfreeze}

    response = calls[operation](customer_a, account_a.card_id)

    assert response.status_code == 409
    assert error_code(response) == "ACCOUNT_CLOSED"
    assert _card_status(customer_a, account_a) == "CLOSED"


def test_unknown_card_is_not_found(customer_a):
    """Fails if freezing a card that does not exist is not answered with 404."""
    response = freeze(customer_a, str(uuid.uuid4()))

    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


def test_freeze_and_unfreeze_are_audited(customer_a, account_a):
    """Fails if a card status change leaves no audit event with its before and after state (AUD-1)."""
    frozen = freeze(customer_a, account_a.card_id)
    unfrozen = unfreeze(customer_a, account_a.card_id)

    (freeze_event,) = audit.list_events(request_id=uuid.UUID(frozen["X-Request-ID"]))
    (unfreeze_event,) = audit.list_events(request_id=uuid.UUID(unfrozen["X-Request-ID"]))
    assert (freeze_event.action, freeze_event.entity_type, freeze_event.actor_role) == (
        "card.frozen",
        "card",
        "customer",
    )
    assert (freeze_event.before_state["status"], freeze_event.after_state["status"]) == (
        "ACTIVE",
        "INACTIVE",
    )
    assert unfreeze_event.action == "card.unfrozen"
    assert (unfreeze_event.before_state["status"], unfreeze_event.after_state["status"]) == (
        "INACTIVE",
        "ACTIVE",
    )


def test_freeze_retry_with_the_same_key_replays_without_a_second_event(customer_a, account_a):
    """Fails if a retried freeze runs again instead of replaying the first answer (INV-4)."""
    first = freeze(customer_a, account_a.card_id, HTTP_IDEMPOTENCY_KEY=KEY)

    retry = freeze(customer_a, account_a.card_id, HTTP_IDEMPOTENCY_KEY=KEY)

    assert retry.status_code == first.status_code == 200
    assert retry.json() == first.json()
    assert retry["Idempotent-Replayed"] == "true"
    assert len(audit.list_events(entity_id=uuid.UUID(account_a.card_id))) == 2  # issued + frozen


def test_freeze_requires_an_idempotency_key(customer_a, account_a):
    """Fails if a card status change runs without an Idempotency-Key (IDEM-1)."""
    response = customer_a.client.post(f"/cards/{account_a.card_id}/freeze", {}, format="json")

    assert response.status_code == 400
    assert error_code(response) == "IDEMPOTENCY_KEY_MISSING"
    assert _card_status(customer_a, account_a) == "ACTIVE"
