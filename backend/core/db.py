"""Database helpers shared by the operations core."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.conf import settings
from django.db import OperationalError, connection, transaction

LOCK_NOT_AVAILABLE = "55P03"


@contextmanager
def money_transaction() -> Iterator[None]:
    """`transaction.atomic()` that never waits for a lock longer than `DB_LOCK_TIMEOUT_MS` (CON-9).

    `SET LOCAL` lasts until the end of the outermost transaction, so nesting is harmless. A timeout
    raises `OperationalError` (SQLSTATE 55P03) and rolls the whole operation back.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [_lock_timeout()])
        yield


def is_lock_timeout(exc: BaseException) -> bool:
    return isinstance(exc, OperationalError) and (
        getattr(exc.__cause__, "sqlstate", None) == LOCK_NOT_AVAILABLE
    )


def _lock_timeout() -> str:
    return f"{int(settings.DB_LOCK_TIMEOUT_MS)}ms"


@contextmanager
def read_only_snapshot() -> Iterator[None]:
    """Run reads in one consistent snapshot.

    When it opens its own transaction, it runs as read-only REPEATABLE READ so every query sees the
    same committed state. Inside a caller's transaction it reuses that transaction's snapshot.
    """
    owns_transaction = not connection.in_atomic_block
    with transaction.atomic():
        if owns_transaction:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield
