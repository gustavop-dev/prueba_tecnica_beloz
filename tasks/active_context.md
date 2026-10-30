# Active Context

> Memory Bank file. Updated 2026-10-30.

## Current state

F0 merged (#1). F1 implemented on branch `phase/f1-money-ledger` (four app subagents in task worktrees, merged into the phase branch; core money and reconcile by the orchestrator) and waiting for the operator's gate review. Contracts used: `tasks/f1-contracts.md`. Repository: https://github.com/gustavop-dev/prueba_tecnica_beloz (public). No domain logic exists yet. Plan: `tasks/implementation-plan.md`.

## Recent focus

- Specs written: business rules, state machines, ledger, invariants, idempotency, security, audit and observability, testing, API, architecture, ADR 0001 (pessimistic locking) and ADR 0002 (idempotency keys).
- Memory Bank initialized with `/methodology-setup`.

## Active decisions

- Orchestrator: Opus, effort `xhigh`, owns infrastructure, shared kernel, cross-app flows, API and cross-app tests.
- Subagents: Opus, effort `high`, one per Django app (`accounts`, `collateral`, `cards`, `ledger`, `idempotency`, `audit`).
- Each phase closes with an orchestrator review gate and stops for operator approval.
- Termination: `make up` and `make test` pass from a clean clone.
- Account opening uses option B: `POST /accounts` creates the `PENDING` deposit and reserves the account id (`docs/api.md`).
- Operations core lives in `backend/core/` (no models).
- Git workflow: one branch per phase (`phase/fN-*`) in its own worktree; one task worktree per subagent; commit at the end of each phase; PR to `main` per phase.

## Next steps

1. Operator reviews F1; PR #2 (AI stack cleanup) awaits the operator's merge.
2. Merge F1 into `main`, then branch `phase/f2-flows-api` from it.
3. F2: orchestrator builds `core/flows`, `core/auth` and `core/api` on the F1 service contracts.
