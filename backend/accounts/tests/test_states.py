import pytest

from accounts.services import InvalidTransition
from accounts.states import TRANSITIONS, Status, assert_transition

ALLOWED = [(current, target) for current in Status for target in TRANSITIONS[current]]
FORBIDDEN = [
    (current, target)
    for current in Status
    for target in Status
    if target not in TRANSITIONS[current]
]


def test_transition_table_matches_the_credit_account_state_machine():
    """Fails if a transition is added or removed from the table in state-machines.md §2 (INV-5),
    for example reopening a CLOSED account."""
    expected = {
        Status.ACTIVE: frozenset({Status.CLOSED}),
        Status.CLOSED: frozenset(),
    }

    assert expected == TRANSITIONS


@pytest.mark.parametrize(("current", "target"), FORBIDDEN)
def test_forbidden_transition_raises_invalid_transition(current, target):
    """Fails if a transition outside the state machine is accepted (TEST-SM-1, INV-5)."""
    with pytest.raises(InvalidTransition) as excinfo:
        assert_transition(current, target)

    assert (excinfo.value.from_status, excinfo.value.to_status) == (current.value, target.value)


@pytest.mark.parametrize(("current", "target"), ALLOWED)
def test_allowed_transition_is_accepted(current, target):
    """Fails if a transition listed in state-machines.md §2 is rejected (TEST-SM-1)."""
    assert assert_transition(current, target) is None
