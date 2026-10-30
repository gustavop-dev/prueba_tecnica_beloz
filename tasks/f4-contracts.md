# F4 Contracts — Security Hardening and Audit

> Written by the orchestrator. The `audit` subagent receives §1 of `tasks/f1-contracts.md` (common conventions) plus §1 of this file. §2 is the orchestrator's part.

## 1. `audit` app — docs/audit.md §1, INV-6

### Model `AuditEvent`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `occurred_at` | datetime | Set by the service (`timezone.now()`), not editable. |
| `actor_id` | UUID | Principal that performed the action. |
| `actor_role` | `CharField` | `customer`, `operator` or `payment_provider` (`CHECK`). |
| `action` | `CharField(64)` | `<entity>.<verb>`, lowercase with underscores (`CHECK action ~ '^[a-z_]+\.[a-z_]+$'`). |
| `entity_type` | `CharField` | `credit_account`, `collateral_deposit` or `card` (`CHECK`). |
| `entity_id` | UUID | |
| `before_state` | `JSONField`, nullable | `NULL` only for creations. |
| `after_state` | `JSONField` | Never null. |
| `request_id` | UUID, nullable | The HTTP request (OBS-2); null outside HTTP (e.g. management commands). |
| `idempotency_key` | UUID, nullable | The request's `Idempotency-Key`. |

Indexes: `(entity_type, entity_id, occurred_at)`, `request_id`.

### Database guards

| ID | Guard |
| --- | --- |
| AUD-3 | Append-only, same pattern as LED-4: `BEFORE UPDATE OR DELETE` row triggers always raise; `BEFORE TRUNCATE` statement trigger raises unless the current role is a superuser. Messages start with `AUD-3:`. |
| — | The `CHECK`s above (role, action format, entity type, `after_state` not null). |

### Services

```python
@dataclass(frozen=True, slots=True)
class AuditContext:
    actor_id: UUID
    actor_role: str             # "customer" | "operator" | "payment_provider"
    request_id: UUID | None
    idempotency_key: UUID | None

@dataclass(frozen=True, slots=True)
class AuditEventDTO:
    id: UUID; occurred_at: datetime; actor_id: UUID; actor_role: str; action: str
    entity_type: str; entity_id: UUID; before_state: dict | None; after_state: dict
    request_id: UUID | None; idempotency_key: UUID | None

def record(*, context: AuditContext, action: str, entity_type: str, entity_id: UUID,
           before: dict | None, after: dict) -> AuditEventDTO
    # Requires an open transaction (AUD-2): the event commits or rolls back with the mutation.
    # Validates every argument (ValueError). Rejects states that could carry secrets (AUD-4):
    # any key, at any depth, whose lowercase name contains "token", "pan", "card_number",
    # "signature", "secret" or "password" raises SensitiveDataRejected; states must be
    # JSON-compatible dicts.

def list_events(*, entity_id: UUID | None = None, request_id: UUID | None = None) -> list[AuditEventDTO]
    # Read-only, ordered by occurred_at then id. At least one filter is required (ValueError).
```

Exceptions: `AuditError`, `SensitiveDataRejected`.

### Mandatory tests

- `record` stores every field exactly; creations with `before=None`; rejects bad role, malformed action, unknown entity type, missing `after`, non-dict states, non-UUID ids; refuses to run outside `transaction.atomic`.
- AUD-4: each forbidden key name (top level and nested) is rejected and nothing is written.
- AUD-3 with raw SQL: update and delete always rejected; truncate rejected for a non-superuser role (`SET LOCAL ROLE`), allowed for the superuser.
- AUD-2: an event recorded inside a transaction that rolls back does not exist afterwards.
- `list_events` filters and ordering.

## 2. Orchestrator (`core`)

- Request context (`contextvars`): request id (OBS-2) and idempotency key, set by the middleware and the idempotent wrapper; read by audit recording and the JSON log formatter.
- Audit recording in every flow, inside its transaction, one event per mutated entity (AUD-1): `deposit.requested`, `deposit.confirmed`, `deposit.failed`, `credit_account.opened`, `card.issued`, `credit_account.purchase_authorized`, `credit_account.repayment_applied`, `credit_account.closed`, `deposit.settled`, `card.closed`. The provider flow gains an explicit `provider_id` actor argument.
- Webhook: HMAC-SHA256 over `"<timestamp>.<raw body>"` in `X-Webhook-Signature: t=<unix seconds>,v1=<hex>`, shared secret `WEBHOOK_SIGNING_SECRET`, 5-minute window both ways, constant-time comparison; principal `PAYMENT_PROVIDER_ID` with role `payment_provider`; bearer tokens are not accepted on the webhook (SEC-HOOK-1..5). Errors `INVALID_SIGNATURE` / `SIGNATURE_EXPIRED`.
- JSON logs (OBS-1..3): one JSON object per line, `request_id` on every line, one `request.completed` line per request; headers and bodies are never logged.
- Tests: TEST-SEC-1..6, permission matrix per view, audit per mutation / none on rollback / none on replay or rejection, log format and secret absence.
