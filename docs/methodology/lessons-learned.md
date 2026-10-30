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
