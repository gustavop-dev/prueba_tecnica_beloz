"""Public services of the `cards` app (contract §5): issuing, reads, authorization and DTO shape."""

import dataclasses
import re
from unittest.mock import Mock
from uuid import uuid4

import pytest

from cards.models import PAN_LIKE_REGEX, TOKEN_REGEX, Card
from cards.services import (
    CardDTO,
    CardNotActive,
    CardNotFound,
    CardsError,
    InvalidTransition,
    close_card,
    ensure_can_authorize,
    freeze_card,
    get_card,
    get_card_for_account,
    issue_card,
    lock_card,
    unfreeze_card,
)
from cards.tests.helpers import issue_tokens

Status = Card.Status
PAN_LIKE = re.compile(PAN_LIKE_REGEX)

LOOKUPS_BY_CARD_ID = [
    get_card,
    lock_card,
    ensure_can_authorize,
    freeze_card,
    unfreeze_card,
    close_card,
]


# --- issue_card ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_issue_card_creates_an_active_card_for_the_account():
    """Fails if a newly issued card is not ACTIVE or not bound to its account (BR-DEP-3)."""
    account_id = uuid4()

    dto = issue_card(account_id)

    stored = Card.objects.get(pk=dto.id)
    assert (dto.credit_account_id, dto.status, dto.closed_at) == (account_id, "ACTIVE", None)
    assert (stored.credit_account_id, stored.status, stored.closed_at) == (
        account_id,
        "ACTIVE",
        None,
    )
    assert (dto.last4, dto.created_at) == (stored.last4, stored.created_at)


@pytest.mark.django_db
def test_issue_card_stores_an_opaque_token_and_four_digit_last4():
    """Fails if the stored token or last 4 leave the SEC-CARD-2 format."""
    dto = issue_card(uuid4())

    stored = Card.objects.get(pk=dto.id)
    assert re.fullmatch(TOKEN_REGEX, stored.token) is not None
    assert re.fullmatch(r"[0-9]{4}", stored.last4) is not None


@pytest.mark.django_db
def test_issue_card_draws_last4_from_secrets_zero_padded(monkeypatch):
    """Fails if last 4 stops coming from `secrets` or loses its leading zeros (SEC-CARD-2)."""
    randbelow = Mock(return_value=7)
    monkeypatch.setattr("cards.services.secrets.randbelow", randbelow)

    dto = issue_card(uuid4())

    assert dto.last4 == "0007"
    randbelow.assert_called_once_with(10_000)


@pytest.mark.django_db
def test_issue_card_builds_the_token_from_secrets(monkeypatch):
    """Fails if the card token stops being `card_` + a `secrets.token_urlsafe` value."""
    monkeypatch.setattr("cards.services.secrets.token_urlsafe", Mock(return_value="A" * 32))

    dto = issue_card(uuid4())

    assert Card.objects.get(pk=dto.id).token == "card_" + "A" * 32


@pytest.mark.django_db
def test_issue_card_discards_a_token_candidate_with_a_pan_like_digit_run(monkeypatch):
    """Fails if a token containing a 13-19 digit run (a possible PAN) is stored (SEC-CARD-1)."""
    token_urlsafe = Mock(side_effect=["4111111111111111" + "a" * 16, "B" * 32])
    monkeypatch.setattr("cards.services.secrets.token_urlsafe", token_urlsafe)

    dto = issue_card(uuid4())

    assert Card.objects.get(pk=dto.id).token == "card_" + "B" * 32
    assert token_urlsafe.call_count == 2


@pytest.mark.django_db
def test_issued_tokens_are_unique_and_never_contain_a_pan_like_run():
    """Fails if token generation repeats values or produces a PAN-like digit run (SEC-CARD-1)."""
    tokens = issue_tokens(200)

    assert len(set(tokens)) == 200
    assert list(filter(PAN_LIKE.search, tokens)) == []


@pytest.mark.django_db
def test_issue_card_rejects_a_second_card_for_the_same_account():
    """Fails if an account can get a second card (BR-ACC-3)."""
    account_id = uuid4()
    first = issue_card(account_id)

    with pytest.raises(ValueError, match="already has a card"):
        issue_card(account_id)

    assert list(Card.objects.filter(credit_account_id=account_id).values_list("id", flat=True)) == [
        first.id
    ]


