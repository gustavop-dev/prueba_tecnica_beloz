"""Reconciliation of the balance cache and the deposits against the ledger (docs/ledger.md §7)."""

from dataclasses import dataclass
from uuid import UUID

from accounts import services as accounts
from collateral import services as collateral
from core.db import read_only_snapshot
from core.money import credit_limit_minor
from ledger import services as ledger


@dataclass(frozen=True, slots=True)
class Mismatch:
    check: str
    expected: object
    actual: object
    account_id: UUID | None = None
    entry_id: UUID | None = None


def find_mismatches() -> list[Mismatch]:
    """Return every inconsistency between the cache, the deposits and the ledger.

    All checks read the same snapshot (`read_only_snapshot`).
    """
    with read_only_snapshot():
        mismatches = [
            Mismatch(check="unbalanced_entry", expected=0, actual="non-zero sum", entry_id=entry_id)
            for entry_id in ledger.find_unbalanced_entry_ids()
        ]
        for account in accounts.iter_accounts():
            mismatches.extend(_account_mismatches(account))
    return mismatches


def find_account_mismatches(account_id: UUID) -> list[Mismatch]:
    """The same per-account checks for one account (used by property tests after every step)."""
    with read_only_snapshot():
        return _account_mismatches(accounts.get_account(account_id))


def _account_mismatches(account: accounts.AccountDTO) -> list[Mismatch]:
    balances = ledger.get_customer_balances(account.id)
    deposit = collateral.get_deposit(account.deposit_id)
    expected_collateral = deposit.amount_minor if account.status == "ACTIVE" else 0
    expected_limit = credit_limit_minor(deposit.amount_minor, account.collateral_ratio_bps)

    checks = [
        ("outstanding_vs_receivable", balances.receivable_minor, account.outstanding_minor),
        ("collateral_vs_deposit", expected_collateral, balances.collateral_minor),
        ("credit_limit_vs_formula", expected_limit, account.credit_limit_minor),
    ]
    return [
        Mismatch(check=name, expected=expected, actual=actual, account_id=account.id)
        for name, expected, actual in checks
        if expected != actual
    ]
