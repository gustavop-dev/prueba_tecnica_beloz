"""Database guards of `CollateralDeposit`, exercised with raw SQL that bypasses Python.

Each violation breaks exactly one constraint, so the reported constraint name identifies the guard
that caught it (BR-MONEY-5, BR-CLO-1..4, state-machines.md §1).
"""

import uuid
from datetime import UTC, datetime

import pytest
from django.db import connection
from django.db.utils import IntegrityError
from psycopg import sql

from collateral.models import DepositStatus

S = DepositStatus
CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def _execute(query: str | sql.Composed, params: list[object]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(query, params)


def _update_raw(deposit_id: uuid.UUID, changes: dict[str, object]) -> None:
    """UPDATE the deposit row with plain SQL, bypassing the model and the services."""
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
    )
    query = sql.SQL("UPDATE collateral_collateraldeposit SET {} WHERE id = %s").format(assignments)
    _execute(query, [*changes.values(), deposit_id])


@pytest.mark.parametrize("amount_minor", [0, -1])
def test_database_rejects_a_deposit_with_a_non_positive_amount(amount_minor):
    """Fails if PostgreSQL stores a zero or negative deposit amount (BR-MONEY-5)."""
    with pytest.raises(IntegrityError) as exc_info:
        _execute(
            "INSERT INTO collateral_collateraldeposit "
            "(id, owner_id, reserved_account_id, amount_minor, status, created_at) "
            "VALUES (%s, %s, %s, %s, 'PENDING', now())",
            [uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), amount_minor],
        )

    diag = exc_info.value.__cause__.diag
    assert (diag.sqlstate, diag.constraint_name) == (
        CHECK_VIOLATION,
        "collateral_deposit_amount_positive",
    )


@pytest.mark.parametrize(
    ("status", "changes", "constraint"),
    [
        (S.PENDING, {"status": "SETTLED"}, "collateral_deposit_status_valid"),
        (
            S.CONFIRMED,
            {"status": "RELEASED", "settled_at": NOW},
            "collateral_deposit_settlement_amounts_iff_settled",
        ),
        (
            S.PENDING,
            {"applied_minor": 0, "refunded_minor": 10_000},
            "collateral_deposit_settlement_amounts_iff_settled",
        ),
        (
            S.CONFIRMED,
            {"applied_minor": 10_000, "refunded_minor": 0},
            "collateral_deposit_settlement_amounts_iff_settled",
        ),
        (
            S.APPLIED,
            {"refunded_minor": None},
            "collateral_deposit_settlement_amounts_iff_settled",
        ),
        (
            S.PARTIALLY_APPLIED,
            {"refunded_minor": 7_499},
            "collateral_deposit_settlement_sums_to_amount",
        ),
        (
            S.PARTIALLY_APPLIED,
            {"refunded_minor": 7_501},
            "collateral_deposit_settlement_sums_to_amount",
        ),
        (
            S.RELEASED,
            {"applied_minor": 1, "refunded_minor": 9_999},
            "collateral_deposit_released_applies_nothing",
        ),
        (
            S.APPLIED,
            {"applied_minor": 9_999, "refunded_minor": 1},
            "collateral_deposit_applied_refunds_nothing",
        ),
        (
            S.PARTIALLY_APPLIED,
            {"applied_minor": 0, "refunded_minor": 10_000},
            "collateral_deposit_partial_applies_strictly_between",
        ),
        (
            S.PARTIALLY_APPLIED,
            {"applied_minor": 10_000, "refunded_minor": 0},
            "collateral_deposit_partial_applies_strictly_between",
        ),
        (
            S.PARTIALLY_APPLIED,
            {"applied_minor": -1, "refunded_minor": 10_001},
            "collateral_deposit_partial_applies_strictly_between",
        ),
        (S.CONFIRMED, {"confirmed_at": None}, "collateral_deposit_confirmed_at_iff_confirmed"),
        (S.APPLIED, {"confirmed_at": None}, "collateral_deposit_confirmed_at_iff_confirmed"),
        (S.PENDING, {"confirmed_at": NOW}, "collateral_deposit_confirmed_at_iff_confirmed"),
        (S.FAILED, {"failed_at": None}, "collateral_deposit_failed_at_iff_failed"),
        (S.CONFIRMED, {"failed_at": NOW}, "collateral_deposit_failed_at_iff_failed"),
        (S.RELEASED, {"settled_at": None}, "collateral_deposit_settled_at_iff_settled"),
        (S.CONFIRMED, {"settled_at": NOW}, "collateral_deposit_settled_at_iff_settled"),
    ],
)
def test_database_rejects_an_inconsistent_deposit_row(status, changes, constraint, make_deposit):
    """Fails if PostgreSQL accepts a deposit row whose settlement or state data is inconsistent.

    Guards BR-CLO-1 (applied + refunded = deposit), BR-CLO-2 (full refund only for debt 0),
    BR-CLO-3/4 (partial application strictly inside the deposit) and the status timestamps.
    """
    deposit = make_deposit(status)

    with pytest.raises(IntegrityError) as exc_info:
        _update_raw(deposit.id, changes)

    diag = exc_info.value.__cause__.diag
    assert (diag.sqlstate, diag.constraint_name) == (CHECK_VIOLATION, constraint)


def test_database_rejects_a_reserved_account_id_shared_by_two_deposits(make_deposit):
    """Fails if two deposits can reserve the same future account id (BR-ACC-2, api.md §2)."""
    first = make_deposit()
    second = make_deposit()

    with pytest.raises(IntegrityError) as exc_info:
        _execute(
            "UPDATE collateral_collateraldeposit SET reserved_account_id = %s WHERE id = %s",
            [first.reserved_account_id, second.id],
        )

    diag = exc_info.value.__cause__.diag
    assert (diag.sqlstate, diag.constraint_name) == (
        UNIQUE_VIOLATION,
        "collateral_collateraldeposit_reserved_account_id_key",
    )
