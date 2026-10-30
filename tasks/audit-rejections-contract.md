# Contract — Auditing rejected attempts

> Written by the orchestrator. Operator decisions: audit authenticated **business and access rejections** (not 400 validation, not 5xx); unauthenticated failures go to **JSON logs only**; store them in the **same `AuditEvent` table** with an `outcome`.
> The `audit` subagent receives §1 of `tasks/f1-contracts.md` (common conventions) plus §1 of this file.

## 1. `audit` app changes

### Model `AuditEvent` — new and changed fields

| Field | Change |
| --- | --- |
| `outcome` | New `CharField`: `succeeded` or `rejected`. Existing rows and `record()` → `succeeded`. |
| `error_code` | New nullable `CharField(64)`: the API error code of a rejection (e.g. `INSUFFICIENT_CREDIT`). |
| `attempt` | New nullable `JSONField`: sanitized parameters of the rejected request (e.g. `{"amount_minor": 5000}`); may be `{}`. |
| `entity_id` | Becomes nullable — **only** for rejections (an opening request rejected before any deposit exists has no entity). |
| `after_state` | Becomes nullable — **only** for rejections (nothing changed). |

### Constraints (replace or adjust the existing ones as needed; keep their intent)

| Rule | Constraint |
| --- | --- |
| Outcome values | `outcome IN ('succeeded', 'rejected')` |
| A success is a mutation | `outcome = 'succeeded'` ⇒ `entity_id IS NOT NULL`, `after_state` is a JSON object, `error_code IS NULL`, `attempt IS NULL` |
| A rejection changed nothing | `outcome = 'rejected'` ⇒ `before_state IS NULL`, `after_state IS NULL`, `error_code ~ '^[A-Z_]+$'`, `attempt` is a JSON object |
| Existing | role, action format, entity type, `before_state` NULL or object — unchanged |

AUD-3 (append-only triggers) applies unchanged to rejections.

### Services

```python
@dataclass(frozen=True, slots=True)
class AuditEventDTO:
    ...existing fields...,
    entity_id: UUID | None          # now optional (rejections only)
    after_state: dict | None        # now optional (rejections only)
    outcome: str                    # "succeeded" | "rejected"
    error_code: str | None
    attempt: dict | None

def record(...) -> AuditEventDTO            # unchanged signature; stores outcome="succeeded"

def record_rejection(*, context: AuditContext, action: str, entity_type: str,
                     entity_id: UUID | None, error_code: str, attempt: dict) -> AuditEventDTO
    # Requires an open transaction (the caller decides which one).
    # Same validation as `record`: context, action format, entity type, entity_id UUID or None,
    # error_code matching ^[A-Z_]+$ (max 64), attempt a dict of JSON-native values,
    # AUD-4 sensitive-key rejection applied to `attempt` too.

def list_events(...)                        # unchanged; returns both outcomes
```

`ACTIONS`/`ENTITY_TYPES` exports stay; add `OUTCOMES`.

### Mandatory tests

- Migration: existing rows become `succeeded`; `makemigrations --check` clean; migrate down and up works.
- `record_rejection` stores every field exactly (before/after NULL, outcome `rejected`); `entity_id=None` accepted; every invalid argument rejected with `ValueError` and nothing written; AUD-4 on `attempt` (top level and nested); outside a transaction → `RuntimeError`.
- `record` still stores `outcome='succeeded'` with no error code or attempt.
- Raw SQL: each new constraint violated in isolation is rejected (success without entity, success with error code, success with attempt, success without after_state, rejection with before or after state, rejection without/malformed error code, rejection with non-object attempt, unknown outcome). Valid rows of both outcomes are accepted.
- AUD-3 still rejects UPDATE/DELETE of a rejected-outcome row.

## 2. Orchestrator (`core`)

- Audited codes: `FORBIDDEN`, `NOT_FOUND`, `ACCOUNT_CLOSED`, `CARD_NOT_ACTIVE`, `DEPOSIT_NOT_PENDING`, `INVALID_STATE_TRANSITION`, `INSUFFICIENT_CREDIT`, `PAYMENT_EXCEEDS_DEBT`, `IDEMPOTENCY_KEY_REUSED`, from an authenticated principal.
- Action = attempted operation per endpoint (`card.purchase`, `credit_account.repay`, `credit_account.close`, `card.freeze`, `card.unfreeze`, `collateral_deposit.request_opening`, `collateral_deposit.report`, `credit_account.read`, `credit_account.read_ledger`); entity from the path (or the webhook body's `deposit_id`).
- Rejections inside an idempotent `POST` are recorded in its transaction, so they commit with the stored response and replays add nothing. Rejections raised outside it (403 before the view, 404 on `GET`, `IDEMPOTENCY_KEY_REUSED`) are recorded in their own transaction.
- `401` (missing/unknown token, invalid/expired webhook signature) → one `security.rejected` JSON log line (code, method, path, remote address, request id), never a database write, never the credential.
