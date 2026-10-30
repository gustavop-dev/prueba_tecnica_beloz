# ADR 0006 — Double-entry, append-only ledger with a balance cache

- Status: **accepted**
- Related: [ledger.md](../ledger.md) (LED-1..6, CACHE-1..4), [invariants.md](../invariants.md) (INV-1..3)

## Context

Balances must be explainable and auditable: every cent has to come from somewhere and go somewhere. At the same time, authorizing a purchase must be fast and must not sum the whole history of the account.

## Decision

- **The ledger is the source of truth.** Every operation is a journal entry whose signed lines sum to zero, across four ledger accounts: `cash` and `merchant_payable` (global), `customer_collateral` and `customer_receivable` (one pair per credit account).
- **Append-only.** Journal rows are never updated or deleted; triggers enforce it in PostgreSQL.
- **Balanced at commit.** Deferred constraint triggers reject an entry that does not sum to zero or has fewer than two lines.
- **A cache on the credit account** (`credit_limit_minor`, `outstanding_minor`) is updated in the same transaction as the entry and guarded by `CHECK (0 <= outstanding_minor <= credit_limit_minor)`.
- **`reconcile`** recomputes the cache from the ledger and reports any mismatch.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| A mutable `balance` column only | Fast, but history is lost and a bug silently corrupts money. |
| Event sourcing with balances computed on every read | Correct, but every authorization would scan the account's history; the cache gives the same answer in one row read. |
| A balance column on the global accounts | Every operation of every customer would lock the same row (CON-7). Global balances are computed from lines instead. |

## Consequences

- Corrections are new entries, never edits.
- The cache can drift only through a bug or direct SQL; `reconcile` detects it (tested by tampering with the cache).
- Each money operation writes a few more rows, which is negligible next to the guarantees.
