# Technical Context

> Memory Bank file. Source of truth: `docs/architecture.md`, `docs/testing.md`, `docs/decisions/`.

## Stack

| Layer | Choice |
| --- | --- |
| Language / framework | Python + Django, latest versions officially supported by DRF, psycopg and Hypothesis (to be pinned in F0). |
| API | Django REST Framework |
| Database | PostgreSQL, latest major, in every environment including tests. No SQLite. |
| Tests | pytest, pytest-django, hypothesis |
| Tooling | uv (dependencies), ruff (lint/format), import-linter (module boundaries) |
| Local runtime | Docker Compose (PostgreSQL + app, health-checked) |
| Infrastructure | Terraform for AWS, delivered as code, not applied |

Versions: _not pinned yet — no code exists (F0 pending)._

## Development setup

Target contract (to be implemented in F0):

```bash
make up     # start PostgreSQL + app with Docker Compose
make test   # run the full test suite against PostgreSQL
```

Both must pass from a clean clone.

## Environment configuration

All secrets come from environment variables (SEC-SECRET-1..4): customer/provider tokens, webhook HMAC secret, database credentials, Django `SECRET_KEY`. `.env.example` is committed; `.env` is git-ignored. The service refuses to start if a required secret is missing.

## Design patterns

- **Modular monolith:** apps talk only through public service functions; no cross-app model imports; cross-app references are UUIDs without `ForeignKey` (MOD-1..6).
- **Double-entry ledger** as source of truth, with a balance cache on `CreditAccount` updated in the same transaction (`ledger.md`).
- **Pessimistic locking** on the credit account row with a fixed lock order (ADR 0001, CON-1..9).
- **Idempotency record** inserted as the first write of the transaction (ADR 0002, IDEM-1..15).
- **Database guards** as last line of defense: `CHECK` on the cache, append-only triggers on ledger and audit, deferred trigger for balanced entries.
- **Integer money** everywhere; collateral ratio in basis points.

## Testing strategy

Conventions from the `backend-test-coverage` skill: three-part definition of done, permission matrix per view, risk-based prioritization. Mandatory suites: real concurrency, idempotency, state machines, security, Hypothesis stateful properties, reconcile, database guards, import-linter. Details: `docs/testing.md`.
