"""Schema constraints of the chart of accounts and the journal, exercised with raw SQL."""

import uuid

import pytest
from django.db import IntegrityError, transaction

CUSTOMER = uuid.UUID("00000000-0000-4000-8000-000000000001")

INSERT_ACCOUNT = (
    "INSERT INTO ledger_ledgeraccount (id, code, kind, credit_account_id) "
    "VALUES (gen_random_uuid(), %s, %s, %s)"
)


@pytest.mark.django_db
def test_migration_creates_exactly_the_two_global_accounts(raw_sql):
    """Fails if the data migration stops creating CASH / MERCHANT_PAYABLE with their kinds."""
    rows = raw_sql(
        "SELECT code, kind FROM ledger_ledgeraccount WHERE credit_account_id IS NULL ORDER BY code"
    )

    assert rows == [("CASH", "ASSET"), ("MERCHANT_PAYABLE", "LIABILITY")]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("code", "kind", "credit_account_id", "constraint"),
    [
        ("CASH", "LIABILITY", None, "ledger_account_kind_matches_code"),
        ("CUSTOMER_COLLATERAL", "ASSET", CUSTOMER, "ledger_account_kind_matches_code"),
        ("CUSTOMER_RECEIVABLE", "LIABILITY", CUSTOMER, "ledger_account_kind_matches_code"),
        ("SAVINGS", "ASSET", CUSTOMER, "ledger_account_kind_matches_code"),
        ("CUSTOMER_RECEIVABLE", "EQUITY", CUSTOMER, "ledger_account_kind_matches_code"),
        ("CASH", "ASSET", CUSTOMER, "ledger_account_scope_matches_code"),
        ("CUSTOMER_COLLATERAL", "LIABILITY", None, "ledger_account_scope_matches_code"),
        ("CASH", "ASSET", None, "ledger_account_unique_global_code"),
        ("MERCHANT_PAYABLE", "LIABILITY", None, "ledger_account_unique_global_code"),
    ],
    ids=[
        "cash as liability",
        "collateral as asset",
        "receivable as liability",
        "unknown code",
        "unknown kind",
        "global code scoped to a customer",
        "customer code without credit account",
        "second CASH",
        "second MERCHANT_PAYABLE",
    ],
)
def test_invalid_ledger_account_is_rejected(raw_sql, code, kind, credit_account_id, constraint):
    """Fails if the DB accepts an account with a wrong kind, scope or a duplicate global code."""
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        raw_sql(INSERT_ACCOUNT, (code, kind, credit_account_id))


@pytest.mark.django_db
def test_duplicate_customer_account_is_rejected(raw_sql, credit_account_id):
    """Fails if a credit account can get two receivables, splitting its balance (ledger.md §3)."""
    with (
        pytest.raises(IntegrityError, match="ledger_account_unique_customer_code"),
        transaction.atomic(),
    ):
        raw_sql(INSERT_ACCOUNT, ("CUSTOMER_RECEIVABLE", "ASSET", credit_account_id))


@pytest.mark.django_db
def test_zero_amount_line_is_rejected(insert_raw_entry):
    """Fails if the DB accepts a zero-amount journal line (LED-5)."""
    with (
        pytest.raises(IntegrityError, match="ledger_journal_line_amount_not_zero"),
        transaction.atomic(),
    ):
        insert_raw_entry(0, 0)


@pytest.mark.django_db
def test_entry_with_unknown_kind_is_rejected(raw_sql):
    """Fails if the DB accepts a journal entry kind outside the declared choices."""
    with (
        pytest.raises(IntegrityError, match="ledger_journal_entry_kind_valid"),
        transaction.atomic(),
    ):
        raw_sql(
            "INSERT INTO ledger_journalentry (id, kind, credit_account_id, created_at) "
            "VALUES (gen_random_uuid(), 'WITHDRAWAL', %s, now())",
            (CUSTOMER,),
        )
