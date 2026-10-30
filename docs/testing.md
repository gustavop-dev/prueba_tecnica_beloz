# Testing

> Status: **final** — implemented and verified (F0–F5).

## 1. Principles

| ID | Rule |
| --- | --- |
| TEST-1 | Every mandatory test must fail if the behavior it protects breaks. A test that stays green with the behavior removed is not valid. |
| TEST-2 | Tests run against real PostgreSQL, never SQLite. Locks, `CHECK` constraints and triggers only exist there. |
| TEST-3 | Tests assert the **effect** (journal entries, cache, states), not only the HTTP status code. |
| TEST-4 | Tools: `pytest`, `pytest-django`, `hypothesis`. Hypothesis profiles: `dev` (30 examples, default) and `ci` (200 examples, 25 stateful steps), selected with `HYPOTHESIS_PROFILE`; CI uses `ci`. |

## 1.1 Test conventions

Taken from the `backend-test-coverage` skill of the AI stack (`.claude/skills/backend-test-coverage/SKILL.md`), which governs how tests are written in this project.

**Definition of done — every test meets all three:**

1. It executes the real behavior: the real unit with real input; mocks only at system boundaries.
2. It asserts an observable outcome with a **concrete value**, never mere existence or truthiness.
3. Its docstring names the bug it would catch. If that line cannot be written, the test is not written.

**Checklist per test:**

- The name describes one specific behavior.
- No conditionals or loops in the test body: use `pytest.mark.parametrize`. Thread setup for concurrency tests lives in helpers and fixtures, not in the body.
- Deterministic: no real clock (freeze time for webhook timestamp tests), no network, no ordering assumptions.
- Isolated: no dependence on another test having run.
- Mocks declare an explicit `return_value` / `side_effect`.
- Arrange → Act → Assert.
- Before adding a test, search for an existing one and extend it. Same shape with different values is a `parametrize` table, not a new test.
- `@pytest.mark.django_db` by default; `transaction=True` only when the test verifies real commit, rollback or locking.
- Factories for per-test data; fixtures for shared, expensive setup.

**Prioritize by risk, not by coverage percentage:** money, permissions, state machines and validation first, then error paths. Coverage is a readout, not the goal. Code without behavior (constants, `__str__`, migrations) is declared as not testable, with the reason, instead of getting a fabricated test.

**Permission matrix, mandatory for every view:**

| Case | Expected |
| --- | --- |
| Anonymous | `401` |
| Authenticated with the wrong role | `403` |
| Owner | `2xx` |
| Another customer's resource | `404` |

Negative cases assert the exact status code, and validation errors also assert the exact field in the response body.

## 2. Real concurrency

Run with `TransactionTestCase` semantics (`@pytest.mark.django_db(transaction=True)`) so each thread uses its own connection and its own committed transaction. Threads start together behind a `threading.Barrier` to maximize contention, and each closes its connection when done.

| ID | Scenario | Must hold |
| --- | --- | --- |
| TEST-CON-1 | N concurrent purchases of the same amount whose total exceeds the limit. Example: limit 1000, 10 × 150. | Exactly `limit // amount` approved (6 in the example), the rest rejected. Total approved `≤ limit`. `outstanding_minor` equals the sum of approved purchases. Reconcile is clean. |
| TEST-CON-2 | Purchase and closure at the same time. | The result matches one of the two serial orders: the purchase lands before closure and is included in `debt_at_closure`, or it is rejected because the account is `CLOSED`. Debt is 0, the account and card are `CLOSED`, `applied + refund = deposit`, and no purchase entry exists after the closure entry. |
| TEST-CON-3 | Payment and purchase at the same time. | The result matches one of the two serial orders. `outstanding_minor = initial + approved purchases − approved payments`, `0 ≤ outstanding ≤ limit`. Reconcile is clean. |
| TEST-CON-4 | Mixed concurrent operations on two accounts of the same user. | No deadlock, and each account only reflects its own operations (BR-OWN-2). |

## 3. Idempotency

