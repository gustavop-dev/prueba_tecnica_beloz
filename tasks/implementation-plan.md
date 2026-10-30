# Implementation Plan

> Status: **approved** on 2026-10-30. Specs are the source of truth: `docs/` (index in `docs/architecture.md` §7). This plan only says who builds what, in which order, and how each phase is verified.

## 1. Team

| Role | Model / effort | Owns |
| --- | --- | --- |
| Orchestrator (main session) | Opus, `xhigh` | Repository and infrastructure (settings, URLs, `pyproject.toml`, `Makefile`, Docker, CI, `import-linter` contracts), the operations core `backend/core/`, cross-app tests, phase reviews. |
| App subagents | Opus, `high` | One per app — `accounts`, `collateral`, `cards`, `ledger`, `idempotency`, `audit` — writing only inside `backend/<app>/`. |

### Working rules for subagents

| ID | Rule |
| --- | --- |
| TEAM-1 | A subagent writes only inside `backend/<app>/`: models, migrations, database guards, state machine, `services.py`, tests. Shared files are written only by the orchestrator. |
| TEAM-2 | Each subagent's brief contains only the spec sections relevant to its app (distributed Memory Bank), plus `docs/architecture.md` §3–4 and `docs/testing.md` §1. |
| TEAM-3 | Services return immutable dataclasses, never model instances (DEP-3). |
| TEAM-4 | Each subagent runs its tests with its own test database: `TEST_DB_NAME=test_<app>`, so parallel runs do not collide on PostgreSQL. |
| TEAM-5 | Tests follow the `backend-test-coverage` skill: three-part definition of done, permission matrix, docstring naming the bug and citing the spec ID. |
| TEAM-6 | A subagent never changes a spec. If a spec looks wrong or incomplete, it stops and reports to the orchestrator. |

### Phase gate (orchestrator, at the end of every phase)

1. `make lint` and `make test` green.
2. Diff reviewed against the spec IDs the phase covers.
3. `reconcile` clean (from F1 on).
4. `tasks/tasks_plan.md` and `tasks/active_context.md` updated.
5. **Stop and report to the operator** before starting the next phase.

## 2. Versions

Python 3.14.10 · Django 6.1.2 · DRF 3.18.3 · psycopg 3.3.6 · Hypothesis 6.170 · PostgreSQL 18.6. Python 3.15 is excluded because Django 6.1 does not support it officially. `pytest-django` 4.14 does not declare Django 6.1 yet: verified in F0.

## 3. Phases

### F0 — Skeleton, repository, Docker, CI · orchestrator only

1. `/repo-cleanup` in report mode over the AI stack extras; apply only what the operator approves.
2. Repository: `git init`, `.gitignore`, create the GitHub repository and push.
3. `backend/` with `uv`, pinned versions, `uv.lock`.
4. Django project `config`: settings from environment, fail closed on missing secrets (SEC-SECRET-3), PostgreSQL only, `TEST_DB_NAME` support.
5. The six empty apps and `backend/core/` with its module layout; `import-linter` contracts DEP-1..2.
6. `GET /health` (OBS-4).
7. `Dockerfile` (`python:3.14-slim` + uv) and Compose: `postgres:18.6-alpine` with `pg_isready`, app `depends_on: service_healthy`, migrations on start, app health check on `/health` (OBS-5).
8. `Makefile`: `up`, `down`, `test`, `lint`, `migrate`. `make test` runs inside the container, so a clean clone only needs Docker and make.
9. GitHub Actions: ruff, `lint-imports`, pytest with a PostgreSQL 18.6 service. Replaces the stack's MySQL workflows.
10. `.env.example` with development-only placeholder values.

**Acceptance criteria**

- From a fresh clone: `make up` leaves both containers healthy and `curl -fsS localhost:8000/health` returns `200`.
- With PostgreSQL stopped, `/health` returns `503`.
- `make test` exits 0, including a test asserting the database vendor is `postgresql`.
- `make lint` exits 0.
- `grep -ri sqlite backend/` returns nothing.
- CI is green on the pushed commit.

### F1 — Money, ledger, models · subagents `ledger`, `accounts`, `collateral`, `cards` in parallel

