"""Database helpers shared by the operations core."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, transaction


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
