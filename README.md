# Acme Secured Credit Card — Backend

A secured credit card backend. A customer funds a **collateral deposit**, gets a **credit line equal to the deposit** (collateral ratio 1.0) and a **card**, spends, repays, and finally **closes** the account: the collateral pays off any remaining debt and the rest is refunded.

The priority is accounting correctness under real-world conditions: money never appears or disappears (double-entry ledger), concurrent requests cannot overspend (row locks + database guards), retries never apply twice (idempotency keys), and every mutation is audited.

**Stack:** Python 3.14 · Django 6.1 · Django REST Framework 3.18 · PostgreSQL 18 · pytest + Hypothesis · Docker Compose · Terraform (AWS).

## Quick start

Only **Docker** and **make** are needed.

```bash
make up            # build and start PostgreSQL + API; waits until both are healthy
make test          # full test suite against PostgreSQL (~850 tests, ~25 s)
make walkthrough   # end-to-end API scenario against the running stack, asserted step by step
make reconcile     # verify the balance cache and deposits against the ledger
make down
```

`.env` is created from `.env.example` on first use (development-only values). Other targets: `make lint` (ruff + import-linter), `make format`, `make infra-check` (Terraform fmt + validate), `make help`.

## API

All amounts are **integers in cents** (`_minor`), currency USD. Every `POST` requires an `Idempotency-Key` (UUID). Customers authenticate with `Authorization: Bearer <token>`; the payment provider signs its webhook with HMAC.

| Method | Path | Who | What |
| --- | --- | --- | --- |
| `POST` | `/accounts` | customer | Request an account: creates the collateral deposit as `PENDING` and reserves the account id |
| `POST` | `/webhooks/payments` | provider (signed) | Deposit outcome: success opens the account, limit and active card atomically; failure marks the deposit `FAILED` |
| `POST` | `/cards/{id}/authorizations` | customer | Purchase, recorded as debt immediately |
| `POST` | `/cards/{id}/freeze` · `/unfreeze` | customer | Freeze or unfreeze the card; a frozen card rejects purchases |
| `POST` | `/accounts/{id}/repayments` | customer | Repayment, never above the debt |
| `POST` | `/accounts/{id}/close` | customer | Collateral pays the debt, remainder refunded, account and card closed |
| `GET` | `/accounts/{id}` | customer | Status, limit, debt, available credit, deposit and card |
| `GET` | `/accounts/{id}/ledger` | customer | The account's journal entries |
| `GET` | `/health` | — | Database connectivity (200 / 503) |

Example with the development tokens (`make up` first):

```bash
curl -s -X POST localhost:8000/accounts \
  -H "Authorization: Bearer local-dev-customer-a-token" \
  -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"deposit_amount_minor": 100000}'
```

`scripts/api-walkthrough.sh` shows the full flow, including how the provider signs the webhook. Errors always use one envelope — `{"error": {"code", "message", "request_id", "details"}}` — with stable codes listed in [docs/api.md](docs/api.md).

## How correctness is guaranteed

| Concern | Mechanism |
| --- | --- |
| Money never appears or disappears | Double-entry ledger: every operation is a journal entry whose signed lines sum to zero; append-only and balanced-at-commit enforced by PostgreSQL triggers ([ADR 0006](docs/decisions/0006-double-entry-ledger-with-balance-cache.md)) |
| No rounding errors | Integer cents everywhere; floats and decimals rejected at the API and forbidden in models ([ADR 0007](docs/decisions/0007-integer-cents-and-basis-points.md)) |
| No overspending under concurrency | Pessimistic row lock on the credit account, fixed lock order, `lock_timeout`; `CHECK (0 <= debt <= limit)` as the last line of defense ([ADR 0001](docs/decisions/0001-pessimistic-locking.md), [ADR 0008](docs/decisions/0008-database-guards-as-last-line-of-defense.md)) |
| Retries applied once | `Idempotency-Key` claimed as the first write of the transaction (`INSERT … ON CONFLICT`); concurrent duplicates wait and replay ([ADR 0002](docs/decisions/0002-idempotency-keys-and-request-fingerprint.md)) |
| Only valid state changes | Explicit state machines per entity, checked under the row lock |
| Customers see only their own data | Owner-scoped lookups; another customer's resource answers exactly like a missing one (404) |
| Webhook cannot be forged or replayed | HMAC-SHA256 over timestamp + raw body, ±5 minutes, idempotency inside the window ([ADR 0009](docs/decisions/0009-simulated-token-auth-and-signed-webhook.md)) |
| Who did what | Append-only audit events in the same transaction as each mutation; rejected attempts of authenticated callers (business rules, wrong role, IDOR probes) audited too; unauthenticated attempts logged as security events; JSON logs with a request id |
| Cache drift detectable | `make reconcile` recomputes balances from the ledger |

