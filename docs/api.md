# API

> Status: **draft**.

## 1. Conventions

| ID | Rule |
| --- | --- |
| API-1 | JSON over HTTP. Amounts are integers in cents with the `_minor` suffix, plus `"currency": "USD"` in responses (BR-MONEY-1..5). |
| API-2 | Every request carries `Authorization: Bearer <token>`, except the webhook (signed, SEC-HOOK-1..5) and `GET /health`. |
| API-3 | Every `POST` requires an `Idempotency-Key` header (IDEM-1..3). |
| API-4 | Resource identifiers are UUIDs (SEC-IDOR-3). |
| API-5 | Unknown body fields are rejected (SEC-INPUT-3). |
| API-6 | Every response carries the `X-Request-ID` header (OBS-2). |

## 2. Endpoints

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| `POST` | `/accounts` | `customer` | Request an account opening: creates the collateral deposit as `PENDING` (see [Account opening](#account-opening)). |
| `POST` | `/webhooks/payments` | `payment_provider` | Report the deposit outcome: success opens the account (BR-DEP-3), failure marks it `FAILED` (BR-DEP-4). |
| `POST` | `/cards/{id}/authorizations` | `customer` | Purchase: authorized and recorded as debt immediately (BR-PUR-1..2). |
| `POST` | `/accounts/{id}/repayments` | `customer` | Payment (BR-PAY-1..3). |
| `POST` | `/accounts/{id}/close` | `customer` | Close the account (BR-CLO-1..6). |
| `GET` | `/accounts/{id}` | `customer` | Account status, limit, outstanding debt, available credit, deposit and card state. |
| `GET` | `/accounts/{id}/ledger` | `customer` | The account's journal lines. |
| `GET` | `/health` | — | Health check (OBS-4). |

Card freeze/unfreeze endpoints belong to the optional add-on and are defined only if it is implemented.

### Account opening

An account cannot exist before its deposit is confirmed (BR-ACC-1, BR-ACC-4, BR-DEP-1), so opening takes two calls:

1. The customer calls `POST /accounts` with the deposit amount (`deposit_amount_minor`). The service creates the `PENDING` deposit and **reserves** a UUID for the future account. The response returns the deposit id, the reserved account id and the deposit status.
2. The provider calls `POST /webhooks/payments`. On success the account is created **with the reserved id**, together with its limit and `ACTIVE` card (BR-DEP-3). On failure the deposit becomes `FAILED` and the reserved id never becomes an account (BR-DEP-4).

Until step 2 succeeds, `GET /accounts/{reserved_id}` returns `404`.

## 3. Error format

Every error uses the same envelope:

```json
{
  "error": {
    "code": "INSUFFICIENT_CREDIT",
    "message": "The purchase amount exceeds the available credit.",
    "request_id": "6f1c…",
    "details": {}
  }
}
```

`code` is stable and machine-readable; `message` is for humans and may change. `details` carries field errors for validation failures.

## 4. Error glossary

| Code | HTTP | When |
| --- | --- | --- |
| `VALIDATION_ERROR` | 400 | Malformed body, unknown field, wrong type, non-positive or non-integer amount. `details` lists the fields. |
| `IDEMPOTENCY_KEY_MISSING` | 400 | `POST` without `Idempotency-Key`. |
| `IDEMPOTENCY_KEY_INVALID` | 400 | `Idempotency-Key` is not a UUID. |
| `UNAUTHENTICATED` | 401 | Missing or unknown bearer token. |
| `INVALID_SIGNATURE` | 401 | Webhook signature does not match. |
| `SIGNATURE_EXPIRED` | 401 | Webhook timestamp outside the 5-minute window. |
| `FORBIDDEN` | 403 | The role may not use this endpoint. |
| `NOT_FOUND` | 404 | The resource does not exist or belongs to another customer (SEC-IDOR-2). |
| `ACCOUNT_CLOSED` | 409 | Operation on a `CLOSED` account. |
| `CARD_NOT_ACTIVE` | 409 | Purchase with a card that is not `ACTIVE`. |
| `DEPOSIT_NOT_PENDING` | 409 | Webhook for a deposit that is no longer `PENDING`. |
| `INSUFFICIENT_CREDIT` | 422 | Purchase amount exceeds the available credit. |
| `PAYMENT_EXCEEDS_DEBT` | 422 | Payment amount exceeds the outstanding debt (including debt 0). |
| `IDEMPOTENCY_KEY_REUSED` | 422 | Same key with a different body (IDEM-2 behavior table). |
| `LOCK_TIMEOUT` | 503 | The account lock was not acquired in time (CON-9). Safe to retry with the same key. |
| `INTERNAL_ERROR` | 500 | Unexpected error. No internal detail is exposed. |

`409` means the operation conflicts with the current state; `422` means it was understood but a business rule rejects it. Both are stored and replayed by idempotency (IDEM-11).
