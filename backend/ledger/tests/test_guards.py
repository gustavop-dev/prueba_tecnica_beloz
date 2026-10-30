"""Database guards of the journal, exercised with raw SQL that bypasses Python.

TEST-DB-1 (LED-4, INV-3): update, delete and truncate are rejected.
TEST-DB-3 (LED-6, INV-2): unbalanced or single-line entries are rejected at commit.
"""

import uuid

import pytest
from django.db import IntegrityError, transaction

from ledger import services

APP_ROLE = "ledger_test_app_role"

MUTATIONS = {
    "update entry": "UPDATE ledger_journalentry SET reference_id = gen_random_uuid() WHERE id = %s",
    "delete entry": "DELETE FROM ledger_journalentry WHERE id = %s",
    "update line": "UPDATE ledger_journalline SET amount_minor = amount_minor * 2 "
    "WHERE entry_id = %s",
    "delete line": "DELETE FROM ledger_journalline WHERE entry_id = %s",
}


@pytest.fixture
def posted_entry_id(raw_sql, credit_account_id) -> uuid.UUID:
    """An entry posted through the service whose deferred LED-6 checks already ran.

    Firing them now (as a commit would) matters for TRUNCATE, which PostgreSQL refuses
    while the table has pending trigger events.
    """
    entry_id = services.post_deposit_confirmed(credit_account_id, 10_000, uuid.uuid4()).id
    raw_sql("SET CONSTRAINTS ALL IMMEDIATE")
    return entry_id


@pytest.fixture
def non_superuser_role(raw_sql) -> str:
    """A non-superuser role with full table privileges, like the production app role.

    It holds the TRUNCATE privilege, so a rejection can only come from the LED-4 trigger.
    Created inside the test transaction, so it is rolled back with it.
    """
    raw_sql(f"CREATE ROLE {APP_ROLE} NOSUPERUSER NOLOGIN")
    raw_sql(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    raw_sql(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    return APP_ROLE


def _journal_snapshot(raw_sql, entry_id):
    return raw_sql(
        "SELECT e.kind, e.reference_id, l.amount_minor FROM ledger_journalentry e "
        "JOIN ledger_journalline l ON l.entry_id = e.id WHERE e.id = %s ORDER BY l.amount_minor",
        (entry_id,),
    )


# --- TEST-DB-1: append-only (LED-4) ------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("sql", MUTATIONS.values(), ids=MUTATIONS.keys())
def test_update_and_delete_on_journal_are_rejected(raw_sql, posted_entry_id, sql):
    """Fails if UPDATE or DELETE can rewrite or erase a journal entry or line (LED-4, TEST-DB-1)."""
    before = _journal_snapshot(raw_sql, posted_entry_id)

    with (
        pytest.raises(IntegrityError, match="LED-4: ledger_journal(entry|line) is append-only"),
        transaction.atomic(),
    ):
        raw_sql(sql, (posted_entry_id,))

    assert len(before) == 2
    assert _journal_snapshot(raw_sql, posted_entry_id) == before


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("sql", "rejecting_table"),
    [
        ("TRUNCATE ledger_journalline", "ledger_journalline"),
        ("TRUNCATE ledger_journalentry, ledger_journalline", "ledger_journalentry"),
    ],
    ids=["lines", "entries and lines"],
)
def test_truncate_on_journal_is_rejected_for_a_non_superuser_role(
    raw_sql, posted_entry_id, non_superuser_role, sql, rejecting_table
):
    """Fails if the application (non-superuser) role can TRUNCATE the journal (LED-4, TEST-DB-1)."""
    with (
        pytest.raises(IntegrityError, match=f"LED-4: {rejecting_table} is append-only; TRUNCATE"),
        transaction.atomic(),
    ):
        raw_sql(f"SET LOCAL ROLE {non_superuser_role}")
        raw_sql(sql)

    assert raw_sql(
        "SELECT count(*) FROM ledger_journalline WHERE entry_id = %s", (posted_entry_id,)
    ) == [(2,)]


@pytest.mark.django_db
def test_truncate_on_journal_is_allowed_for_a_superuser(raw_sql, posted_entry_id):
    """Fails if the superuser exemption is lost, which would break Django's test flush (LED-4)."""
    is_superuser = raw_sql("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")

    raw_sql("TRUNCATE ledger_journalentry, ledger_journalline")

    assert is_superuser == [(True,)]
    assert raw_sql("SELECT count(*) FROM ledger_journalentry") == [(0,)]


# --- TEST-DB-3: balanced entries (LED-6) -------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("amounts", "message"),
    [
        ((10_000, -9_999), "LED-6: journal entry .* does not balance"),
        ((10_000, -5_000, -4_000), "LED-6: journal entry .* does not balance"),
        ((10_000,), "LED-6: journal entry .* has 1 line\\(s\\); at least 2 are required"),
        ((), "LED-6: journal entry .* has 0 line\\(s\\); at least 2 are required"),
    ],
    ids=["unbalanced pair", "unbalanced three lines", "single line", "no lines"],
)
def test_unbalanced_or_short_entry_is_rejected_by_the_deferred_trigger(
    raw_sql, insert_raw_entry, amounts, message
):
    """Fails if an entry not summing to zero or with < 2 lines can commit (LED-6, TEST-DB-3)."""
    with pytest.raises(IntegrityError, match=message), transaction.atomic():
        insert_raw_entry(*amounts)
        raw_sql("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.django_db
def test_line_appended_to_an_existing_entry_is_rejected(
    raw_sql, posted_entry_id, ledger_account_ids
):
    """Fails if a later transaction can unbalance a posted entry by adding a line (LED-6)."""
    with (
        pytest.raises(
            IntegrityError,
            match="(?s)LED-6: .* does not balance.*ledger_assert_line_entry_balanced",
        ),
        transaction.atomic(),
    ):
        raw_sql(
            "INSERT INTO ledger_journalline (id, entry_id, ledger_account_id, amount_minor) "
            "VALUES (gen_random_uuid(), %s, %s, 500)",
            (posted_entry_id, ledger_account_ids["CASH"]),
        )
        raw_sql("SET CONSTRAINTS ALL IMMEDIATE")

    assert raw_sql(
        "SELECT count(*) FROM ledger_journalline WHERE entry_id = %s", (posted_entry_id,)
    ) == [(2,)]


@pytest.mark.django_db(transaction=True)
def test_unbalanced_entry_is_rejected_at_commit_and_nothing_persists(raw_sql, insert_raw_entry):
    """Fails if LED-6 is not checked at commit, letting an unbalanced entry persist (TEST-DB-3)."""
    with (
        pytest.raises(IntegrityError, match="LED-6: journal entry .* does not balance"),
        transaction.atomic(),
    ):
        entry_id = insert_raw_entry(10_000, -1)

    assert raw_sql("SELECT count(*) FROM ledger_journalentry WHERE id = %s", (entry_id,)) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_balanced_entry_commits_although_unbalanced_between_line_inserts(raw_sql, insert_raw_entry):
    """Fails if LED-6 runs per statement instead of deferred to commit, rejecting valid entries."""
    with transaction.atomic():
        entry_id = insert_raw_entry(10_000, -10_000)

    assert raw_sql(
        "SELECT sum(amount_minor), count(*) FROM ledger_journalline WHERE entry_id = %s",
        (entry_id,),
    ) == [(0, 2)]