## Tests

PostgreSQL only — never SQLite ([ADR 0005](docs/decisions/0005-postgresql-in-every-environment.md)). Every test names in its docstring the bug it catches and the spec rule it protects; all 35 business rules are covered.

- **Real concurrency**: threads with their own connections released together — 10 purchases racing for one limit approve exactly the possible count; purchase vs closure and repayment vs purchase always match a serial order. Run 10 times in a row without failures.
- **Idempotency**: replays, reused keys with another body, 8 concurrent requests with one key → exactly one effect.
- **Property-based (Hypothesis)**: a state machine drives random purchases, repayments, closures, card freezes and retries through the API, checking every invariant and a reference model after each step (200 examples × 25 steps in CI).
- **Security**: permission matrix per endpoint, IDOR, webhook signature tampering and expiry.
- **Database guards**: raw SQL that bypasses Python proves each trigger and constraint.

CI runs lint and module boundaries, the suite (with the Hypothesis CI profile), `make up` + walkthrough + reconcile + tests from a clean checkout, and Terraform validation. Deployment to AWS is a separate, manual workflow (`Deploy`: staging or production) that reruns those gates, shows the Terraform plan for approval, migrates, then rolls out ([ADR 0010](docs/decisions/0010-deploy-pipeline.md)).

## Architecture

One deployable Django service — a **modular monolith** ([ADR 0003](docs/decisions/0003-modular-monolith-with-service-boundaries.md)):

```
backend/
  accounts/     CreditAccount and its balance cache
  collateral/   CollateralDeposit and its state machine
  cards/        Card (internal token + last 4 digits, never a card number)
  ledger/       LedgerAccount, JournalEntry, JournalLine
  idempotency/  IdempotencyRecord
  audit/        AuditEvent
  core/         operations core: flows, API, auth, money, observability (no models)
infra/terraform/  AWS: ECS Fargate, RDS PostgreSQL 18, ALB, Secrets Manager (code only)
docs/             specifications and decisions
tasks/            implementation plan and phase contracts
```

The six apps never import each other; the operations core calls only their `services.py`, which return immutable DTOs. `import-linter` fails the build on any violation. Cross-app references are UUIDs ([ADR 0004](docs/decisions/0004-uuid-references-without-foreign-keys.md)).

## Documentation

| Document | Content |
| --- | --- |
| [docs/business-rules.md](docs/business-rules.md) | Scope, money, account opening, limit, purchases, repayments, closure |
| [docs/state-machines.md](docs/state-machines.md) | Deposit, account and card states |
| [docs/ledger.md](docs/ledger.md) | Ledger accounts, postings, cache, reconciliation |
| [docs/invariants.md](docs/invariants.md) | Non-negotiable invariants and concurrency rules |
| [docs/idempotency.md](docs/idempotency.md) | Idempotency keys and records |
| [docs/security.md](docs/security.md) | Authentication, roles, IDOR, webhook signatures, secrets |
| [docs/audit.md](docs/audit.md) | Audit events, JSON logs, health check |
| [docs/api.md](docs/api.md) | Endpoints, bodies, error glossary |
| [docs/testing.md](docs/testing.md) | Test conventions and mandatory suites |
| [docs/architecture.md](docs/architecture.md) | Technical decisions and module boundaries |
| [docs/decisions/](docs/decisions/) | ADR 0001–0010 |
| [infra/terraform/README.md](infra/terraform/README.md) | AWS architecture, deploy pipeline and its one-time setup |

## Scope and assumptions

- USD only; one collateral deposit and one card per account; a customer may own several fully isolated accounts.
- The deposit provider is simulated by the signed webhook; a failed deposit simply opens nothing.
- Authentication is simulated with static tokens but behaves like real authentication.
- **Out of scope:** interest, fees, statements, a real card network, a frontend, additional deposits or limit increases, authorization/capture split.
- Terraform and the deploy pipeline are delivered as validated code; neither has run against a real AWS account.
