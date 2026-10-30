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
| Money never appears or disappears | Double-entry ledger: every operation is a journal entry whose signed lines sum to zero; append-only and balanced-at-commit enforced by PostgreSQL triggers ([ADR 0006](docs/adr/0006-double-entry-ledger-with-balance-cache.md)) |
| No rounding errors | Integer cents everywhere; floats and decimals rejected at the API and forbidden in models ([ADR 0007](docs/adr/0007-integer-cents-and-basis-points.md)) |
| No overspending under concurrency | Pessimistic row lock on the credit account, fixed lock order, `lock_timeout`; `CHECK (0 <= debt <= limit)` as the last line of defense ([ADR 0001](docs/adr/0001-pessimistic-locking.md), [ADR 0008](docs/adr/0008-database-guards-as-last-line-of-defense.md)) |
| Retries applied once | `Idempotency-Key` claimed as the first write of the transaction (`INSERT … ON CONFLICT`); concurrent duplicates wait and replay ([ADR 0002](docs/adr/0002-idempotency-keys-and-request-fingerprint.md)) |
| Only valid state changes | Explicit state machines per entity, checked under the row lock |
| Customers see only their own data | Owner-scoped lookups; another customer's resource answers exactly like a missing one (404) |
| Webhook cannot be forged or replayed | HMAC-SHA256 over timestamp + raw body, ±5 minutes, idempotency inside the window ([ADR 0009](docs/adr/0009-simulated-token-auth-and-signed-webhook.md)) |
| Who did what | Append-only audit events in the same transaction as each mutation; rejected attempts of authenticated callers (business rules, wrong role, IDOR probes) audited too; unauthenticated attempts logged as security events; JSON logs with a request id |
| Cache drift detectable | `make reconcile` recomputes balances from the ledger |

## Tests

PostgreSQL only — never SQLite ([ADR 0005](docs/adr/0005-postgresql-in-every-environment.md)). Every test names in its docstring the bug it catches and the spec rule it protects; all 35 business rules are covered.

- **Real concurrency**: threads with their own connections released together — 10 purchases racing for one limit approve exactly the possible count; purchase vs closure and repayment vs purchase always match a serial order. Run 10 times in a row without failures.
- **Idempotency**: replays, reused keys with another body, 8 concurrent requests with one key → exactly one effect.
- **Property-based (Hypothesis)**: a state machine drives random purchases, repayments, closures, card freezes and retries through the API, checking every invariant and a reference model after each step (200 examples × 25 steps in CI).
- **Security**: permission matrix per endpoint, IDOR, webhook signature tampering and expiry.
- **Database guards**: raw SQL that bypasses Python proves each trigger and constraint.

CI runs lint and module boundaries, the suite (with the Hypothesis CI profile), `make up` + walkthrough + reconcile + tests from a clean checkout, and Terraform validation. Deployment to AWS is a separate, manual workflow (`Deploy`: staging or production) that reruns those gates, shows the Terraform plan for approval, migrates, then rolls out ([ADR 0010](docs/adr/0010-deploy-pipeline.md)).

## Architecture

One deployable Django service — a **modular monolith** ([ADR 0003](docs/adr/0003-modular-monolith-with-service-boundaries.md)):

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

The six apps never import each other; the operations core calls only their `services.py`, which return immutable DTOs. `import-linter` fails the build on any violation. Cross-app references are UUIDs ([ADR 0004](docs/adr/0004-uuid-references-without-foreign-keys.md)).

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
| [docs/adr/](docs/adr/) | ADR 0001–0010, indexed with the origin of each decision |
| [infra/terraform/README.md](infra/terraform/README.md) | AWS architecture, deploy pipeline and its one-time setup; deployment ADR in [infra/terraform/adr/](infra/terraform/adr/) |
| [PROMPTS.md](PROMPTS.md) | The author's instructions, verbatim and in order |

## Planning and technical decisions

This exercise was built with an AI coding assistant. The author designed the system and made the technical decisions; the assistant wrote the specifications down, proposed options where something was open, and implemented them. The author's own messages are in [PROMPTS.md](PROMPTS.md), unedited.

### How the work was planned

