"""Card model: an internal opaque token and the last 4 digits, never a PAN (SEC-CARD-1)."""

import uuid

from django.db import models
from django.db.models import Q

# Shape of every token `services._generate_token` produces: "card_" + token_urlsafe(24).
TOKEN_REGEX = r"^card_[A-Za-z0-9_-]{32}$"  # noqa: S105 - a format pattern, not a secret.
# A run of 13 or more digits could be a PAN (PANs are 13-19 digits); no column may hold one.
PAN_LIKE_REGEX = r"[0-9]{13,}"
LAST4_REGEX = r"^[0-9]{4}$"


class CardStatus(models.TextChoices):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CLOSED = "CLOSED"


class Card(models.Model):
    Status = CardStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reference to accounts.CreditAccount, without a ForeignKey (MOD-3).
    credit_account_id = models.UUIDField()
    token = models.CharField(max_length=64)
    last4 = models.CharField(max_length=4)
    status = models.CharField(max_length=8, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # One card per account (BR-ACC-3).
            models.UniqueConstraint(
                fields=["credit_account_id"], name="cards_card_one_per_account"
            ),
            models.UniqueConstraint(fields=["token"], name="cards_card_token_unique"),
            models.CheckConstraint(
                condition=Q(status__in=CardStatus.values),
                name="cards_card_status_valid",
            ),
            # SEC-CARD-2: exactly four digits.
            models.CheckConstraint(
                condition=Q(last4__regex=LAST4_REGEX), name="cards_card_last4_four_digits"
            ),
            # SEC-CARD-1: the token column only accepts opaque internal tokens, never a PAN.
            models.CheckConstraint(
                condition=Q(token__regex=TOKEN_REGEX), name="cards_card_token_format"
            ),
            models.CheckConstraint(
                condition=~Q(token__regex=PAN_LIKE_REGEX), name="cards_card_token_not_pan"
            ),
            # CLOSED <=> closed_at IS NOT NULL.
            models.CheckConstraint(
                condition=(
                    Q(status=CardStatus.CLOSED, closed_at__isnull=False)
                    | (~Q(status=CardStatus.CLOSED) & Q(closed_at__isnull=True))
                ),
                name="cards_card_closed_at_iff_closed",
            ),
        ]

    def __str__(self) -> str:
        # Never include the token (SEC-CARD-3).
        return f"Card {self.id} ****{self.last4} {self.status}"
