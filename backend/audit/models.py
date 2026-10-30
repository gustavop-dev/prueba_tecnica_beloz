"""`AuditEvent`: who did what to which entity, when and in which request (INV-6, docs/audit.md).

Private to the app (DEP-2): other code goes through `audit.services`. Besides the constraints
declared here, migration `0002_event_guards` adds the AUD-3 PL/pgSQL triggers: the table is
append-only (no update or delete; no truncate for a non-superuser role).
"""

import uuid

from django.db import models
from django.db.models import Func, Q
from django.db.models.lookups import Exact

# `<entity>.<verb>`, lowercase letters and underscores. The same pattern is enforced by the
# service (`re.fullmatch`) and by PostgreSQL (`~`, where `$` only matches at the very end).
ACTION_PATTERN = r"^[a-z_]+\.[a-z_]+$"


class JSONBTypeOf(Func):
    """`jsonb_typeof(<jsonb>)`: `object`, `array`, `string`, `number`, `boolean` or `null`."""

    function = "JSONB_TYPEOF"
    output_field = models.CharField()


class ActorRole(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    OPERATOR = "operator", "Operator"
    PAYMENT_PROVIDER = "payment_provider", "Payment provider"


class EntityType(models.TextChoices):
    CREDIT_ACCOUNT = "credit_account", "Credit account"
    COLLATERAL_DEPOSIT = "collateral_deposit", "Collateral deposit"
    CARD = "card", "Card"


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Set by the service (`timezone.now()`), inside the mutation's transaction.
    occurred_at = models.DateTimeField(editable=False)
    # Principal that performed the action (MOD-3: plain UUID, no ForeignKey).
    actor_id = models.UUIDField()
    actor_role = models.CharField(max_length=32, choices=ActorRole.choices)
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=32, choices=EntityType.choices)
    entity_id = models.UUIDField()
    # NULL only for creations; otherwise a JSON object, like `after_state`.
    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField()
    # The HTTP request (OBS-2); NULL outside HTTP (e.g. management commands).
    request_id = models.UUIDField(null=True, blank=True)
    # The request's `Idempotency-Key` header, when it had one.
    idempotency_key = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(actor_role__in=ActorRole.values),
                name="audit_auditevent_actor_role_valid",
            ),
            models.CheckConstraint(
                condition=Q(action__regex=ACTION_PATTERN),
                name="audit_auditevent_action_format",
            ),
            models.CheckConstraint(
                condition=Q(entity_type__in=EntityType.values),
                name="audit_auditevent_entity_type_valid",
            ),
            # States are JSON objects. `after_state` is never NULL (also NOT NULL) nor JSON `null`.
            models.CheckConstraint(
                condition=Q(after_state__isnull=False)
                & Q(Exact(JSONBTypeOf("after_state"), "object")),
                name="audit_auditevent_after_state_is_object",
            ),
            models.CheckConstraint(
                condition=Q(before_state__isnull=True)
                | Q(Exact(JSONBTypeOf("before_state"), "object")),
                name="audit_auditevent_before_state_is_object",
            ),
        ]
        indexes = [
            models.Index(
                fields=["entity_type", "entity_id", "occurred_at"],
                name="audit_event_entity_idx",
            ),
            models.Index(fields=["request_id"], name="audit_event_request_idx"),
        ]

    def __str__(self) -> str:
        return f"AuditEvent {self.action} {self.entity_type} {self.entity_id}"
