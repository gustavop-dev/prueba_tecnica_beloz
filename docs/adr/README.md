# Architecture Decision Records

Each ADR records one decision: its context, the decision, the alternatives considered and the consequences. **Origin** says who made it: the author of the exercise, or the assistant with the author's explicit approval. The author's original instructions are in [PROMPTS.md](../ai-logs/PROMPTS.md).

| ADR | Decision | Origin |
| --- | --- | --- |
| [0001](0001-pessimistic-locking.md) | Pessimistic row locking for money operations, fixed lock order; optimistic locking discussed and rejected for this workload | Author |
| [0002](0002-idempotency-keys-and-request-fingerprint.md) | `Idempotency-Key` on every state-changing `POST`, `UNIQUE (principal, endpoint, key)`, SHA-256 request fingerprint, record written with the effect | Author |
| [0003](0003-modular-monolith-with-service-boundaries.md) | One deployable Django modular monolith; six apps that talk only through service functions | Author |
| [0004](0004-uuid-references-without-foreign-keys.md) | Cross-app references by UUID without foreign keys | Assistant, approved by the author |
| [0005](0005-postgresql-in-every-environment.md) | PostgreSQL in every environment, tests included; no SQLite | Author |
| [0006](0006-double-entry-ledger-with-balance-cache.md) | Double-entry, append-only ledger as source of truth, with a balance cache guarded by a `CHECK` and a `reconcile` command | Author |
| [0007](0007-integer-cents-and-basis-points.md) | Money as integer cents, no floats in any layer; collateral ratio in basis points | Author (integer cents); assistant, approved (basis points) |
| [0008](0008-database-guards-as-last-line-of-defense.md) | Database guards (`CHECK`s, append-only and balance triggers) as the last line of defense | Author (cache `CHECK`, append-only trigger); assistant, approved (the other guards) |
| [0009](0009-simulated-token-auth-and-signed-webhook.md) | Simulated static-token authentication with roles, IDOR answered as `404`, HMAC-signed webhook with a 5-minute window, no PAN | Author |
| [0010](0010-deploy-pipeline.md) | Manual, approved deploy pipeline: plan before approval, migrate then roll out, OIDC roles | Author chose among the assistant's options |
| [0011](../../infra/terraform/adr/0011-aws-deployment-architecture.md) | AWS deployment architecture: ECS Fargate behind an ALB, RDS PostgreSQL 18, Secrets Manager (lives with the Terraform code) | Author (AWS); assistant, approved (services) |

## Format

```
# ADR NNNN — Title
- Status: accepted | superseded by NNNN
- Related: specs and other ADRs
## Context
## Decision
## Alternatives considered
## Consequences
```

An accepted ADR is not edited to change its decision: a new ADR supersedes it.
