# ADR 0009 — Simulated token authentication and an HMAC-signed webhook

- Status: **accepted**
- Related: [security.md](../security.md) (SEC-AUTH-*, SEC-ROLE-*, SEC-IDOR-*, SEC-HOOK-*)

## Context

The exercise allows simulated authentication, but it must behave like real authentication: the rest of the code works only with a resolved principal and its role. The payment provider is a machine, not a user, and must prove both identity and message integrity.

## Decision

- **Customers**: static bearer tokens from `AUTH_TOKENS` (`role:principal_id:token`), validated at startup and compared in constant time. The resolved `Principal(id, role)` is all the code sees.
- **Authorization**: role permissions per view (`403`), and ownership enforced in the flows by owner-scoped lookups; another customer's resource answers exactly like a missing one (`404`).
- **Provider webhook**: authenticated **only** by an HMAC-SHA256 signature over `"<timestamp>.<raw body>"`, with a ±5-minute window. Inside the window, replays are neutralized by idempotency and by the deposit state machine.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| OAuth2 / OIDC with JWTs | The right choice for production, but needs an identity provider; out of scope. The principal abstraction makes the swap local to `core/auth`. |
| Bearer token for the provider | Proves identity but not integrity: a proxy could alter the amount or the outcome. |
| Signature without timestamp | A captured message could be replayed forever. |

## Consequences

- Swapping the token store for a real identity provider changes one authentication class, not the flows.
- The webhook rejects bearer tokens entirely; the provider's identity is `PAYMENT_PROVIDER_ID`.
- Tokens, secrets and signatures never reach logs or audit events (tested).
