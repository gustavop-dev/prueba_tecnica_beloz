"""Database guards on idempotency records, so the rules hold even outside Python.

IDEM-7 (INV-4): a record is written in the same transaction as the effect and is never visible
half-done to another transaction.
  - Deferred constraint trigger (`DEFERRABLE INITIALLY DEFERRED`) on insert and update: at commit
    the record must have `response_status`, `response_body` and `completed_at`. A deferred trigger
    receives the row as it was when the event fired, so it re-reads the current row.

IDEM-6 / IDEM-12: the stored response is final and records never expire.
  - BEFORE UPDATE ... FOR EACH ROW: a completed record (`OLD.response_status IS NOT NULL`) is
    immutable. While in flight, the only allowed change is completing it: the identity columns
    (id, principal, endpoint, key, request hash, creation time) never change.
  - BEFORE DELETE ... FOR EACH ROW: always raises.
  - BEFORE TRUNCATE ... FOR EACH STATEMENT: raises unless the current role is a superuser, exactly
    like LED-4. A superuser can disable any trigger anyway; the exemption only lets Django's test
    flush run. The application role in production is not a superuser and can never truncate.

Errors use SQLSTATE class 23 (integrity constraint violation), surfaced by Django as
`django.db.utils.IntegrityError`. Messages start with the rule ID.
"""

from django.db import migrations

IMMUTABLE_SQL = """
CREATE FUNCTION idempotency_reject_record_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.response_status IS NOT NULL THEN
        RAISE EXCEPTION 'IDEM-6: idempotency record % is completed and immutable; UPDATE is not allowed',
            OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.principal_id IS DISTINCT FROM OLD.principal_id
        OR NEW.endpoint IS DISTINCT FROM OLD.endpoint
        OR NEW.key IS DISTINCT FROM OLD.key
        OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'IDEM-4: the identity of idempotency record % cannot change', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION idempotency_reject_record_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'IDEM-12: idempotency records never expire; DELETE is not allowed'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE FUNCTION idempotency_reject_record_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false) THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'IDEM-12: idempotency records never expire; TRUNCATE is not allowed'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE TRIGGER idempotency_record_immutable_when_completed
    BEFORE UPDATE ON idempotency_idempotencyrecord
    FOR EACH ROW EXECUTE FUNCTION idempotency_reject_record_update();

CREATE TRIGGER idempotency_record_no_delete
    BEFORE DELETE ON idempotency_idempotencyrecord
    FOR EACH ROW EXECUTE FUNCTION idempotency_reject_record_delete();

CREATE TRIGGER idempotency_record_no_truncate
    BEFORE TRUNCATE ON idempotency_idempotencyrecord
    FOR EACH STATEMENT EXECUTE FUNCTION idempotency_reject_record_truncate();
"""

IMMUTABLE_REVERSE_SQL = """
DROP TRIGGER idempotency_record_no_truncate ON idempotency_idempotencyrecord;
DROP TRIGGER idempotency_record_no_delete ON idempotency_idempotencyrecord;
DROP TRIGGER idempotency_record_immutable_when_completed ON idempotency_idempotencyrecord;
DROP FUNCTION idempotency_reject_record_truncate();
DROP FUNCTION idempotency_reject_record_delete();
DROP FUNCTION idempotency_reject_record_update();
"""

COMPLETED_AT_COMMIT_SQL = """
CREATE FUNCTION idempotency_assert_record_completed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    current_record idempotency_idempotencyrecord%ROWTYPE;
BEGIN
    SELECT * INTO current_record FROM idempotency_idempotencyrecord WHERE id = NEW.id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF current_record.response_status IS NULL
        OR current_record.response_body IS NULL
        OR current_record.completed_at IS NULL THEN
        RAISE EXCEPTION 'IDEM-7: idempotency record % would commit without its response',
            NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER idempotency_record_completed_at_commit
    AFTER INSERT OR UPDATE ON idempotency_idempotencyrecord
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION idempotency_assert_record_completed();
"""

COMPLETED_AT_COMMIT_REVERSE_SQL = """
DROP TRIGGER idempotency_record_completed_at_commit ON idempotency_idempotencyrecord;
DROP FUNCTION idempotency_assert_record_completed();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("idempotency", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(IMMUTABLE_SQL, IMMUTABLE_REVERSE_SQL),
        migrations.RunSQL(COMPLETED_AT_COMMIT_SQL, COMPLETED_AT_COMMIT_REVERSE_SQL),
    ]
