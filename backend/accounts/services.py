"""Public API of the `accounts` app: the only module other code may import (MOD-1).

Every mutating function requires a transaction opened by the caller (MOD-4, CON-1), locks the
credit account row with `SELECT ... FOR UPDATE` and validates and writes inside that lock
(CON-2, CON-3, CACHE-4). The credit limit arrives already computed (BR-LIM-3 lives in the core).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from accounts.exceptions import (
    AccountClosed,
    AccountNotFound,
    AccountsError,
    InsufficientCredit,
    InvalidTransition,
    PaymentExceedsDebt,
)
from accounts.models import CreditAccount
from accounts.states import assert_transition

__all__ = [
    "AccountClosed",
    "AccountDTO",
    "AccountNotFound",
    "AccountsError",
    "ClosureDTO",
    "InsufficientCredit",
    "InvalidTransition",
    "PaymentExceedsDebt",
    "close_account",
    "decrease_outstanding",
    "get_account",
    "get_account_for_owner",
    "increase_outstanding",
    "iter_accounts",
    "lock_account",
    "open_account",
]

_Status = CreditAccount.Status


@dataclass(frozen=True, slots=True)
class AccountDTO:
    id: UUID
    owner_id: UUID
    deposit_id: UUID
    status: str
    collateral_ratio_bps: int
    credit_limit_minor: int
    outstanding_minor: int
    created_at: datetime
    closed_at: datetime | None

    @property
    def available_credit_minor(self) -> int:
        """BR-LIM-5: `available_credit = credit_limit - outstanding_debt`."""
        return self.credit_limit_minor - self.outstanding_minor


@dataclass(frozen=True, slots=True)
class ClosureDTO:
    account: AccountDTO
    debt_at_closure_minor: int


def open_account(
    *,
    account_id: UUID,
    owner_id: UUID,
    deposit_id: UUID,
    credit_limit_minor: int,
    collateral_ratio_bps: int,
) -> AccountDTO:
    """Create the ACTIVE account for a confirmed deposit, with no debt (BR-DEP-3)."""
    _require_atomic("open_account")
    _require_uuid("account_id", account_id)
    _require_uuid("owner_id", owner_id)
    _require_uuid("deposit_id", deposit_id)
    _require_int("credit_limit_minor", credit_limit_minor, minimum=0)
    _require_int("collateral_ratio_bps", collateral_ratio_bps, minimum=1)
    account = CreditAccount.objects.create(
        id=account_id,
        owner_id=owner_id,
        deposit_id=deposit_id,
        status=_Status.ACTIVE,
        collateral_ratio_bps=collateral_ratio_bps,
        credit_limit_minor=credit_limit_minor,
        outstanding_minor=0,
    )
    return _to_dto(account)


def lock_account(account_id: UUID) -> AccountDTO:
    """Read the account with `SELECT ... FOR UPDATE` (CON-2). Raise AccountNotFound."""
    _require_atomic("lock_account")
    return _to_dto(_locked(account_id))


def get_account(account_id: UUID) -> AccountDTO:
    """Read the account without locking it. Raise AccountNotFound."""
    try:
        return _to_dto(CreditAccount.objects.get(pk=account_id))
    except CreditAccount.DoesNotExist:
        raise AccountNotFound(str(account_id)) from None


def get_account_for_owner(account_id: UUID, owner_id: UUID) -> AccountDTO:
    """Read the account scoped to its owner (SEC-IDOR-1).

    A missing account and another owner's account raise the same AccountNotFound (SEC-IDOR-2).
    """
    try:
        return _to_dto(CreditAccount.objects.get(pk=account_id, owner_id=owner_id))
    except CreditAccount.DoesNotExist:
        raise AccountNotFound(str(account_id)) from None


def increase_outstanding(account_id: UUID, amount_minor: int) -> AccountDTO:
    """Add a purchase to the debt cache (BR-PUR-2, CACHE-2).

    Raise AccountClosed on a CLOSED account and InsufficientCredit when the amount exceeds the
    available credit. There are no partial approvals.
    """
    _require_atomic("increase_outstanding")
    _require_int("amount_minor", amount_minor, minimum=1)
    account = _locked(account_id)
    _require_active(account)
    if amount_minor > account.credit_limit_minor - account.outstanding_minor:
        raise InsufficientCredit(str(account_id))
    account.outstanding_minor += amount_minor
    account.save(update_fields=["outstanding_minor"])
    return _to_dto(account)


def decrease_outstanding(account_id: UUID, amount_minor: int) -> AccountDTO:
    """Apply a payment to the debt cache (BR-PAY-1).

    Raise AccountClosed on a CLOSED account and PaymentExceedsDebt when the amount exceeds the
    outstanding debt, which includes any payment while the debt is 0 (BR-PAY-2/3).
    """
    _require_atomic("decrease_outstanding")
    _require_int("amount_minor", amount_minor, minimum=1)
    account = _locked(account_id)
    _require_active(account)
    if amount_minor > account.outstanding_minor:
        raise PaymentExceedsDebt(str(account_id))
    account.outstanding_minor -= amount_minor
    account.save(update_fields=["outstanding_minor"])
    return _to_dto(account)


def close_account(account_id: UUID) -> ClosureDTO:
    """Close the account: capture the debt, leave it at 0 and mark it CLOSED (BR-CLO-5).

    Raise AccountClosed when the account is already CLOSED (BR-CLO-6).
    """
    _require_atomic("close_account")
    account = _locked(account_id)
    _require_active(account)
    assert_transition(account.status, _Status.CLOSED)
    debt_at_closure_minor = account.outstanding_minor
    account.outstanding_minor = 0
    account.status = _Status.CLOSED
    account.closed_at = timezone.now()
    account.save(update_fields=["outstanding_minor", "status", "closed_at"])
    return ClosureDTO(account=_to_dto(account), debt_at_closure_minor=debt_at_closure_minor)


def iter_accounts() -> Iterator[AccountDTO]:
    """Yield every account, in a stable order, for reconciliation."""
    for account in CreditAccount.objects.order_by("created_at", "id").iterator():
        yield _to_dto(account)


def _locked(account_id: UUID) -> CreditAccount:
    try:
        return CreditAccount.objects.select_for_update().get(pk=account_id)
    except CreditAccount.DoesNotExist:
        raise AccountNotFound(str(account_id)) from None


def _require_active(account: CreditAccount) -> None:
    if account.status == _Status.CLOSED:
        raise AccountClosed(str(account.id))


def _require_atomic(function_name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{function_name} must run inside transaction.atomic")


def _require_int(name: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")


def _require_uuid(name: str, value: object) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID, got {type(value).__name__}")


def _to_dto(account: CreditAccount) -> AccountDTO:
    return AccountDTO(
        id=account.id,
        owner_id=account.owner_id,
        deposit_id=account.deposit_id,
        status=str(account.status),
        collateral_ratio_bps=account.collateral_ratio_bps,
        credit_limit_minor=account.credit_limit_minor,
        outstanding_minor=account.outstanding_minor,
        created_at=account.created_at,
        closed_at=account.closed_at,
    )
