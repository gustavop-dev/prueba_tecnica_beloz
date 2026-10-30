# Tasks Plan

> Memory Bank file. Status of the implementation phases defined in `tasks/implementation-plan.md`. Counts verified with `find` on 2026-10-30.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| Specs | `docs/*.md`, ADR 0001–0010 in `docs/adr/`, ADR 0011 in `infra/terraform/adr/` | ✅ Final |
| F0 | Skeleton, repository, Docker, CI | ✅ Merged (#1) |
| F1 | Money, ledger, models | ✅ Merged (#3) |
| F2 | Flows, API, principal and ownership | ✅ Merged (#4) |
| F3 | Concurrency and idempotency | ✅ Merged (#5) |
| F4 | Security hardening and audit | ✅ Merged (#6) |
| F5 | Documentation, architecture ADRs, Terraform | ✅ Merged (#7) |
| Follow-up | Card freeze / unfreeze endpoints | ✅ Implemented (#9) |
| Follow-up | Audit of rejected attempts | ✅ Implemented (#10) |
| Follow-up | Manual, approved deploy pipeline (staging + production) | ✅ Implemented (#11), never run against AWS |
| Follow-up | ADRs moved to `docs/adr/`, deployment ADR 0011, planning section in README, `PROMPTS.md` | ✅ Done (#12) |
| Follow-up | `PROMPTS.md` and the exported session (redacted) in `docs/ai-logs/` | ✅ Done |

## Codebase counts

| Item | Count |
| --- | --- |
| Models | 8 (`CreditAccount`, `CollateralDeposit`, `Card`, `LedgerAccount`, `JournalEntry`, `JournalLine`, `IdempotencyRecord`, `AuditEvent`) |
| Migrations | 10 |
| Backend test files | 40 (306 test functions, 851 cases after parametrization) |
| `urls.py` files | 2 (8 routes: `/health` + 7 API endpoints) |
| ADRs | 9 |
| Terraform files | 8 (`infra/terraform/`) + `sql/app_role.sql` |
| Frontend | none (out of scope) |

## Known issues

- Test-only, not reachable in F3 suites (openings run sequentially before contention): `ledger.provision_customer_accounts` recreates the global accounts with `get_or_create` after a test flush; concurrent openings right after a flush could race on the partial unique index. Make it conflict-safe (`ON CONFLICT DO NOTHING`) or use `serialized_rollback` in transactional tests.
- The AI stack's quality gate (`$OPS_ROOT/scripts/qa/qa-agent.sh`) is unavailable until `vps-ops-toolkit` is cloned.

## Testing status

Follow-up #10: 1040 passing (rejected-attempt audit, security log for 401s). Follow-up #9: 881 passing (card freeze/unfreeze). F5: 851 passing (+ database TLS mode from the environment). Termination: from a fresh clone, `make up && make test` and `make walkthrough` + `make reconcile` pass; `make infra-check` validates the Terraform. F4: 849 passing — TEST-SEC-1..6 (HMAC signature, tampered body, 5-minute window both ways, IDOR, missing/unknown token, wrong role), audit per mutation / none on rejection, replay or rollback, JSON logs with request id and no secrets; live walkthrough with a signed webhook, 45/45 JSON log lines. F3: 656 passing — TEST-CON-1..4 (real threads, 10/10 consecutive runs), TEST-IDEM-1..6 at the API plus claim blocking proven via `pg_locks`, TEST-PROP-1 (stateful machine over the API with retries, 200 examples × 25 steps in CI), TEST-PROP-2, CON-9 lock timeout → 503 and successful retry. F2: 550 passing — API happy paths, every rejection code, permission matrix per view, IDOR (404 indistinguishable from missing), strict amount input, atomicity under injected failures, full lifecycle reconciled; every BR-* rule cited by a test (35/35). F1: 412 passing — TEST-SM-1 (all apps with states), TEST-DB-1..3, TEST-REC-1..2, money parsing and limit properties (Hypothesis), no float/decimal fields, row-lock tests per app with real threads. Mandatory suites are specified in `docs/testing.md`.
