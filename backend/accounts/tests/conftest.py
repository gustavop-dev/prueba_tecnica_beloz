from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from accounts.models import CreditAccount

# Fixed, deterministic timestamp for accounts created already CLOSED.
CLOSED_AT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def make_account() -> Callable[..., CreditAccount]:
    """Factory for stored accounts, written through the ORM to arrange a precise starting state."""

    def _make(
        *,
        owner_id: UUID | None = None,
        credit_limit_minor: int = 100_000,
        outstanding_minor: int = 0,
        collateral_ratio_bps: int = 10_000,
        closed: bool = False,
    ) -> CreditAccount:
        return CreditAccount.objects.create(
            id=uuid4(),
            owner_id=owner_id or uuid4(),
            deposit_id=uuid4(),
            status=CreditAccount.Status.CLOSED if closed else CreditAccount.Status.ACTIVE,
            collateral_ratio_bps=collateral_ratio_bps,
            credit_limit_minor=credit_limit_minor,
            outstanding_minor=outstanding_minor,
            closed_at=CLOSED_AT if closed else None,
        )

    return _make


@pytest.fixture
def stored() -> Callable[[UUID], dict[str, object]]:
    """Read the persisted mutable columns of an account, bypassing any in-memory instance."""

    def _stored(account_id: UUID) -> dict[str, object]:
        return CreditAccount.objects.values(
            "status", "credit_limit_minor", "outstanding_minor", "closed_at"
        ).get(pk=account_id)

    return _stored