1. **Specification before code.** Nothing was executed until the author allowed it. The author defined the system one area at a time, in a fixed order: context → technical and architecture rules → business rules → state machines → the ledger as source of truth → non-negotiable invariants → idempotency → security → audit → tests → deliverables. After each area, open questions and contradictions were resolved and the result was written to `docs/` with an ID per rule (`BR-*`, `INV-*`, `CON-*`, `IDEM-*`, `SEC-*`, `AUD-*`, `TEST-*`), so code and tests could cite them.
2. **A phased plan with verifiable gates.** The author set the phases — F0 skeleton/Docker/CI, F1 money/ledger/models, F2 flows/API, F3 concurrency/idempotency, F4 security/audit, F5 documentation — each with acceptance criteria that can be checked by running commands, and `make up` + `make test` from a clean clone as the termination criterion. The assistant argued six adjustments (R0–R6); the author accepted them, e.g. locking and ownership from F2 rather than later, and option B for account opening. See [tasks/implementation-plan.md](tasks/implementation-plan.md).
3. **An orchestrator and one subagent per Django app.** The author's distribution: an orchestrator (Opus, effort `xhigh`) owning the operations core, the API and the integration; one subagent per app (Opus, effort `high`) working only inside its app, from written contracts ([tasks/f1-contracts.md](tasks/f1-contracts.md) … `f4`). Each phase had its own branch and git worktree, each subagent its own worktree, and every phase ended with a review, a pull request and a stop until the author approved the merge.
4. **After the plan**, the author requested three follow-ups and chose among the options offered: card freeze/unfreeze endpoints (#9), auditing of rejected attempts (#10) and a manual, approved deploy pipeline (#11).

### Key decisions

| Decision | Taken by | Where |
| --- | --- | --- |
| Single deployable modular monolith; six apps communicating only through service functions | Author | [ADR 0003](docs/adr/0003-modular-monolith-with-service-boundaries.md) |
| PostgreSQL everywhere, no SQLite even in tests | Author | [ADR 0005](docs/adr/0005-postgresql-in-every-environment.md) |
| Integer cents, floats forbidden in every layer; collateral ratio 1.0 | Author | [ADR 0007](docs/adr/0007-integer-cents-and-basis-points.md) |
| Double-entry, append-only ledger with its postings; balance cache with a `CHECK`; `reconcile` | Author | [ADR 0006](docs/adr/0006-double-entry-ledger-with-balance-cache.md) |
| Pessimistic locking with a fixed lock order (optimistic locking analysed and rejected for this workload) | Author | [ADR 0001](docs/adr/0001-pessimistic-locking.md) |
| Idempotency keys with a SHA-256 request fingerprint, stored in the same transaction | Author | [ADR 0002](docs/adr/0002-idempotency-keys-and-request-fingerprint.md) |
| Static tokens mapped to principal + role, IDOR answered with `404`, HMAC webhook with a 5-minute window, no PAN | Author | [ADR 0009](docs/adr/0009-simulated-token-auth-and-signed-webhook.md) |
| State machines, opening via the provider's confirmation, closure rules (full refund iff debt is 0) | Author | [docs/state-machines.md](docs/state-machines.md), [docs/business-rules.md](docs/business-rules.md) |
| Mandatory tests: real concurrency, idempotency, invalid transitions, security, Hypothesis, reconcile | Author | [docs/testing.md](docs/testing.md) |
| Cross-app UUID references without foreign keys; basis points for the ratio; extra database guards | Assistant, approved by the author | [ADR 0004](docs/adr/0004-uuid-references-without-foreign-keys.md), [ADR 0007](docs/adr/0007-integer-cents-and-basis-points.md), [ADR 0008](docs/adr/0008-database-guards-as-last-line-of-defense.md) |
| Deploy pipeline: manual with approval, staging + production, migrate then roll out | Author, among the assistant's options | [ADR 0010](docs/adr/0010-deploy-pipeline.md) |
| AWS as the target; ECS Fargate, RDS PostgreSQL 18 and Secrets Manager as the services | Author (AWS); assistant, approved (services) | [ADR 0011](infra/terraform/adr/0011-aws-deployment-architecture.md) |

## Scope and assumptions

- USD only; one collateral deposit and one card per account; a customer may own several fully isolated accounts.
- The deposit provider is simulated by the signed webhook; a failed deposit simply opens nothing.
- Authentication is simulated with static tokens but behaves like real authentication.
- **Out of scope:** interest, fees, statements, a real card network, a frontend, additional deposits or limit increases, authorization/capture split.
- Terraform and the deploy pipeline are delivered as validated code; neither has run against a real AWS account.
