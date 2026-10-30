"""Domain exceptions of the `accounts` app.

Defined here so `states.py` and `services.py` share them without a circular import. Callers import
them from `accounts.services`, which re-exports every one.
"""


class AccountsError(Exception):
    """Base class for every business rejection raised by the `accounts` app."""


class AccountNotFound(AccountsError):
    """The account does not exist, or it is not owned by the caller (SEC-IDOR-1/2)."""


class AccountClosed(AccountsError):
    """The account is CLOSED: no operation is allowed (BR-CLO-6)."""


class InsufficientCredit(AccountsError):
    """The amount exceeds the available credit (BR-PUR-2)."""


class PaymentExceedsDebt(AccountsError):
    """The payment exceeds the outstanding debt (BR-PAY-2/3)."""


class InvalidTransition(AccountsError):
    """The status change is not in the credit account state machine (INV-5)."""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"invalid credit account transition {from_status} -> {to_status}")
