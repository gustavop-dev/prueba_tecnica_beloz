"""Money operations on an open account: purchase, repayment and closure.

Each one runs in a single transaction, locks the credit account row first and takes the other
locks in the fixed order `CreditAccount → CollateralDeposit → Card` (CON-1..4). Ownership is checked
before the transaction: a resource of another customer is indistinguishable from a missing one
(SEC-IDOR-1/2).
"""

import uuid
from dataclasses import dataclass
from uuid import UUID

from accounts import services as accounts
from cards import services as cards
from collateral import services as collateral
from core.auth.principals import Role
from core.db import money_transaction
from core.flows import audit_trail as trail
from core.money import parse_amount_minor
from ledger import services as ledger

ACTIVE = "ACTIVE"


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    authorization_id: UUID
    amount_minor: int
    account: accounts.AccountDTO
    card: cards.CardDTO


@dataclass(frozen=True, slots=True)
class RepaymentResult:
    repayment_id: UUID
    amount_minor: int
    account: accounts.AccountDTO


@dataclass(frozen=True, slots=True)
class ClosureResult:
    closure_id: UUID
    debt_at_closure_minor: int
    refund_minor: int
    account: accounts.AccountDTO
    deposit: collateral.DepositDTO
    card: cards.CardDTO


def authorize_purchase(*, owner_id: UUID, card_id: UUID, amount_minor: int) -> AuthorizationResult:
    """Authorize a purchase and record it as debt immediately (BR-PUR-1, BR-PUR-2)."""
    amount = parse_amount_minor(amount_minor)
    account_id = cards.get_card(card_id).credit_account_id
    accounts.get_account_for_owner(account_id, owner_id)
    with money_transaction():
        before = accounts.lock_account(account_id)
        _require_open(before)
        card = cards.ensure_can_authorize(card_id)
        account = accounts.increase_outstanding(account_id, amount)
        authorization_id = uuid.uuid4()
        ledger.post_purchase(account_id, amount, reference_id=authorization_id)
        _record_account_change(owner_id, "credit_account.purchase_authorized", before, account)
    return AuthorizationResult(
        authorization_id=authorization_id, amount_minor=amount, account=account, card=card
    )


def repay(*, owner_id: UUID, account_id: UUID, amount_minor: int) -> RepaymentResult:
    """Reduce the outstanding debt; never above it, so there is no credit balance (BR-PAY-1..3)."""
    amount = parse_amount_minor(amount_minor)
    accounts.get_account_for_owner(account_id, owner_id)
    with money_transaction():
        before = accounts.lock_account(account_id)
        _require_open(before)
        account = accounts.decrease_outstanding(account_id, amount)
        repayment_id = uuid.uuid4()
        ledger.post_repayment(account_id, amount, reference_id=repayment_id)
        _record_account_change(owner_id, "credit_account.repayment_applied", before, account)
    return RepaymentResult(repayment_id=repayment_id, amount_minor=amount, account=account)


def close(*, owner_id: UUID, account_id: UUID) -> ClosureResult:
    """Close the account in one atomic step (BR-CLO-1..6).

    The collateral pays off the outstanding debt and the remainder is refunded, so the debt ends at
    zero; the deposit is settled, the card closed and the account closed together.
    """
    accounts.get_account_for_owner(account_id, owner_id)
    with money_transaction():
        account = accounts.lock_account(account_id)
        _require_open(account)
        deposit = collateral.lock_deposit(account.deposit_id)
        card_id = cards.get_card_for_account(account_id).id
        card_before = cards.lock_card(card_id)

        closure = accounts.close_account(account_id)
        debt = closure.debt_at_closure_minor
        refund = deposit.amount_minor - debt
        settled = collateral.settle_on_closure(deposit.id, debt)
        closure_id = uuid.uuid4()
        ledger.post_closure(
            account_id, debt_minor=debt, refund_minor=refund, reference_id=closure_id
        )
        card = cards.close_card(card_id)

        _record_account_change(owner_id, "credit_account.closed", account, closure.account)
        trail.record(
            actor_id=owner_id,
            actor_role=Role.CUSTOMER,
            action="deposit.settled",
            entity_type=trail.COLLATERAL_DEPOSIT,
            entity_id=deposit.id,
            before=trail.deposit_state(deposit),
            after=trail.deposit_state(settled),
        )
        trail.record(
            actor_id=owner_id,
            actor_role=Role.CUSTOMER,
            action="card.closed",
            entity_type=trail.CARD,
            entity_id=card_id,
            before=trail.card_state(card_before),
            after=trail.card_state(card),
        )
    return ClosureResult(
        closure_id=closure_id,
        debt_at_closure_minor=debt,
        refund_minor=refund,
        account=closure.account,
        deposit=settled,
        card=card,
    )


def freeze_card(*, owner_id: UUID, card_id: UUID) -> cards.CardDTO:
    """ACTIVE → INACTIVE: purchases are rejected until the card is unfrozen (state-machines.md §3).

    Repayments and closure stay available: they act on the account, not the card.
    """
    return _change_card_status(owner_id, card_id, cards.freeze_card, "card.frozen")


def unfreeze_card(*, owner_id: UUID, card_id: UUID) -> cards.CardDTO:
    """INACTIVE → ACTIVE: the card can authorize purchases again (state-machines.md §3)."""
    return _change_card_status(owner_id, card_id, cards.unfreeze_card, "card.unfrozen")


def _change_card_status(owner_id: UUID, card_id: UUID, transition, action: str) -> cards.CardDTO:
    account_id = cards.get_card(card_id).credit_account_id
    accounts.get_account_for_owner(account_id, owner_id)
    with money_transaction():
        # Same lock order as purchases (CON-4): account first, then card.
        _require_open(accounts.lock_account(account_id))
        before = cards.lock_card(card_id)
        after = transition(card_id)
        trail.record(
            actor_id=owner_id,
            actor_role=Role.CUSTOMER,
            action=action,
            entity_type=trail.CARD,
            entity_id=card_id,
            before=trail.card_state(before),
            after=trail.card_state(after),
        )
    return after


def _record_account_change(
    owner_id: UUID, action: str, before: accounts.AccountDTO, after: accounts.AccountDTO
) -> None:
    trail.record(
        actor_id=owner_id,
        actor_role=Role.CUSTOMER,
        action=action,
        entity_type=trail.CREDIT_ACCOUNT,
        entity_id=after.id,
        before=trail.account_state(before),
        after=trail.account_state(after),
    )


def _require_open(account: accounts.AccountDTO) -> None:
    """Reject any operation on a closed account before touching other rows (BR-CLO-6)."""
    if account.status != ACTIVE:
        raise accounts.AccountClosed(f"Credit account {account.id} is closed.")
