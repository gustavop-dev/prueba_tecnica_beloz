"""Environment variable helpers. Secrets come only from the environment (SEC-SECRET-1)."""

import os

from django.core.exceptions import ImproperlyConfigured


def required(name: str) -> str:
    """Return a required variable, failing closed when it is missing or empty (SEC-SECRET-3)."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Required environment variable {name} is not set.")
    return value


def optional(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in optional(name, default).split(",") if item.strip()]
