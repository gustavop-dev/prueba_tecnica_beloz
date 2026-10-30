# ADR 0001 — Pessimistic locking for money operations

- Status: **accepted**
- Related: [invariants.md](../invariants.md) (INV-1, CON-1..9), [ledger.md](../ledger.md)

## Context

Purchases, payments and closure read the credit account's balance, validate it and write a journal entry plus the updated cache. Two concurrent operations on the same account can both read the same balance and, together, exceed the limit or overpay the debt.

The database `CHECK` would stop the inconsistency, but as a database error instead of a clean business rejection.

## Decision

Use **pessimistic locking**: each money operation locks the credit account row with `SELECT … FOR UPDATE` before validating, and validates and writes inside that lock.

The race is assumed to happen and is prevented up front: the first transaction locks the row and the second one waits in line until the first finishes, then reads the committed state. Waiting is mandatory queuing, not retrying.

## Alternatives considered

### Optimistic locking

Each transaction reads a version, works without locks and, on write, checks the version has not changed. If it changed, the transaction aborts and must retry.

Rejected for this system because:

- Contention is expected on a single row per account. Under contention optimistic locking produces failures and retries; many retries saturate the system and are costly.
- Correct retries on money operations need careful design (backoff, limits, idempotency of every attempt).

Optimistic locking is still valid for money in other contexts:

- Many fintech ledgers are built on DynamoDB, which offers no pessimistic row locks. Optimistic concurrency with conditional writes works there, but it requires more expertise in designing retries.
- It fits workloads with many reads and few writes.
- It fits long edits, such as a user editing a shared document for minutes: holding a lock that long would block everyone else.

None of these applies here: we use PostgreSQL, writes are short and conflicts concentrate on one row.

## Consequences

- Operations on the same credit account are serialized. Operations on different accounts do not block each other (CON-6, CON-7).
- A fixed lock order (CON-4) is required to avoid deadlocks.
- Throughput per account is bounded by the duration of one transaction, so transactions must stay short and never call external services while holding the lock.
