# State Machines

> Status: **draft**.

Transitions not listed here are forbidden. Every transition happens inside the atomic operation that triggers it (see [business-rules.md](business-rules.md)).

## 1. Collateral deposit

| State | Description |
| --- | --- |
| `PENDING` | Created before any account exists. Waiting for the provider's report. |
| `CONFIRMED` | Funds received. The account and card exist and the deposit backs the credit limit. |
| `FAILED` | Terminal. The provider reported a failure; no account was created. No further handling in this exercise. |
| `RELEASED` | Terminal. Returned in full at closure (debt was 0). |
| `PARTIALLY_APPLIED` | Terminal. Partly used to pay off the debt at closure; the remainder was returned. |
| `APPLIED` | Terminal. Fully used to pay off the debt at closure; nothing was returned. |

| From | To | Trigger | Guard |
| --- | --- | --- | --- |
| _(none)_ | `PENDING` | Deposit creation | — |
| `PENDING` | `CONFIRMED` | Provider reports success | — |
| `PENDING` | `FAILED` | Provider reports failure | — |
| `CONFIRMED` | `RELEASED` | Account closure | `debt_at_closure = 0` |
| `CONFIRMED` | `PARTIALLY_APPLIED` | Account closure | `0 < debt_at_closure < deposit` |
| `CONFIRMED` | `APPLIED` | Account closure | `debt_at_closure = deposit` |

The closure guards are mutually exclusive and, given `0 ≤ debt ≤ deposit`, exhaustive.

## 2. Credit account

| State | Description |
| --- | --- |
| `ACTIVE` | Accepts purchases, payments and closure. |
| `CLOSED` | Terminal. No operation is allowed (BR-CLO-6). |

| From | To | Trigger |
| --- | --- | --- |
| _(none)_ | `ACTIVE` | Deposit `PENDING → CONFIRMED` (BR-DEP-3). |
| `ACTIVE` | `CLOSED` | Account closure. |

## 3. Card

| State | Description |
| --- | --- |
| `ACTIVE` | Accepts purchases. |
| `INACTIVE` | Frozen (optional add-on). Purchases are rejected. Reversible. |
| `CLOSED` | Terminal. Closed together with its credit account. |

| From | To | Trigger |
| --- | --- | --- |
| _(none)_ | `ACTIVE` | Deposit `PENDING → CONFIRMED` (BR-DEP-3). |
| `ACTIVE` | `INACTIVE` | Freeze (add-on). |
| `INACTIVE` | `ACTIVE` | Unfreeze (add-on). |
| `ACTIVE`, `INACTIVE` | `CLOSED` | Account closure. |

In a real product `INACTIVE` could mean a permanent block that leaves the card unusable and requires issuing a new one. That is out of scope: here it only means frozen.

## 4. Combined states

Deposit, credit account and card change together inside the same atomic operations, so only these combinations can exist:

| Deposit | Credit account | Card |
| --- | --- | --- |
| `PENDING` | — | — |
| `FAILED` | — | — |
| `CONFIRMED` | `ACTIVE` | `ACTIVE` or `INACTIVE` |
| `RELEASED`, `PARTIALLY_APPLIED` or `APPLIED` | `CLOSED` | `CLOSED` |

## 5. Ledger impact

| Transition | Journal entry |
| --- | --- |
| Deposit → `PENDING` | None. |
| Deposit `PENDING → FAILED` | None: money never entered the system. |
| Deposit `PENDING → CONFIRMED` | `Dr cash / Cr customer_collateral` |
| Card `ACTIVE ↔ INACTIVE` | None. |
| Account `ACTIVE → CLOSED` | Closure postings (see [ledger.md](ledger.md#5-postings)). |
