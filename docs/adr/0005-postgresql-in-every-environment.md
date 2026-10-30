# ADR 0005 — PostgreSQL in every environment, tests included

- Status: **accepted**
- Related: [architecture.md](../architecture.md) (STACK-3), [testing.md](../testing.md) (TEST-2)

## Context

The correctness of this system rests on database features: `SELECT … FOR UPDATE` row locks (ADR 0001), `CHECK` constraints on the balance cache, PL/pgSQL triggers that make the ledger, the audit log and idempotency records append-only, deferred constraint triggers that reject unbalanced journal entries at commit, `INSERT … ON CONFLICT` for idempotency claims, and `lock_timeout`.

## Decision

PostgreSQL 18 is the only database: locally (Docker Compose), in CI (a service container and a clean-clone job) and in AWS (RDS). SQLite is never configured, not even for tests; a test asserts the engine and the server version.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| SQLite for fast tests | No row locks, no PL/pgSQL triggers, different constraint and concurrency semantics. Tests would pass against a database that cannot enforce the invariants they claim to test. |
| Mocks for the database | The guards are the database; mocking them tests nothing. |

## Consequences

- Concurrency tests use real threads and real connections; guard tests use raw SQL that bypasses Python.
- The full suite (≈ 850 tests) still runs in about 25 seconds in Docker.
- Parallel work needs separate databases: each git worktree gets its own Compose project and PostgreSQL container.
