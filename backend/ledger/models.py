"""Ledger models: chart of accounts and the append-only journal (docs/ledger.md).

Never imported outside this app (DEP-2): other code goes through `ledger.services`.
"""

import uuid

from django.db import models
from django.db.models import Q


class LedgerAccountCode(models.TextChoices):
    CASH = "CASH", "Cash"
    MERCHANT_PAYABLE = "MERCHANT_PAYABLE", "Merchant payable"
    CUSTOMER_COLLATERAL = "CUSTOMER_COLLATERAL", "Customer collateral"
    CUSTOMER_RECEIVABLE = "CUSTOMER_RECEIVABLE", "Customer receivable"


class LedgerAccountKind(models.TextChoices):
    ASSET = "ASSET", "Asset"
    LIABILITY = "LIABILITY", "Liability"


GLOBAL_CODES = (LedgerAccountCode.CASH, LedgerAccountCode.MERCHANT_PAYABLE)
CUSTOMER_CODES = (LedgerAccountCode.CUSTOMER_COLLATERAL, LedgerAccountCode.CUSTOMER_RECEIVABLE)

KIND_BY_CODE = {
    LedgerAccountCode.CASH: LedgerAccountKind.ASSET,
    LedgerAccountCode.CUSTOMER_RECEIVABLE: LedgerAccountKind.ASSET,
    LedgerAccountCode.MERCHANT_PAYABLE: LedgerAccountKind.LIABILITY,
    LedgerAccountCode.CUSTOMER_COLLATERAL: LedgerAccountKind.LIABILITY,
}


class LedgerAccount(models.Model):
    """A bookkeeping account. Global (`CASH`, `MERCHANT_PAYABLE`) or scoped to a credit account."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, choices=LedgerAccountCode.choices)
    kind = models.CharField(max_length=16, choices=LedgerAccountKind.choices)
    # MOD-3: reference to `accounts.CreditAccount` without a ForeignKey. NULL for global accounts.
    credit_account_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            # Also restricts `code` and `kind` to their declared values.
            models.CheckConstraint(
                condition=(
                    Q(
                        code__in=[LedgerAccountCode.CASH, LedgerAccountCode.CUSTOMER_RECEIVABLE],
                        kind=LedgerAccountKind.ASSET,
                    )
                    | Q(
                        code__in=[
                            LedgerAccountCode.MERCHANT_PAYABLE,
                            LedgerAccountCode.CUSTOMER_COLLATERAL,
                        ],
                        kind=LedgerAccountKind.LIABILITY,
                    )
                ),
                name="ledger_account_kind_matches_code",
            ),
            models.CheckConstraint(
                condition=(
                    Q(code__in=GLOBAL_CODES, credit_account_id__isnull=True)
                    | Q(code__in=CUSTOMER_CODES, credit_account_id__isnull=False)
                ),
                name="ledger_account_scope_matches_code",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=Q(credit_account_id__isnull=True),
                name="ledger_account_unique_global_code",
            ),
            models.UniqueConstraint(
                fields=["credit_account_id", "code"],
                name="ledger_account_unique_customer_code",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.credit_account_id or 'global'})"


class JournalEntryKind(models.TextChoices):
    DEPOSIT_CONFIRMED = "DEPOSIT_CONFIRMED", "Deposit confirmed"
    PURCHASE = "PURCHASE", "Purchase"
    REPAYMENT = "REPAYMENT", "Repayment"
    CLOSURE = "CLOSURE", "Closure"


class JournalEntry(models.Model):
    """One business transaction (LED-1). Append-only (LED-3, LED-4); balanced at commit (LED-6)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=JournalEntryKind.choices)
    # MOD-3: the credit account the entry concerns, without a ForeignKey.
    credit_account_id = models.UUIDField()
    # Business object that caused the entry (deposit id, authorization id, ...).
    reference_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=JournalEntryKind.values),
                name="ledger_journal_entry_kind_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["credit_account_id", "created_at"],
                name="ledger_entry_account_created",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.id}"


class JournalLine(models.Model):
    """One movement of an entry into one ledger account. Signed: debit > 0, credit < 0 (LED-5)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    ledger_account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT)
    amount_minor = models.BigIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(amount_minor=0),
                name="ledger_journal_line_amount_not_zero",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ledger_account_id} {self.amount_minor}"
