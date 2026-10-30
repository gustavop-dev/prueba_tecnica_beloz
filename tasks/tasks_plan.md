# Tasks Plan

> Memory Bank file. Status of the implementation phases defined in `tasks/implementation-plan.md`. Counts verified with `find` on 2026-10-30.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| Specs | `docs/*.md`, ADR 0001–0002 | ✅ Done (draft) |
| F0 | Skeleton, repository, Docker, CI | ⏳ Not started |
| F1 | Money, ledger, models | ⏳ Not started |
| F2 | Flows, API, principal and ownership | ⏳ Not started |
| F3 | Concurrency and idempotency | ⏳ Not started |
| F4 | Security hardening and audit | ⏳ Not started |
| F5 | Documentation, architecture ADRs, Terraform | ⏳ Not started |

## Codebase counts

| Item | Count |
| --- | --- |
| Models | 0 |
| Backend test files | 0 |
| `urls.py` files | 0 |
| Frontend | none (out of scope) |

## Known issues

- The directory is not a git repository yet.
- The AI stack's quality gate (`$OPS_ROOT/scripts/qa/qa-agent.sh`) is unavailable until `vps-ops-toolkit` is cloned.

## Testing status

No tests yet. Mandatory suites are specified in `docs/testing.md`.
