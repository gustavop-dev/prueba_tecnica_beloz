"""Credit account state machine (docs/state-machines.md §2)."""

from accounts.exceptions import InvalidTransition
from accounts.models import CreditAccount

Status = CreditAccount.Status

TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.ACTIVE: frozenset({Status.CLOSED}),
    Status.CLOSED: frozenset(),
}


def assert_transition(current: Status, target: Status) -> None:
    """Raise InvalidTransition when target is not in TRANSITIONS[current]."""
    if Status(target) not in TRANSITIONS[Status(current)]:
        raise InvalidTransition(from_status=str(current), to_status=str(target))
