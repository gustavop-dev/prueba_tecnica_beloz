"""Shared fixtures for the `collateral` tests: a consistent-row factory and thread helpers."""

import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from collateral.models import CollateralDeposit, DepositStatus

AMOUNT_MINOR = 10_000
PARTIAL_APPLIED_MINOR = 2_500
FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

# Column values that satisfy every database constraint for each status.
_STATUS_FIELDS: dict[DepositStatus, Callable[[int, datetime], dict[str, object]]] = {
    DepositStatus.PENDING: lambda amount, now: {},
    DepositStatus.CONFIRMED: lambda amount, now: {"confirmed_at": now},
    DepositStatus.FAILED: lambda amount, now: {"failed_at": now},
    DepositStatus.RELEASED: lambda amount, now: {
        "confirmed_at": now,
        "settled_at": now,
        "applied_minor": 0,
        "refunded_minor": amount,
    },
    DepositStatus.PARTIALLY_APPLIED: lambda amount, now: {
        "confirmed_at": now,
        "settled_at": now,
        "applied_minor": PARTIAL_APPLIED_MINOR,
        "refunded_minor": amount - PARTIAL_APPLIED_MINOR,
    },
    DepositStatus.APPLIED: lambda amount, now: {
        "confirmed_at": now,
        "settled_at": now,
        "applied_minor": amount,
        "refunded_minor": 0,
    },
}


@pytest.fixture
def make_deposit() -> Callable[..., CollateralDeposit]:
    """Insert a deposit directly in any status, with columns consistent with that status."""

    def _make(
        status: DepositStatus = DepositStatus.PENDING,
        *,
        amount_minor: int = AMOUNT_MINOR,
        owner_id: uuid.UUID | None = None,
    ) -> CollateralDeposit:
        return CollateralDeposit.objects.create(
            owner_id=owner_id or uuid.uuid4(),
            amount_minor=amount_minor,
            status=status,
            **_STATUS_FIELDS[status](amount_minor, timezone.now()),
        )

    return _make


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Make `timezone.now()` return FIXED_NOW, for services and `auto_now_add` alike."""
    monkeypatch.setattr(timezone, "now", lambda: FIXED_NOW)
    return FIXED_NOW


def stored_row(deposit_id: uuid.UUID) -> dict[str, object]:
    """Every column of the stored deposit row, read fresh from the database."""
    return CollateralDeposit.objects.filter(id=deposit_id).values().get()


class BackgroundTransaction:
    """Run `fn` inside its own `transaction.atomic` on a separate thread and connection."""

    def __init__(self, fn: Callable[[], object]) -> None:
        self.result: object = None
        self.error: BaseException | None = None
        self._fn = fn
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            with transaction.atomic():
                self.result = self._fn()
        except BaseException as exc:
            self.error = exc
        finally:
            connections.close_all()

    def join(self, timeout_seconds: int = 10) -> None:
        self._thread.join(timeout_seconds)
        assert not self._thread.is_alive(), "background transaction did not finish"


def wait_for_lock_waiters(expected: int, timeout_seconds: int = 10) -> None:
    """Poll until `expected` database sessions are waiting to acquire a lock."""
    deadline = time.monotonic() + timeout_seconds
    waiting = -1
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            # pg_locks is read live; pg_stat_activity would stay frozen for this transaction.
            cursor.execute("SELECT count(DISTINCT pid) FROM pg_locks WHERE NOT granted")
            (waiting,) = cursor.fetchone()
        if waiting == expected:
            return
    raise AssertionError(f"expected {expected} lock waiters, saw {waiting}")


def try_lock_nowait(deposit_id: uuid.UUID) -> str | None:
    """From another connection, try `FOR UPDATE NOWAIT` on the row; return the SQLSTATE or None."""
    outcome: dict[str, str | None] = {"sqlstate": None}

    def _attempt() -> None:
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM collateral_collateraldeposit WHERE id = %s FOR UPDATE NOWAIT",
                    [deposit_id],
                )
        except Exception as exc:
            outcome["sqlstate"] = exc.__cause__.sqlstate
        finally:
            connections.close_all()

    thread = threading.Thread(target=_attempt, daemon=True)
    thread.start()
    thread.join(10)
    return outcome["sqlstate"]
