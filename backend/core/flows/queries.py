"""Read-only views of an account for its owner (GET endpoints)."""

from dataclasses import dataclass
from uuid import UUID

from accounts import services as accounts
from cards import services as cards
from collateral import services as collateral
from core.db import read_only_snapshot
from ledger import services as ledger


@dataclass(frozen=True, slots=True)
class AccountView:
    account: accounts.AccountDTO
    deposit: collateral.DepositDTO
    card: cards.CardDTO


def get_account_view(*, owner_id: UUID, account_id: UUID) -> AccountView:
    """Account, deposit and card read from one snapshot (SEC-IDOR-1/2 via the owner lookup)."""
    with read_only_snapshot():
        account = accounts.get_account_for_owner(account_id, owner_id)
        return AccountView(
            account=account,
            deposit=collateral.get_deposit(account.deposit_id),
            card=cards.get_card_for_account(account.id),
        )


def get_ledger_entries(*, owner_id: UUID, account_id: UUID) -> list[ledger.EntryDTO]:
    """The account's journal entries, oldest first."""
    with read_only_snapshot():
        accounts.get_account_for_owner(account_id, owner_id)
        return ledger.list_entries(account_id)
