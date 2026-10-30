"""`CreditAccount`: account status, credit limit and the `outstanding_minor` balance cache.

Private to the app (DEP-2): other code goes through `accounts.services`.
"""

from django.db import models
from django.db.models import F, Q


class CreditAccount(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"

    # Set by the caller to the id reserved for the account: there is no default generation, so an
    # insert without an explicit id fails instead of silently creating an unrelated account.
    id = models.UUIDField(primary_key=True, editable=False)
    # Customer principal id (MOD-3: plain UUID, no ForeignKey).
    owner_id = models.UUIDField(db_index=True)
    # Opening collateral deposit (BR-ACC-1/2): one account per deposit.
    deposit_id = models.UUIDField(unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    collateral_ratio_bps = models.PositiveIntegerField()
    credit_limit_minor = models.BigIntegerField()
    # Cache of the customer receivable balance in the ledger (CACHE-1).
    outstanding_minor = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["ACTIVE", "CLOSED"]),
                name="accounts_creditaccount_status_valid",
            ),
            # CACHE-3 / INV-1: debt is never negative and never exceeds the limit.
            models.CheckConstraint(
                condition=Q(outstanding_minor__gte=0)
                & Q(outstanding_minor__lte=F("credit_limit_minor")),
                name="accounts_creditaccount_outstanding_within_limit",
            ),
            models.CheckConstraint(
                condition=Q(credit_limit_minor__gte=0),
                name="accounts_creditaccount_credit_limit_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(collateral_ratio_bps__gt=0),
                name="accounts_creditaccount_collateral_ratio_positive",
            ),
            # `closed_at` is set exactly when the account is CLOSED.
            models.CheckConstraint(
                condition=(Q(status="CLOSED") & Q(closed_at__isnull=False))
                | (~Q(status="CLOSED") & Q(closed_at__isnull=True)),
                name="accounts_creditaccount_closed_at_iff_closed",
            ),
            # BR-CLO-5: after closure the outstanding debt is always 0.
            models.CheckConstraint(
                condition=~Q(status="CLOSED") | Q(outstanding_minor=0),
                name="accounts_creditaccount_closed_has_no_outstanding",
            ),
        ]

    def __str__(self) -> str:
        return f"CreditAccount {self.id} ({self.status})"
