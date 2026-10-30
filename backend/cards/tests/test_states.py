"""Card state machine (state-machines.md §3), exercised through the public services."""

import itertools

import pytest

from cards.models import Card
from cards.services import InvalidTransition, close_card, freeze_card, unfreeze_card
from cards.states import TRANSITIONS

Status = Card.Status

# The only service that moves a card into each target status.
SERVICE_FOR_TARGET = {
    Status.ACTIVE: unfreeze_card,
    Status.INACTIVE: freeze_card,
    Status.CLOSED: close_card,
}

ALLOWED = sorted((src, dst) for src, targets in TRANSITIONS.items() for dst in targets)
FORBIDDEN = sorted(set(itertools.product(Status, Status)) - set(ALLOWED))


def _pair_id(pair: tuple[Status, Status]) -> str:
    return f"{pair[0].value}->{pair[1].value}"


def test_transition_table_matches_state_machines_spec():
    """Fails if the card transition table drifts from docs/state-machines.md §3 (INV-5)."""
    spec_table = {
        Status.ACTIVE: frozenset({Status.INACTIVE, Status.CLOSED}),
        Status.INACTIVE: frozenset({Status.ACTIVE, Status.CLOSED}),
        Status.CLOSED: frozenset(),
    }

    assert spec_table == TRANSITIONS


def test_every_status_has_a_service_that_targets_it():
    """Fails if a status is added without a service, so TEST-SM-1 would silently skip it."""
    assert set(SERVICE_FOR_TARGET) == set(Status)


@pytest.mark.django_db
@pytest.mark.parametrize("pair", FORBIDDEN, ids=_pair_id)
def test_forbidden_transition_is_rejected_and_leaves_the_card_unchanged(make_card, pair):
    """Fails if a transition outside state-machines.md §3 is applied (TEST-SM-1, INV-5)."""
    from_status, to_status = pair
    card = make_card(from_status)

    with pytest.raises(InvalidTransition) as exc_info:
        SERVICE_FOR_TARGET[to_status](card.id)

    stored = Card.objects.get(pk=card.id)
    assert (exc_info.value.from_status, exc_info.value.to_status) == (
        from_status.value,
        to_status.value,
    )
    assert (stored.status, stored.closed_at) == (card.status, card.closed_at)


@pytest.mark.django_db
@pytest.mark.parametrize("pair", ALLOWED, ids=_pair_id)
def test_allowed_transition_is_persisted(make_card, pair):
    """Fails if an allowed card transition (state-machines.md §3) is rejected or not stored,
    or if `closed_at` is not set exactly when the card becomes CLOSED."""
    from_status, to_status = pair
    card = make_card(from_status)

    dto = SERVICE_FOR_TARGET[to_status](card.id)

    stored = Card.objects.get(pk=card.id)
    assert (dto.status, stored.status) == (to_status.value, to_status.value)
    assert dto.closed_at == stored.closed_at
    assert (stored.closed_at is not None) is (to_status == Status.CLOSED)
