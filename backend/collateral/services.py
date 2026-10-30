"""Public API of the `collateral` app: the only module other code may import (MOD-1).

Mutating services require a transaction opened by the caller (MOD-4) and validate and write under
the deposit row lock (CON-2, CON-5). They return frozen DTOs, never model instances (DEP-3).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from collateral.exceptions import (
    CollateralError,
    DepositNotFound,
    DepositNotPending,
    InvalidTransition,
)
from collateral.models import CollateralDeposit, DepositStatus
from collateral.states import TRANSITIONS, assert_transition

__all__ = [
    "TRANSITIONS",
    "CollateralError",
    "DepositDTO",
    "DepositNotFound",
    "DepositNotPending",
    "DepositStatus",
    "InvalidTransition",
    "confirm_deposit",
    "create_pending_deposit",
    "fail_deposit",
    "get_deposit",
    "get_deposit_for_owner",
    "lock_deposit",
    "settle_on_closure",
]


@dataclass(frozen=True, slots=True)
class DepositDTO:
    id: UUID
    owner_id: UUID
    reserved_account_id: UUID
    amount_minor: int
    status: str
    applied_minor: int | None
    refunded_minor: int | None
    created_at: datetime
    confirmed_at: datetime | None
    failed_at: datetime | None
    settled_at: datetime | None


def _to_dto(deposit: CollateralDeposit) -> DepositDTO:
    return DepositDTO(
        id=deposit.id,
        owner_id=deposit.owner_id,
        reserved_account_id=deposit.reserved_account_id,
        amount_minor=deposit.amount_minor,
        status=str(deposit.status),
        applied_minor=deposit.applied_minor,
        refunded_minor=deposit.refunded_minor,
        created_at=deposit.created_at,
        confirmed_at=deposit.confirmed_at,
        failed_at=deposit.failed_at,
        settled_at=deposit.settled_at,
    )


def _require_atomic(function_name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{function_name} must run inside transaction.atomic")


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _lock_row(deposit_id: UUID) -> CollateralDeposit:
    try:
        return CollateralDeposit.objects.select_for_update().get(id=deposit_id)
    except CollateralDeposit.DoesNotExist:
        raise DepositNotFound(deposit_id) from None


def create_pending_deposit(*, owner_id: UUID, amount_minor: int) -> DepositDTO:
    """Create the opening deposit as PENDING and reserve the future account id (BR-DEP-1)."""
    _require_atomic("create_pending_deposit")
    if not isinstance(owner_id, UUID):
        raise ValueError(f"owner_id must be a UUID, got {type(owner_id).__name__}")
    if _require_int(amount_minor, "amount_minor") <= 0:
        raise ValueError("amount_minor must be > 0")
    deposit = CollateralDeposit.objects.create(
        owner_id=owner_id,
        reserved_account_id=uuid.uuid4(),
        amount_minor=amount_minor,
        status=DepositStatus.PENDING,
    )
    return _to_dto(deposit)


def lock_deposit(deposit_id: UUID) -> DepositDTO:
    """Read the deposit with SELECT ... FOR UPDATE (CON-2, CON-4); DepositNotFound if missing."""
    _require_atomic("lock_deposit")
    return _to_dto(_lock_row(deposit_id))


def get_deposit(deposit_id: UUID) -> DepositDTO:
    """Read the deposit without locking it; DepositNotFound if missing."""
    try:
        return _to_dto(CollateralDeposit.objects.get(id=deposit_id))
    except CollateralDeposit.DoesNotExist:
        raise DepositNotFound(deposit_id) from None


def get_deposit_for_owner(deposit_id: UUID, owner_id: UUID) -> DepositDTO:
    """Read the deposit only if it belongs to owner_id; DepositNotFound otherwise."""
    try:
        return _to_dto(CollateralDeposit.objects.get(id=deposit_id, owner_id=owner_id))
    except CollateralDeposit.DoesNotExist:
        raise DepositNotFound(deposit_id) from None


def _resolve_pending(deposit_id: UUID, target: DepositStatus) -> DepositDTO:
    deposit = _lock_row(deposit_id)
    if deposit.status != DepositStatus.PENDING:
        raise DepositNotPending(from_status=str(deposit.status), to_status=str(target))
    assert_transition(DepositStatus(deposit.status), target)
    now = timezone.now()
    deposit.status = target
    if target == DepositStatus.CONFIRMED:
        deposit.confirmed_at = now
    else:
        deposit.failed_at = now
    deposit.save(update_fields=["status", "confirmed_at", "failed_at"])
    return _to_dto(deposit)


def confirm_deposit(deposit_id: UUID) -> DepositDTO:
    """PENDING -> CONFIRMED (BR-DEP-3, BR-DEP-6); DepositNotPending from any other status."""
    _require_atomic("confirm_deposit")
    return _resolve_pending(deposit_id, DepositStatus.CONFIRMED)


def fail_deposit(deposit_id: UUID) -> DepositDTO:
    """PENDING -> FAILED (BR-DEP-4, BR-DEP-6); DepositNotPending from any other status."""
    _require_atomic("fail_deposit")
    return _resolve_pending(deposit_id, DepositStatus.FAILED)


def _settlement_status(debt_minor: int, amount_minor: int) -> DepositStatus:
    """The closure guards of state-machines.md §1, given 0 <= debt <= amount."""
    if debt_minor == 0:
        return DepositStatus.RELEASED
    if debt_minor == amount_minor:
        return DepositStatus.APPLIED
    return DepositStatus.PARTIALLY_APPLIED


def settle_on_closure(deposit_id: UUID, debt_at_closure_minor: int) -> DepositDTO:
    """CONFIRMED -> RELEASED | PARTIALLY_APPLIED | APPLIED at account closure (BR-CLO-1..4).

    applied = debt, refunded = amount - debt. ValueError if debt < 0 or debt > amount: closure
    relies on debt <= deposit (collateral_ratio <= 1), so a larger debt is a programming error.
    """
    _require_atomic("settle_on_closure")
    if _require_int(debt_at_closure_minor, "debt_at_closure_minor") < 0:
        raise ValueError("debt_at_closure_minor must be >= 0")
    deposit = _lock_row(deposit_id)
    if debt_at_closure_minor > deposit.amount_minor:
        raise ValueError("debt_at_closure_minor must not exceed the deposit amount")
    target = _settlement_status(debt_at_closure_minor, deposit.amount_minor)
    assert_transition(DepositStatus(deposit.status), target)
    deposit.status = target
    deposit.applied_minor = debt_at_closure_minor
    deposit.refunded_minor = deposit.amount_minor - debt_at_closure_minor
    deposit.settled_at = timezone.now()
    deposit.save(update_fields=["status", "applied_minor", "refunded_minor", "settled_at"])
    return _to_dto(deposit)
