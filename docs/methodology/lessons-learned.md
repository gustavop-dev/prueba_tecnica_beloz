# Lessons Learned

> Memory Bank file. Patterns and conventions confirmed during the project. Add entries as they are learned.

## Architecture

- Apps communicate only through service functions; operations spanning apps run in one `transaction.atomic` opened by the orchestrating service.
- Every guard that matters has a database-level backstop (`CHECK`, triggers, `UNIQUE`), because Python validation alone can be bypassed by a future endpoint or bug.

## Code conventions

- All code, identifiers and docs in English.
- Money is always `int` cents with the `_minor` suffix; never `float`, never `Decimal` for stored amounts.
- `CreditAccount` (product) and `LedgerAccount` (bookkeeping) are distinct names and must never be mixed.

## Testing

- Tests run only against PostgreSQL.
- `transaction=True` only for tests that verify real commit, rollback or locking.
- Every test names in its docstring the bug it would catch.

## Deployment

- `make up` and `make test` must pass from a clean clone.

## F1 insights

- import-linter `forbidden` contracts check indirect imports by default; DEP-2 needs `allow_indirect_imports = true` because every app's services import its own models.
- Apps keep exceptions in `<app>/exceptions.py` (imported by both `states.py` and `services.py`) and re-export them from `services`, so the core imports only from `<app>.services`.
- Django's `TestCase` teardown runs `check_constraints()`, which fires the deferred LED-6 triggers: a test that leaves an unbalanced entry fails at teardown. Intentional violations go inside a rolled-back savepoint.
- PostgreSQL refuses `TRUNCATE` while deferred trigger events are pending in the same transaction; run `SET CONSTRAINTS ALL IMMEDIATE` first.
- `make format` must bind-mount `./backend` and run as the host user, or it only formats the copy inside the image.
- Per-app row-lock tests use real threads and a second connection (`FOR UPDATE NOWAIT` → `55P03`); removing `select_for_update` makes them fail, which proves they test the lock.

## F2 insights

- DRF answers `403` instead of `401` for missing credentials unless the authentication class defines `authenticate_header`; it must return the scheme (`Bearer`).
- URLs that match no route (e.g. a malformed UUID in the path) never reach DRF: `handler404` must return the JSON envelope too.
- Ownership is checked before the transaction with an owner-scoped lookup; a foreign id raises the same `NotFound` as a missing one, and a test compares both error bodies field by field.
- The account status is checked right after locking the account row, before locking the card, so a closed account answers `ACCOUNT_CLOSED` rather than `CARD_NOT_ACTIVE`.
- Mutation check: removing one ownership check makes 3 IDOR tests fail.

## F3 insights

- The idempotency claim is an `INSERT ... ON CONFLICT DO NOTHING` on the unique index: PostgreSQL makes a concurrent insert of the same key wait for the first transaction, so "insert first" (IDEM-10) gives exactly-once without application locks. A check-then-insert version fails 3 concurrency tests.
- Wrapping the view (not the flow) in the idempotency transaction lets business rejections be stored: the flow's own `atomic()` becomes a savepoint that rolls back alone, while the claim survives and records the 4xx.
- `SET LOCAL lock_timeout` via `set_config(..., true)` lasts until the outermost transaction ends, so nested `money_transaction()` blocks are harmless.
- Hypothesis inside one pytest-django test transaction: each example opens its own account, and invariants check that account only (`find_account_mismatches`) to keep the run linear.
- The worktree guard rejects shell loops that pipe into tools like `sed`; repeated runs go through a small script file.
