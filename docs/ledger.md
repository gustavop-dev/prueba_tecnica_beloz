# Ledger — Source of Truth

> Status: **draft**.

## 1. Principle

Money never appears or disappears inside the system: it only moves between ledger accounts. From the customer's point of view, money enters at the deposit confirmation and leaves at account closure.

The ledger is the source of truth. Credit limit and available credit are business rules derived from it, not ledger accounts.

## 2. Glossary

| Term | Meaning |
| --- | --- |
| Credit account | The customer's product (`accounts` app): status, limit, card. |
| Ledger account | A bookkeeping account where money is recorded (`ledger` app). |
| Journal entry | One business transaction recorded in the ledger. |
| Journal line | One movement of a journal entry into a single ledger account. |

The two meanings of "account" must never be mixed in code: `CreditAccount` vs `LedgerAccount`.

## 3. Ledger accounts

| Ledger account | Scope | Type | Meaning |
| --- | --- | --- | --- |
| `cash` | Global | Asset | Liquid money Acme holds in the bank. |
| `merchant_payable` | Global | Liability | What Acme owes to merchants for customer purchases. |
| `customer_collateral` | Per credit account | Liability | The customer's deposit: held by Acme, owed back to the customer. |
| `customer_receivable` | Per credit account | Asset | What the customer has spent and owes to Acme. |

The per-customer accounts are scoped per **credit account**, not per user, so that accounts of the same user stay isolated (BR-OWN-2).

## 4. Double-entry rules

| ID | Rule |
| --- | --- |
| LED-1 | Every business transaction is recorded as one journal entry with at least two journal lines. |
| LED-2 | The lines of an entry always sum to zero: total debits equal total credits. |
| LED-3 | The ledger is append-only. Journal entries and lines are never updated or deleted. |
| LED-4 | PostgreSQL triggers reject `UPDATE` and `DELETE` on journal entries and lines for every role, and `TRUNCATE` for every non-superuser role, so the rule holds even outside Python. A superuser can disable any trigger anyway; exempting it from the `TRUNCATE` guard lets the test runner flush tables, while the production application role (never a superuser) cannot truncate. |
| LED-5 | Lines store a signed integer `amount_minor`: debit is positive, credit is negative, zero is forbidden (`CHECK amount_minor <> 0`). |
| LED-6 | LED-2 is also enforced in PostgreSQL with a deferred constraint trigger that checks each entry's sum at commit. |

## 5. Postings

Each operation debits (Dr) one ledger account and credits (Cr) another for the same amount.

| Operation | Dr | Cr | Amount |
| --- | --- | --- | --- |
| Deposit confirmed | `cash` | `customer_collateral` | deposit |
| Purchase | `customer_receivable` | `merchant_payable` | purchase |
| Payment | `cash` | `customer_receivable` | payment |
| Closure: apply collateral to debt | `customer_collateral` | `customer_receivable` | `debt_at_closure` |
| Closure: return remainder | `customer_collateral` | `cash` | `deposit − debt_at_closure` |

Closure is one atomic operation. A pair whose amount would be zero is not written: closing with no debt writes only the refund pair; closing with debt equal to the deposit writes only the application pair.

Paying merchants (`Dr merchant_payable / Cr cash`) belongs to the real card network integration and is out of scope.

### Expected balances

| Credit account state | `customer_collateral` | `customer_receivable` |
| --- | --- | --- |
| `ACTIVE` | deposit | outstanding debt |
| `CLOSED` | 0 | 0 |

## 6. Balance cache on the credit account

| ID | Rule |
| --- | --- |
| CACHE-1 | `CreditAccount` stores `credit_limit_minor` and `outstanding_minor` as a cache of the ledger, so a purchase is authorized without reading the whole ledger. |
| CACHE-2 | The cache is updated in the same database transaction as the journal entry. A purchase writes the entry and increments `outstanding_minor`: both happen or neither does. |
| CACHE-3 | PostgreSQL enforces `CHECK (0 <= outstanding_minor AND outstanding_minor <= credit_limit_minor)` as the last line of defense: debt is never negative and spending never exceeds the limit, even if a future endpoint or bug bypasses the Python validation. |
| CACHE-4 | Operations that change `outstanding_minor` lock the credit account row (`SELECT … FOR UPDATE`) before validating, so concurrent purchases or payments are serialized and rejected cleanly instead of hitting the `CHECK`. See [invariants.md](invariants.md#concurrency-rules) and [ADR 0001](decisions/0001-pessimistic-locking.md). |

The `_minor` suffix means the amount is in the currency's minor unit (cents).

## 7. Reconciliation

A `reconcile` management command verifies, for every credit account, that the cache matches the ledger:

- `outstanding_minor` equals the balance of its `customer_receivable`.

Additional checks:

- Every journal entry sums to zero.
- `customer_collateral` matches the expected balance for the account state (see [Expected balances](#expected-balances)).
- `credit_limit_minor = deposit × ratio_bps // 10000` (BR-LIM-3).

The command is read-only: it reports mismatches and exits with a non-zero code when it finds any, so it can run in CI or on a schedule.
