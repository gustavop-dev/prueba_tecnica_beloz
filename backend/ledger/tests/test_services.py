"""Public API of the ledger: provisioning, postings (ledger.md §5), balances and reads."""

import uuid
from decimal import Decimal

import pytest
from django.db import transaction

from ledger import services
from ledger.services import (
    CustomerBalancesDTO,
    LedgerAccountsNotProvisioned,
    LineDTO,
)

REFERENCE = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


def _deposit(credit_account_id, amount):
    return services.post_deposit_confirmed(credit_account_id, amount, REFERENCE)


def _purchase(credit_account_id, amount):
    return services.post_purchase(credit_account_id, amount, REFERENCE)


def _repayment(credit_account_id, amount):
    return services.post_repayment(credit_account_id, amount, REFERENCE)


def _closure(credit_account_id, amount):
    return services.post_closure(credit_account_id, amount, 0, REFERENCE)


SINGLE_AMOUNT_POSTINGS = {
    "deposit": services.post_deposit_confirmed,
    "purchase": services.post_purchase,
    "repayment": services.post_repayment,
}


def _stored_lines(raw_sql, entry_id):
    return raw_sql(
        "SELECT a.code, l.amount_minor FROM ledger_journalline l "
        "JOIN ledger_ledgeraccount a ON a.id = l.ledger_account_id "
        "WHERE l.entry_id = %s ORDER BY l.amount_minor DESC, a.code",
        (entry_id,),
    )


def _post_activity(credit_account_id, activity):
    for post, amount in activity:
        post(credit_account_id, amount)


def _journal_count(raw_sql):
    return raw_sql(
        "SELECT (SELECT count(*) FROM ledger_journalentry), "
        "(SELECT count(*) FROM ledger_journalline)"
    )


# --- Provisioning ------------------------------------------------------------------------


@pytest.mark.django_db
def test_provision_creates_the_two_customer_accounts_with_their_kinds(raw_sql):
    """Fails if provisioning misses an account or gives it the wrong kind (ledger.md §3)."""
    credit_account_id = uuid.uuid4()

    services.provision_customer_accounts(credit_account_id)

    assert raw_sql(
        "SELECT code, kind FROM ledger_ledgeraccount WHERE credit_account_id = %s ORDER BY code",
        (credit_account_id,),
    ) == [("CUSTOMER_COLLATERAL", "LIABILITY"), ("CUSTOMER_RECEIVABLE", "ASSET")]


@pytest.mark.django_db
def test_provision_is_idempotent(raw_sql, credit_account_id):
    """Fails if provisioning twice errors or duplicates accounts (retried account opening)."""
    services.provision_customer_accounts(credit_account_id)

    assert raw_sql("SELECT count(*) FROM ledger_ledgeraccount") == [(4,)]


@pytest.mark.django_db(transaction=True)
def test_provision_recreates_global_accounts_after_a_flush(raw_sql):
    """Fails if postings break after a test flush wiped CASH / MERCHANT_PAYABLE."""
    raw_sql("TRUNCATE ledger_journalline, ledger_journalentry, ledger_ledgeraccount")
    credit_account_id = uuid.uuid4()

    with transaction.atomic():
        services.provision_customer_accounts(credit_account_id)
        entry = _deposit(credit_account_id, 5_000)

    assert entry.lines == (
        LineDTO("CASH", 5_000),
        LineDTO("CUSTOMER_COLLATERAL", -5_000),
    )
    assert raw_sql(
        "SELECT code, kind FROM ledger_ledgeraccount WHERE credit_account_id IS NULL ORDER BY code"
    ) == [("CASH", "ASSET"), ("MERCHANT_PAYABLE", "LIABILITY")]


