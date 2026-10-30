"""Public API of the `ledger` app: the only module other code may import (MOD-1).

Every posting writes one balanced journal entry (LED-1, LED-2) with signed lines: debit
positive, credit negative (LED-5). Mutating functions require a transaction opened by the
caller (MOD-4) so the entry commits together with the caller's other writes (CACHE-2).
Amounts are integers in minor units; floats and decimals are rejected (BR-MONEY-3).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q, Sum

from ledger.models import (
    CUSTOMER_CODES,
    GLOBAL_CODES,
    KIND_BY_CODE,
    JournalEntry,
    JournalEntryKind,
    JournalLine,
    LedgerAccount,
    LedgerAccountCode,
)

__all__ = [
    "CustomerBalancesDTO",
    "EntryDTO",
    "LedgerAccountsNotProvisioned",
    "LedgerError",
    "LineDTO",
    "find_unbalanced_entry_ids",
    "get_customer_balances",
    "list_entries",
    "post_closure",
    "post_deposit_confirmed",
    "post_purchase",
    "post_repayment",
    "provision_customer_accounts",
]


# --- DTOs -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineDTO:
    ledger_account_code: str
    amount_minor: int  # signed: debit > 0, credit < 0 (LED-5)


@dataclass(frozen=True, slots=True)
class EntryDTO:
    id: UUID
    kind: str
    credit_account_id: UUID
    reference_id: UUID | None
    created_at: datetime
    lines: tuple[LineDTO, ...]


@dataclass(frozen=True, slots=True)
class CustomerBalancesDTO:
    """Balances in their normal sign: positive when the account holds money."""

    collateral_minor: int
    receivable_minor: int


# --- Exceptions -------------------------------------------------------------------------


class LedgerError(Exception):
    """Base class of the ledger's domain errors."""


class LedgerAccountsNotProvisioned(LedgerError):
    """The ledger accounts a posting needs do not exist."""

    def __init__(self, credit_account_id: UUID, missing_codes: Iterable[str]):
        self.credit_account_id = credit_account_id
        self.missing_codes = tuple(sorted(missing_codes))
        super().__init__(
            f"ledger accounts {', '.join(self.missing_codes)} are not provisioned "
            f"for credit account {credit_account_id}"
        )


# --- Guards -----------------------------------------------------------------------------


