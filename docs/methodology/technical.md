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

Pinned in `backend/uv.lock` (F0): Python 3.14.10 (Docker `python:3.14.10-slim`) · Django 6.1.2 · DRF 3.18.3 · psycopg 3.3.6 · gunicorn 26.2.0 · pytest 9.2.0 · pytest-django 4.14.0 · Hypothesis 6.170.14 · ruff 0.17.3 · import-linter 2.15 · uv 0.12.23 · PostgreSQL `18.6-alpine`.

## Development setup

Only Docker and make are needed on the host. `.env` is created from `.env.example` on first use.

```bash
make up       # build and start PostgreSQL + API, wait until both are healthy
make test     # full pytest suite inside the container against PostgreSQL (ARGS="-k ...")
make lint     # ruff check + ruff format --check + lint-imports
make format   # apply ruff fixes and formatting
make migrate  # apply migrations
make down     # stop the stack
```

The `dev` Docker target includes test and lint tools; `runtime` is the production target. The container runs as a non-root user. CI (`.github/workflows/ci.yml`) runs lint, pytest against a PostgreSQL 18.6 service, and `make up` + `make test` from a clean checkout.

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
