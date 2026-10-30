import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("extra_env", "expected"), [({}, "prefer"), ({"POSTGRES_SSLMODE": "require"}, "require")]
)
def test_database_tls_mode_comes_from_the_environment(extra_env, expected):
    """Fails if POSTGRES_SSLMODE cannot require TLS to the database, as RDS enforces in AWS (ARCH-2)."""
    child_env = {key: val for key, val in os.environ.items() if key != "POSTGRES_SSLMODE"}
    child_env.update(extra_env)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config import settings; print(settings.DATABASES['default']['OPTIONS']['sslmode'])",
        ],
        cwd=BACKEND_DIR,
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == expected


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