def _require_atomic(function_name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{function_name} must run inside transaction.atomic")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_positive_amount(name: str, value: object) -> None:
    if not _is_int(value) or value <= 0:
        raise ValueError(f"{name} must be a positive int in minor units, got {value!r}")


def _require_non_negative_amount(name: str, value: object) -> None:
    if not _is_int(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative int in minor units, got {value!r}")


# --- Internals --------------------------------------------------------------------------


def _line_sort_key(line: LineDTO) -> tuple[bool, str, int]:
    # Canonical order shared by postings and reads: debits first, then by account code.
    return (line.amount_minor < 0, line.ledger_account_code, line.amount_minor)


def _resolve_accounts(credit_account_id: UUID, codes: set[str]) -> dict[str, LedgerAccount]:
    global_codes = codes & set(GLOBAL_CODES)
    customer_codes = codes & set(CUSTOMER_CODES)
    accounts = {
        account.code: account
        for account in LedgerAccount.objects.filter(
            Q(credit_account_id__isnull=True, code__in=global_codes)
            | Q(credit_account_id=credit_account_id, code__in=customer_codes)
        )
    }
    missing = codes - accounts.keys()
    if missing:
        raise LedgerAccountsNotProvisioned(credit_account_id, missing)
    return accounts


def _post(
    *,
    kind: JournalEntryKind,
    credit_account_id: UUID,
    reference_id: UUID | None,
    pairs: list[tuple[LedgerAccountCode, LedgerAccountCode, int]],
) -> EntryDTO:
    """Write one entry with a debit and a credit line per (debit, credit, amount) pair."""
    codes = {code for debit, credit, _ in pairs for code in (debit, credit)}
    accounts = _resolve_accounts(credit_account_id, codes)

    entry = JournalEntry.objects.create(
        kind=kind, credit_account_id=credit_account_id, reference_id=reference_id
    )
    lines = [
        JournalLine(entry=entry, ledger_account=accounts[code], amount_minor=signed_amount)
        for debit, credit, amount in pairs
        for code, signed_amount in ((debit, amount), (credit, -amount))
    ]
    JournalLine.objects.bulk_create(lines)
    return _entry_to_dto(entry, lines)


def _entry_to_dto(entry: JournalEntry, lines: Iterable[JournalLine]) -> EntryDTO:
    return EntryDTO(
        id=entry.id,
        kind=str(entry.kind),
        credit_account_id=entry.credit_account_id,
        reference_id=entry.reference_id,
        created_at=entry.created_at,
        lines=tuple(
            sorted(
                (
                    LineDTO(
                        ledger_account_code=str(line.ledger_account.code),
                        amount_minor=line.amount_minor,
                    )
                    for line in lines
                ),
                key=_line_sort_key,
            )
        ),
    )


# --- Public API -------------------------------------------------------------------------


def provision_customer_accounts(credit_account_id: UUID) -> None:
    """Create the credit account's `CUSTOMER_COLLATERAL` and `CUSTOMER_RECEIVABLE`. Idempotent.

    Also ensures the two global accounts exist. The data migration creates them; recreating
    them here keeps the ledger usable after a test-database flush (TransactionTestCase).
    """
    _require_atomic("provision_customer_accounts")
    for code in GLOBAL_CODES:
        LedgerAccount.objects.get_or_create(
            code=code, credit_account_id=None, defaults={"kind": KIND_BY_CODE[code]}
        )
    for code in CUSTOMER_CODES:
        LedgerAccount.objects.get_or_create(
            code=code, credit_account_id=credit_account_id, defaults={"kind": KIND_BY_CODE[code]}
        )


def post_deposit_confirmed(
    credit_account_id: UUID, amount_minor: int, reference_id: UUID
) -> EntryDTO:
    """Dr CASH / Cr CUSTOMER_COLLATERAL (ledger.md §5)."""
    _require_atomic("post_deposit_confirmed")
    _require_positive_amount("amount_minor", amount_minor)
    return _post(
        kind=JournalEntryKind.DEPOSIT_CONFIRMED,
        credit_account_id=credit_account_id,
        reference_id=reference_id,
        pairs=[(LedgerAccountCode.CASH, LedgerAccountCode.CUSTOMER_COLLATERAL, amount_minor)],
    )


def post_purchase(credit_account_id: UUID, amount_minor: int, reference_id: UUID) -> EntryDTO:
    """Dr CUSTOMER_RECEIVABLE / Cr MERCHANT_PAYABLE (ledger.md §5)."""
    _require_atomic("post_purchase")
    _require_positive_amount("amount_minor", amount_minor)
    return _post(
        kind=JournalEntryKind.PURCHASE,
        credit_account_id=credit_account_id,
        reference_id=reference_id,
        pairs=[
            (
                LedgerAccountCode.CUSTOMER_RECEIVABLE,
                LedgerAccountCode.MERCHANT_PAYABLE,
                amount_minor,
            )
        ],
    )


def post_repayment(credit_account_id: UUID, amount_minor: int, reference_id: UUID) -> EntryDTO:
    """Dr CASH / Cr CUSTOMER_RECEIVABLE (ledger.md §5)."""
    _require_atomic("post_repayment")
    _require_positive_amount("amount_minor", amount_minor)
    return _post(
        kind=JournalEntryKind.REPAYMENT,
        credit_account_id=credit_account_id,
        reference_id=reference_id,
        pairs=[(LedgerAccountCode.CASH, LedgerAccountCode.CUSTOMER_RECEIVABLE, amount_minor)],
    )


def post_closure(
    credit_account_id: UUID, debt_minor: int, refund_minor: int, reference_id: UUID
) -> EntryDTO:
    """One entry (ledger.md §5), skipping a pair whose amount is 0.

    Dr CUSTOMER_COLLATERAL / Cr CUSTOMER_RECEIVABLE for `debt_minor`;
    Dr CUSTOMER_COLLATERAL / Cr CASH for `refund_minor`.
    """
    _require_atomic("post_closure")
    _require_non_negative_amount("debt_minor", debt_minor)
    _require_non_negative_amount("refund_minor", refund_minor)
    if debt_minor + refund_minor <= 0:
        raise ValueError("debt_minor + refund_minor must be positive")
    pairs = [
        (LedgerAccountCode.CUSTOMER_COLLATERAL, credit, amount)
        for credit, amount in (
            (LedgerAccountCode.CUSTOMER_RECEIVABLE, debt_minor),
            (LedgerAccountCode.CASH, refund_minor),
        )
        if amount > 0
    ]
    return _post(
        kind=JournalEntryKind.CLOSURE,
        credit_account_id=credit_account_id,
        reference_id=reference_id,
        pairs=pairs,
    )


def get_customer_balances(credit_account_id: UUID) -> CustomerBalancesDTO:
    """Collateral (liability, credit-normal) and receivable (asset, debit-normal) balances."""
    accounts = _resolve_accounts(credit_account_id, set(CUSTOMER_CODES))
    totals = dict(
        JournalLine.objects.filter(ledger_account__in=accounts.values())
        .values("ledger_account__code")
        .annotate(total=Sum("amount_minor"))
        .values_list("ledger_account__code", "total")
    )
    return CustomerBalancesDTO(
        collateral_minor=-int(totals.get(LedgerAccountCode.CUSTOMER_COLLATERAL, 0)),
        receivable_minor=int(totals.get(LedgerAccountCode.CUSTOMER_RECEIVABLE, 0)),
    )


def list_entries(credit_account_id: UUID) -> list[EntryDTO]:
    """The credit account's entries in chronological order, lines included."""
    entries = (
        JournalEntry.objects.filter(credit_account_id=credit_account_id)
        .prefetch_related("lines__ledger_account")
        .order_by("created_at", "id")
    )
    return [_entry_to_dto(entry, entry.lines.all()) for entry in entries]


def find_unbalanced_entry_ids() -> list[UUID]:
    """Entries that break LED-1/LED-2: fewer than 2 lines or a non-zero sum (for reconcile)."""
    return list(
        JournalEntry.objects.annotate(line_count=Count("lines"), total=Sum("lines__amount_minor"))
        .filter(Q(line_count__lt=2) | ~Q(total=0))
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
