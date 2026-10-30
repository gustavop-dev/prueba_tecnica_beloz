import dataclasses
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import CreditAccount
from accounts.services import (
    AccountClosed,
    AccountDTO,
    AccountNotFound,
    InsufficientCredit,
    PaymentExceedsDebt,
    close_account,
    decrease_outstanding,
    get_account,
    get_account_for_owner,
    increase_outstanding,
    iter_accounts,
    lock_account,
    open_account,
)
from accounts.tests.conftest import CLOSED_AT

# Non-int or non-positive amounts: programming errors rejected before touching the row.
INVALID_AMOUNTS = [
    pytest.param(0, id="zero"),
    pytest.param(-1, id="negative"),
    pytest.param(True, id="bool"),
    pytest.param("10", id="string"),
    pytest.param(None, id="none"),
]


def _open(**overrides) -> AccountDTO:
    kwargs = {
        "account_id": uuid4(),
        "owner_id": uuid4(),
        "deposit_id": uuid4(),
        "credit_limit_minor": 50_000,
        "collateral_ratio_bps": 10_000,
    } | overrides
    return open_account(**kwargs)


# --- open_account -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_open_account_creates_an_active_account_without_debt(stored):
    """Fails if a new account is not ACTIVE with the given limit and zero debt (BR-DEP-3,
    BR-LIM-5)."""
    account_id, owner_id, deposit_id = uuid4(), uuid4(), uuid4()

    account = open_account(
        account_id=account_id,
        owner_id=owner_id,
        deposit_id=deposit_id,
        credit_limit_minor=50_000,
        collateral_ratio_bps=10_000,
    )

    assert (account.id, account.owner_id, account.deposit_id) == (account_id, owner_id, deposit_id)
    assert (account.status, account.collateral_ratio_bps) == ("ACTIVE", 10_000)
    assert (account.credit_limit_minor, account.outstanding_minor) == (50_000, 0)
    assert account.available_credit_minor == 50_000
    assert account.closed_at is None
    assert stored(account_id) == {
        "status": "ACTIVE",
        "credit_limit_minor": 50_000,
        "outstanding_minor": 0,
        "closed_at": None,
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"credit_limit_minor": -1}, id="negative-limit"),
        pytest.param({"credit_limit_minor": True}, id="bool-limit"),
        pytest.param({"credit_limit_minor": "50000"}, id="string-limit"),
        pytest.param({"collateral_ratio_bps": 0}, id="zero-ratio"),
        pytest.param({"collateral_ratio_bps": True}, id="bool-ratio"),
        pytest.param({"account_id": "not-a-uuid"}, id="string-account-id"),
        pytest.param({"owner_id": None}, id="missing-owner-id"),
        pytest.param({"deposit_id": 7}, id="int-deposit-id"),
    ],
)
def test_open_account_rejects_invalid_arguments_without_writing(overrides):
    """Fails if open_account stops re-validating its inputs (defense in depth, BR-MONEY-4,
    BR-LIM-2) and persists an account from a bool, string or non-positive value."""
    with pytest.raises(ValueError):
        _open(**overrides)

    assert CreditAccount.objects.count() == 0


@pytest.mark.django_db
def test_open_account_rejects_a_second_account_for_the_same_deposit():
    """Fails if the same deposit can open two accounts (BR-ACC-2)."""
    deposit_id = uuid4()
    _open(deposit_id=deposit_id)

    with pytest.raises(IntegrityError), transaction.atomic():
        _open(deposit_id=deposit_id)

    assert CreditAccount.objects.filter(deposit_id=deposit_id).count() == 1


