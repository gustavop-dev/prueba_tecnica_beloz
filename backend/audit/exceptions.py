"""Domain exceptions of the `audit` app. Callers import them from `audit.services`."""


class AuditError(Exception):
    """Base class for every error raised by the `audit` app."""


class SensitiveDataRejected(AuditError):
    """A state carried a key whose name suggests a secret (AUD-4, SEC-CARD-3).

    Nothing is written. `path` locates the offending key (e.g. `after.card.pan`); the value is
    never included, neither here nor in the message, so the error itself cannot leak it.
    """

    def __init__(self, *, path: str) -> None:
        self.path = path
        super().__init__(
            f"audit states must not carry PAN, tokens, signatures or secrets: key {path!r} "
            "is not allowed"
        )
