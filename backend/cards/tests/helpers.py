"""Plain helpers for the cards tests: raw SQL access and thread probes (no fixtures here)."""

import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

from django.db import OperationalError, connection, transaction

from cards.models import Card
from cards.services import issue_card

FIXED_CLOSED_AT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

# Maps hex digits to letters so a generated test token can never contain a PAN-like digit run.
_HEX_TO_LETTERS = str.maketrans("0123456789", "ghijklmnop")


def letters_only_token() -> str:
    """A unique token in the stored format (`card_` + 32 url-safe chars) with no digits."""
    return "card_" + uuid4().hex.translate(_HEX_TO_LETTERS)


def insert_card_row(**overrides) -> None:
    """Insert a card with raw SQL, bypassing the ORM and services, inside a savepoint."""
    row = {
        "id": uuid4(),
        "credit_account_id": uuid4(),
        "token": letters_only_token(),
        "last4": "4242",
        "status": "ACTIVE",
        "closed_at": None,
        **overrides,
    }
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO cards_card"
            " (id, credit_account_id, token, last4, status, created_at, closed_at)"
            " VALUES (%(id)s, %(credit_account_id)s, %(token)s, %(last4)s, %(status)s,"
            " now(), %(closed_at)s)",
            row,
        )


def update_card_row(card_id: UUID, column: str, value: object) -> None:
    """Update one column with raw SQL, bypassing the ORM and services, inside a savepoint."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"UPDATE cards_card SET {column} = %s WHERE id = %s", [value, card_id])  # noqa: S608


def row_is_locked_elsewhere(card_id: UUID) -> bool:
    """From another thread (own connection), try `FOR UPDATE NOWAIT` on the card row.

    Returns True when the row is locked by another transaction (NOWAIT raises immediately, so
    the probe never waits and needs no timing).
    """
    result: dict[str, bool] = {}

    def probe() -> None:
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM cards_card WHERE id = %s FOR UPDATE NOWAIT", [card_id]
                )
            result["locked"] = False
        except OperationalError:
            result["locked"] = True
        finally:
            connection.close()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=30)
    return result["locked"]


def issue_tokens(count: int) -> list[str]:
    """Issue `count` cards for distinct accounts and return their stored tokens."""
    ids = [issue_card(uuid4()).id for _ in range(count)]
    return list(Card.objects.filter(pk__in=ids).values_list("token", flat=True))