# --- reads --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_account_returns_the_stored_values(make_account):
    """Fails if the DTO stops mirroring the stored account or available credit is miscomputed
    (DEP-3, BR-LIM-5)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=250)

    dto = get_account(account.id)

    assert dto == AccountDTO(
        id=account.id,
        owner_id=account.owner_id,
        deposit_id=account.deposit_id,
        status="ACTIVE",
        collateral_ratio_bps=10_000,
        credit_limit_minor=1_000,
        outstanding_minor=250,
        created_at=account.created_at,
        closed_at=None,
    )
    assert dto.available_credit_minor == 750


@pytest.mark.django_db
def test_account_dto_is_immutable(make_account):
    """Fails if a caller can mutate the returned DTO, which must never act as a writable model
    (DEP-3)."""
    dto = get_account(make_account(outstanding_minor=0).id)

    with pytest.raises(dataclasses.FrozenInstanceError):
        dto.outstanding_minor = 1  # type: ignore[misc]


@pytest.mark.django_db
@pytest.mark.parametrize("read", [get_account, lock_account], ids=["get_account", "lock_account"])
def test_read_of_a_missing_account_raises_account_not_found(read):
    """Fails if an unknown account id leaks a Django DoesNotExist instead of the domain error."""
    with pytest.raises(AccountNotFound):
        read(uuid4())


@pytest.mark.django_db
def test_get_account_for_owner_returns_the_owners_account(make_account):
    """Fails if the owner cannot read their own account (SEC-IDOR-1)."""
    owner_id = uuid4()
    account = make_account(owner_id=owner_id)

    dto = get_account_for_owner(account.id, owner_id)

    assert (dto.id, dto.owner_id) == (account.id, owner_id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "requested_id",
    [
        pytest.param(lambda victim: victim.id, id="another-owners-account"),
        pytest.param(lambda victim: uuid4(), id="missing-account"),
    ],
)
def test_get_account_for_owner_hides_accounts_the_caller_does_not_own(make_account, requested_id):
    """Fails if another customer's account is returned, or is distinguishable from a missing one,
    when looked up by id alone (SEC-IDOR-1, SEC-IDOR-2)."""
    account_id = requested_id(make_account())

    with pytest.raises(AccountNotFound) as excinfo:
        get_account_for_owner(account_id, uuid4())

    assert excinfo.value.args == (str(account_id),)


@pytest.mark.django_db
def test_lock_account_returns_the_stored_values(make_account):
    """Fails if lock_account returns anything but the locked account's current values (CON-2)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=400)

    dto = lock_account(account.id)

    assert (dto.id, dto.outstanding_minor, dto.available_credit_minor) == (account.id, 400, 600)


@pytest.mark.django_db
def test_iter_accounts_yields_every_account(make_account):
    """Fails if reconciliation skips an account or reports a stale cache (ledger.md §7)."""
    first = make_account(outstanding_minor=10)
    second = make_account(outstanding_minor=0, closed=True)

    snapshot = {dto.id: (dto.status, dto.outstanding_minor) for dto in iter_accounts()}

    assert snapshot == {first.id: ("ACTIVE", 10), second.id: ("CLOSED", 0)}


# --- increase_outstanding (purchases) ----------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("amount", "expected_outstanding"),
    [
        pytest.param(1, 401, id="one-cent"),
        pytest.param(600, 1_000, id="exactly-the-available-credit"),
    ],
)
def test_increase_outstanding_adds_the_amount_to_the_debt(
    make_account, stored, amount, expected_outstanding
):
    """Fails if an approved purchase is not added to the cache, or if a purchase equal to the
    available credit is rejected (BR-PUR-2, CACHE-2)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=400)

    dto = increase_outstanding(account.id, amount)

    assert dto.outstanding_minor == expected_outstanding
    assert dto.available_credit_minor == 1_000 - expected_outstanding
    assert stored(account.id)["outstanding_minor"] == expected_outstanding


@pytest.mark.django_db
def test_increase_outstanding_rejects_an_amount_above_the_available_credit(make_account, stored):
    """Fails if a purchase above the available credit is approved, fully or partially
    (BR-PUR-2, INV-1)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=400)

    with pytest.raises(InsufficientCredit):
        increase_outstanding(account.id, 601)

    assert stored(account.id)["outstanding_minor"] == 400


@pytest.mark.django_db
@pytest.mark.parametrize("amount", INVALID_AMOUNTS)
def test_increase_outstanding_rejects_invalid_amounts(make_account, stored, amount):
    """Fails if the service accepts a non-int or non-positive amount (BR-MONEY-4, BR-MONEY-5)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=400)

    with pytest.raises(ValueError):
        increase_outstanding(account.id, amount)

    assert stored(account.id)["outstanding_minor"] == 400


@pytest.mark.django_db
def test_increase_outstanding_only_changes_the_target_account(make_account, stored):
    """Fails if a purchase leaks into another account of the same owner (BR-OWN-2)."""
    owner_id = uuid4()
    target = make_account(owner_id=owner_id, outstanding_minor=0)
    sibling = make_account(owner_id=owner_id, outstanding_minor=0)

    increase_outstanding(target.id, 300)

    assert (stored(target.id)["outstanding_minor"], stored(sibling.id)["outstanding_minor"]) == (
        300,
        0,
    )


# --- decrease_outstanding (payments) -----------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("amount", "expected_outstanding"),
    [
        pytest.param(150, 250, id="partial-payment"),
        pytest.param(400, 0, id="full-payoff"),
    ],
)
def test_decrease_outstanding_subtracts_the_payment(
    make_account, stored, amount, expected_outstanding
):
    """Fails if a payment does not reduce the debt by its amount, or a full payoff is rejected
    (BR-PAY-1)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=400)

    dto = decrease_outstanding(account.id, amount)

    assert dto.outstanding_minor == expected_outstanding
    assert stored(account.id)["outstanding_minor"] == expected_outstanding


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("outstanding", "amount"),
    [
        pytest.param(400, 401, id="BR-PAY-2-above-debt"),
        pytest.param(0, 1, id="BR-PAY-3-no-debt"),
    ],
)
def test_decrease_outstanding_rejects_a_payment_above_the_debt(
    make_account, stored, outstanding, amount
):
    """Fails if a payment larger than the debt is accepted, leaving a credit balance
    (BR-PAY-2, BR-PAY-3)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=outstanding)

    with pytest.raises(PaymentExceedsDebt):
        decrease_outstanding(account.id, amount)

    assert stored(account.id)["outstanding_minor"] == outstanding


@pytest.mark.django_db
@pytest.mark.parametrize("amount", INVALID_AMOUNTS)
def test_decrease_outstanding_rejects_invalid_amounts(make_account, stored, amount):
    """Fails if the service accepts a non-int or non-positive payment (BR-MONEY-4, BR-MONEY-5)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=400)

    with pytest.raises(ValueError):
        decrease_outstanding(account.id, amount)

    assert stored(account.id)["outstanding_minor"] == 400


