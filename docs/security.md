# Security

> Status: **draft**. Items marked are recommendations pending confirmation.

## 1. Approach

Basic, well-known precautions chosen by asking two questions: **what can someone do that they should not?** and **what prevents it?** No single control is trusted alone: each threat is covered by more than one layer, so breaking one does not expose everything (defense in depth).

Authentication is simulated, as the exercise allows, but it must behave like real authentication: the rest of the code only ever works with the resolved principal and its role.

## 2. Authentication

| ID | Rule |
| --- | --- |
| SEC-AUTH-1 | Every request carries a static token in the `Authorization: Bearer <token>` header. |
| SEC-AUTH-2 | Tokens are configured through the `AUTH_TOKENS` environment variable as comma-separated `role:principal_id:token` entries, and never committed to the repository. Tokens shorter than 16 characters, unknown roles, malformed ids and duplicated tokens stop the service at startup. |
| SEC-AUTH-3 | The system resolves the token into a **principal**: who is calling (identity) and with which **role** (what they may do). |
| SEC-AUTH-4 | A missing or unknown token is rejected with `401`. |
| SEC-AUTH-5 | Tokens are compared in constant time (`hmac.compare_digest`) to avoid timing attacks. |

## 3. Roles and permissions

| Role | Can | Cannot |
| --- | --- | --- |
| `customer` | Operate **only its own** accounts: open (create the deposit), purchase, pay, request closure, read them. | Access other customers' accounts. Call the webhook. |
| `operator` | Internal role that acts as the system itself. It exposes no endpoints in this exercise: only customer actions are in scope. | Call the webhook or any customer endpoint. |
| `payment_provider` | Call the deposit webhook only. | Purchase, pay, close or read accounts. |

| ID | Rule |
| --- | --- |
| SEC-ROLE-1 | A role calling an endpoint it is not allowed to use gets `403`. |
| SEC-ROLE-2 | Permissions are checked in the API layer and ownership again in the service layer, so a new endpoint that forgets a check is still protected. |
| SEC-ROLE-3 | Opening an account is always initiated by the customer, who creates the `PENDING` deposit. |

## 4. Object-level authorization (IDOR)

IDOR (Insecure Direct Object Reference) is one of the most common API vulnerabilities: customer A is authenticated and calls `/accounts/42/repayments`, but account 42 belongs to customer B. A system that only checks *that* the caller is authenticated, and not *whose* account it is, lets A operate on B's account.

| ID | Rule |
| --- | --- |
| SEC-IDOR-1 | Every customer request is scoped to the principal: accounts are looked up by `id` **and** owner, never by `id` alone. |
| SEC-IDOR-2 | An account that does not exist and an account owned by someone else both return `404`, never `403`, so an attacker cannot learn which accounts exist. |
| SEC-IDOR-3 | Account identifiers are UUIDs, not sequential integers, so they cannot be enumerated. |

## 5. Provider webhook

The provider and Acme share a secret. The provider signs each message; Acme recomputes the signature and compares.

| ID | Rule |
| --- | --- |
| SEC-HOOK-1 | Each request carries a timestamp and an HMAC-SHA256 signature in headers. The signature covers the timestamp **and** the raw body. |
| SEC-HOOK-2 | Acme recomputes the HMAC over the raw received bytes with the shared secret and compares in constant time. A mismatch is rejected with `401`. This proves who sent the message and that it was not altered. |
| SEC-HOOK-3 | Messages whose timestamp is more than 5 minutes away from the server time are rejected. Because the timestamp is signed, an attacker who captures a valid webhook cannot resend it later or change its timestamp. |
| SEC-HOOK-4 | Inside the 5-minute window a resent message is neutralized by idempotency (IDEM-13..15): it replays the stored response without applying the effect again. |
| SEC-HOOK-5 | The webhook is authenticated by its signature, which identifies the principal as `payment_provider`. It does not need a bearer token. |

This is the standard scheme used by Stripe and other real providers.

## 6. Card data

| ID | Rule |
| --- | --- |
| SEC-CARD-1 | The system never stores the PAN (the full card number). Custody of the PAN belongs to the card issuer/processor only. |
| SEC-CARD-2 | A card is stored as an internal token plus the last 4 digits of its PAN. |
| SEC-CARD-3 | The PAN, card tokens, bearer tokens and signatures never appear in logs, error responses or audit records. |

## 7. Secrets

| ID | Rule |
| --- | --- |
| SEC-SECRET-1 | Secrets (API tokens, webhook secret, database credentials, Django `SECRET_KEY`) come only from environment variables. |
| SEC-SECRET-2 | No secret is committed. The repository ships a `.env.example` with placeholders; `.env` is git-ignored. |
| SEC-SECRET-3 | The service refuses to start if a required secret is missing (fail closed). |
| SEC-SECRET-4 | On AWS, secrets live in Secrets Manager or SSM Parameter Store and are injected as environment variables. |

## 8. Input validation

| ID | Rule |
| --- | --- |
| SEC-INPUT-1 | Every input is validated strictly before reaching the services: types, formats and required fields. |
| SEC-INPUT-2 | Amounts follow BR-MONEY-4/5: JSON integers only, strictly positive. Floats, strings and booleans are rejected. |
| SEC-INPUT-3 | Unknown fields are rejected, not ignored. |
| SEC-INPUT-4 | Identifiers and idempotency keys must be valid UUIDs. |

## 9. Threat summary

| What could be done that should not | What prevents it |
| --- | --- |
| Call the API without credentials | SEC-AUTH-1..4 |
| Operate on another customer's account (IDOR) | SEC-IDOR-1, SEC-ROLE-2 |
| Discover which accounts exist | SEC-IDOR-2, SEC-IDOR-3 |
| A customer or operator confirming their own deposit | SEC-ROLE-1, SEC-HOOK-1..2 |
| Forge or alter a provider webhook | SEC-HOOK-1..2 |
| Replay a captured webhook | SEC-HOOK-3, SEC-HOOK-4, IDEM-15 |
| Steal card numbers from the database or logs | SEC-CARD-1..3 |
| Find secrets in the repository | SEC-SECRET-1..2 |
| Send malformed or float amounts | SEC-INPUT-1..4, BR-MONEY-4 |
| Overspend or overpay through a bug or a new endpoint | INV-1 guards: row lock + PostgreSQL `CHECK` |

## 10. Out of scope

Real identity provider (OAuth2/OIDC), token rotation and expiration, rate limiting, TLS termination (assumed at the AWS load balancer), and PCI DSS compliance.
