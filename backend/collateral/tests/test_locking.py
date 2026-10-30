"""Transaction and row-lock requirements of the mutating services (MOD-4, CON-2, CON-5).

These tests need real commits and a second connection, hence `transaction=True`.
"""

import uuid

import pytest
from django.db import transaction

from collateral.models import CollateralDeposit, DepositStatus
from collateral.services import (
    DepositNotPending,
    InvalidTransition,
    confirm_deposit,
    create_pending_deposit,
    fail_deposit,
    lock_deposit,
    settle_on_closure,
)
from collateral.tests.conftest import (
    AMOUNT_MINOR,
    BackgroundTransaction,
    stored_row,
    try_lock_nowait,
    wait_for_lock_waiters,
)

S = DepositStatus
LOCK_NOT_AVAILABLE = "55P03"

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("name", "call"),
    [
        (
            "create_pending_deposit",
            lambda: create_pending_deposit(owner_id=uuid.uuid4(), amount_minor=1_000),
        ),
        ("lock_deposit", lambda: lock_deposit(uuid.uuid4())),
        ("confirm_deposit", lambda: confirm_deposit(uuid.uuid4())),
        ("fail_deposit", lambda: fail_deposit(uuid.uuid4())),
        ("settle_on_closure", lambda: settle_on_closure(uuid.uuid4(), 0)),
    ],
)
def test_mutating_service_refuses_to_run_outside_a_transaction(name, call):
    """Fails if a mutating service runs in autocommit, where a partial operation could persist.

    The operations core opens the transaction so every app's writes commit together (MOD-4,
    BR-DEP-5).
    """
    with pytest.raises(RuntimeError, match=f"^{name} must run inside transaction.atomic$"):
        call()

    assert CollateralDeposit.objects.count() == 0


def test_lock_deposit_holds_the_row_lock_until_the_transaction_ends(make_deposit):
    """Fails if lock_deposit reads without SELECT ... FOR UPDATE (CON-2, CON-4)."""
    deposit = make_deposit(S.CONFIRMED)

    with transaction.atomic():
        lock_deposit(deposit.id)
        sqlstate_while_locked = try_lock_nowait(deposit.id)
    sqlstate_after_commit = try_lock_nowait(deposit.id)

    assert (sqlstate_while_locked, sqlstate_after_commit) == (LOCK_NOT_AVAILABLE, None)


def test_concurrent_provider_reports_resolve_a_pending_deposit_only_once(make_deposit):
    """Fails if a success and a failure report both apply to one deposit (CON-5, BR-DEP-6).

    The failure report reads the deposit while the confirmation is still uncommitted. Without the
    row lock it would read PENDING, wait only at its UPDATE and then overwrite CONFIRMED.
    """
    deposit = make_deposit(S.PENDING)

    with transaction.atomic():
        confirm_deposit(deposit.id)
        contender = BackgroundTransaction(lambda: fail_deposit(deposit.id))
        wait_for_lock_waiters(1)
    contender.join()

    assert type(contender.error) is DepositNotPending
    assert (stored_row(deposit.id)["status"], stored_row(deposit.id)["failed_at"]) == (
        "CONFIRMED",
        None,
    )


def test_concurrent_closures_settle_a_deposit_only_once(make_deposit):
    """Fails if two closures both settle one deposit, refunding it twice (CON-2, BR-CLO-2)."""
    deposit = make_deposit(S.CONFIRMED)

    with transaction.atomic():
        settle_on_closure(deposit.id, 0)
        contender = BackgroundTransaction(lambda: settle_on_closure(deposit.id, AMOUNT_MINOR))
        wait_for_lock_waiters(1)
    contender.join()

    assert type(contender.error) is InvalidTransition
    row = stored_row(deposit.id)
    assert (row["status"], row["applied_minor"], row["refunded_minor"]) == (
        "RELEASED",
        0,
        AMOUNT_MINOR,
    )
