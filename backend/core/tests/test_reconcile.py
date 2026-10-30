import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from accounts import services as accounts
from collateral import services as collateral
from core.money import COLLATERAL_RATIO_BPS, credit_limit_minor
from core.reconciliation import find_mismatches
from ledger import services as ledger

DEPOSIT_MINOR = 100_000
PURCHASE_MINOR = 30_000


@pytest.fixture
def active_account_with_debt():
    """An opened account with one purchase, built through the public services only."""
    owner_id = uuid.uuid4()
    deposit = collateral.create_pending_deposit(owner_id=owner_id, amount_minor=DEPOSIT_MINOR)
    collateral.confirm_deposit(deposit.id)
    account = accounts.open_account(
        account_id=deposit.reserved_account_id,
        owner_id=owner_id,
        deposit_id=deposit.id,
        credit_limit_minor=credit_limit_minor(DEPOSIT_MINOR, COLLATERAL_RATIO_BPS),
        collateral_ratio_bps=COLLATERAL_RATIO_BPS,
    )
    ledger.provision_customer_accounts(account.id)
    ledger.post_deposit_confirmed(account.id, DEPOSIT_MINOR, reference_id=deposit.id)
    accounts.increase_outstanding(account.id, PURCHASE_MINOR)
    ledger.post_purchase(account.id, PURCHASE_MINOR, reference_id=uuid.uuid4())
    return account


def _tamper_outstanding(account_id, value):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE accounts_creditaccount SET outstanding_minor = %s WHERE id = %s",
            [value, account_id],
        )


@pytest.mark.django_db
def test_reconcile_reports_nothing_when_cache_matches_ledger(active_account_with_debt):
    """Fails if reconcile flags a consistent account as broken (TEST-REC-2)."""
    out = StringIO()

    call_command("reconcile", stdout=out)

    assert find_mismatches() == []
    assert "OK: cache, deposits and ledger are consistent." in out.getvalue()


@pytest.mark.django_db
def test_reconcile_detects_a_tampered_outstanding_cache(active_account_with_debt):
    """Fails if a cache altered behind the services goes unnoticed (TEST-REC-1)."""
    account_id = active_account_with_debt.id
    _tamper_outstanding(account_id, PURCHASE_MINOR + 1)
    out = StringIO()

    with pytest.raises(CommandError, match="1 mismatch"):
        call_command("reconcile", stdout=out)

    assert (
        f"MISMATCH outstanding_vs_receivable account={account_id} "
        f"expected={PURCHASE_MINOR} actual={PURCHASE_MINOR + 1}"
    ) in out.getvalue()
