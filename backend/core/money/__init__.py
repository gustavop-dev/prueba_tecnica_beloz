"""Money rules shared by the operations core: strict amount parsing and the credit limit formula.

Amounts are integers in cents (minor units). Floats and decimals never appear (BR-MONEY-2/3).
"""

BPS_DENOMINATOR = 10_000
COLLATERAL_RATIO_BPS = 10_000  # 1.0 for this exercise (BR-LIM-4)


class InvalidAmount(ValueError):
    """The value is not a strictly positive integer number of cents (BR-MONEY-4/5)."""


def parse_amount_minor(value: object) -> int:
    """Return `value` if it is a strictly positive int; reject everything else.

    `bool` is a subclass of `int` in Python, so the check is on the exact type.
    """
    if type(value) is not int:
        raise InvalidAmount("Amount must be an integer number of cents.")
    if value <= 0:
        raise InvalidAmount("Amount must be greater than zero.")
    return value


def credit_limit_minor(deposit_minor: int, ratio_bps: int) -> int:
    """`deposit × ratio_bps // 10000` with integer arithmetic only (BR-LIM-1..3).

    Multiplying before the floor division means no intermediate value is rounded, and flooring
    guarantees the limit never exceeds what the ratio allows.
    """
    parse_amount_minor(deposit_minor)
    if type(ratio_bps) is not int or ratio_bps <= 0:
        raise ValueError("ratio_bps must be a positive integer.")
    return deposit_minor * ratio_bps // BPS_DENOMINATOR
