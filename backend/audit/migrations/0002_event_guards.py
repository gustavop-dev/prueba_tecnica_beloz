"""Database guards on audit events, so the audit log stays complete even outside Python.

AUD-3 (INV-6): the audit log is append-only, exactly like the journal (LED-4).
  - BEFORE UPDATE OR DELETE ... FOR EACH ROW trigger always raises.
  - BEFORE TRUNCATE ... FOR EACH STATEMENT trigger raises unless the current role is a superuser.
    A superuser can disable any trigger anyway; the exemption only lets Django's test flush run.
    The application role in production is not a superuser and can never truncate.

Errors use SQLSTATE class 23 (integrity constraint violation), surfaced by Django as
`django.db.utils.IntegrityError`. Messages start with the rule ID.
"""

from django.db import migrations

APPEND_ONLY_SQL = """
CREATE FUNCTION audit_reject_event_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AUD-3: % is append-only; % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE FUNCTION audit_reject_event_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false) THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'AUD-3: % is append-only; TRUNCATE is not allowed', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE TRIGGER audit_auditevent_append_only
    BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_reject_event_mutation();

CREATE TRIGGER audit_auditevent_no_truncate
    BEFORE TRUNCATE ON audit_auditevent
    FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_event_truncate();
"""

APPEND_ONLY_REVERSE_SQL = """
DROP TRIGGER audit_auditevent_no_truncate ON audit_auditevent;
DROP TRIGGER audit_auditevent_append_only ON audit_auditevent;
DROP FUNCTION audit_reject_event_truncate();
DROP FUNCTION audit_reject_event_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(APPEND_ONLY_SQL, APPEND_ONLY_REVERSE_SQL),
    ]
