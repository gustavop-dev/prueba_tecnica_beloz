# ADR 0004 — Cross-app references by UUID, without foreign keys

- Status: **accepted**
- Related: [architecture.md](../architecture.md) (MOD-3), ADR 0003

## Context

With module boundaries (ADR 0003), a `ForeignKey` from `cards.Card` to `accounts.CreditAccount` would require `cards` to import `accounts.models`, breaking the boundary. Identifiers are also exposed in URLs, where sequential integers are enumerable (SEC-IDOR-3).

## Decision

- Every primary key is a UUID (v4).
- A reference to another app's row is a plain `UUIDField` named `<thing>_id`, without a database foreign key. Existence is checked through the owning app's service.
- Foreign keys are used only inside one app (e.g. `JournalLine → JournalEntry`).

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Foreign keys across apps | Referential integrity for free, but couples the apps' models and migrations. |
| Integer ids | Smaller indexes, but enumerable in URLs and they leak volumes. |

## Consequences

- The database no longer guarantees that a `credit_account_id` exists. This is acceptable because every cross-app row is created **in the same transaction** as the row it references (opening creates deposit confirmation, account, ledger accounts and card together), and `reconcile` cross-checks accounts, deposits and the ledger.
- Lookups by another customer's id are indistinguishable from missing ids (SEC-IDOR-2), and ids cannot be guessed.
