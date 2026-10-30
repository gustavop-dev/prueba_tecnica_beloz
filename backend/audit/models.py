"""`AuditEvent`: who did what to which entity, when and in which request (INV-6, docs/audit.md).

An event has an `outcome`:
  - `succeeded`: a mutation. It names its entity, carries the after state (and the before state
    unless it is a creation) and has neither error code nor attempt.
  - `rejected`: an authenticated attempt refused with an API error code. Nothing changed, so it
    has no states; it carries the error code and the sanitized request parameters (`attempt`).
    The entity may be unknown (e.g. an opening rejected before any deposit exists).

Private to the app (DEP-2): other code goes through `audit.services`. Besides the constraints
declared here, migration `0002_event_guards` adds the AUD-3 PL/pgSQL triggers: the table is
append-only (no update or delete; no truncate for a non-superuser role), whatever the outcome.
"""

import uuid

from django.db import models
from django.db.models import Func, Q
from django.db.models.lookups import Exact

# `<entity>.<verb>`, lowercase letters and underscores. The same pattern is enforced by the
# service (`re.fullmatch`) and by PostgreSQL (`~`, where `$` only matches at the very end).
ACTION_PATTERN = r"^[a-z_]+\.[a-z_]+$"
# API error codes of rejections (e.g. `INSUFFICIENT_CREDIT`): uppercase letters and underscores.
ERROR_CODE_PATTERN = r"^[A-Z_]+$"


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


class Outcome(models.TextChoices):
    SUCCEEDED = "succeeded", "Succeeded"
    REJECTED = "rejected", "Rejected"


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Set by the service (`timezone.now()`), inside the mutation's transaction.
    occurred_at = models.DateTimeField(editable=False)
    # Principal that performed the action (MOD-3: plain UUID, no ForeignKey).
    actor_id = models.UUIDField()
    actor_role = models.CharField(max_length=32, choices=ActorRole.choices)
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=32, choices=EntityType.choices)
    # NULL only for a rejection whose entity does not exist (yet).
    entity_id = models.UUIDField(null=True, blank=True)
    # NULL for creations and rejections; otherwise a JSON object, like `after_state`.
    before_state = models.JSONField(null=True, blank=True)
    # NULL only for rejections (nothing changed); otherwise a JSON object.
    after_state = models.JSONField(null=True, blank=True)
    # The Python default only serves `record()`; the column has no database default, so a row
    # written outside the service must state its outcome.
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.SUCCEEDED)
    # Rejections only: the API error code and the sanitized request parameters (may be `{}`).
    # NULL rather than "" when absent (hence DJ001): the CHECKs tell absence from a bad code.
    error_code = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    attempt = models.JSONField(null=True, blank=True)
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
            models.CheckConstraint(
                condition=Q(outcome__in=Outcome.values),
                name="audit_auditevent_outcome_valid",
            ),
            # States and attempts are SQL NULL or JSON objects, never JSON `null`, arrays or
            # scalars. Which of them must be present depends on the outcome (below).
            models.CheckConstraint(
                condition=Q(after_state__isnull=True)
                | Q(Exact(JSONBTypeOf("after_state"), "object")),
                name="audit_auditevent_after_state_is_object",
            ),
            models.CheckConstraint(
                condition=Q(before_state__isnull=True)
                | Q(Exact(JSONBTypeOf("before_state"), "object")),
                name="audit_auditevent_before_state_is_object",
            ),
            models.CheckConstraint(
                condition=Q(attempt__isnull=True) | Q(Exact(JSONBTypeOf("attempt"), "object")),
                name="audit_auditevent_attempt_is_object",
            ),
            models.CheckConstraint(
                condition=Q(error_code__isnull=True) | Q(error_code__regex=ERROR_CODE_PATTERN),
                name="audit_auditevent_error_code_format",
            ),
            # A success is a mutation: an entity and its after state, no error code or attempt.
            models.CheckConstraint(
                condition=~Q(outcome=Outcome.SUCCEEDED)
                | Q(
                    entity_id__isnull=False,
                    after_state__isnull=False,
                    error_code__isnull=True,
                    attempt__isnull=True,
                ),
                name="audit_auditevent_succeeded_is_mutation",
            ),
            # A rejection changed nothing: no states, but an error code and an attempt.
            models.CheckConstraint(
                condition=~Q(outcome=Outcome.REJECTED)
                | Q(
                    before_state__isnull=True,
                    after_state__isnull=True,
                    error_code__isnull=False,
                    attempt__isnull=False,
                ),
                name="audit_auditevent_rejected_changed_nothing",
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
        return f"AuditEvent {self.action} {self.outcome} {self.entity_type} {self.entity_id}"
