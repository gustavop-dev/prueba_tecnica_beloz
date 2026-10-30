# Audit and Observability

> Status: **final** — implemented and verified (F0–F5).

Guarantees INV-6: every mutation is recorded with who, what, when and in which request.

## 1. Audit events

| ID | Rule |
| --- | --- |
| AUD-1 | Every mutation writes one `AuditEvent`. Mutations: deposit created, confirmed and failed; purchase; payment; account closure; card freeze/unfreeze. |
| AUD-2 | The audit event is written in the same database transaction as the mutation: both commit or neither does. |
| AUD-3 | The audit log is append-only. A PostgreSQL trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`, as for the ledger (LED-4). |
| AUD-4 | Audit events never contain the PAN, tokens or signatures (SEC-CARD-3). The recording service rejects any state key containing `token`, `pan`, `card_number`, `signature`, `secret` or `password`, at any depth, and accepts only JSON-native values (no floats, no decimals). |

### Fields

| Field | Meaning |
| --- | --- |
| `actor` | Principal identity that performed the action. |
| `actor_role` | `customer`, `operator` or `payment_provider`. |
| `action` | What was done (e.g. `purchase.approved`, `deposit.confirmed`, `account.closed`). |
| `entity_type`, `entity_id` | What it was done to. |
| `before_state`, `after_state` | Entity state before and after the mutation. `before_state` is empty on creation. |
| `request_id` | The request that caused it (see [OBS-2](#2-structured-logs)). |
| `idempotency_key` | The `Idempotency-Key` of that request. |
| `occurred_at` | Timestamp of the mutation. |

### Actions

One event per mutated entity, inside the operation's transaction. Business rejections and idempotent replays change nothing and record nothing.

| Operation | Events (actor) |
| --- | --- |
| `POST /accounts` | `deposit.requested` (customer) |
| Provider report, success | `deposit.confirmed`, `credit_account.opened`, `card.issued` (payment_provider) |
| Provider report, failure | `deposit.failed` (payment_provider) |
| Purchase | `credit_account.purchase_authorized` (customer) |
| Repayment | `credit_account.repayment_applied` (customer) |
| Closure | `credit_account.closed`, `deposit.settled`, `card.closed` (customer) |
| Card freeze / unfreeze | `card.frozen` / `card.unfrozen` (customer) |

States hold statuses and amounts; a card is identified only by its last 4 digits.

## 2. Structured logs

| ID | Rule |
| --- | --- |
| OBS-1 | Logs are structured JSON, one object per line. |
| OBS-2 | Every log line carries a `request_id`. It is taken from the incoming `X-Request-ID` header when present and valid, otherwise generated as a UUID, and returned in the `X-Request-ID` response header. |
| OBS-3 | Logs never contain the PAN, tokens or signatures (SEC-CARD-3): only method, path, status and duration are logged per request, never headers or bodies. |
| OBS-6 | Django and gunicorn share the same JSON formatter on stdout; each request produces one `request.completed` line. Only the `migrate` step at container start prints plain text. |

## 3. Health check

| ID | Rule |
| --- | --- |
| OBS-4 | `GET /health` verifies the database connection and returns `200` when healthy and `503` otherwise. It requires no authentication and reveals no internal details. |
| OBS-5 | In Docker Compose, the application starts only after PostgreSQL reports healthy (`pg_isready` health check + `depends_on: condition: service_healthy`), and the application's own health check uses `/health`. |