# --- Postings: exact lines and signs (ledger.md §5, LED-5) --------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("post", "kind", "expected_lines"),
    [
        (
            services.post_deposit_confirmed,
            "DEPOSIT_CONFIRMED",
            (LineDTO("CASH", 12_345), LineDTO("CUSTOMER_COLLATERAL", -12_345)),
        ),
        (
            services.post_purchase,
            "PURCHASE",
            (LineDTO("CUSTOMER_RECEIVABLE", 12_345), LineDTO("MERCHANT_PAYABLE", -12_345)),
        ),
        (
            services.post_repayment,
            "REPAYMENT",
            (LineDTO("CASH", 12_345), LineDTO("CUSTOMER_RECEIVABLE", -12_345)),
        ),
    ],
    ids=["deposit", "purchase", "repayment"],
)
def test_posting_writes_the_debit_and_credit_lines_of_ledger_spec(
    raw_sql, credit_account_id, post, kind, expected_lines
):
    """Fails if a posting debits/credits the wrong account or flips a sign (ledger.md §5, LED-5)."""
    entry = post(credit_account_id, 12_345, REFERENCE)

    assert (entry.kind, entry.credit_account_id, entry.reference_id) == (
        kind,
        credit_account_id,
        REFERENCE,
    )
    assert entry.lines == expected_lines
    assert _stored_lines(raw_sql, entry.id) == [
        (line.ledger_account_code, line.amount_minor) for line in expected_lines
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("debt", "refund", "expected_lines"),
    [
        (
            3_000,
            7_000,
            (
                LineDTO("CUSTOMER_COLLATERAL", 3_000),
                LineDTO("CUSTOMER_COLLATERAL", 7_000),
                LineDTO("CASH", -7_000),
                LineDTO("CUSTOMER_RECEIVABLE", -3_000),
            ),
        ),
        (
            0,
            10_000,
            (LineDTO("CUSTOMER_COLLATERAL", 10_000), LineDTO("CASH", -10_000)),
        ),
        (
            10_000,
            0,
            (LineDTO("CUSTOMER_COLLATERAL", 10_000), LineDTO("CUSTOMER_RECEIVABLE", -10_000)),
        ),
    ],
    ids=["debt and refund", "no debt: refund pair only", "debt equals deposit: apply pair only"],
)
def test_closure_writes_one_entry_and_skips_zero_pairs(
    raw_sql, credit_account_id, debt, refund, expected_lines
):
    """Fails if closure splits into several entries or writes a zero pair (ledger.md §5)."""
    _deposit(credit_account_id, 10_000)

    entry = services.post_closure(credit_account_id, debt, refund, REFERENCE)

    assert entry.kind == "CLOSURE"
    assert entry.lines == expected_lines
    assert raw_sql(
        "SELECT count(*) FROM ledger_journalentry "
        "WHERE kind = 'CLOSURE' AND credit_account_id = %s",
        (credit_account_id,),
    ) == [(1,)]
    assert sorted(_stored_lines(raw_sql, entry.id)) == sorted(
        (line.ledger_account_code, line.amount_minor) for line in expected_lines
    )


# --- Validation (BR-MONEY-3, defense in depth) -------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("post", SINGLE_AMOUNT_POSTINGS.values(), ids=SINGLE_AMOUNT_POSTINGS.keys())
@pytest.mark.parametrize(
    "amount",
    [0, -1, True, 10.5, Decimal("10"), "100", None],
    ids=["zero", "negative", "bool", "float", "decimal", "string", "none"],
)
def test_posting_rejects_a_non_positive_or_non_int_amount(raw_sql, credit_account_id, post, amount):
    """Fails if a posting accepts a zero, negative, bool, float or Decimal amount (BR-MONEY-3)."""
    with pytest.raises(ValueError, match="amount_minor must be a positive int"):
        post(credit_account_id, amount, REFERENCE)

    assert _journal_count(raw_sql) == [(0, 0)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("debt", "refund", "message"),
    [
        (0, 0, "debt_minor \\+ refund_minor must be positive"),
        (-1, 5_000, "debt_minor must be a non-negative int"),
        (5_000, -1, "refund_minor must be a non-negative int"),
        (False, 5_000, "debt_minor must be a non-negative int"),
        (5_000, 1.0, "refund_minor must be a non-negative int"),
        (Decimal("1"), 0, "debt_minor must be a non-negative int"),
    ],
    ids=["both zero", "negative debt", "negative refund", "bool debt", "float refund", "decimal"],
)
def test_closure_rejects_invalid_amounts(raw_sql, credit_account_id, debt, refund, message):
    """Fails if closure accepts negative, non-int or all-zero amounts (ledger.md §5)."""
    with pytest.raises(ValueError, match=message):
        services.post_closure(credit_account_id, debt, refund, REFERENCE)

    assert _journal_count(raw_sql) == [(0, 0)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "post",
    [_deposit, _purchase, _repayment, _closure],
    ids=["deposit", "purchase", "repayment", "closure"],
)
def test_posting_for_an_unprovisioned_credit_account_is_rejected(raw_sql, post):
    """Fails if a posting writes lines for a credit account with no ledger accounts."""
    unknown = uuid.uuid4()

    with pytest.raises(LedgerAccountsNotProvisioned) as excinfo:
        post(unknown, 1_000)

    assert excinfo.value.credit_account_id == unknown
    assert _journal_count(raw_sql) == [(0, 0)]


@pytest.mark.django_db
def test_balances_of_an_unprovisioned_credit_account_are_rejected():
    """Fails if balances of an unknown credit account silently read as zero."""
    unknown = uuid.uuid4()

    with pytest.raises(LedgerAccountsNotProvisioned) as excinfo:
        services.get_customer_balances(unknown)

    assert excinfo.value.missing_codes == ("CUSTOMER_COLLATERAL", "CUSTOMER_RECEIVABLE")


