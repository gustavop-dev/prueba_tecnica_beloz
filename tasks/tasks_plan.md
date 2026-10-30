# Tasks Plan

> Memory Bank file. Status of the implementation phases defined in `tasks/implementation-plan.md`. Counts verified with `find` on 2026-10-30.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| Specs | `docs/*.md`, ADR 0001–0002 | ✅ Done (draft) |
| F0 | Skeleton, repository, Docker, CI | 🔎 In review (branch `phase/f0-skeleton`) |
| F1 | Money, ledger, models | ⏳ Not started |
| F2 | Flows, API, principal and ownership | ⏳ Not started |
| F3 | Concurrency and idempotency | ⏳ Not started |
| F4 | Security hardening and audit | ⏳ Not started |
| F5 | Documentation, architecture ADRs, Terraform | ⏳ Not started |

## Codebase counts

| Item | Count |
| --- | --- |
| Models | 0 (six empty `models.py`) |
| Backend test files | 3 (12 tests) |
| `urls.py` files | 1 (1 route: `/health`) |
| Frontend | none (out of scope) |

## Known issues

- The AI stack's untracked `.github/workflows/*.yml` in the main checkout collide with the new `ci.yml` when F0 is merged; they must be removed first (repo-cleanup report).
- The AI stack's quality gate (`$OPS_ROOT/scripts/qa/qa-agent.sh`) is unavailable until `vps-ops-toolkit` is cloned.

## Testing status

F0 smoke suite: 12 passing (health 200/503/405, PostgreSQL engine and live 18.x connection, fail-closed secrets). Mandatory suites are specified in `docs/testing.md`.
