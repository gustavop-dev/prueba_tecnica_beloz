# Invariants

> Status: **final** — implemented and verified (F0–F5).

Non-negotiable properties of the system. They hold at every commit, for every credit account, under any concurrency. Each one is protected by Python **and** at least one guard that does not depend on application code being correct.

| ID | Invariant | Guards |
| --- | --- | --- |
| INV-1 | `available_credit = credit_limit − outstanding` is never negative, including under concurrency. Debt is never negative either: `0 ≤ outstanding ≤ credit_limit`. | Service validation under a row lock ([concurrency](#concurrency-rules)); PostgreSQL `CHECK` (CACHE-3). |
| INV-2 | Every journal entry sums to zero. | Ledger service; PostgreSQL deferred constraint trigger (LED-6). |
| INV-3 | The ledger is append-only: entries and lines are never updated or deleted. | No update/delete paths in code; PostgreSQL trigger (LED-4). |
| INV-4 | No money operation is applied twice, no matter how many times it is retried. | `UNIQUE (principal, endpoint, key)` written in the same transaction as the effect ([idempotency.md](idempotency.md)); the deposit state machine for the provider webhook (IDEM-15). |
| INV-5 | No invalid state transition is possible. | State machines in [state-machines.md](state-machines.md), checked under the row lock. |
| INV-6 | Every mutation is recorded in the audit log with who, what, when and in which request. | `AuditEvent` written in the same transaction as the mutation; append-only trigger ([audit.md](audit.md)). |

## Concurrency rules

These rules make INV-1 and INV-5 hold under concurrent requests. The rationale is in [ADR 0001](decisions/0001-pessimistic-locking.md).

| ID | Rule |
| --- | --- |
| CON-1 | Every money operation runs in a single database transaction. |
| CON-2 | Its first step is locking the credit account row with `SELECT … FOR UPDATE`. A concurrent operation on the same account waits until the first one commits or rolls back. |
| CON-3 | Balance and state validation happen **after** acquiring the lock, and the writes happen inside the same lock. Nothing read before the lock may be used to decide. |
| CON-4 | Locks are always acquired in this fixed order: `CreditAccount` → `CollateralDeposit` → `Card`. An operation may skip a step, never reverse it. This prevents deadlocks. The idempotency record insert (IDEM-10) precedes all of them; it only conflicts with requests carrying the same key, which hold no other lock while they wait. |
| CON-5 | Deposit confirmation, where no credit account exists yet, locks the `PENDING` deposit row. This respects CON-4 because the account it creates is new and not visible to other transactions. |
| CON-6 | No operation locks two credit accounts: accounts are isolated (BR-OWN-2). |
| CON-7 | Global ledger accounts (`cash`, `merchant_payable`) are never locked and keep no mutable balance column. Their balance is the sum of their lines, so every operation only inserts rows and they do not become a hot spot that serializes all accounts. |
| CON-8 | The PostgreSQL `CHECK` (CACHE-3) is the last line of defense if any of these rules is broken. |
| CON-9 | Money transactions set `lock_timeout` (`DB_LOCK_TIMEOUT_MS`, default 5000 ms) so a request never waits indefinitely. A timeout rolls the whole operation back and returns `503 LOCK_TIMEOUT`; the same `Idempotency-Key` can be retried. |
