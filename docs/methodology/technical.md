# Technical Context

> Memory Bank file, refreshed in F5. Source of truth: `docs/architecture.md`, `docs/testing.md`, `docs/decisions/`, `infra/terraform/README.md`.

## Stack

| Layer | Choice |
| --- | --- |
| Language / framework | Python 3.14.10 · Django 6.1.2 · Django REST Framework 3.18.3 |
| Database | PostgreSQL 18 everywhere (Docker `18.6-alpine`, CI service, RDS); psycopg 3.3.6. No SQLite. |
| Server | gunicorn 26.2 with a JSON log config (`backend/gunicorn.conf.py`) |
| Tests | pytest 9.2 · pytest-django 4.14 · Hypothesis 6.170 (profiles `dev` 30 examples, `ci` 200 × 25 steps) |
| Tooling | uv 0.12.23 (lock: `backend/uv.lock`) · ruff 0.17.3 · import-linter 2.15 |
| Local runtime | Docker Compose: PostgreSQL + app, both health-checked; one Compose project per git worktree |
| Infrastructure | Terraform 1.17.1 + AWS provider 6.71 (locked), validated in Docker, not applied |

## Development setup

Only Docker and make on the host; `.env` is created from `.env.example` on first use.

```bash
make up            # build and start PostgreSQL + API, wait until healthy
make test          # pytest inside the container (ARGS="-k ..."); HYPOTHESIS_PROFILE=ci for the CI profile
make lint          # ruff check + format check + lint-imports
make format        # ruff format + safe fixes on the host files
make walkthrough   # end-to-end API scenario against the running stack (signed webhook included)
make reconcile     # cache and deposits vs ledger
make infra-check   # terraform fmt -check + validate (Docker)
make migrate | logs | down | help
```

Docker targets: `dev` (tests and lint tools) and `runtime` (production). Containers run as a non-root user.

## Environment configuration

| Variable | Notes |
| --- | --- |
| `DJANGO_SECRET_KEY`, `AUTH_TOKENS`, `WEBHOOK_SIGNING_SECRET`, `PAYMENT_PROVIDER_ID`, `POSTGRES_*` | Required; the service refuses to start if any is missing or malformed. |
| `AUTH_TOKENS` | `role:principal_id:token`, comma-separated; tokens ≥ 16 characters. |
| `WEBHOOK_SIGNING_SECRET` | ≥ 32 characters. |
| `POSTGRES_SSLMODE` | `prefer` by default; `require` on AWS. |
| `DB_LOCK_TIMEOUT_MS` | 5000 by default (CON-9). |
| `LOG_LEVEL`, `DJANGO_ALLOWED_HOSTS`, `APP_PORT`, `HYPOTHESIS_PROFILE` | Optional. |

## Design patterns

- Modular monolith; apps expose `services.py` with frozen DTOs; exceptions live in `<app>/exceptions.py` and are re-exported (ADR 0003).
- Double-entry, append-only ledger with a cached balance and `reconcile` (ADR 0006).
- Pessimistic locking with a fixed lock order and `lock_timeout` (ADR 0001).
- Idempotency claim as the first write: `INSERT … ON CONFLICT DO NOTHING` (ADR 0002).
- Database guards for every invariant (ADR 0008).
- Request correlation through a `ContextVar` (request id, idempotency key) shared by audit and logs.

## Testing strategy

Conventions from the `backend-test-coverage` skill: three-part definition of done, docstring naming the bug and the spec ID, permission matrix per view, no conditionals in test bodies. Suites: real-thread concurrency, idempotency, state machines, security (IDOR, webhook signatures), Hypothesis state machine over the API, raw-SQL database guards, reconcile, JSON logs, audit trail. Every BR-* rule is cited by at least one test.

## CI (`.github/workflows/ci.yml`)

Lint and module boundaries · tests on a PostgreSQL 18.6 service with the Hypothesis `ci` profile · clean checkout `make up` + walkthrough + reconcile + `make test` · Terraform fmt + validate.
