"""Public services of the `collateral` app (BR-DEP, BR-CLO-1..4, state-machines.md §1)."""

import dataclasses
import itertools
import uuid

import pytest

from collateral import services
from collateral.models import CollateralDeposit, DepositStatus
from collateral.services import (
    DepositDTO,
    DepositNotFound,
    DepositNotPending,
    InvalidTransition,
    confirm_deposit,
    create_pending_deposit,
    fail_deposit,
    get_deposit,
    get_deposit_for_owner,
    lock_deposit,
    settle_on_closure,
)
from collateral.states import TRANSITIONS
from collateral.tests.conftest import AMOUNT_MINOR, FIXED_NOW, stored_row

S = DepositStatus

# The service that drives a deposit towards each reachable target status, with
# AMOUNT_MINOR = 10_000 and the closure guards of state-machines.md §1.
DRIVERS = {
    S.CONFIRMED: confirm_deposit,
    S.FAILED: fail_deposit,
    S.RELEASED: lambda deposit_id: settle_on_closure(deposit_id, 0),
    S.PARTIALLY_APPLIED: lambda deposit_id: settle_on_closure(deposit_id, 2_500),
    S.APPLIED: lambda deposit_id: settle_on_closure(deposit_id, AMOUNT_MINOR),
}
# Target PENDING has no service: creation is the only way in (covered in test_states.py).
SERVICE_PAIRS = [(src, dst) for src, dst in itertools.product(S, DRIVERS)]
ALLOWED_SERVICE_PAIRS = [(src, dst) for src, dst in SERVICE_PAIRS if dst in TRANSITIONS[src]]
FORBIDDEN_SERVICE_PAIRS = [(src, dst) for src, dst in SERVICE_PAIRS if dst not in TRANSITIONS[src]]

pytestmark = pytest.mark.django_db


# --- create_pending_deposit -------------------------------------------------------------------


def test_create_pending_deposit_stores_a_pending_deposit_with_no_settlement(frozen_now):
    """Fails if a new deposit is not PENDING or carries settlement data (BR-DEP-1)."""
    owner_id = uuid.uuid4()

    dto = create_pending_deposit(owner_id=owner_id, amount_minor=12_345)

    assert stored_row(dto.id) == {
        "id": dto.id,
        "owner_id": owner_id,
        "reserved_account_id": dto.reserved_account_id,
        "amount_minor": 12_345,
        "status": "PENDING",
        "applied_minor": None,
        "refunded_minor": None,
        "created_at": FIXED_NOW,
        "confirmed_at": None,
        "failed_at": None,
        "settled_at": None,
    }


def test_create_pending_deposit_returns_a_frozen_dto_mirroring_the_row():
    """Fails if the service leaks a mutable model instance instead of a frozen DTO (DEP-3)."""
    dto = create_pending_deposit(owner_id=uuid.uuid4(), amount_minor=500)

    assert type(dto) is DepositDTO
    assert dataclasses.asdict(dto) == stored_row(dto.id)
    with pytest.raises(dataclasses.FrozenInstanceError):
        dto.status = "CONFIRMED"


def test_create_pending_deposit_reserves_a_distinct_account_id_per_deposit():
    """Fails if two openings of the same owner share a reserved account id (BR-OWN-1, api.md §2)."""
    owner_id = uuid.uuid4()

    first = create_pending_deposit(owner_id=owner_id, amount_minor=1_000)
    second = create_pending_deposit(owner_id=owner_id, amount_minor=1_000)

    assert len({first.id, first.reserved_account_id, second.id, second.reserved_account_id}) == 4


@pytest.mark.parametrize("amount_minor", [0, -1, True, "1000", None])
def test_create_pending_deposit_rejects_non_positive_or_non_int_amounts(amount_minor):
    """Fails if a zero, negative, bool, str or None amount creates a deposit (BR-MONEY-4/5)."""
    with pytest.raises(ValueError, match="amount_minor"):
        create_pending_deposit(owner_id=uuid.uuid4(), amount_minor=amount_minor)

    assert CollateralDeposit.objects.count() == 0


