"""Domain exceptions of the `idempotency` app. Callers import them from `idempotency.services`."""

from uuid import UUID


class IdempotencyError(Exception):
    """Base class for every error raised by the `idempotency` app."""


class KeyReusedWithDifferentRequest(IdempotencyError):
    """The key was already used by this principal on this endpoint for a different request body.

    The operation must not run; the API answers `422 IDEMPOTENCY_KEY_REUSED` (idempotency.md §3).
    """

    def __init__(self, *, principal_id: UUID, endpoint: str, key: UUID) -> None:
        self.principal_id = principal_id
        self.endpoint = endpoint
        self.key = key
        super().__init__(
            f"idempotency key {key} was already used on {endpoint} for a different request"
        )
