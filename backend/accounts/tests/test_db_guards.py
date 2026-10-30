"""Database guards on `accounts_creditaccount`, exercised with raw SQL that bypasses Python
(TEST-DB-2). Each rejection asserts the exact constraint PostgreSQL reports."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from accounts.models import CreditAccount
from accounts.tests.conftest import CLOSED_AT

INSERT_SQL = (
    "INSERT INTO accounts_creditaccount (id, owner_id, deposit_id, status, collateral_ratio_bps, "
    "credit_limit_minor, outstanding_minor, created_at, closed_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)"
)


def _insert(*, status, ratio_bps, limit, outstanding, closed_at, deposit_id=None):
    with connection.cursor() as cursor:
        cursor.execute(
            INSERT_SQL,
            [
                uuid4(),
                uuid4(),
                deposit_id or uuid4(),
                status,
                ratio_bps,
                limit,
                outstanding,
                closed_at,
            ],
        )


def _violated_constraint(excinfo) -> str:
    return excinfo.value.__cause__.diag.constraint_name


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "ratio_bps", "limit", "outstanding", "closed_at", "constraint"),
    [
        pytest.param(
            "ACTIVE",
            10_000,
            100,
            -1,
            None,
            "accounts_creditaccount_outstanding_within_limit",
            id="CACHE-3-negative-debt",
        ),
        pytest.param(
            "ACTIVE",
            10_000,
            100,
            101,
            None,
            "accounts_creditaccount_outstanding_within_limit",
            id="CACHE-3-debt-above-limit",
        ),
        pytest.param(
            "ACTIVE",
            10_000,
            -1,
            0,
            None,
            "accounts_creditaccount_credit_limit_non_negative",
            id="negative-limit",
        ),
        pytest.param(
            "ACTIVE",
            0,
            100,
            0,
            None,
            "accounts_creditaccount_collateral_ratio_positive",
            id="zero-ratio",
        ),
        pytest.param(
            "FROZEN",
            10_000,
            100,
            0,
            None,
            "accounts_creditaccount_status_valid",
            id="unknown-status",
        ),
        pytest.param(
            "CLOSED",
            10_000,
            100,
            0,
            None,
            "accounts_creditaccount_closed_at_iff_closed",
            id="closed-without-closed-at",
        ),
        pytest.param(
            "ACTIVE",
            10_000,
            100,
            0,
            CLOSED_AT,
            "accounts_creditaccount_closed_at_iff_closed",
            id="active-with-closed-at",
        ),
        pytest.param(
            "CLOSED",
            10_000,
            100,
            50,
            CLOSED_AT,
            "accounts_creditaccount_closed_has_no_outstanding",
            id="BR-CLO-5-closed-with-debt",
        ),
    ],
)
def test_insert_violating_a_guard_is_rejected(
    status, ratio_bps, limit, outstanding, closed_at, constraint
):
    """Fails if a CHECK on the credit account is dropped or loosened, so a bug or a future endpoint
    could persist negative debt, debt above the limit or a closed account with debt
    (TEST-DB-2, CACHE-3, BR-CLO-5)."""
    with pytest.raises(IntegrityError) as excinfo:
        _insert(
            status=status,
            ratio_bps=ratio_bps,
            limit=limit,
            outstanding=outstanding,
            closed_at=closed_at,
        )

    assert _violated_constraint(excinfo) == constraint


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "limit", "outstanding", "closed_at"),
    [
        pytest.param("ACTIVE", 100, 0, None, id="no-debt"),
        pytest.param("ACTIVE", 100, 100, None, id="debt-equal-to-limit"),
        pytest.param("ACTIVE", 0, 0, None, id="zero-limit"),
        pytest.param("CLOSED", 100, 0, CLOSED_AT, id="closed-without-debt"),
    ],
)
def test_insert_on_the_boundary_of_the_guards_is_accepted(status, limit, outstanding, closed_at):
    """Fails if a CHECK is made stricter than the spec and rejects a valid boundary such as debt
    equal to the limit (CACHE-3, BR-CLO-5)."""
    _insert(
        status=status, ratio_bps=10_000, limit=limit, outstanding=outstanding, closed_at=closed_at
    )

    assert (
        CreditAccount.objects.filter(
            status=status, credit_limit_minor=limit, outstanding_minor=outstanding
        ).count()
        == 1
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("sql", "params", "constraint"),
    [
        pytest.param(
            "UPDATE accounts_creditaccount SET outstanding_minor = %s WHERE id = %s",
            [-1],
            "accounts_creditaccount_outstanding_within_limit",
            id="CACHE-3-debt-below-zero",
        ),
        pytest.param(
            "UPDATE accounts_creditaccount SET outstanding_minor = %s WHERE id = %s",
            [101],
            "accounts_creditaccount_outstanding_within_limit",
            id="CACHE-3-debt-above-limit",
        ),
        pytest.param(
            "UPDATE accounts_creditaccount SET credit_limit_minor = %s WHERE id = %s",
            [39],
            "accounts_creditaccount_outstanding_within_limit",
            id="CACHE-3-limit-below-debt",
        ),
        pytest.param(
            "UPDATE accounts_creditaccount SET status = 'CLOSED', closed_at = %s WHERE id = %s",
            [CLOSED_AT],
            "accounts_creditaccount_closed_has_no_outstanding",
            id="BR-CLO-5-close-with-debt",
        ),
    ],
)
def test_update_violating_a_guard_is_rejected_and_leaves_the_row_unchanged(
    make_account, stored, sql, params, constraint
):
    """Fails if raw SQL can move an existing account outside its guards, e.g. overspend or close
    with debt, bypassing the service validation (TEST-DB-2, CACHE-3, BR-CLO-5)."""
    account = make_account(credit_limit_minor=100, outstanding_minor=40)

    with (
        pytest.raises(IntegrityError) as excinfo,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql, [*params, account.id])

    assert _violated_constraint(excinfo) == constraint
    assert stored(account.id) == {
        "status": "ACTIVE",
        "credit_limit_minor": 100,
        "outstanding_minor": 40,
        "closed_at": None,
    }


@pytest.mark.django_db
def test_second_account_for_the_same_deposit_is_rejected():
    """Fails if two accounts can be opened from one collateral deposit (BR-ACC-2)."""
    deposit_id = uuid4()
    _insert(
        status="ACTIVE",
        ratio_bps=10_000,
        limit=100,
        outstanding=0,
        closed_at=None,
        deposit_id=deposit_id,
    )

    with pytest.raises(IntegrityError) as excinfo:
        _insert(
            status="ACTIVE",
            ratio_bps=10_000,
            limit=100,
            outstanding=0,
            closed_at=None,
            deposit_id=deposit_id,
        )

    assert _violated_constraint(excinfo) == "accounts_creditaccount_deposit_id_key"


@pytest.mark.django_db
def test_account_without_an_explicit_id_is_not_generated():
    """Fails if the model starts generating its own primary key, so an account could be created
    under an id other than the one reserved by the caller (contract §3: no default id)."""
    with pytest.raises(IntegrityError) as excinfo:
        CreditAccount.objects.create(
            owner_id=uuid4(),
            deposit_id=uuid4(),
            collateral_ratio_bps=10_000,
            credit_limit_minor=100,
        )

    assert excinfo.value.__cause__.diag.column_name == "id"
