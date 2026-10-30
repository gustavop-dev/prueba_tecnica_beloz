"""Domain exceptions of the `collateral` app. Re-exported by `collateral.services`."""


class CollateralError(Exception):
    """Base class for every business rejection raised by the `collateral` app."""


class DepositNotFound(CollateralError):
    """The deposit does not exist, or does not belong to the requesting owner."""

    def __init__(self, deposit_id: object) -> None:
        super().__init__(f"Collateral deposit {deposit_id} not found.")
        self.deposit_id = deposit_id


class InvalidTransition(CollateralError):
    """The requested status change is not in the deposit state machine (INV-5)."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"Invalid collateral deposit transition {from_status} -> {to_status}.")
        self.from_status = from_status
        self.to_status = to_status


class DepositNotPending(InvalidTransition):
    """Only a PENDING deposit can be confirmed or failed (BR-DEP-6).

    Subclass of InvalidTransition: it is the PENDING-only case of the state machine, so a caller
    catching InvalidTransition also catches it.
    """
