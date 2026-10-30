"""Card state machine: exactly docs/state-machines.md §3 (INACTIVE = frozen, reversible)."""

from cards.exceptions import InvalidTransition
from cards.models import Card

Status = Card.Status

# Issuing a card (none -> ACTIVE) is a creation, not a transition: see services.issue_card.
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.ACTIVE: frozenset({Status.INACTIVE, Status.CLOSED}),
    Status.INACTIVE: frozenset({Status.ACTIVE, Status.CLOSED}),
    Status.CLOSED: frozenset(),
}


def assert_transition(current: Status, target: Status) -> None:
    """Raise InvalidTransition when target is not in TRANSITIONS[current]."""
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(from_status=current.value, to_status=target.value)
