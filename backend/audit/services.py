"""Public API of the `audit` app: the only module other code may import (MOD-1).

The operations core records one event per mutated entity inside the mutation's own transaction
(AUD-1, AUD-2), so the event commits or rolls back with it:

    with transaction.atomic():
        ...mutate...
        record(context=ctx, action="card.issued", entity_type="card", entity_id=card.id,
               before=None, after={"status": "ACTIVE", "last4": "4242"})

States are JSON-compatible dicts (str keys; str, int, bool, None, list and dict values). Floats are
rejected like everywhere else (BR-MONEY-3), and UUIDs or datetimes must be passed as strings.
Keys that suggest a secret are rejected at any depth (AUD-4, SEC-CARD-3). The table is
append-only in PostgreSQL (AUD-3, migration `0002_event_guards`).
"""

import copy
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from audit.exceptions import AuditError, SensitiveDataRejected
from audit.models import ACTION_PATTERN, ActorRole, AuditEvent, EntityType

__all__ = [
    "ACTOR_ROLES",
    "ENTITY_TYPES",
    "SENSITIVE_KEY_FRAGMENTS",
    "AuditContext",
    "AuditError",
    "AuditEventDTO",
    "SensitiveDataRejected",
    "list_events",
    "record",
]

ACTOR_ROLES: frozenset[str] = frozenset(ActorRole.values)
ENTITY_TYPES: frozenset[str] = frozenset(EntityType.values)

# AUD-4: a key whose lowercase name contains any of these is rejected, at any depth.
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "token",
    "pan",
    "card_number",
    "signature",
    "secret",
    "password",
)

_ACTION_RE = re.compile(ACTION_PATTERN.removeprefix("^").removesuffix("$"))
_ACTION_MAX_LENGTH = AuditEvent._meta.get_field("action").max_length
# Entity states are flat or shallow snapshots; the limit only stops runaway or cyclic structures.
_MAX_STATE_DEPTH = 16


# --- DTOs -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditContext:
    actor_id: UUID
    actor_role: str  # "customer" | "operator" | "payment_provider"
    request_id: UUID | None
    idempotency_key: UUID | None


@dataclass(frozen=True, slots=True)
class AuditEventDTO:
    id: UUID
    occurred_at: datetime
    actor_id: UUID
    actor_role: str
    action: str
    entity_type: str
    entity_id: UUID
    before_state: dict | None
    after_state: dict
    request_id: UUID | None
    idempotency_key: UUID | None


def _to_dto(event: AuditEvent) -> AuditEventDTO:
    return AuditEventDTO(
        id=event.id,
        occurred_at=event.occurred_at,
        actor_id=event.actor_id,
        actor_role=event.actor_role,
        action=event.action,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        before_state=event.before_state,
        after_state=event.after_state,
        request_id=event.request_id,
        idempotency_key=event.idempotency_key,
    )


# --- Guards -----------------------------------------------------------------------------


def _require_atomic(function_name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{function_name} must run inside transaction.atomic")


def _require_uuid(name: str, value: object) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID, got {value!r}")


def _require_optional_uuid(name: str, value: object) -> None:
    if value is not None:
        _require_uuid(name, value)


def _require_context(context: object) -> None:
    if not isinstance(context, AuditContext):
        raise ValueError(f"context must be an AuditContext, got {type(context).__name__}")
    _require_uuid("context.actor_id", context.actor_id)
    if not isinstance(context.actor_role, str) or context.actor_role not in ACTOR_ROLES:
        raise ValueError(
            f"context.actor_role must be one of {sorted(ACTOR_ROLES)}, got {context.actor_role!r}"
        )
    _require_optional_uuid("context.request_id", context.request_id)
    _require_optional_uuid("context.idempotency_key", context.idempotency_key)


def _require_action(action: object) -> None:
    if (
        not isinstance(action, str)
        or len(action) > _ACTION_MAX_LENGTH
        or _ACTION_RE.fullmatch(action) is None
    ):
        raise ValueError(
            f"action must match {ACTION_PATTERN} (<entity>.<verb>) and be at most "
            f"{_ACTION_MAX_LENGTH} characters, got {action!r}"
        )


def _require_entity_type(entity_type: object) -> None:
    if not isinstance(entity_type, str) or entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}, got {entity_type!r}")


