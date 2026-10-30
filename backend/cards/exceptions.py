"""Domain exceptions of the `cards` app. Re-exported by `cards.services` (the public API).

Messages carry ids and statuses only, never the card token (SEC-CARD-3).
"""

from uuid import UUID


class CardsError(Exception):
    """Base class for every business rejection raised by the `cards` app."""


class CardNotFound(CardsError):
    def __init__(self, lookup: str, value: UUID) -> None:
        self.lookup = lookup
        self.value = value
        super().__init__(f"card not found for {lookup}={value}")


class CardNotActive(CardsError):
    def __init__(self, card_id: UUID, status: str) -> None:
        self.card_id = card_id
        self.status = status
        super().__init__(f"card {card_id} is {status}, not ACTIVE")


class InvalidTransition(CardsError):
    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"invalid card transition {from_status} -> {to_status}")
