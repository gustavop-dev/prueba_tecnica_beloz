# Active Context

> Memory Bank file. Updated 2026-10-30.

## Current state

Specification phase finished. All specs live in `docs/` (index in `docs/architecture.md` §7). No application code exists. The phased implementation plan (F0–F5) is approved: `tasks/implementation-plan.md`.

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
- No code is written until the operator starts F0.

## Next steps

1. Operator confirms repository visibility/name, commit policy at phase gates and the `vps-ops-toolkit` URL.
2. Start F0 when the operator says so.
