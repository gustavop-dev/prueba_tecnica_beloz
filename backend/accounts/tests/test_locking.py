"""Row locking of the mutating services under real concurrent transactions (CACHE-4, CON-2, CON-3).

Each scenario holds the account lock in the test thread, starts a competing operation in another
thread (its own connection and transaction), checks that it waits, then commits a change that makes
the competing operation invalid. A service that validated before locking, or without a lock, would
decide on the stale balance and succeed instead of being rejected.
"""

import threading
from collections.abc import Callable
from uuid import UUID

import pytest
from django.db import connection, transaction

from accounts.services import (
    AccountsError,
    close_account,
    decrease_outstanding,
    increase_outstanding,
    lock_account,
)

BLOCKED_WAIT_SECONDS = 0.5
FINISH_WAIT_SECONDS = 10


def _start_competitor(operation: Callable[[], object]) -> tuple[threading.Thread, list[str]]:
    """Run `operation` in its own thread and transaction, recording how it ended."""
    outcome: list[str] = []

    def run() -> None:
        try:
            with transaction.atomic():
                operation()
            outcome.append("committed")
        except AccountsError as exc:
            outcome.append(type(exc).__name__)
        finally:
            connection.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _purchase(amount: int) -> Callable[[UUID], object]:
    return lambda account_id: increase_outstanding(account_id, amount)


def _payment(amount: int) -> Callable[[UUID], object]:
    return lambda account_id: decrease_outstanding(account_id, amount)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("initial_outstanding", "first", "competitor", "expected_outcome", "final_outstanding"),
    [
        pytest.param(0, _purchase(60), _purchase(60), "InsufficientCredit", 60, id="two-purchases"),
        pytest.param(40, _payment(40), _payment(40), "PaymentExceedsDebt", 0, id="two-payments"),
        pytest.param(40, close_account, _purchase(10), "AccountClosed", 0, id="closure-purchase"),
    ],
)
def test_competing_operation_waits_for_the_lock_and_revalidates(
    make_account,
    stored,
    initial_outstanding,
    first,
    competitor,
    expected_outcome,
    final_outstanding,
):
    """Fails if a mutating service reads the balance before taking the row lock: the competing
    operation would approve on a stale balance (overspending, overpaying or buying on a closed
    account) instead of waiting and being rejected (CACHE-4, CON-2, CON-3, INV-1, BR-CLO-6)."""
    account = make_account(credit_limit_minor=100, outstanding_minor=initial_outstanding)

    with transaction.atomic():
        lock_account(account.id)
        thread, outcome = _start_competitor(lambda: competitor(account.id))
        thread.join(timeout=BLOCKED_WAIT_SECONDS)
        competitor_waited_for_the_lock = thread.is_alive()
        first(account.id)
    thread.join(timeout=FINISH_WAIT_SECONDS)

    assert competitor_waited_for_the_lock is True
    assert outcome == [expected_outcome]
    assert stored(account.id)["outstanding_minor"] == final_outstanding
