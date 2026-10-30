# Architecture and Technical Decisions

> Status: **final** — implemented and verified (F0–F5).

## 1. Deployment

| ID | Decision |
| --- | --- |
| ARCH-1 | A single deployable service: a **modular monolith**. |
| ARCH-2 | Target cloud: AWS. Infrastructure is delivered as Terraform code under `infra/terraform/`; it is not applied to a real account in this exercise. |
| ARCH-3 | Local environment runs with Docker Compose: PostgreSQL plus the application (see [audit.md](audit.md#3-health-check) for health checks). |

## 2. Stack

| ID | Decision |
| --- | --- |
| STACK-1 | Python 3.14 and Django 6.1: the latest versions officially supported by Django REST Framework 3.18, psycopg 3.3 and Hypothesis 6.170 (pinned in `backend/uv.lock`). |
| STACK-2 | API: Django REST Framework. |
| STACK-3 | Database: PostgreSQL, latest major version, in **every** environment including tests. SQLite is forbidden. |
| STACK-4 | Tests: `pytest`, `pytest-django`, `hypothesis` (see [testing.md](testing.md)). |
| STACK-5 | Dependency management: `uv`. Lint and format: `ruff`. Module boundaries: `import-linter`. |

## 3. Apps

The Django project has these apps, each owning its models:

| App | Owns |
| --- | --- |
| `accounts` | `CreditAccount`: status, `credit_limit_minor`, `outstanding_minor` cache. |
| `collateral` | `CollateralDeposit`. |
| `cards` | `Card`: internal token, last 4, status. |
| `ledger` | `LedgerAccount`, `JournalEntry`, `JournalLine`. |
| `idempotency` | `IdempotencyRecord`. |
| `audit` | `AuditEvent`. |

### Operations core

`backend/core/` is the **operations core**: it owns no models and no migrations, so it is not a seventh domain app. It is listed in `INSTALLED_APPS` only so Django discovers its management commands. It holds:

| Module | Content |
| --- | --- |
| `core/flows` | Cross-app operations: opening, provider report, purchase authorization, repayment, closure. Each runs in one `transaction.atomic`. |
| `core/api` | DRF views, serializers, URLs, exception handler with the error envelope ([api.md](api.md)). |
| `core/money` | Strict amount validation and the basis-points limit calculation. |
| `core/auth` | Token → principal + role resolution, permissions, webhook signature verification. |
| `core/observability` | Request id middleware, JSON logging, `/health`. |
| `core/management` | The `reconcile` command. |

### Dependency direction

```
core ──► <app>.services   (for each of the 6 apps)
apps ──► nothing          (the 6 apps never import each other)
```

| ID | Rule |
| --- | --- |
| DEP-1 | The six apps are independent: none imports another. |
| DEP-2 | `core` imports only `<app>.services` (and the DTOs they return), never `<app>.models`. |
| DEP-3 | Services return immutable dataclasses, never model instances, so no caller can save or mutate another app's rows. |

## 4. Module boundaries

| ID | Rule |
| --- | --- |
| MOD-1 | Apps communicate **only** through public service functions. |
| MOD-2 | An app never imports another app's models and never writes to another app's tables. |
| MOD-3 | Cross-app references are UUIDs without a `ForeignKey`. Existence is validated through the owning app's service. This trades database referential integrity for real module boundaries. |
| MOD-4 | An operation spanning several apps runs in one `transaction.atomic` opened by the orchestrating service, which calls the other apps' services inside it, so all their writes commit or roll back together. |
| MOD-5 | Dependencies between apps have no cycles. |
| MOD-6 | `import-linter` enforces MOD-1, MOD-2, MOD-5 and DEP-1..2 in CI (TEST-ARCH-1). |

## 5. Repository layout

```
backend/            Django project and apps
infra/terraform/    AWS infrastructure as code
docs/               Specifications and decisions
tasks/              Work breakdown for implementation
```

## 6. Language

All code, identifiers, documentation and Markdown files are written in English.

## 7. Documentation index

| Document | Content |
| --- | --- |
| [api.md](api.md) | Endpoints, error format, error glossary. |
| [business-rules.md](business-rules.md) | Scope, money, accounts, limit, deposit, purchases, payments, closure. |
| [state-machines.md](state-machines.md) | Deposit, credit account and card states and transitions. |
| [ledger.md](ledger.md) | Double-entry ledger, postings, balance cache, reconciliation. |
| [invariants.md](invariants.md) | Non-negotiable invariants and concurrency rules. |
| [idempotency.md](idempotency.md) | Idempotency keys and records. |
| [security.md](security.md) | Authentication, roles, IDOR, webhook signing, card data, secrets, input validation. |
| [audit.md](audit.md) | Audit events, structured logs, health check. |
| [testing.md](testing.md) | Test conventions and mandatory tests. |
| [decisions/](decisions/) | ADR 0001 pessimistic locking · 0002 idempotency keys · 0003 modular monolith · 0004 UUID references without FKs · 0005 PostgreSQL everywhere · 0006 double-entry ledger with cache · 0007 integer cents and basis points · 0008 database guards · 0009 token auth and signed webhook · 0010 deploy pipeline. |
| [../infra/terraform/README.md](../infra/terraform/README.md) | AWS architecture, security decisions and deployment outline (Terraform, not applied). |