def test_create_pending_deposit_rejects_an_owner_id_that_is_not_a_uuid():
    """Fails if a non-UUID owner reference is stored (MOD-3)."""
    with pytest.raises(ValueError, match="owner_id"):
        create_pending_deposit(owner_id=str(uuid.uuid4()), amount_minor=1_000)

    assert CollateralDeposit.objects.count() == 0


# --- reads ------------------------------------------------------------------------------------


@pytest.mark.parametrize("read", [get_deposit, lock_deposit])
def test_reads_return_the_stored_deposit(read, make_deposit):
    """Fails if get/lock return another row or stale values (DEP-3)."""
    deposit = make_deposit(S.CONFIRMED)

    dto = read(deposit.id)

    assert dataclasses.asdict(dto) == stored_row(deposit.id)


def test_get_deposit_for_owner_returns_the_owners_deposit(make_deposit):
    """Fails if an owner cannot read their own deposit (BR-OWN-2)."""
    owner_id = uuid.uuid4()
    deposit = make_deposit(owner_id=owner_id)

    dto = get_deposit_for_owner(deposit.id, owner_id)

    assert (dto.id, dto.owner_id) == (deposit.id, owner_id)


def test_get_deposit_for_owner_hides_another_owners_deposit(make_deposit):
    """Fails if a customer can read a deposit that belongs to someone else (BR-OWN-2)."""
    deposit = make_deposit(owner_id=uuid.uuid4())

    with pytest.raises(DepositNotFound) as exc_info:
        get_deposit_for_owner(deposit.id, uuid.uuid4())

    assert exc_info.value.deposit_id == deposit.id


@pytest.mark.parametrize(
    "call",
    [
        get_deposit,
        lock_deposit,
        lambda deposit_id: get_deposit_for_owner(deposit_id, uuid.uuid4()),
        confirm_deposit,
        fail_deposit,
        lambda deposit_id: settle_on_closure(deposit_id, 0),
    ],
    ids=["get", "lock", "get_for_owner", "confirm", "fail", "settle"],
)
def test_unknown_deposit_id_raises_deposit_not_found(call):
    """Fails if a missing deposit is reported as anything but DepositNotFound (api.md 404)."""
    missing_id = uuid.uuid4()

    with pytest.raises(DepositNotFound) as exc_info:
        call(missing_id)

    assert exc_info.value.deposit_id == missing_id


# --- transitions (TEST-SM-1) ------------------------------------------------------------------


@pytest.mark.parametrize(("current", "target"), ALLOWED_SERVICE_PAIRS)
def test_service_performs_every_documented_transition(current, target, make_deposit):
    """Fails if a transition of state-machines.md §1 is refused by its service (INV-5)."""
    deposit = make_deposit(current)

    dto = DRIVERS[target](deposit.id)

    assert (dto.status, stored_row(deposit.id)["status"]) == (target.value, target.value)


@pytest.mark.parametrize(("current", "target"), FORBIDDEN_SERVICE_PAIRS)
def test_service_rejects_every_undocumented_transition_and_keeps_the_row(
    current, target, make_deposit
):
    """Fails if a service performs a transition outside state-machines.md §1 (TEST-SM-1, INV-5)."""
    deposit = make_deposit(current)
    before = stored_row(deposit.id)

    with pytest.raises(InvalidTransition) as exc_info:
        DRIVERS[target](deposit.id)

    assert (exc_info.value.from_status, exc_info.value.to_status) == (current.value, target.value)
    assert stored_row(deposit.id) == before


