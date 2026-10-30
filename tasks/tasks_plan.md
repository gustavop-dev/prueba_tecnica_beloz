# Tasks Plan

> Memory Bank file. Status of the implementation phases defined in `tasks/implementation-plan.md`. Counts verified with `find` on 2026-10-30.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| Specs | `docs/*.md`, ADR 0001–0002 | ✅ Done (draft) |
| F0 | Skeleton, repository, Docker, CI | ✅ Merged (#1) |
| F1 | Money, ledger, models | ✅ Merged (#3) |
| F2 | Flows, API, principal and ownership | ✅ Merged (#4) |
| F3 | Concurrency and idempotency | 🔎 In review (branch `phase/f3-concurrency-idempotency`) |
| F4 | Security hardening and audit | ⏳ Not started |
| F5 | Documentation, architecture ADRs, Terraform | ⏳ Not started |

## Codebase counts

| Item | Count |
| --- | --- |
| Models | 7 (`CreditAccount`, `CollateralDeposit`, `Card`, `LedgerAccount`, `JournalEntry`, `JournalLine`, `IdempotencyRecord`) |
| Backend test files | 35 (656 tests) |
| `urls.py` files | 2 (8 routes: `/health` + 7 API endpoints) |
| Frontend | none (out of scope) |

## Known issues

- Test-only, not reachable in F3 suites (openings run sequentially before contention): `ledger.provision_customer_accounts` recreates the global accounts with `get_or_create` after a test flush; concurrent openings right after a flush could race on the partial unique index. Make it conflict-safe (`ON CONFLICT DO NOTHING`) or use `serialized_rollback` in transactional tests.
- The AI stack's quality gate (`$OPS_ROOT/scripts/qa/qa-agent.sh`) is unavailable until `vps-ops-toolkit` is cloned.

## Testing status

F3: 656 passing — TEST-CON-1..4 (real threads, 10/10 consecutive runs), TEST-IDEM-1..6 at the API plus claim blocking proven via `pg_locks`, TEST-PROP-1 (stateful machine over the API with retries, 200 examples × 25 steps in CI), TEST-PROP-2, CON-9 lock timeout → 503 and successful retry. F2: 550 passing — API happy paths, every rejection code, permission matrix per view, IDOR (404 indistinguishable from missing), strict amount input, atomicity under injected failures, full lifecycle reconciled; every BR-* rule cited by a test (35/35). F1: 412 passing — TEST-SM-1 (all apps with states), TEST-DB-1..3, TEST-REC-1..2, money parsing and limit properties (Hypothesis), no float/decimal fields, row-lock tests per app with real threads. Mandatory suites are specified in `docs/testing.md`.
