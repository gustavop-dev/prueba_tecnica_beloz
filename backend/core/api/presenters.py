"""Response bodies (docs/api.md). Amounts are integer cents with `_minor` plus the currency."""

from accounts.services import AccountDTO
from cards.services import CardDTO
from collateral.services import DepositDTO
from core import flows
from ledger.services import EntryDTO

CURRENCY = "USD"


def _iso(value):
    return value.isoformat() if value is not None else None


def deposit(dto: DepositDTO) -> dict:
    return {
        "id": str(dto.id),
        "status": dto.status,
        "amount_minor": dto.amount_minor,
        "applied_minor": dto.applied_minor,
        "refunded_minor": dto.refunded_minor,
        "currency": CURRENCY,
    }


def card(dto: CardDTO) -> dict:
    return {"id": str(dto.id), "last4": dto.last4, "status": dto.status}


def card_status(dto: CardDTO) -> dict:
    return {"account_id": str(dto.credit_account_id), "card": card(dto)}


def balances(dto: AccountDTO) -> dict:
    return {
        "credit_limit_minor": dto.credit_limit_minor,
        "outstanding_minor": dto.outstanding_minor,
        "available_credit_minor": dto.available_credit_minor,
        "currency": CURRENCY,
    }


def opening(dto: DepositDTO) -> dict:
    return {"account_id": str(dto.reserved_account_id), "deposit": deposit(dto)}


def provider_report(result: flows.ProviderReportResult) -> dict:
    return {
        "deposit": deposit(result.deposit),
        "account_id": str(result.account.id) if result.account else None,
    }


def authorization(result: flows.AuthorizationResult) -> dict:
    return {
        "authorization_id": str(result.authorization_id),
        "status": "APPROVED",
        "card_id": str(result.card.id),
        "account_id": str(result.account.id),
        "amount_minor": result.amount_minor,
        **balances(result.account),
    }


def repayment(result: flows.RepaymentResult) -> dict:
    return {
        "repayment_id": str(result.repayment_id),
        "account_id": str(result.account.id),
        "amount_minor": result.amount_minor,
        **balances(result.account),
    }


def closure(result: flows.ClosureResult) -> dict:
    return {
        "account_id": str(result.account.id),
        "status": result.account.status,
        "debt_at_closure_minor": result.debt_at_closure_minor,
        "refund_minor": result.refund_minor,
        "currency": CURRENCY,
        "deposit": deposit(result.deposit),
        "card": card(result.card),
        "closed_at": _iso(result.account.closed_at),
    }


def account_view(view: flows.AccountView) -> dict:
    return {
        "id": str(view.account.id),
        "status": view.account.status,
        "collateral_ratio_bps": view.account.collateral_ratio_bps,
        **balances(view.account),
        "deposit": deposit(view.deposit),
        "card": card(view.card),
        "created_at": _iso(view.account.created_at),
        "closed_at": _iso(view.account.closed_at),
    }


def ledger_entries(account_id, entries: list[EntryDTO]) -> dict:
    return {
        "account_id": str(account_id),
        "currency": CURRENCY,
        "entries": [
            {
                "id": str(entry.id),
                "kind": entry.kind,
                "reference_id": str(entry.reference_id) if entry.reference_id else None,
                "created_at": _iso(entry.created_at),
                "lines": [
                    {"ledger_account": line.ledger_account_code, "amount_minor": line.amount_minor}
                    for line in entry.lines
                ],
            }
            for entry in entries
        ],
    }