MUTATING_CALLS = {
    "provision": lambda: services.provision_customer_accounts(uuid.uuid4()),
    "deposit": lambda: services.post_deposit_confirmed(uuid.uuid4(), 1, REFERENCE),
    "purchase": lambda: services.post_purchase(uuid.uuid4(), 1, REFERENCE),
    "repayment": lambda: services.post_repayment(uuid.uuid4(), 1, REFERENCE),
    "closure": lambda: services.post_closure(uuid.uuid4(), 1, 0, REFERENCE),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("call", MUTATING_CALLS.values(), ids=MUTATING_CALLS.keys())
def test_mutating_service_requires_an_open_transaction(raw_sql, call):
    """Fails if a ledger write can run in autocommit, outside the caller's transaction (MOD-4)."""
    with pytest.raises(RuntimeError, match="must run inside transaction.atomic"):
        call()

    assert _journal_count(raw_sql) == [(0, 0)]
    assert raw_sql(
        "SELECT count(*) FROM ledger_ledgeraccount WHERE credit_account_id IS NOT NULL"
    ) == [(0,)]


# --- Balances (ledger.md §5 "Expected balances") -----------------------------------------


@pytest.mark.django_db
def test_new_credit_account_has_zero_balances(credit_account_id):
    """Fails if a freshly provisioned account reports a non-zero balance."""
    assert services.get_customer_balances(credit_account_id) == CustomerBalancesDTO(0, 0)


@pytest.mark.django_db
def test_active_account_balances_are_deposit_and_outstanding_debt(credit_account_id):
    """Fails if ACTIVE collateral != deposit or receivable != outstanding debt (ledger.md §5)."""
    _deposit(credit_account_id, 50_000)
    _purchase(credit_account_id, 12_000)
    _purchase(credit_account_id, 3_000)
    _repayment(credit_account_id, 4_000)

    assert services.get_customer_balances(credit_account_id) == CustomerBalancesDTO(
        collateral_minor=50_000, receivable_minor=11_000
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("activity", "debt"),
    [
        (((_purchase, 12_000), (_repayment, 2_000)), 10_000),
        ((), 0),
        (((_purchase, 50_000),), 50_000),
        (((_purchase, 20_000), (_repayment, 20_000)), 0),
    ],
    ids=["debt and refund", "no activity", "debt equals deposit", "debt fully repaid"],
)
def test_closed_account_balances_are_zero(credit_account_id, activity, debt):
    """Fails if closure leaves money in collateral or receivable (ledger.md §5, CLOSED row)."""
    _deposit(credit_account_id, 50_000)
    _post_activity(credit_account_id, activity)

    services.post_closure(credit_account_id, debt, 50_000 - debt, REFERENCE)

    assert services.get_customer_balances(credit_account_id) == CustomerBalancesDTO(0, 0)


@pytest.mark.django_db
def test_balances_are_isolated_between_credit_accounts(credit_account_id, other_credit_account_id):
    """Fails if one credit account's postings leak into another's balances (BR-OWN-2)."""
    _deposit(credit_account_id, 20_000)
    _purchase(credit_account_id, 5_000)
    _deposit(other_credit_account_id, 7_000)

    assert services.get_customer_balances(other_credit_account_id) == CustomerBalancesDTO(
        collateral_minor=7_000, receivable_minor=0
    )


# --- Reads -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_list_entries_returns_the_account_entries_in_chronological_order_with_lines(
    credit_account_id, other_credit_account_id
):
    """Fails if list_entries mixes credit accounts, loses order or drops lines (LED-1)."""
    deposit = _deposit(credit_account_id, 10_000)
    _deposit(other_credit_account_id, 9_999)
    purchase = _purchase(credit_account_id, 2_500)
    repayment = _repayment(credit_account_id, 1_000)

    entries = services.list_entries(credit_account_id)

    assert entries == [deposit, purchase, repayment]
    assert [entry.kind for entry in entries] == ["DEPOSIT_CONFIRMED", "PURCHASE", "REPAYMENT"]
    assert entries[1].lines == (
        LineDTO("CUSTOMER_RECEIVABLE", 2_500),
        LineDTO("MERCHANT_PAYABLE", -2_500),
    )


@pytest.mark.django_db
def test_list_entries_of_an_account_without_postings_is_empty(credit_account_id):
    """Fails if list_entries returns another account's entries for an account with none."""
    _deposit(credit_account_id, 1_000)

    assert services.list_entries(uuid.uuid4()) == []


@pytest.mark.django_db
def test_find_unbalanced_entry_ids_returns_only_broken_entries(
    credit_account_id, insert_raw_entry, discarded_savepoint
):
    """Fails if reconcile misses an unbalanced, single-line or empty entry, or flags a valid one.

    The raw inserts never reach a commit, so the deferred LED-6 trigger does not fire.
    """
    _deposit(credit_account_id, 10_000)
    _closure(credit_account_id, 10_000)

    with discarded_savepoint():
        unbalanced = insert_raw_entry(10_000, -9_000)
        single_line = insert_raw_entry(500)
        empty = insert_raw_entry()
        insert_raw_entry(700, -700)
        found = services.find_unbalanced_entry_ids()

    assert set(found) == {unbalanced, single_line, empty}
    assert len(found) == 3


@pytest.mark.django_db
def test_find_unbalanced_entry_ids_is_empty_for_a_consistent_ledger(credit_account_id):
    """Fails if reconcile reports false positives on correctly posted entries (INV-2)."""
    _deposit(credit_account_id, 10_000)
    _purchase(credit_account_id, 4_000)
    services.post_closure(credit_account_id, 4_000, 6_000, REFERENCE)

    assert services.find_unbalanced_entry_ids() == []
