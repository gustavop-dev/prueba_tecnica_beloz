"""Public API of the `cards` app: the only module other code may import (MOD-1).

Services return `CardDTO`, never model instances (DEP-3), and the DTO never carries the card
token (SEC-CARD-3). Mutating and locking services require a transaction opened by the caller and
validate and write under the card row lock (CON-3). The card is the last lock in the fixed order
`CreditAccount` -> `CollateralDeposit` -> `Card` (CON-4); the caller is responsible for it.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from cards.exceptions import CardNotActive, CardNotFound, CardsError, InvalidTransition
from cards.models import PAN_LIKE_REGEX, Card
from cards.states import assert_transition

__all__ = [
    "CardDTO",
    "CardNotActive",
    "CardNotFound",
    "CardsError",
    "InvalidTransition",
    "close_card",
    "ensure_can_authorize",
    "freeze_card",
    "get_card",
    "get_card_for_account",
    "issue_card",
    "lock_card",
    "unfreeze_card",
]

_TOKEN_PREFIX = "card_"  # noqa: S105 - a fixed public prefix, not a secret.
_TOKEN_BYTES = 24
_PAN_LIKE = re.compile(PAN_LIKE_REGEX)


@dataclass(frozen=True, slots=True)
class CardDTO:
    """Read-only view of a card. Never includes the token (SEC-CARD-3)."""

    id: UUID
    credit_account_id: UUID
    last4: str
    status: str
    created_at: datetime
    closed_at: datetime | None


def issue_card(credit_account_id: UUID) -> CardDTO:
    """Issue the single card of a new credit account, in status ACTIVE (BR-ACC-3, BR-DEP-3)."""
    _require_atomic("issue_card")
    _require_uuid("credit_account_id", credit_account_id)
    if Card.objects.filter(credit_account_id=credit_account_id).exists():
        # Programming error: the opening flow issues exactly one card per account. The unique
        # constraint `cards_card_one_per_account` is the last line of defense.
        raise ValueError(f"credit account {credit_account_id} already has a card")
    card = Card.objects.create(
        credit_account_id=credit_account_id,
        token=_generate_token(),
        last4=_generate_last4(),
        status=Card.Status.ACTIVE,
    )
    return _to_dto(card)


def get_card(card_id: UUID) -> CardDTO:
    _require_uuid("card_id", card_id)
    card = Card.objects.filter(pk=card_id).first()
    if card is None:
        raise CardNotFound("id", card_id)
    return _to_dto(card)


def get_card_for_account(credit_account_id: UUID) -> CardDTO:
    _require_uuid("credit_account_id", credit_account_id)
    card = Card.objects.filter(credit_account_id=credit_account_id).first()
    if card is None:
        raise CardNotFound("credit_account_id", credit_account_id)
    return _to_dto(card)


def lock_card(card_id: UUID) -> CardDTO:
    """Read the card with `SELECT ... FOR UPDATE` (CON-2, CON-3)."""
    _require_atomic("lock_card")
    return _to_dto(_locked_card(card_id))


def ensure_can_authorize(card_id: UUID) -> CardDTO:
    """Lock the card and reject the purchase unless it is ACTIVE (BR-PUR-2)."""
    _require_atomic("ensure_can_authorize")
    card = _locked_card(card_id)
    if card.status != Card.Status.ACTIVE:
        raise CardNotActive(card_id=card.id, status=card.status)
    return _to_dto(card)


def freeze_card(card_id: UUID) -> CardDTO:
    """ACTIVE -> INACTIVE (state-machines.md §3)."""
    _require_atomic("freeze_card")
    return _transition(card_id, Card.Status.INACTIVE)


def unfreeze_card(card_id: UUID) -> CardDTO:
    """INACTIVE -> ACTIVE (state-machines.md §3)."""
    _require_atomic("unfreeze_card")
    return _transition(card_id, Card.Status.ACTIVE)


def close_card(card_id: UUID) -> CardDTO:
    """ACTIVE | INACTIVE -> CLOSED, together with its credit account (state-machines.md §3)."""
    _require_atomic("close_card")
    return _transition(card_id, Card.Status.CLOSED)


def _transition(card_id: UUID, target: Card.Status) -> CardDTO:
    card = _locked_card(card_id)
    assert_transition(Card.Status(card.status), target)
    card.status = target
    update_fields = ["status"]
    if target == Card.Status.CLOSED:
        card.closed_at = timezone.now()
        update_fields.append("closed_at")
    card.save(update_fields=update_fields)
    return _to_dto(card)


def _locked_card(card_id: UUID) -> Card:
    _require_uuid("card_id", card_id)
    card = Card.objects.select_for_update().filter(pk=card_id).first()
    if card is None:
        raise CardNotFound("id", card_id)
    return card


def _generate_token() -> str:
    """Opaque internal token. Never a PAN: a candidate with a PAN-like digit run is discarded."""
    while True:
        token = _TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
        if not _PAN_LIKE.search(token):
            return token


def _generate_last4() -> str:
    return f"{secrets.randbelow(10_000):04d}"


def _require_atomic(function_name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{function_name} must run inside transaction.atomic")


def _require_uuid(name: str, value: object) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID, got {type(value).__name__}")


def _to_dto(card: Card) -> CardDTO:
    return CardDTO(
        id=card.id,
        credit_account_id=card.credit_account_id,
        last4=card.last4,
        status=Card.Status(card.status).value,
        created_at=card.created_at,
        closed_at=card.closed_at,
    )
