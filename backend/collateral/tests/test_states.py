"""The deposit transition table and `assert_transition` (state-machines.md §1, INV-5, TEST-SM-1)."""

import itertools

import pytest

from collateral.models import DepositStatus
from collateral.services import InvalidTransition
from collateral.states import TRANSITIONS, assert_transition

S = DepositStatus

ALL_PAIRS = list(itertools.product(S, S))
ALLOWED_PAIRS = [(src, dst) for src, dst in ALL_PAIRS if dst in TRANSITIONS[src]]
FORBIDDEN_PAIRS = [(src, dst) for src, dst in ALL_PAIRS if dst not in TRANSITIONS[src]]


def test_transition_table_is_exactly_the_documented_state_machine():
    """Fails if a deposit transition is added to or removed from state-machines.md §1 (INV-5)."""
    assert {
        S.PENDING: frozenset({S.CONFIRMED, S.FAILED}),
        S.CONFIRMED: frozenset({S.RELEASED, S.PARTIALLY_APPLIED, S.APPLIED}),
        S.FAILED: frozenset(),
        S.RELEASED: frozenset(),
        S.PARTIALLY_APPLIED: frozenset(),
        S.APPLIED: frozenset(),
    } == TRANSITIONS


@pytest.mark.parametrize(("current", "target"), ALLOWED_PAIRS)
def test_assert_transition_accepts_every_documented_transition(current, target):
    """Fails if a transition of state-machines.md §1 is rejected by the guard (INV-5)."""
    assert assert_transition(current, target) is None


@pytest.mark.parametrize(("current", "target"), FORBIDDEN_PAIRS)
def test_assert_transition_rejects_every_undocumented_transition(current, target):
    """Fails if any (from, to) pair outside state-machines.md §1 is accepted (TEST-SM-1, INV-5)."""
    with pytest.raises(InvalidTransition) as exc_info:
        assert_transition(current, target)

    assert (exc_info.value.from_status, exc_info.value.to_status) == (current.value, target.value)
