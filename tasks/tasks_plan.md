# Tasks Plan

> Memory Bank file. Status of the implementation phases defined in `tasks/implementation-plan.md`. Counts verified with `find` on 2026-10-30.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| Specs | `docs/*.md`, ADR 0001–0002 | ✅ Done (draft) |
| F0 | Skeleton, repository, Docker, CI | ✅ Merged (#1) |
| F1 | Money, ledger, models | ✅ Merged (#3) |
| F2 | Flows, API, principal and ownership | 🔎 In review (branch `phase/f2-flows-api`) |
| F3 | Concurrency and idempotency | ⏳ Not started |
| F4 | Security hardening and audit | ⏳ Not started |
| F5 | Documentation, architecture ADRs, Terraform | ⏳ Not started |

## Codebase counts

| Item | Count |
| --- | --- |
| Models | 6 (`CreditAccount`, `CollateralDeposit`, `Card`, `LedgerAccount`, `JournalEntry`, `JournalLine`) |
| Backend test files | 28 (550 tests) |
| `urls.py` files | 2 (8 routes: `/health` + 7 API endpoints) |
| Frontend | none (out of scope) |

## Known issues

- F3: `ledger.provision_customer_accounts` recreates the global accounts with `get_or_create` after a test flush; concurrent openings right after a flush could race on the partial unique index. Make it conflict-safe (`ON CONFLICT DO NOTHING`) or use `serialized_rollback` in transactional tests.
- F3: `lock_timeout` (CON-9) is set by the operations core when it opens each transaction.
- The AI stack's quality gate (`$OPS_ROOT/scripts/qa/qa-agent.sh`) is unavailable until `vps-ops-toolkit` is cloned.

## Testing status

F2: 550 passing — API happy paths, every rejection code, permission matrix per view, IDOR (404 indistinguishable from missing), strict amount input, atomicity under injected failures, full lifecycle reconciled; every BR-* rule cited by a test (35/35). F1: 412 passing — TEST-SM-1 (all apps with states), TEST-DB-1..3, TEST-REC-1..2, money parsing and limit properties (Hypothesis), no float/decimal fields, row-lock tests per app with real threads. Mandatory suites are specified in `docs/testing.md`.
