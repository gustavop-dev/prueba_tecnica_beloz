import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config import env

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_required_env_fails_closed_when_missing(monkeypatch):
    """Fails if a missing secret is accepted instead of stopping startup (SEC-SECRET-3)."""
    monkeypatch.delenv("SOME_SECRET", raising=False)

    with pytest.raises(ImproperlyConfigured, match="SOME_SECRET"):
        env.required("SOME_SECRET")


@pytest.mark.parametrize("value", ["", "   "])
def test_required_env_fails_closed_when_blank(monkeypatch, value):
    """Fails if a blank secret is treated as configured (SEC-SECRET-3)."""
    monkeypatch.setenv("SOME_SECRET", value)

    with pytest.raises(ImproperlyConfigured, match="SOME_SECRET"):
        env.required("SOME_SECRET")


def test_required_env_returns_the_configured_value(monkeypatch):
    """Fails if a configured secret is altered or dropped when read."""
    monkeypatch.setenv("SOME_SECRET", "s3cret")

    assert env.required("SOME_SECRET") == "s3cret"


@pytest.mark.parametrize("missing", ["DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "POSTGRES_HOST"])
def test_settings_refuse_to_load_without_a_required_secret(missing):
    """Fails if Django can start with a required secret missing (SEC-SECRET-3)."""
    child_env = {k: v for k, v in os.environ.items() if k != missing}

    result = subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=BACKEND_DIR,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert f"Required environment variable {missing} is not set." in result.stderr
