# Business Rules

> Status: **draft**.

## 1. Scope

**In scope:** a secured credit card account funded by a single collateral deposit, with one card, purchases, payments and account closure.

**Out of scope:**

- Interest, fees and account statements.
- Integration with a real card network.
- Frontend.
- Additional deposits and credit limit increases (future add-on).
- Split authorization / capture flow (future extension).
- Handling of failed deposits: retries, rejection reasons, notifications or any follow-up. A failed deposit simply opens no account.
- Permanently blocking a card and issuing a replacement.

**Optional add-on** (only if the core scope is finished): freezing a card (reversible).

## 2. Money

| ID | Rule |
| --- | --- |
| BR-MONEY-1 | The only supported currency is USD. |
| BR-MONEY-2 | Every amount is an integer number of cents. |
| BR-MONEY-3 | Floating-point types are forbidden for money in every layer: API payloads, serializers, services, models, database columns and tests. |
| BR-MONEY-4 | The system always receives amounts as integers in cents. Any other representation (JSON floats such as `10.0`, strings, booleans) is rejected. |
| BR-MONEY-5 | Every amount (deposit, purchase, payment) must be strictly positive (`> 0`). |
| BR-MONEY-6 | There is no business maximum for amounts. |

## 3. Account opening

| ID | Rule |
| --- | --- |
| BR-ACC-1 | An account cannot exist without its opening collateral deposit. |
| BR-ACC-2 | An account has exactly one collateral deposit: the opening one. |
| BR-ACC-3 | An account has exactly one card. |
| BR-ACC-4 | An account is opened only by a successful provider confirmation of its deposit (see [section 5](#5-deposit-confirmation)). There is no pending account or card state. |

### Ownership and isolation

| ID | Rule |
| --- | --- |
| BR-OWN-1 | A user can own more than one account, each with its own card. |
| BR-OWN-2 | Accounts are fully isolated from each other, even when they belong to the same user: deposit, credit limit, debt, available credit, purchases and payments never cross or aggregate between accounts. |
| BR-OWN-3 | There are no shared balances between accounts. |

## 4. Credit limit

| ID | Rule |
| --- | --- |
| BR-LIM-1 | `credit_limit = confirmed_deposit × collateral_ratio`. |
| BR-LIM-2 | The collateral ratio is stored as an integer in basis points (`10000 = 1.0`). |
| BR-LIM-3 | The limit is computed with integer arithmetic only: `credit_limit = confirmed_deposit × ratio_bps // 10000`. The multiplication happens before the floor division, so no intermediate value is rounded. Floor rounding guarantees the limit never exceeds what the ratio allows. |
| BR-LIM-4 | For this exercise `ratio_bps = 10000`, so `credit_limit = confirmed_deposit` exactly. |
| BR-LIM-5 | `available_credit = credit_limit − outstanding_debt`. |

In a real product the collateral ratio could depend on the customer's risk profile and be greater than 1, allowing a spending limit above the deposit. This exercise does not model that.

## 5. Deposit confirmation

| ID | Rule |
| --- | --- |
| BR-DEP-1 | A deposit exists as `PENDING` before any account exists. |
| BR-DEP-2 | The deposit outcome is reported through an endpoint that simulates the funding provider. The provider reports either success or failure. |
| BR-DEP-3 | A successful confirmation is a single atomic operation that marks the deposit as `CONFIRMED`, creates the account, sets the credit limit and issues the card as `ACTIVE`. Either everything happens or nothing does. |
| BR-DEP-4 | A failed confirmation marks the deposit as `FAILED` and nothing else: no account, limit or card is created. No further handling is defined in this exercise. |
| BR-DEP-5 | A technical failure while processing the provider's report leaves no partial state; the whole operation is rolled back. |
| BR-DEP-6 | Only a `PENDING` deposit can be confirmed or failed. |

## 6. Purchases

| ID | Rule |
| --- | --- |
| BR-PUR-1 | A purchase is authorized and recorded as debt immediately, in a single atomic operation. |
| BR-PUR-2 | A purchase is approved only if the card is `ACTIVE` and `amount ≤ available_credit`. Otherwise it is rejected in full; there are no partial approvals. |

## 7. Payments

| ID | Rule |
| --- | --- |
| BR-PAY-1 | A payment reduces the outstanding debt by its amount. |
| BR-PAY-2 | A payment greater than the outstanding debt is rejected. Debt is never negative and accounts never carry a credit balance. |
| BR-PAY-3 | Consequently, any payment is rejected while the outstanding debt is 0. |

## 8. Account closure

Closure is a single atomic operation that applies the collateral to the outstanding debt, leaves the debt at 0, returns the remaining collateral and closes the account.

| ID | Rule |
| --- | --- |
| BR-CLO-1 | `refund = deposit − debt_at_closure`. |
| BR-CLO-2 | The full deposit is refunded **if and only if** `debt_at_closure = 0`. The collateral is never returned under any other condition or at any other moment. |
| BR-CLO-3 | If `debt_at_closure > 0`, the collateral pays off the debt and only the remainder is returned. |
| BR-CLO-4 | `0 ≤ refund ≤ deposit`. Boundary cases: debt 0 refunds the full deposit; debt equal to the deposit refunds 0. |
| BR-CLO-5 | After closure the outstanding debt is always 0. |
| BR-CLO-6 | No operation is allowed on a closed account. |

BR-CLO-4 relies on `debt ≤ deposit`, which holds only while `collateral_ratio ≤ 1`.

## 9. Relationship with other documents

Invariants, state machines, the ledger, idempotency, security, audit and testing rules are specified in their own documents and must stay consistent with this one.
