# Architecture (Memory Bank)

> Memory Bank file, refreshed in F5 against the code. Source of truth: `docs/architecture.md` and ADR 0001–0009.

## System overview

```mermaid
flowchart LR
    C[Customer] -- Bearer token --> API
    P[Payment provider] -- HMAC-signed webhook --> API
    subgraph Service[Single deployable: Django modular monolith]
        API[core/api: DRF views, idempotent POST wrapper, error envelope] --> FLOWS[core/flows]
        FLOWS --> accounts
        FLOWS --> collateral
        FLOWS --> cards
        FLOWS --> ledger
        FLOWS --> audit
        API --> idempotency
    end
    Service --> DB[(PostgreSQL 18)]
```

`core` imports only `<app>.services`; the six apps import nothing from each other or from `core` (import-linter DEP-1/DEP-2).

## Request flow (money operation)

```mermaid
sequenceDiagram
    participant Client
    participant MW as Request id middleware
    participant View as DRF view + idempotent wrapper
    participant Flow as core/flows
    participant DB as PostgreSQL
    Client->>MW: POST + Bearer/Signature + Idempotency-Key
    MW->>View: request_id in ContextVar
    View->>View: authenticate → principal + role, permission, key check
    View->>DB: BEGIN; SET LOCAL lock_timeout
    View->>DB: INSERT idempotency record ON CONFLICT DO NOTHING (first write)
    alt key already completed
        View-->>Client: stored response + Idempotent-Replayed
    end
    View->>Flow: run operation (savepoint)
    Flow->>DB: SELECT credit account FOR UPDATE (then deposit, then card)
    Flow->>Flow: validate state and balance under the lock
    Flow->>DB: journal entry + lines, cache update (CHECK), state changes
    Flow->>DB: audit events (same transaction)
    View->>DB: store response (< 500) in the idempotency record
    View->>DB: COMMIT (deferred triggers: balanced entries, complete record)
    View-->>Client: response + X-Request-ID
```

## Data model

Cross-app references are UUIDs without foreign keys (dashed).

```mermaid
erDiagram
    COLLATERAL_DEPOSIT ||..o| CREDIT_ACCOUNT : "reserved_account_id = id"
    CREDIT_ACCOUNT ||..|| CARD : "credit_account_id"
    CREDIT_ACCOUNT ||..|{ LEDGER_ACCOUNT : "customer collateral + receivable"
    CREDIT_ACCOUNT ||..o{ JOURNAL_ENTRY : "credit_account_id"
    JOURNAL_ENTRY ||--|{ JOURNAL_LINE : contains
    LEDGER_ACCOUNT ||--o{ JOURNAL_LINE : "posted to"
    CREDIT_ACCOUNT {
        uuid id
        uuid owner_id
        uuid deposit_id
        string status "ACTIVE | CLOSED"
        int collateral_ratio_bps
        bigint credit_limit_minor
        bigint outstanding_minor "CHECK 0..limit"
    }
    COLLATERAL_DEPOSIT {
        uuid id
        uuid owner_id
        uuid reserved_account_id
        bigint amount_minor
        string status "PENDING | CONFIRMED | FAILED | RELEASED | PARTIALLY_APPLIED | APPLIED"
        bigint applied_minor
        bigint refunded_minor
    }
    CARD {
        uuid id
        uuid credit_account_id
        string token "internal, never a PAN"
        string last4
        string status "ACTIVE | INACTIVE | CLOSED"
    }
    LEDGER_ACCOUNT {
        uuid id
        string code "CASH | MERCHANT_PAYABLE | CUSTOMER_COLLATERAL | CUSTOMER_RECEIVABLE"
        uuid credit_account_id "null for globals"
    }
    JOURNAL_LINE {
        bigint amount_minor "signed, non-zero"
    }
    IDEMPOTENCY_RECORD {
        uuid principal_id
        string endpoint
        uuid key
        bytes request_hash "SHA-256"
        json response_body
    }
    AUDIT_EVENT {
        uuid actor_id
        string actor_role
        string action
        json before_state
        json after_state
        uuid request_id
        uuid idempotency_key
    }
```

## Deployment

```mermaid
flowchart LR
    Dev[Local: Docker Compose] --> PG[(postgres:18.6-alpine)]
    Dev --> App[App container: dev target, non-root]
    Internet -- HTTPS --> ALB[ALB, TLS]
    ALB --> ECS[ECS Fargate API tasks, private subnets]
    ECS --> RDS[(RDS PostgreSQL 18, TLS only)]
    MIG[ECS migrate task] --> RDS
    ECS -.-> SM[Secrets Manager]
    ECS -.-> CW[CloudWatch JSON logs]
```

AWS is Terraform code under `infra/terraform/`, validated (`make infra-check`, CI) and not applied.

## Module responsibilities

| Module | Owns |
| --- | --- |
| `accounts` | `CreditAccount`, its state machine and balance cache guards |
| `collateral` | `CollateralDeposit`, its state machine and settlement guards |
| `cards` | `Card` (token + last 4), its state machine |
| `ledger` | Ledger accounts, journal entries and lines, posting templates, append-only and balance triggers |
| `idempotency` | `IdempotencyRecord`, fingerprint, claim/complete |
| `audit` | `AuditEvent`, append-only, secret-free states |
| `core` | Flows, API, auth (tokens, webhook signatures), money parsing, request context, JSON logging, `reconcile` |
