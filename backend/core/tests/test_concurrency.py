"""Real concurrency against PostgreSQL (docs/testing.md §2): INV-1 and INV-5 under contention.

Every operation runs in its own thread and connection, released together by a barrier. The flows
lock the credit account row first (CON-2), so the database serializes them; each test asserts the
result equals one of the possible serial orders and that the cache still matches the ledger.
"""

import threading
import uuid

import pytest
from django.db import connection, transaction

from accounts import services as accounts
from core import flows
from core.db import is_lock_timeout
from core.reconciliation import find_mismatches
from core.tests.concurrency import (
    approved_amount,
    error_names,
    open_account,
    outcome_names,
    run_concurrently,
)
from ledger import services as ledger

pytestmark = pytest.mark.django_db(transaction=True)

OWNER = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")


def _purchase(card_id, amount):
    return lambda: flows.authorize_purchase(owner_id=OWNER, card_id=card_id, amount_minor=amount)


def _repay(account_id, amount):
    return lambda: flows.repay(owner_id=OWNER, account_id=account_id, amount_minor=amount)


def _close(account_id):
    return lambda: flows.close(owner_id=OWNER, account_id=account_id)


def _freeze(card_id):
    return lambda: flows.freeze_card(owner_id=OWNER, card_id=card_id)


def _purchase_entries(account_id):
    return [entry for entry in ledger.list_entries(account_id) if entry.kind == "PURCHASE"]


@pytest.mark.parametrize("round_", range(5))
def test_purchase_racing_card_freeze_matches_a_serial_order(round_):
    """Fails if a purchase and a freeze racing each other deadlock or leave a state no serial order allows (CON-4)."""
    opened = open_account(OWNER, deposit_minor=1_000)

    purchase, frozen = run_concurrently(_purchase(opened.card.id, 300), _freeze(opened.card.id))

    serial_orders = {
        ("OK", "OK"): 300,  # purchase first, then the card is frozen
        ("CardNotActive", "OK"): 0,  # freeze first: the purchase is rejected
    }
    assert outcome_names([purchase, frozen]) in serial_orders
    assert frozen.value.status == "INACTIVE"
    assert (
        accounts.get_account(opened.account.id).outstanding_minor
        == serial_orders[outcome_names([purchase, frozen])]
    )
    assert find_mismatches() == []


def test_concurrent_purchases_never_exceed_the_limit():
    """Fails if concurrent purchases together overspend the limit or approve the wrong count (TEST-CON-1, INV-1)."""
    opened = open_account(OWNER, deposit_minor=1_000)
    purchases = [_purchase(opened.card.id, 150) for _ in range(10)]

    outcomes = run_concurrently(*purchases)

    account = accounts.get_account(opened.account.id)
    assert sum(outcome.ok for outcome in outcomes) == 1_000 // 150
    assert error_names(outcomes) == ["InsufficientCredit"] * 4
    assert approved_amount(outcomes) == 900 <= account.credit_limit_minor
    assert account.outstanding_minor == 900
    assert len(_purchase_entries(opened.account.id)) == 6
    assert find_mismatches() == []


@pytest.mark.parametrize("round_", range(5))
def test_purchase_racing_closure_matches_a_serial_order(round_):
    """Fails if a purchase racing a closure is lost, double-counted or lands after closure (TEST-CON-2, BR-CLO-6)."""
    opened = open_account(OWNER, deposit_minor=1_000)
    flows.authorize_purchase(owner_id=OWNER, card_id=opened.card.id, amount_minor=200)

    purchase, closure = run_concurrently(_purchase(opened.card.id, 300), _close(opened.account.id))

    approved = approved_amount([purchase])
    assert closure.ok
    assert error_names([purchase]) in ([], ["AccountClosed"])
    assert closure.value.debt_at_closure_minor == 200 + approved
    assert closure.value.refund_minor == 1_000 - 200 - approved
    assert accounts.get_account(opened.account.id).outstanding_minor == 0
    assert find_mismatches() == []


@pytest.mark.parametrize("round_", range(5))
def test_repayment_racing_purchase_matches_a_serial_order(round_):
    """Fails if a repayment and a purchase racing each other produce a state no serial order allows (TEST-CON-3)."""
    opened = open_account(OWNER, deposit_minor=1_000)
    flows.authorize_purchase(owner_id=OWNER, card_id=opened.card.id, amount_minor=500)

    outcomes = run_concurrently(_purchase(opened.card.id, 300), _repay(opened.account.id, 600))

    serial_orders = {
        ("OK", "OK"): 200,  # purchase first: 500 + 300 − 600
        ("OK", "PaymentExceedsDebt"): 800,  # repayment first: 600 > 500 is rejected
    }
    assert outcome_names(outcomes) in serial_orders
    assert (
        accounts.get_account(opened.account.id).outstanding_minor
        == serial_orders[outcome_names(outcomes)]
    )
    assert find_mismatches() == []


def test_mixed_operations_on_two_accounts_of_one_user_stay_isolated():
    """Fails if concurrent operations on two accounts deadlock or leak into each other (TEST-CON-4, BR-OWN-2)."""
    first = open_account(OWNER, deposit_minor=10_000)
    second = open_account(OWNER, deposit_minor=10_000)
    operations = [
        _purchase(first.card.id, 1_000),
        _purchase(second.card.id, 2_000),
        _purchase(first.card.id, 1_000),
        _purchase(second.card.id, 2_000),
        _purchase(first.card.id, 1_000),
        _purchase(second.card.id, 2_000),
    ]

    outcomes = run_concurrently(*operations)

    assert all(outcome.ok for outcome in outcomes)
    assert accounts.get_account(first.account.id).outstanding_minor == 3_000
    assert accounts.get_account(second.account.id).outstanding_minor == 6_000
    assert find_mismatches() == []


def test_waiting_for_a_held_lock_gives_up_after_the_lock_timeout(settings):
    """Fails if an operation waits indefinitely for a locked account instead of timing out (CON-9)."""
    settings.DB_LOCK_TIMEOUT_MS = 200
    opened = open_account(OWNER, deposit_minor=1_000)
    flows.authorize_purchase(owner_id=OWNER, card_id=opened.card.id, amount_minor=100)
    locked, release = threading.Event(), threading.Event()

    def hold_account_lock():
        try:
            with transaction.atomic():
                accounts.lock_account(opened.account.id)
                locked.set()
                release.wait(10)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_account_lock, daemon=True)
    holder.start()
    locked.wait(10)
    try:
        with pytest.raises(Exception) as raised:
            flows.repay(owner_id=OWNER, account_id=opened.account.id, amount_minor=50)
    finally:
        release.set()
        holder.join(10)

    assert is_lock_timeout(raised.value)
    assert accounts.get_account(opened.account.id).outstanding_minor == 100
