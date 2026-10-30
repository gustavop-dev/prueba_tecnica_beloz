# ADR 0008 — Database guards as the last line of defense

- Status: **accepted**
- Related: [invariants.md](../invariants.md), [ledger.md](../ledger.md), [idempotency.md](../idempotency.md), [audit.md](../audit.md)

## Context

Every invariant is checked in Python, under row locks. But Python checks can be bypassed by a future endpoint, a script, a bug or a migration. For money, a single bypass is enough to break the books.

## Decision

Each non-negotiable invariant has a guard inside PostgreSQL that does not depend on application code:

| Invariant | Guard |
| --- | --- |
| Debt never negative, never above the limit (INV-1) | `CHECK (0 <= outstanding_minor <= credit_limit_minor)`; closed accounts have zero debt |
| Every entry balances (INV-2) | Deferred constraint triggers at commit |
| Ledger, audit and idempotency records are append-only (INV-3, AUD-3) | Triggers reject `UPDATE`/`DELETE`, and `TRUNCATE` for non-superusers |
| Applied once (INV-4) | `UNIQUE (principal, endpoint, key)` plus a deferred trigger forbidding a record without its response |
| Deposit settlement adds up (BR-CLO-1..4) | `CHECK`s on applied and refunded amounts per status |
| No full card number stored (SEC-CARD-1) | `CHECK` on the token format and against 13+ digit runs |

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Python validation only | One bypass corrupts money silently. |
| Guards only, no Python validation | Users would get database errors instead of clear business rejections; under contention, row locks are what turn races into clean rejections. |

## Consequences

- Guards are tested with raw SQL that skips the services, so a migration that drops one fails the suite.
- `TRUNCATE` is allowed to PostgreSQL superusers only, so Django's test runner can reset tables; a superuser could disable any trigger anyway. On RDS no role is a superuser, and the API runs as a role without DDL rights.
- Guard errors carry the rule ID in their message (`LED-4: …`, `AUD-3: …`), which makes violations traceable to the spec.
