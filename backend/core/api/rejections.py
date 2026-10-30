"""Rejected attempts (docs/audit.md §1): audit events for authenticated callers, logs for the rest.

- Business and access rejections from an authenticated principal become `rejected` audit events.
  Inside an idempotent POST they are written in its transaction (one event per logical attempt,
  none on replay); elsewhere in a transaction of their own.
- Unauthenticated failures (401) never touch the database: one `security.rejected` log line.
- Validation errors (400) and technical failures (5xx) are not audited.
"""

import logging
import uuid

from django.db import DatabaseError, transaction
from rest_framework.exceptions import ParseError

from audit import services as audit
from core.auth.principals import Principal
from core.observability import context

security_log = logging.getLogger("core.security")
audit_log = logging.getLogger("core.audit")

AUDITED_CODES = frozenset(
    {
        "FORBIDDEN",
        "NOT_FOUND",
        "ACCOUNT_CLOSED",
        "CARD_NOT_ACTIVE",
        "DEPOSIT_NOT_PENDING",
        "INVALID_STATE_TRANSITION",
        "INSUFFICIENT_CREDIT",
        "PAYMENT_EXCEEDS_DEBT",
        "IDEMPOTENCY_KEY_REUSED",
    }
)

# URL name → (entity type, attempted operation, where the entity id comes from).
OPERATIONS = {
    "accounts": ("collateral_deposit", "request_opening", None),
    "webhook-payments": ("collateral_deposit", "report", ("body", "deposit_id")),
    "card-authorizations": ("card", "purchase", ("path", "card_id")),
    "card-freeze": ("card", "freeze", ("path", "card_id")),
    "card-unfreeze": ("card", "unfreeze", ("path", "card_id")),
    "account-repayments": ("credit_account", "repay", ("path", "account_id")),
    "account-close": ("credit_account", "close", ("path", "account_id")),
    "account-detail": ("credit_account", "read", ("path", "account_id")),
    "account-ledger": ("credit_account", "read_ledger", ("path", "account_id")),
}

# Request fields worth keeping as the attempt's parameters; nothing else from the body is stored.
ATTEMPT_FIELDS = ("amount_minor", "deposit_amount_minor", "outcome")
MAX_ATTEMPT_STRING = 64


def record_in_current_transaction(request, code: str) -> None:
    """Record an audited rejection inside the caller's open transaction."""
    event = _event(request, code)
    if event is not None:
        audit.record_rejection(**event)


def record_in_own_transaction(request, code: str) -> None:
    """Record an audited rejection that happened outside any operation's transaction."""
    event = _event(request, code)
    if event is None:
        return
    try:
        with transaction.atomic():
            audit.record_rejection(**event)
    except DatabaseError:
        # The rejection itself was already decided; losing its trail must be visible, not fatal.
        audit_log.exception("audit.rejection_not_recorded", extra={"code": code})


def log_unauthenticated(request, code: str) -> None:
    """401s have no trustworthy actor: log them, never write them (no credential is logged)."""
    security_log.warning(
        "security.rejected",
        extra={
            "code": code,
            "method": request.method,
            "path": request.path,
            "remote_addr": request.META.get("REMOTE_ADDR"),
        },
    )


def _event(request, code: str) -> dict | None:
    principal = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    if code not in AUDITED_CODES or not isinstance(principal, Principal) or match is None:
        return None
    operation = OPERATIONS.get(match.url_name)
    if operation is None:
        return None
    entity_type, verb, entity_source = operation
    current = context.current()
    return {
        "context": audit.AuditContext(
            actor_id=principal.id,
            actor_role=principal.role.value,
            request_id=current.request_id,
            idempotency_key=current.idempotency_key,
        ),
        "action": f"{entity_type}.{verb}",
        "entity_type": entity_type,
        "entity_id": _entity_id(request, match.kwargs, entity_source),
        "error_code": code,
        "attempt": _attempt(request),
    }


def _entity_id(request, path_kwargs, source) -> uuid.UUID | None:
    if source is None:
        return None
    where, name = source
    raw = path_kwargs.get(name) if where == "path" else _body(request).get(name)
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except ValueError:
        return None


def _attempt(request) -> dict:
    body = _body(request)
    return {
        name: _clean(body[name])
        for name in ATTEMPT_FIELDS
        if name in body and type(body[name]) in (int, str)
    }


def _clean(value):
    return value[:MAX_ATTEMPT_STRING] if isinstance(value, str) else value


def _body(request) -> dict:
    try:
        data = request.data
    except ParseError:
        return {}
    return data if isinstance(data, dict) else {}