# --- reads --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_card_and_get_card_for_account_return_the_same_card(make_card):
    """Fails if a card cannot be found by its id or by its account id."""
    card = make_card(Status.INACTIVE)

    by_id = get_card(card.id)
    by_account = get_card_for_account(card.credit_account_id)

    assert by_id == by_account
    assert (by_id.id, by_id.status, by_id.last4) == (card.id, "INACTIVE", card.last4)


@pytest.mark.django_db
@pytest.mark.parametrize("service", LOOKUPS_BY_CARD_ID, ids=lambda fn: fn.__name__)
def test_service_raises_card_not_found_for_an_unknown_card_id(service):
    """Fails if an unknown card id is not reported as CardNotFound (a CardsError)."""
    missing_id = uuid4()

    with pytest.raises(CardNotFound) as exc_info:
        service(missing_id)

    assert (exc_info.value.lookup, exc_info.value.value) == ("id", missing_id)
    assert isinstance(exc_info.value, CardsError)


@pytest.mark.django_db
def test_get_card_for_account_raises_card_not_found_for_an_account_without_card():
    """Fails if an account with no card is not reported as CardNotFound."""
    account_id = uuid4()

    with pytest.raises(CardNotFound) as exc_info:
        get_card_for_account(account_id)

    assert (exc_info.value.lookup, exc_info.value.value) == ("credit_account_id", account_id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "service",
    [*LOOKUPS_BY_CARD_ID, issue_card, get_card_for_account],
    ids=lambda fn: fn.__name__,
)
def test_service_rejects_a_non_uuid_identifier(service):
    """Fails if a non-UUID identifier reaches the database instead of a ValueError."""
    with pytest.raises(ValueError, match="must be a UUID"):
        service(str(uuid4()))


# --- authorization (BR-PUR-2) -------------------------------------------------------------------


@pytest.mark.django_db
def test_ensure_can_authorize_accepts_an_active_card(make_card):
    """Fails if an ACTIVE card is rejected for purchases (BR-PUR-2)."""
    card = make_card(Status.ACTIVE)

    dto = ensure_can_authorize(card.id)

    assert (dto.id, dto.status) == (card.id, "ACTIVE")


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Status.INACTIVE, Status.CLOSED], ids=str)
def test_ensure_can_authorize_rejects_a_card_that_is_not_active(make_card, status):
    """Fails if a frozen or closed card can authorize a purchase (BR-PUR-2, TEST-SM-3)."""
    card = make_card(status)

    with pytest.raises(CardNotActive) as exc_info:
        ensure_can_authorize(card.id)

    assert (exc_info.value.card_id, exc_info.value.status) == (card.id, status.value)
    assert Card.objects.get(pk=card.id).status == status.value


# --- SEC-CARD-3: the token never leaves the app -------------------------------------------------


def test_card_dto_has_exactly_the_contract_fields_and_no_token():
    """Fails if CardDTO gains a `token` field or drifts from the contract (SEC-CARD-3)."""
    assert [field.name for field in dataclasses.fields(CardDTO)] == [
        "id",
        "credit_account_id",
        "last4",
        "status",
        "created_at",
        "closed_at",
    ]


@pytest.mark.django_db
def test_card_dto_is_immutable_and_its_repr_never_shows_the_token():
    """Fails if a returned card is mutable or its repr leaks the token (DEP-3, SEC-CARD-3)."""
    dto = issue_card(uuid4())
    token = Card.objects.get(pk=dto.id).token

    with pytest.raises(dataclasses.FrozenInstanceError):
        dto.status = "CLOSED"  # type: ignore[misc]

    assert token not in repr(dto)


@pytest.mark.django_db
def test_domain_error_messages_never_include_the_token(make_card):
    """Fails if a business rejection message leaks the card token (SEC-CARD-3)."""
    card = make_card(Status.CLOSED)

    with pytest.raises(CardNotActive) as not_active:
        ensure_can_authorize(card.id)
    with pytest.raises(InvalidTransition) as invalid:
        freeze_card(card.id)

    assert card.token not in f"{not_active.value} {invalid.value} {card}"
    assert str(not_active.value) == f"card {card.id} is CLOSED, not ACTIVE"
