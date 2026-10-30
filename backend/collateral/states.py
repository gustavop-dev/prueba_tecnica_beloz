"""Collateral deposit state machine: exactly the table of docs/state-machines.md §1."""

from collateral.exceptions import InvalidTransition
from collateral.models import DepositStatus

# Creation (none -> PENDING) is not a transition between stored statuses.
TRANSITIONS: dict[DepositStatus, frozenset[DepositStatus]] = {
    DepositStatus.PENDING: frozenset({DepositStatus.CONFIRMED, DepositStatus.FAILED}),
    DepositStatus.CONFIRMED: frozenset(
        {DepositStatus.RELEASED, DepositStatus.PARTIALLY_APPLIED, DepositStatus.APPLIED}
    ),
    DepositStatus.FAILED: frozenset(),
    DepositStatus.RELEASED: frozenset(),
    DepositStatus.PARTIALLY_APPLIED: frozenset(),
    DepositStatus.APPLIED: frozenset(),
}


def assert_transition(current: DepositStatus, target: DepositStatus) -> None:
    """Raise InvalidTransition when target is not in TRANSITIONS[current]."""
    if target not in TRANSITIONS[DepositStatus(current)]:
        raise InvalidTransition(from_status=str(current), to_status=str(target))
