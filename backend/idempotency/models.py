"""`IdempotencyRecord`: one row per `(principal, endpoint, key)` with the response it produced.

Private to the app (DEP-2): other code goes through `idempotency.services`. Besides the constraints
declared here, migration `0002_record_guards` adds the PL/pgSQL triggers: IDEM-7 (a record only
commits completed) and immutability of completed records (no update, delete or truncate).
"""

import uuid

from django.db import models
from django.db.models import Func, Q
from django.db.models.lookups import Exact

# IDEM-4: `claim` targets this constraint by name in its `INSERT ... ON CONFLICT`.
UNIQUE_KEY_CONSTRAINT = "idempotency_record_unique_key"

# SHA-256 digest size (IDEM-5, ADR 0002).
REQUEST_HASH_BYTES = 32


class OctetLength(Func):
    """`octet_length(<bytea>)`: the size of a binary value in bytes."""

    function = "OCTET_LENGTH"
    output_field = models.IntegerField()


class IdempotencyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Authenticated caller, customer or provider (IDEM-4, IDEM-13). MOD-3: plain UUID, no FK.
    principal_id = models.UUIDField()
    # HTTP method plus concrete path, e.g. `POST /accounts/<uuid>/repayments` (IDEM-8).
    endpoint = models.CharField(max_length=255)
    # Client-generated `Idempotency-Key` header (IDEM-2).
    key = models.UUIDField()
    # SHA-256 of the canonical request body (IDEM-5, IDEM-9).
    request_hash = models.BinaryField()
    # Stored response (IDEM-6). NULL only while the claiming transaction is still running (IDEM-7).
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # IDEM-4: one record per principal, endpoint and key.
            models.UniqueConstraint(
                fields=["principal_id", "endpoint", "key"],
                name=UNIQUE_KEY_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=Exact(OctetLength("request_hash"), REQUEST_HASH_BYTES),
                name="idempotency_record_request_hash_is_sha256",
            ),
            models.CheckConstraint(
                condition=Q(response_status__isnull=True)
                | Q(response_status__gte=100, response_status__lte=599),
                name="idempotency_record_response_status_is_http",
            ),
            # The response is stored all at once: status, body and completion time are either
            # all set (completed) or all NULL (claimed, in flight).
            models.CheckConstraint(
                condition=Q(
                    response_status__isnull=True,
                    response_body__isnull=True,
                    completed_at__isnull=True,
                )
                | Q(
                    response_status__isnull=False,
                    response_body__isnull=False,
                    completed_at__isnull=False,
                ),
                name="idempotency_record_completed_atomically",
            ),
        ]

    def __str__(self) -> str:
        state = "completed" if self.response_status is not None else "in flight"
        return f"IdempotencyRecord {self.endpoint} {self.key} ({state})"
