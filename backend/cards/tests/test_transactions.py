"""Transaction and row-lock requirements of the card services (contract §1, CON-2, CON-3).

These tests use `transaction=True`: the default `django_db` wrapper would hide a missing
`transaction.atomic`, and another connection can only see (and contend for) committed rows.
"""

from uuid import uuid4

import pytest
from django.db import transaction

from cards.models import Card
from cards.services import (
    close_card,
    ensure_can_authorize,
    freeze_card,
    get_card,
    issue_card,
    lock_card,
    unfreeze_card,
)
from cards.tests.helpers import row_is_locked_elsewhere

REQUIRES_ATOMIC = [lock_card, ensure_can_authorize, freeze_card, unfreeze_card, close_card]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("service", REQUIRES_ATOMIC, ids=lambda fn: fn.__name__)
def test_service_outside_a_transaction_fails_without_touching_the_card(committed_card, service):
    """Fails if a locking/mutating card service runs outside `transaction.atomic` (CON-1)."""
    expected = f"{service.__name__} must run inside transaction.atomic"

    with pytest.raises(RuntimeError, match=expected):
        service(committed_card.id)

    assert Card.objects.get(pk=committed_card.id).status == "ACTIVE"


@pytest.mark.django_db(transaction=True)
def test_issue_card_outside_a_transaction_fails_without_creating_a_card():
    """Fails if a card can be issued outside the caller's `transaction.atomic` (CON-1, MOD-4)."""
    account_id = uuid4()

    with pytest.raises(RuntimeError, match="issue_card must run inside transaction.atomic"):
        issue_card(account_id)

    assert Card.objects.filter(credit_account_id=account_id).count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("service", "expect_locked"),
    [(get_card, False), (lock_card, True), (ensure_can_authorize, True)],
    ids=["get_card", "lock_card", "ensure_can_authorize"],
)
def test_locking_services_hold_the_card_row_lock_until_commit(
    committed_card, service, expect_locked
):
    """Fails if `lock_card` / `ensure_can_authorize` read without `SELECT ... FOR UPDATE`, so a
    concurrent transaction could change the card between validation and write (CON-2, CON-3).
    `get_card` is the control: a plain read must not lock."""
    with transaction.atomic():
        service(committed_card.id)
        locked = row_is_locked_elsewhere(committed_card.id)

    assert locked is expect_locked
