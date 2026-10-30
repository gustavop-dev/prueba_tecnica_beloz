"""Helpers for real-concurrency tests: one thread per operation, each with its own connection."""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from django.db import connection

from core import flows

JOIN_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Outcome:
    value: object = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run_concurrently(*operations: Callable[[], object]) -> list[Outcome]:
    """Start every operation at the same instant (barrier) and collect its result or exception."""
    barrier = threading.Barrier(len(operations))
    outcomes: list[Outcome | None] = [None] * len(operations)

    def worker(index: int, operation: Callable[[], object]) -> None:
        try:
            barrier.wait()
            outcomes[index] = Outcome(value=operation())
        except Exception as exc:  # noqa: BLE001 — the test inspects every outcome
            outcomes[index] = Outcome(error=exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(index, operation), daemon=True)
        for index, operation in enumerate(operations)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT_SECONDS)
    stuck = [thread for thread in threads if thread.is_alive()]
    assert not stuck, f"{len(stuck)} operation(s) still running: possible deadlock"
    return outcomes


def open_account(owner_id: uuid.UUID, deposit_minor: int) -> flows.ProviderReportResult:
    deposit = flows.request_account_opening(owner_id=owner_id, deposit_amount_minor=deposit_minor)
    return flows.apply_provider_report(deposit_id=deposit.id, succeeded=True)


def error_names(outcomes: list[Outcome]) -> list[str]:
    return sorted(type(outcome.error).__name__ for outcome in outcomes if not outcome.ok)


def approved_amount(outcomes: list[Outcome]) -> int:
    return sum(outcome.value.amount_minor for outcome in outcomes if outcome.ok)


def outcome_names(outcomes: list[Outcome]) -> tuple[str, ...]:
    return tuple("OK" if outcome.ok else type(outcome.error).__name__ for outcome in outcomes)