| ID | Scenario | Must hold |
| --- | --- | --- |
| TEST-IDEM-1 | Replay: same key, same body. | Same status and body as the first response. Exactly one journal entry. |
| TEST-IDEM-2 | Same key, different body. | `422`. No new journal entry, cache unchanged. |
| TEST-IDEM-3 | Same key sent concurrently from several threads. | Exactly one effect. Every request receives the same response. |
| TEST-IDEM-4 | Duplicate provider webhook (same key). | One account, one card, one deposit entry. The duplicate gets the stored response. |
| TEST-IDEM-5 | Provider webhook resent with a **new** key for an already processed deposit. | Rejected by the state machine (IDEM-15). No second effect. |
| TEST-IDEM-6 | Missing or non-UUID `Idempotency-Key`. | `400`, no effect. |

## 4. State machines

| ID | Scenario | Must hold |
| --- | --- | --- |
| TEST-SM-1 | Every transition **not** listed in [state-machines.md](state-machines.md), for deposit, credit account and card. | Rejected, state unchanged, no journal entry. |
| TEST-SM-2 | Purchase, payment or closure on a `CLOSED` account. | Rejected (BR-CLO-6). |
| TEST-SM-3 | Purchase with an `INACTIVE` card (add-on). | Rejected (BR-PUR-2). |

TEST-SM-1 is generated from the transition tables: the test enumerates every `(from, to)` pair, subtracts the allowed ones and parametrizes the rest. Adding a state automatically adds its invalid transitions to the suite.

## 5. Security

| ID | Scenario | Must hold |
| --- | --- | --- |
| TEST-SEC-1 | Webhook with an invalid signature. | `401`, no effect. |
| TEST-SEC-2 | Webhook with a valid signature but a body altered after signing. | `401`, no effect. |
| TEST-SEC-3 | Webhook with a timestamp older than 5 minutes (and one more than 5 minutes in the future). | `401`, no effect. A timestamp just inside the window is accepted. |
| TEST-SEC-4 | Customer A accessing customer B's accounts, cards and operations (read, purchase, pay, close). | `404`, the same response as for a non-existent resource. B's data is unchanged. |
| TEST-SEC-5 | Missing or unknown token. | `401`. |
| TEST-SEC-6 | Wrong role: provider calling customer endpoints, customer calling the webhook. | `403`, no effect. |

## 6. Property-based tests (Hypothesis)

| ID | Scenario | Must hold |
| --- | --- | --- |
| TEST-PROP-1 | A Hypothesis stateful test (`RuleBasedStateMachine`) runs random sequences of operations on an account: purchases, payments, closure, retries with a reused key and, if implemented, freeze/unfreeze. | After **every** step: `0 ≤ outstanding ≤ limit`; every journal entry sums 0; the cache matches the ledger; only valid state combinations exist; after closure, debt is 0 and `applied + refund = deposit`. |
| TEST-PROP-2 | Random amounts, including invalid ones (zero, negative, floats, strings, booleans). | Invalid amounts are always rejected and produce no effect. |

## 7. Reconciliation

| ID | Scenario | Must hold |
| --- | --- | --- |
| TEST-REC-1 | Alter `outstanding_minor` directly in the database, bypassing the services, to a value that still satisfies the `CHECK`. | `reconcile` reports the mismatch for that account and exits with a non-zero code. |
| TEST-REC-2 | Consistent data. | `reconcile` reports nothing and exits with 0. |

## 8. Database guards

The last lines of defense must be tested too, or a migration could drop them unnoticed. These tests bypass Python and write raw SQL.

| ID | Scenario | Must hold |
| --- | --- | --- |
| TEST-DB-1 | `UPDATE` and `DELETE` on journal entries and lines; `TRUNCATE` as a non-superuser role. | Rejected by the trigger (LED-4). |
| TEST-DB-2 | Set `outstanding_minor` below 0 or above `credit_limit_minor`. | Rejected by the `CHECK` (CACHE-3). |
| TEST-DB-3 | Commit an unbalanced journal entry. | Rejected by the deferred trigger (LED-6). |

## 9. Architecture checks

| ID | Rule |
| --- | --- |
| TEST-ARCH-1 | `import-linter` runs in CI and fails the build if an app imports another app's models, or anything other than its public service functions. |
