# Product Requirements

> Memory Bank file. The specifications in `docs/*.md` are the source of truth; this file summarizes them and points to them. If they disagree, the specification wins and this file must be refreshed.

## Overview

Acme offers a **secured credit card**: a customer funds a collateral deposit, gets a credit line equal to `deposit × collateral_ratio` (ratio 1.0 in this exercise) and a card to spend it. The customer repays the debt and, when closing the account, the collateral pays off any remaining debt and the rest is refunded.

The deliverable is a backend service (Django + DRF + PostgreSQL) whose priority is accounting correctness: money never appears or disappears, every movement is recorded in a double-entry ledger, and concurrent or retried requests can never break the invariants.

## Problems it solves

- Customers without credit history get a card backed by their own deposit.
- Acme never lends more than the collateral covers: debt can always be settled from the deposit.

## Users and roles

| Role | Description |
| --- | --- |
| `customer` | Opens accounts, purchases, repays, closes, reads their own accounts. Can own several isolated accounts. |
| `payment_provider` | Reports the outcome of collateral deposits through a signed webhook. |
| `operator` | Internal role acting as the system. No endpoints in scope. |

## Features (in scope)

| Feature | Spec |
| --- | --- |
| Account opening through a confirmed collateral deposit | `business-rules.md` §3, §5 · `state-machines.md` |
| Purchases authorized and recorded as debt immediately | `business-rules.md` §6 |
| Repayments, never above the outstanding debt | `business-rules.md` §7 |
| Account closure: collateral settles debt, remainder refunded | `business-rules.md` §8 |
| Account status and ledger queries | `api.md` |
| Double-entry, append-only ledger with balance cache and reconcile command | `ledger.md` |
| Idempotent `POST`s | `idempotency.md` |
| Token authentication, roles, IDOR protection, HMAC-signed webhook | `security.md` |
| Audit log, JSON logs with request id, health check | `audit.md` |

**Optional add-on:** freezing a card (`INACTIVE`, reversible) — implemented in the `cards` domain and its state machine, exercised by the Hypothesis machine; no HTTP endpoint is exposed.

**Status:** all features implemented and verified (F0–F5); see `tasks/tasks_plan.md`.

**Out of scope:** interest, fees, statements, real card network, frontend, additional deposits, limit increases, auth/capture split, handling of failed deposits, permanent card blocks.

## Key business rules

- USD only; amounts are strictly positive integers in cents; floats are forbidden in every layer.
- `credit_limit = deposit × ratio_bps // 10000`; `available = limit − outstanding`, never negative.
- One deposit and one card per account; accounts of the same user are fully isolated.
- Full refund **iff** debt at closure is 0; `refund = deposit − debt_at_closure`.
- No operation on a closed account.

Full list with IDs: `docs/business-rules.md`. Invariants: `docs/invariants.md`.
