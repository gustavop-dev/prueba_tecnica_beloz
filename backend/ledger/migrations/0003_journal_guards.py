"""Database guards on the journal, so the ledger rules hold even outside Python.

LED-4 (INV-3): journal entries and lines are append-only.
  - BEFORE UPDATE OR DELETE ... FOR EACH ROW triggers always raise.
  - BEFORE TRUNCATE ... FOR EACH STATEMENT triggers raise unless the current role is a
    superuser. A superuser can disable any trigger anyway; the exemption only lets Django's
    test flush run. The application role in production is not a superuser and can never
    truncate.

LED-6 (INV-2): every journal entry balances, checked at commit.
  - Deferred constraint trigger on line insert: the entry's lines sum to 0.
  - Deferred constraint trigger on entry insert: the entry has at least 2 lines (LED-1) and
    they sum to 0 (this also catches an entry committed with no lines at all).
  Update and delete are impossible (LED-4), so insert is the only event to check.

Errors use SQLSTATE class 23 (integrity constraint violation), surfaced by Django as
`django.db.utils.IntegrityError`. Messages start with the rule ID.
"""

from django.db import migrations

APPEND_ONLY_SQL = """
CREATE FUNCTION ledger_reject_journal_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'LED-4: % is append-only; % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE FUNCTION ledger_reject_journal_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false) THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'LED-4: % is append-only; TRUNCATE is not allowed', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE TRIGGER ledger_journalentry_append_only
    BEFORE UPDATE OR DELETE ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_reject_journal_mutation();

CREATE TRIGGER ledger_journalline_append_only
    BEFORE UPDATE OR DELETE ON ledger_journalline
    FOR EACH ROW EXECUTE FUNCTION ledger_reject_journal_mutation();

CREATE TRIGGER ledger_journalentry_no_truncate
    BEFORE TRUNCATE ON ledger_journalentry
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_reject_journal_truncate();

CREATE TRIGGER ledger_journalline_no_truncate
    BEFORE TRUNCATE ON ledger_journalline
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_reject_journal_truncate();
"""

APPEND_ONLY_REVERSE_SQL = """
DROP TRIGGER ledger_journalline_no_truncate ON ledger_journalline;
DROP TRIGGER ledger_journalentry_no_truncate ON ledger_journalentry;
DROP TRIGGER ledger_journalline_append_only ON ledger_journalline;
DROP TRIGGER ledger_journalentry_append_only ON ledger_journalentry;
DROP FUNCTION ledger_reject_journal_truncate();
DROP FUNCTION ledger_reject_journal_mutation();
"""

BALANCED_ENTRY_SQL = """
CREATE FUNCTION ledger_assert_line_entry_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    total numeric;
BEGIN
    SELECT COALESCE(SUM(amount_minor), 0) INTO total
    FROM ledger_journalline WHERE entry_id = NEW.entry_id;
    IF total <> 0 THEN
        RAISE EXCEPTION 'LED-6: journal entry % does not balance (sum of lines = %)',
            NEW.entry_id, total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION ledger_assert_entry_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    line_count bigint;
    total numeric;
BEGIN
    SELECT COUNT(*), COALESCE(SUM(amount_minor), 0) INTO line_count, total
    FROM ledger_journalline WHERE entry_id = NEW.id;
    IF line_count < 2 THEN
        RAISE EXCEPTION 'LED-6: journal entry % has % line(s); at least 2 are required',
            NEW.id, line_count
            USING ERRCODE = 'check_violation';
    END IF;
    IF total <> 0 THEN
        RAISE EXCEPTION 'LED-6: journal entry % does not balance (sum of lines = %)',
            NEW.id, total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER ledger_journalline_entry_balanced
    AFTER INSERT ON ledger_journalline
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_assert_line_entry_balanced();

CREATE CONSTRAINT TRIGGER ledger_journalentry_balanced
    AFTER INSERT ON ledger_journalentry
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_assert_entry_balanced();
"""

BALANCED_ENTRY_REVERSE_SQL = """
DROP TRIGGER ledger_journalentry_balanced ON ledger_journalentry;
DROP TRIGGER ledger_journalline_entry_balanced ON ledger_journalline;
DROP FUNCTION ledger_assert_entry_balanced();
DROP FUNCTION ledger_assert_line_entry_balanced();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0002_global_accounts"),
    ]

    operations = [
        migrations.RunSQL(APPEND_ONLY_SQL, APPEND_ONLY_REVERSE_SQL),
        migrations.RunSQL(BALANCED_ENTRY_SQL, BALANCED_ENTRY_REVERSE_SQL),
    ]