def _require_storable_string(path: str, value: str) -> None:
    # PostgreSQL `jsonb` rejects U+0000 and text that is not valid UTF-8 (lone surrogates).
    if "\x00" in value:
        raise ValueError(f"{path} contains a NUL character, which jsonb cannot store")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8 text: {exc.reason}") from exc


def _check_value(path: str, value: object, depth: int) -> None:
    """Raise unless `value` is JSON-compatible and every nested key is safe (AUD-4)."""
    if depth > _MAX_STATE_DEPTH:
        raise ValueError(f"{path} nests deeper than {_MAX_STATE_DEPTH} levels")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} has a non-string key {key!r}; JSON keys are strings")
            _require_storable_string(f"{path} key", key)
            item_path = f"{path}.{key}"
            lowered = key.lower()
            if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
                raise SensitiveDataRejected(path=item_path)
            _check_value(item_path, item, depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_value(f"{path}[{index}]", item, depth + 1)
    elif isinstance(value, str):
        _require_storable_string(path, value)
    elif value is None or isinstance(value, (bool, int)):
        return
    else:
        # float (BR-MONEY-3), Decimal, UUID, datetime, tuple, set, bytes...
        raise ValueError(
            f"{path} has a value of type {type(value).__name__}; audit states only hold "
            "str, int, bool, None, list and dict"
        )


def _require_state(name: str, state: object) -> None:
    if not isinstance(state, dict):
        raise ValueError(f"{name} must be a dict, got {type(state).__name__}")
    _check_value(name, state, depth=1)


# --- Public API -------------------------------------------------------------------------


def record(
    *,
    context: AuditContext,
    action: str,
    entity_type: str,
    entity_id: UUID,
    before: dict | None,
    after: dict,
) -> AuditEventDTO:
    """Write one audit event in the caller's transaction and return it (AUD-1, AUD-2).

    `before` is None only for creations. Raises `ValueError` for any malformed argument and
    `SensitiveDataRejected` when a state key suggests a PAN, token, signature or secret (AUD-4);
    nothing is written in either case.
    """
    _require_atomic("record")
    _require_context(context)
    _require_action(action)
    _require_entity_type(entity_type)
    _require_uuid("entity_id", entity_id)
    if before is not None:
        _require_state("before", before)
    _require_state("after", after)

    # Snapshots: later changes to the caller's dicts reach neither the row nor the DTO. Validation
    # restricted them to JSON types, so the copies equal what PostgreSQL stores.
    before, after = copy.deepcopy(before), copy.deepcopy(after)
    event = AuditEvent.objects.create(
        occurred_at=timezone.now(),
        actor_id=context.actor_id,
        actor_role=context.actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before,
        after_state=after,
        request_id=context.request_id,
        idempotency_key=context.idempotency_key,
    )
    return _to_dto(event)


def list_events(
    *, entity_id: UUID | None = None, request_id: UUID | None = None
) -> list[AuditEventDTO]:
    """Events matching every given filter, ordered by `occurred_at` then `id`. Read-only.

    At least one filter is required: the audit log is never listed whole.
    """
    if entity_id is None and request_id is None:
        raise ValueError("list_events requires entity_id, request_id or both")
    _require_optional_uuid("entity_id", entity_id)
    _require_optional_uuid("request_id", request_id)

    events = AuditEvent.objects.all()
    if entity_id is not None:
        events = events.filter(entity_id=entity_id)
    if request_id is not None:
        events = events.filter(request_id=request_id)
    return [_to_dto(event) for event in events.order_by("occurred_at", "id")]
