"""`CollateralDeposit`: the opening deposit that backs a credit account.

Never imported outside this app (DEP-2); other code goes through `collateral.services`.
"""

import uuid

from django.db import models
from django.db.models import F, Q


class DepositStatus(models.TextChoices):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    RELEASED = "RELEASED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    APPLIED = "APPLIED"


# Terminal statuses reached at account closure (state-machines.md §1).
SETTLED_STATUSES = (
    DepositStatus.RELEASED,
    DepositStatus.PARTIALLY_APPLIED,
    DepositStatus.APPLIED,
)

_SETTLED = Q(status__in=SETTLED_STATUSES)
_CONFIRMED_OR_SETTLED = Q(status__in=(DepositStatus.CONFIRMED, *SETTLED_STATUSES))


class CollateralDeposit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.UUIDField()
    # Reserved at creation; becomes the credit account id on confirmation (api.md §2).
    reserved_account_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    amount_minor = models.BigIntegerField()
    status = models.CharField(
        max_length=32, choices=DepositStatus.choices, default=DepositStatus.PENDING
    )
    # Set only at settlement (BR-CLO-1..4): applied = debt, refunded = amount - debt.
    applied_minor = models.BigIntegerField(null=True, blank=True)
    refunded_minor = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # BR-MONEY-5.
            models.CheckConstraint(
                condition=Q(amount_minor__gt=0),
                name="collateral_deposit_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(status__in=DepositStatus.values),
                name="collateral_deposit_status_valid",
            ),
            # Settlement amounts exist if and only if the deposit is settled.
            models.CheckConstraint(
                condition=(_SETTLED & Q(applied_minor__isnull=False, refunded_minor__isnull=False))
                | (~_SETTLED & Q(applied_minor__isnull=True, refunded_minor__isnull=True)),
                name="collateral_deposit_settlement_amounts_iff_settled",
            ),
            # BR-CLO-1: refund = deposit - debt, i.e. applied + refunded = amount.
            models.CheckConstraint(
                condition=Q(applied_minor__isnull=True)
                | Q(amount_minor=F("applied_minor") + F("refunded_minor")),
                name="collateral_deposit_settlement_sums_to_amount",
            ),
            # BR-CLO-2: full refund only when the debt was 0.
            models.CheckConstraint(
                condition=~Q(status=DepositStatus.RELEASED) | Q(applied_minor=0),
                name="collateral_deposit_released_applies_nothing",
            ),
            # BR-CLO-4 upper boundary: debt equal to the deposit refunds 0.
            models.CheckConstraint(
                condition=~Q(status=DepositStatus.APPLIED) | Q(refunded_minor=0),
                name="collateral_deposit_applied_refunds_nothing",
            ),
            # BR-CLO-3: a partial application covers a debt strictly between 0 and the deposit.
            models.CheckConstraint(
                condition=~Q(status=DepositStatus.PARTIALLY_APPLIED)
                | Q(applied_minor__gt=0, applied_minor__lt=F("amount_minor")),
                name="collateral_deposit_partial_applies_strictly_between",
            ),
            # State timestamps match the status they record.
            models.CheckConstraint(
                condition=(_CONFIRMED_OR_SETTLED & Q(confirmed_at__isnull=False))
                | (~_CONFIRMED_OR_SETTLED & Q(confirmed_at__isnull=True)),
                name="collateral_deposit_confirmed_at_iff_confirmed",
            ),
            models.CheckConstraint(
                condition=(Q(status=DepositStatus.FAILED) & Q(failed_at__isnull=False))
                | (~Q(status=DepositStatus.FAILED) & Q(failed_at__isnull=True)),
                name="collateral_deposit_failed_at_iff_failed",
            ),
            models.CheckConstraint(
                condition=(_SETTLED & Q(settled_at__isnull=False))
                | (~_SETTLED & Q(settled_at__isnull=True)),
                name="collateral_deposit_settled_at_iff_settled",
            ),
        ]

    def __str__(self) -> str:
        return f"CollateralDeposit({self.id}, {self.status})"
