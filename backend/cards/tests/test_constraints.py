"""Database guards on `cards_card`, tested with raw SQL that bypasses Python (testing.md §8)."""

from uuid import uuid4

import pytest
from django.db import IntegrityError

from cards.models import Card
from cards.tests.helpers import FIXED_CLOSED_AT, insert_card_row, update_card_row

VISA_TEST_PAN = "4111111111111111"


@pytest.mark.django_db
def test_raw_insert_of_a_valid_card_row_is_accepted():
    """Fails if the raw-SQL helper itself is broken, which would make every guard test vacuous."""
    card_id = uuid4()

    insert_card_row(id=card_id, last4="0042")

    assert Card.objects.get(pk=card_id).last4 == "0042"


@pytest.mark.django_db
@pytest.mark.parametrize("last4", ["123", "12a4", "abcd", " 123", ""])
def test_last4_that_is_not_four_digits_is_rejected_by_the_database(last4):
    """Fails if the `last4` CHECK is dropped and malformed last 4 are stored (SEC-CARD-2)."""
    with pytest.raises(IntegrityError, match="cards_card_last4_four_digits"):
        insert_card_row(last4=last4)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "token",
    [
        VISA_TEST_PAN,
        "card_" + VISA_TEST_PAN + "a" * 16,
        "card_abc" + "5555555555554444" + "a" * 13,
        "card_" + "0" * 13 + "a" * 19,
    ],
)
def test_token_holding_a_pan_like_digit_run_is_rejected_by_the_database(token):
    """Fails if the `token` column can store a 13-19 digit run, i.e. a PAN (SEC-CARD-1)."""
    with pytest.raises(IntegrityError, match=r"cards_card_token_(format|not_pan)"):
        insert_card_row(token=token)


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["", "card_short", "tok_" + "a" * 32, "card_" + "a" * 31 + "!"])
def test_token_outside_the_opaque_format_is_rejected_by_the_database(token):
    """Fails if the `token` column accepts non-opaque-token values (SEC-CARD-2)."""
    with pytest.raises(IntegrityError, match="cards_card_token_format"):
        insert_card_row(token=token)


@pytest.mark.django_db
def test_raw_update_of_a_token_to_a_pan_is_rejected(make_card):
    """Fails if an existing card's token can be overwritten with a PAN (SEC-CARD-1)."""
    card = make_card()

    with pytest.raises(IntegrityError, match=r"cards_card_token_(format|not_pan)"):
        update_card_row(card.id, "token", VISA_TEST_PAN)

    assert Card.objects.get(pk=card.id).token == card.token


@pytest.mark.django_db
def test_second_card_for_the_same_account_is_rejected_by_the_database():
    """Fails if the one-card-per-account unique constraint is dropped (BR-ACC-3)."""
    account_id = uuid4()
    insert_card_row(credit_account_id=account_id)

    with pytest.raises(IntegrityError, match="cards_card_one_per_account"):
        insert_card_row(credit_account_id=account_id)

    assert Card.objects.filter(credit_account_id=account_id).count() == 1


@pytest.mark.django_db
def test_duplicate_token_is_rejected_by_the_database(make_card):
    """Fails if two cards can share one internal token (SEC-CARD-2)."""
    card = make_card()

    with pytest.raises(IntegrityError, match="cards_card_token_unique"):
        insert_card_row(token=card.token)


@pytest.mark.django_db
def test_unknown_status_is_rejected_by_the_database():
    """Fails if the status CHECK is dropped and an undeclared status is stored (INV-5)."""
    with pytest.raises(IntegrityError, match="cards_card_status_valid"):
        insert_card_row(status="FROZEN")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "closed_at"),
    [("CLOSED", None), ("ACTIVE", FIXED_CLOSED_AT), ("INACTIVE", FIXED_CLOSED_AT)],
)
def test_closed_at_disagreeing_with_status_is_rejected_by_the_database(status, closed_at):
    """Fails if a card is CLOSED without `closed_at`, or open with one (state-machines.md §3)."""
    with pytest.raises(IntegrityError, match="cards_card_closed_at_iff_closed"):
        insert_card_row(status=status, closed_at=closed_at)
