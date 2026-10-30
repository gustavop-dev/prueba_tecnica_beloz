# ADR 0003 — Modular monolith with service-function boundaries

- Status: **accepted**
- Related: [architecture.md](../architecture.md) (ARCH-1, MOD-1..6, DEP-1..3)

## Context

The domain has six clear areas — credit accounts, collateral, cards, ledger, idempotency, audit — and every money operation touches several of them **atomically**: a purchase moves the account's cache, writes a journal entry, an idempotency record and an audit event, all or nothing.

## Decision

One deployable Django service, split into six apps that never import each other. Cross-app operations live in an operations core (`backend/core/`) that owns no models and calls only each app's `services.py`, which returns immutable dataclasses. `import-linter` enforces the boundaries in CI.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Microservices | Atomicity across services needs sagas or two-phase commit; for money this adds failure modes (partial postings, compensations) with no benefit at this scale. |
| A single Django app | Simplest start, but nothing stops the ledger code from being written to by any view; boundaries erode silently. |
| Apps importing each other's models | Django's default style. Any app can then `save()` another app's rows and bypass its invariants. |

## Consequences

- One transaction spans the whole operation (MOD-4); the database guarantees all-or-nothing.
- Apps can be developed in parallel by different people (F1 was built by four agents at once) because the contracts are the service signatures.
- Boundary violations fail the build (`lint-imports`), verified by injecting one on purpose.
- Extracting an app into its own service later is possible: its public surface is already a function API with DTOs.
