# Active Context

> Memory Bank file. Updated 2026-10-30.

## Current state

F0 (#1), stack cleanup (#2), F1 (#3), F2 (#4) and F3 (#5) merged. F4 implemented on branch `phase/f4-security-audit` (audit app by its subagent; request context, audit trail in every flow, HMAC webhook, JSON logs by the orchestrator) and waiting for the gate review. Contracts: `tasks/f4-contracts.md`. Repository: https://github.com/gustavop-dev/prueba_tecnica_beloz (public). No domain logic exists yet. Plan: `tasks/implementation-plan.md`.

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

1. Operator reviews F4.
2. F5: README, specs to final, architecture ADRs 0003–0009, Terraform for AWS (code only, including a non-superuser application role), Memory Bank refresh, clean-clone termination check.
