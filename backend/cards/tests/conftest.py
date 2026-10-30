from uuid import uuid4

import pytest
from django.db import transaction

from cards.models import Card
from cards.services import issue_card
from cards.tests.helpers import FIXED_CLOSED_AT


@pytest.fixture
def make_card():
    """Factory: issue a card through the service, then force it into the requested status."""

    def _make(status: Card.Status = Card.Status.ACTIVE) -> Card:
        dto = issue_card(uuid4())
        closed_at = FIXED_CLOSED_AT if status == Card.Status.CLOSED else None
        Card.objects.filter(pk=dto.id).update(status=status, closed_at=closed_at)
        return Card.objects.get(pk=dto.id)

    return _make


@pytest.fixture
def committed_card() -> Card:
    """An ACTIVE card committed to the database, for `transaction=True` tests."""
    with transaction.atomic():
        dto = issue_card(uuid4())
    return Card.objects.get(pk=dto.id)
