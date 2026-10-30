"""Audit events for the flows (AUD-1, AUD-2, INV-6).

Each mutated entity gets one event, recorded inside the flow's transaction so it commits or rolls
back with the mutation. States hold statuses and amounts only — never tokens or full card numbers
(AUD-4); the card is identified by its last 4 digits.
"""

from uuid import UUID

from accounts import services as accounts
from audit import services as audit
from cards import services as cards
from collateral import services as collateral
from core.auth.principals import Role
from core.observability import context

CREDIT_ACCOUNT = "credit_account"
COLLATERAL_DEPOSIT = "collateral_deposit"
CARD = "card"


def account_state(dto: accounts.AccountDTO) -> dict:
    return {
        "status": dto.status,
        "credit_limit_minor": dto.credit_limit_minor,
        "outstanding_minor": dto.outstanding_minor,
    }


def deposit_state(dto: collateral.DepositDTO) -> dict:
    return {
        "status": dto.status,
        "amount_minor": dto.amount_minor,
        "applied_minor": dto.applied_minor,
        "refunded_minor": dto.refunded_minor,
    }


def card_state(dto: cards.CardDTO) -> dict:
    return {"status": dto.status, "last4": dto.last4}


def record(
    *,
    actor_id: UUID,
    actor_role: Role,
    action: str,
    entity_type: str,
    entity_id: UUID,
    before: dict | None,
    after: dict,
) -> None:
    request = context.current()
    audit.record(
        context=audit.AuditContext(
            actor_id=actor_id,
            actor_role=actor_role.value,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
        ),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
    )