@pytest.mark.parametrize("current", [s for s in S if s != S.PENDING])
@pytest.mark.parametrize("resolve", [confirm_deposit, fail_deposit])
def test_provider_report_on_a_non_pending_deposit_raises_deposit_not_pending(
    resolve, current, make_deposit
):
    """Fails if a non-PENDING deposit can be confirmed or failed again (BR-DEP-6)."""
    deposit = make_deposit(current)

    with pytest.raises(DepositNotPending):
        resolve(deposit.id)

    assert stored_row(deposit.id)["status"] == current.value


@pytest.mark.parametrize(
    ("resolve", "expected_stamps"),
    [
        (confirm_deposit, {"confirmed_at": FIXED_NOW, "failed_at": None, "settled_at": None}),
        (fail_deposit, {"confirmed_at": None, "failed_at": FIXED_NOW, "settled_at": None}),
    ],
    ids=["confirm", "fail"],
)
def test_provider_report_stamps_only_its_own_timestamp(
    resolve, expected_stamps, make_deposit, frozen_now
):
    """Fails if confirmation/failure records the wrong state timestamp (BR-DEP-3, BR-DEP-4)."""
    deposit = make_deposit(S.PENDING)

    dto = resolve(deposit.id)

    stamps = {
        "confirmed_at": dto.confirmed_at,
        "failed_at": dto.failed_at,
        "settled_at": dto.settled_at,
    }
    assert stamps == expected_stamps


# --- settle_on_closure (BR-CLO-1..4) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("amount_minor", "debt_minor", "expected"),
    [
        (10_000, 0, ("RELEASED", 0, 10_000)),
        (10_000, 1, ("PARTIALLY_APPLIED", 1, 9_999)),
        (10_000, 9_999, ("PARTIALLY_APPLIED", 9_999, 1)),
        (10_000, 10_000, ("APPLIED", 10_000, 0)),
        (1, 0, ("RELEASED", 0, 1)),
        (1, 1, ("APPLIED", 1, 0)),
    ],
    ids=["debt-0", "debt-1", "debt-amount-minus-1", "debt-amount", "unit-debt-0", "unit-debt-1"],
)
def test_settle_on_closure_applies_the_debt_and_refunds_the_rest(
    amount_minor, debt_minor, expected, make_deposit, frozen_now
):
    """Fails if closure refunds other than deposit - debt or picks a wrong status (BR-CLO-1..4)."""
    deposit = make_deposit(S.CONFIRMED, amount_minor=amount_minor)

    dto = settle_on_closure(deposit.id, debt_minor)

    row = stored_row(deposit.id)
    assert (dto.status, dto.applied_minor, dto.refunded_minor) == expected
    assert (row["status"], row["applied_minor"], row["refunded_minor"]) == expected
    assert (row["settled_at"], row["confirmed_at"]) == (FIXED_NOW, deposit.confirmed_at)


@pytest.mark.parametrize(
    "debt_minor",
    [-1, AMOUNT_MINOR + 1, True, "0", None],
    ids=["negative", "above", "bool", "str", "none"],
)
def test_settle_on_closure_rejects_an_impossible_debt_and_keeps_the_row(debt_minor, make_deposit):
    """Fails if a debt outside 0..deposit, or not an int, settles the deposit (BR-CLO-4)."""
    deposit = make_deposit(S.CONFIRMED)
    before = stored_row(deposit.id)

    with pytest.raises(ValueError, match="debt_at_closure_minor"):
        settle_on_closure(deposit.id, debt_minor)

    assert stored_row(deposit.id) == before


@pytest.mark.parametrize("exception", [DepositNotFound, DepositNotPending, InvalidTransition])
def test_every_domain_exception_is_a_collateral_error(exception):
    """Fails if a business rejection escapes the core's `except CollateralError` mapping (F2)."""
    assert issubclass(exception, services.CollateralError)


def test_deposit_not_pending_is_an_invalid_transition():
    """Fails if a BR-DEP-6 rejection is not caught as an InvalidTransition (TEST-SM-1)."""
    assert issubclass(DepositNotPending, InvalidTransition)