| Owner | Work |
| --- | --- |
| `ledger` | `LedgerAccount`, `JournalEntry`, `JournalLine` (signed `amount_minor`, `CHECK <> 0`); append-only trigger (LED-4); deferred balanced-entry trigger (LED-6); data migration for global accounts; services: provision per credit account, `post_entry`, posting helpers per operation (ledger.md §5), balance, lines per credit account. |
| `accounts` | `CreditAccount` with `CHECK` CACHE-3; `ACTIVE → CLOSED`; `lock_for_update`; cache updates; owner-scoped lookup. |
| `collateral` | `CollateralDeposit` with reserved account id; full state machine including closure outcomes. |
| `cards` | `Card` with internal token and last 4, never a PAN; state machine (`INACTIVE` only for the add-on). |
| Orchestrator | `core/money` (strict integer validation, rejects `bool`; `deposit × ratio_bps // 10000`); `reconcile` command (ledger.md §7). |

**Acceptance criteria**

- `make test` green with TEST-SM-1, TEST-DB-1..3 and TEST-REC-1..2.
- A test fails if any model declares `FloatField` or `DecimalField`.
- `manage.py migrate` applies on an empty database and `manage.py reconcile` exits 0.
- `make lint` green (no app imports another).

### F2 — Flows, API, principal and ownership · orchestrator; subagents only to fill service gaps

1. `core/flows`: opening request, provider report (success: confirm deposit → create account with the reserved id → provision ledger → deposit entry → issue `ACTIVE` card; failure: `FAILED`), purchase authorization, repayment, closure.
2. Every money flow locks with `SELECT … FOR UPDATE` in the fixed order (CON-1..8) from the start.
3. `core/auth`: static token → principal + role; owner-scoped lookups everywhere (SEC-AUTH-1..5, SEC-IDOR-1..3, SEC-ROLE-1..3).
4. `core/api`: DRF views, strict serializers (unknown fields rejected), error envelope and glossary, `GET /accounts/{id}`, `GET /accounts/{id}/ledger`. The webhook is not signed yet (F4).

**Acceptance criteria**

- A documented `curl` walkthrough runs end to end — open → webhook → purchase → repayment → close — with balances verified at each step.
- Every `BR-*` rule has at least one test citing its ID (`grep` over test docstrings).
- TEST-SM-2/3 pass, and closure is tested with debt 0, partial debt and debt equal to the deposit.
- IDOR tests: another customer's account, card or operation returns `404`.
- `reconcile` exits 0 after the suite.

### F3 — Concurrency and idempotency · subagent `idempotency` + orchestrator

| Owner | Work |
| --- | --- |
| `idempotency` | `IdempotencyRecord` with `UNIQUE (principal, endpoint, key)`; canonical SHA-256 hash; `claim`, `complete`, `replay` services. |
| Orchestrator | Wire every `POST` (record as first write, IDEM-10); store business rejections (IDEM-11); `lock_timeout` + `LOCK_TIMEOUT` (CON-9). |

**Acceptance criteria**

- TEST-CON-1..4, TEST-IDEM-1..6 and TEST-PROP-1..2 pass.
- The concurrency suite (≥ 10 threads) passes 10 consecutive runs.
- Hypothesis runs ≥ 200 examples in the CI profile.

### F4 — Security hardening and audit · subagent `audit` + orchestrator

| Owner | Work |
| --- | --- |
| `audit` | `AuditEvent` with append-only trigger; recording service that never stores PAN, tokens or signatures. |
| Orchestrator | Audit in the same transaction as every mutation (AUD-1..4); full role permission matrix; webhook HMAC-SHA256 + 5-minute timestamp window, constant-time comparisons (SEC-HOOK-1..5); JSON logs with `request_id` (OBS-1..3). |

**Acceptance criteria**

- TEST-SEC-1..6 pass and every view has the four-row permission matrix.
- Every mutation produces exactly one audit event; a rolled-back operation produces none; `UPDATE`/`DELETE` on audit events is rejected.
- Every log line parses as JSON and carries `request_id`; no PAN, token or signature appears in logs or audit.

### F5 — Documentation, architecture ADRs, Terraform

1. `README.md`: setup, `make` targets, `curl` examples, assumptions, scope.
2. Specs move from **draft** to final.
3. Architecture ADRs: 0003 modular monolith and service boundaries · 0004 UUID references without FKs · 0005 PostgreSQL everywhere · 0006 double-entry ledger with balance cache · 0007 integer cents and basis points · 0008 database guards as last line of defense · 0009 simulated token auth and signed webhook.
4. `infra/terraform/` for AWS, as code only (ARCH-2).
5. Refresh the Memory Bank with `/methodology-setup`.

**Termination criteria**

- From a fresh clone: `make up && make test` exit 0.
- `terraform fmt -check` and `terraform validate` pass (Terraform run through Docker).
- No spec is left in draft and no open question remains.
