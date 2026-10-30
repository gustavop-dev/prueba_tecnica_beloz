import pytest
from django.conf import settings
from django.db import connection


def test_default_database_engine_is_postgresql():
    """Fails if the configured engine is anything other than PostgreSQL (STACK-3)."""
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


@pytest.mark.django_db
def test_tests_run_against_a_live_postgresql_connection():
    """Fails if the test suite silently runs on a non-PostgreSQL backend (TEST-2)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT version()")
        (version,) = cursor.fetchone()

    assert connection.vendor == "postgresql"
    assert version.startswith("PostgreSQL 18.")
