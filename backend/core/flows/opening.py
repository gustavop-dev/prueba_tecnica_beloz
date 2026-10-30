"""Account opening: the customer's request and the provider's report (BR-ACC-*, BR-DEP-*)."""

from dataclasses import dataclass
from uuid import UUID

from accounts import services as accounts
from cards import services as cards
from collateral import services as collateral
from core.auth.principals import Role
from core.db import money_transaction
from core.flows import audit_trail as trail
from core.money import COLLATERAL_RATIO_BPS, credit_limit_minor, parse_amount_minor
from ledger import services as ledger


@dataclass(frozen=True, slots=True)
class ProviderReportResult:
    deposit: collateral.DepositDTO
    account: accounts.AccountDTO | None
    card: cards.CardDTO | None


def request_account_opening(*, owner_id: UUID, deposit_amount_minor: int) -> collateral.DepositDTO:
    """Create the `PENDING` deposit and reserve the account id (BR-DEP-1, SEC-ROLE-3).

    No account exists until the provider confirms the deposit (BR-ACC-1, BR-ACC-4).
    """
    amount = parse_amount_minor(deposit_amount_minor)
    with money_transaction():
        deposit = collateral.create_pending_deposit(owner_id=owner_id, amount_minor=amount)
        trail.record(
            actor_id=owner_id,
            actor_role=Role.CUSTOMER,
            action="deposit.requested",
            entity_type=trail.COLLATERAL_DEPOSIT,
            entity_id=deposit.id,
            before=None,
            after=trail.deposit_state(deposit),
        )
        return deposit


def apply_provider_report(
    *, provider_id: UUID, deposit_id: UUID, succeeded: bool
) -> ProviderReportResult:
    """Apply the provider's deposit outcome in one transaction.

    Success confirms the deposit, opens the account with the reserved id, sets the limit, records
    `Dr cash / Cr customer_collateral` and issues the card as ACTIVE (BR-DEP-3). Failure only marks
    the deposit FAILED (BR-DEP-4). Only a PENDING deposit can be reported (BR-DEP-6).
    """
    actor = {"actor_id": provider_id, "actor_role": Role.PAYMENT_PROVIDER}
    with money_transaction():
        # CON-5: the account does not exist yet, so the PENDING deposit row is the lock.
        pending = collateral.lock_deposit(deposit_id)
        if not succeeded:
            failed = collateral.fail_deposit(deposit_id)
            trail.record(
                **actor,
                action="deposit.failed",
                entity_type=trail.COLLATERAL_DEPOSIT,
                entity_id=deposit_id,
                before=trail.deposit_state(pending),
                after=trail.deposit_state(failed),
            )
            return ProviderReportResult(deposit=failed, account=None, card=None)

        deposit = collateral.confirm_deposit(deposit_id)
        account = accounts.open_account(
            account_id=deposit.reserved_account_id,
            owner_id=deposit.owner_id,
            deposit_id=deposit.id,
            credit_limit_minor=credit_limit_minor(deposit.amount_minor, COLLATERAL_RATIO_BPS),
            collateral_ratio_bps=COLLATERAL_RATIO_BPS,
        )
        ledger.provision_customer_accounts(account.id)
        ledger.post_deposit_confirmed(account.id, deposit.amount_minor, reference_id=deposit.id)
        card = cards.issue_card(account.id)

        trail.record(
            **actor,
            action="deposit.confirmed",
            entity_type=trail.COLLATERAL_DEPOSIT,
            entity_id=deposit.id,
            before=trail.deposit_state(pending),
            after=trail.deposit_state(deposit),
        )
        trail.record(
            **actor,
            action="credit_account.opened",
            entity_type=trail.CREDIT_ACCOUNT,
            entity_id=account.id,
            before=None,
            after=trail.account_state(account),
        )
        trail.record(
            **actor,
            action="card.issued",
            entity_type=trail.CARD,
            entity_id=card.id,
            before=None,
            after=trail.card_state(card),
        )
        return ProviderReportResult(deposit=deposit, account=account, card=card)
