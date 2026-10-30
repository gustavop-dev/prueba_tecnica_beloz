# Active Context

> Memory Bank file. Updated 2026-10-30.

## Current state

The implementation plan is **complete**: F0–F5 are merged into `main` (#1, #3–#7; #2 removed AI stack extras that do not apply to this project). Repository: https://github.com/gustavop-dev/prueba_tecnica_beloz (public). Plan: `tasks/implementation-plan.md`.

The service implements every business rule in `docs/` with 851 tests against PostgreSQL 18. From a fresh clone, `make up`, `make test`, `make walkthrough` and `make reconcile` pass, and `make infra-check` validates the AWS Terraform (code only, never applied). CI runs lint, the suite (Hypothesis `ci` profile), the clean-clone scenario and Terraform validation on every pull request.

## Recent focus

- F5: README, specs marked final, architecture ADRs 0003–0009, AWS Terraform with a least-privilege database role, Memory Bank refreshed with `/methodology-setup`, clean-clone termination check.
- F4: audit trail, HMAC-signed webhook, JSON logs. F3: idempotency and real-concurrency suites. F2: flows and API. F1: ledger and domain apps. F0: skeleton, Docker, CI.

## Active decisions

- Orchestrator: Opus, effort `xhigh`, owns infrastructure, shared kernel, cross-app flows, API and cross-app tests.
- Subagents: Opus, effort `high`, one per Django app (`accounts`, `collateral`, `cards`, `ledger`, `idempotency`, `audit`).
- Each phase closes with an orchestrator review gate and stops for operator approval.
- Termination: `make up` and `make test` pass from a clean clone.
- Account opening uses option B: `POST /accounts` creates the `PENDING` deposit and reserves the account id (`docs/api.md`).
- Operations core lives in `backend/core/` (no models).
- Git workflow: one branch per phase (`phase/fN-*`) in its own worktree, branched from `main` only after the previous phase is merged; one task worktree per subagent; commit at the end of each phase; one squash-merged PR to `main` per phase.

## Next steps

No work is planned.

Done after the plan: card freeze/unfreeze endpoints (#9); audit of rejected attempts (#10); manual, approved deploy pipeline (#11, ADR 0010) — validated but never run against AWS: connecting an account follows the one-time setup in `infra/terraform/README.md`.