# --- close_account ------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "outstanding",
    [pytest.param(0, id="no-debt"), pytest.param(300, id="with-debt")],
)
def test_close_account_captures_the_debt_and_leaves_it_at_zero(make_account, stored, outstanding):
    """Fails if closure loses the debt it must settle against the collateral, leaves debt behind,
    or does not mark the account CLOSED with its closing time (BR-CLO-1, BR-CLO-5)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=outstanding)
    before = timezone.now()

    closure = close_account(account.id)

    after = timezone.now()
    assert closure.debt_at_closure_minor == outstanding
    assert (closure.account.status, closure.account.outstanding_minor) == ("CLOSED", 0)
    assert before <= closure.account.closed_at <= after
    assert stored(account.id) == {
        "status": "CLOSED",
        "credit_limit_minor": 1_000,
        "outstanding_minor": 0,
        "closed_at": closure.account.closed_at,
    }


# --- operations on a CLOSED account (TEST-SM-1, TEST-SM-2) --------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda account_id: increase_outstanding(account_id, 1), id="purchase"),
        pytest.param(lambda account_id: decrease_outstanding(account_id, 1), id="payment"),
        pytest.param(close_account, id="closure-CLOSED-to-CLOSED"),
    ],
)
def test_operation_on_a_closed_account_is_rejected_and_changes_nothing(
    make_account, stored, operation
):
    """Fails if a CLOSED account accepts a purchase, a payment or a second closure, or if the
    rejection modifies the row (BR-CLO-6, TEST-SM-1, TEST-SM-2)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=0, closed=True)

    with pytest.raises(AccountClosed):
        operation(account.id)

    assert stored(account.id) == {
        "status": "CLOSED",
        "credit_limit_minor": 1_000,
        "outstanding_minor": 0,
        "closed_at": CLOSED_AT,
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda account_id: increase_outstanding(account_id, 1), id="purchase"),
        pytest.param(lambda account_id: decrease_outstanding(account_id, 1), id="payment"),
        pytest.param(close_account, id="closure"),
    ],
)
def test_mutation_of_a_missing_account_raises_account_not_found(operation):
    """Fails if a mutation on an unknown account leaks DoesNotExist instead of the domain error."""
    with pytest.raises(AccountNotFound):
        operation(uuid4())


# --- transaction requirement (CON-1, MOD-4) -----------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("name", "operation"),
    [
        pytest.param("lock_account", lock_account, id="lock_account"),
        pytest.param(
            "increase_outstanding",
            lambda account_id: increase_outstanding(account_id, 1),
            id="increase_outstanding",
        ),
        pytest.param(
            "decrease_outstanding",
            lambda account_id: decrease_outstanding(account_id, 1),
            id="decrease_outstanding",
        ),
        pytest.param("close_account", close_account, id="close_account"),
    ],
)
def test_mutating_service_refuses_to_run_outside_a_transaction(
    make_account, stored, name, operation
):
    """Fails if a service locks or writes in autocommit mode, where the row lock is released at
    once and the write cannot roll back together with the journal entry (CON-1, CON-2, CACHE-2)."""
    account = make_account(credit_limit_minor=1_000, outstanding_minor=400)

    with pytest.raises(RuntimeError, match=f"^{name} must run inside transaction.atomic$"):
        operation(account.id)

    assert stored(account.id) == {
        "status": "ACTIVE",
        "credit_limit_minor": 1_000,
        "outstanding_minor": 400,
        "closed_at": None,
    }


@pytest.mark.django_db(transaction=True)
def test_open_account_refuses_to_run_outside_a_transaction():
    """Fails if an account can be created outside the transaction that confirms its deposit,
    leaving an account without its deposit on failure (BR-DEP-3, BR-DEP-5, MOD-4)."""
    with pytest.raises(RuntimeError, match="^open_account must run inside transaction.atomic$"):
        _open()

    assert CreditAccount.objects.count() == 0
