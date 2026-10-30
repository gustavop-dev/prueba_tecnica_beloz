import uuid
from contextlib import contextmanager

import pytest
from django.db import connection, transaction

from ledger import services


def _provisioned_credit_account() -> uuid.UUID:
    # Own atomic block, so the fixture also works in `transaction=True` tests.
    account_id = uuid.uuid4()
    with transaction.atomic():
        services.provision_customer_accounts(account_id)
    return account_id


@pytest.fixture
def credit_account_id() -> uuid.UUID:
    """A provisioned credit account (only its ledger accounts exist: MOD-3, no FK)."""
    return _provisioned_credit_account()


@pytest.fixture
def other_credit_account_id() -> uuid.UUID:
    """A second provisioned credit account, to check isolation (BR-OWN-2)."""
    return _provisioned_credit_account()


@pytest.fixture
def raw_sql():
    """Run raw SQL, bypassing Python, and return all rows (or None for statements)."""

    def run(sql: str, params: tuple = ()):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.description else None

    return run


@pytest.fixture
def discarded_savepoint():
    """A savepoint that is always rolled back, for rows that must never reach a commit.

    Django's TestCase teardown runs `check_constraints()`, which fires the deferred LED-6
    triggers: an unbalanced entry left in the test transaction would fail the teardown.
    """

    @contextmanager
    def savepoint():
        with transaction.atomic():
            yield
            transaction.set_rollback(True)

    return savepoint


@pytest.fixture
def ledger_account_ids(raw_sql, credit_account_id) -> dict[str, uuid.UUID]:
    """Ids of CASH and the provisioned credit account's CUSTOMER_COLLATERAL, read via SQL."""
    rows = raw_sql(
        "SELECT code, id FROM ledger_ledgeraccount "
        "WHERE (code = 'CASH' AND credit_account_id IS NULL) "
        "OR (code = 'CUSTOMER_COLLATERAL' AND credit_account_id = %s)",
        (credit_account_id,),
    )
    return dict(rows)


@pytest.fixture
def insert_raw_entry(raw_sql, credit_account_id, ledger_account_ids):
    """Insert an entry and lines (CASH, then CUSTOMER_COLLATERAL, alternating) with raw SQL.

    Returns the entry id. The deferred LED-6 triggers only fire at commit or on
    `SET CONSTRAINTS ALL IMMEDIATE`.
    """

    def insert(*amounts: int) -> uuid.UUID:
        entry_id = uuid.uuid4()
        raw_sql(
            "INSERT INTO ledger_journalentry (id, kind, credit_account_id, created_at) "
            "VALUES (%s, 'DEPOSIT_CONFIRMED', %s, now())",
            (entry_id, credit_account_id),
        )
        account_cycle = [ledger_account_ids["CASH"], ledger_account_ids["CUSTOMER_COLLATERAL"]]
        for index, amount in enumerate(amounts):
            raw_sql(
                "INSERT INTO ledger_journalline (id, entry_id, ledger_account_id, amount_minor) "
                "VALUES (%s, %s, %s, %s)",
                (uuid.uuid4(), entry_id, account_cycle[index % 2], amount),
            )
        return entry_id

    return insert
