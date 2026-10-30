from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.money import (
    BPS_DENOMINATOR,
    COLLATERAL_RATIO_BPS,
    InvalidAmount,
    credit_limit_minor,
    parse_amount_minor,
)


@pytest.mark.parametrize("value", [1, 500, 10**15])
def test_parse_amount_accepts_positive_integers(value):
    """Fails if a valid amount in cents is rejected or altered (BR-MONEY-2)."""
    assert parse_amount_minor(value) == value


@pytest.mark.parametrize(
    "value",
    [10.0, 0.5, "1000", True, False, None, Decimal("10"), [100]],
    ids=["float", "fraction", "string", "true", "false", "none", "decimal", "list"],
)
def test_parse_amount_rejects_non_integer_types(value):
    """Fails if a float, string, bool or decimal is accepted as an amount (BR-MONEY-3/4)."""
    with pytest.raises(InvalidAmount, match="integer number of cents"):
        parse_amount_minor(value)


@pytest.mark.parametrize("value", [0, -1, -500])
def test_parse_amount_rejects_non_positive_integers(value):
    """Fails if a zero or negative amount is accepted (BR-MONEY-5)."""
    with pytest.raises(InvalidAmount, match="greater than zero"):
        parse_amount_minor(value)


def test_exercise_collateral_ratio_is_one():
    """Fails if the configured collateral ratio stops being 1.0 (BR-LIM-4)."""
    assert COLLATERAL_RATIO_BPS == BPS_DENOMINATOR


@given(deposit=st.integers(min_value=1, max_value=10**15))
def test_limit_equals_deposit_with_ratio_one(deposit):
    """Fails if a 1.0 ratio produces a limit different from the deposit (BR-LIM-4)."""
    assert credit_limit_minor(deposit, COLLATERAL_RATIO_BPS) == deposit


@given(
    deposit=st.integers(min_value=1, max_value=10**15),
    ratio_bps=st.integers(min_value=1, max_value=50_000),
)
def test_limit_is_the_exact_floor_of_deposit_times_ratio(deposit, ratio_bps):
    """Fails if the limit is rounded up, or rounded down by more than one cent (BR-LIM-3)."""
    limit = credit_limit_minor(deposit, ratio_bps)

    assert limit * BPS_DENOMINATOR <= deposit * ratio_bps < (limit + 1) * BPS_DENOMINATOR


def test_limit_floors_fractional_cents():
    """Fails if a fractional-cent limit is rounded up instead of floored (BR-LIM-3)."""
    assert credit_limit_minor(3, 5_000) == 1


@pytest.mark.parametrize("ratio_bps", [0, -1, 1.0, True])
def test_limit_rejects_invalid_ratios(ratio_bps):
    """Fails if a zero, negative, float or bool ratio is used to compute a limit (BR-LIM-2)."""
    with pytest.raises(ValueError, match="ratio_bps"):
        credit_limit_minor(1_000, ratio_bps)


def test_limit_rejects_invalid_deposit():
    """Fails if a limit is computed from a non-integer deposit (BR-MONEY-4)."""
    with pytest.raises(InvalidAmount):
        credit_limit_minor(1000.0, COLLATERAL_RATIO_BPS)
