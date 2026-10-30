# Architecture (Memory Bank)

> Memory Bank file. Source of truth: `docs/architecture.md`. Diagrams describe the **target** design; no code exists yet.

## System overview

```mermaid
flowchart LR
    C[Customer] -- Bearer token --> API
    P[Payment provider] -- HMAC-signed webhook --> API
    subgraph Service[Single deployable: Django modular monolith]
        API[DRF API layer] --> SVC[Orchestrating services]
        SVC --> accounts
        SVC --> collateral
        SVC --> cards
        SVC --> ledger
        SVC --> idempotency
        SVC --> audit
    end
    Service --> DB[(PostgreSQL)]
```

## Request flow (money operation)

```mermaid
sequenceDiagram
    participant Client
    participant API as DRF view
    participant Svc as Orchestrating service
    participant DB as PostgreSQL
    Client->>API: POST + Bearer + Idempotency-Key
    API->>API: authenticate → principal + role, validate input
    API->>Svc: call service
    Svc->>DB: BEGIN
    Svc->>DB: INSERT idempotency record (first write)
    Svc->>DB: SELECT credit account FOR UPDATE
    Svc->>Svc: validate state and balance under the lock
    Svc->>DB: INSERT journal entry + lines
    Svc->>DB: UPDATE outstanding_minor (CHECK guard)
    Svc->>DB: INSERT audit event
    Svc->>DB: store response in idempotency record
    Svc->>DB: COMMIT
    API-->>Client: response + X-Request-ID
```

## Data model (target)

Cross-app references are UUIDs without foreign keys (dashed).

```mermaid
erDiagram
    CREDIT_ACCOUNT ||..|| COLLATERAL_DEPOSIT : "deposit_id (UUID)"
    CREDIT_ACCOUNT ||..|| CARD : "account_id (UUID)"
    CREDIT_ACCOUNT ||..o{ LEDGER_ACCOUNT : "per-account ledger accounts"
    JOURNAL_ENTRY ||--|{ JOURNAL_LINE : contains
    LEDGER_ACCOUNT ||--o{ JOURNAL_LINE : "posted to"
    CREDIT_ACCOUNT {
        uuid id
        uuid owner_id
        string status
        bigint credit_limit_minor
        bigint outstanding_minor
    }
    COLLATERAL_DEPOSIT {
        uuid id
        uuid owner_id
        bigint amount_minor
        string status
    }
    CARD {
        uuid id
        string token
        string last4
        string status
    }
    JOURNAL_LINE {
        bigint amount_minor "signed, non-zero"
    }
    IDEMPOTENCY_RECORD {
        string principal
        string endpoint
        uuid key
        bytes request_hash
    }
    AUDIT_EVENT {
        string actor
        string action
        json before_state
        json after_state
        uuid request_id
    }
```

## Deployment (target)

```mermaid
flowchart LR
    Dev[Local: Docker Compose] --> PG[(PostgreSQL container)]
    Dev --> App[App container]
    AWS[AWS via Terraform, not applied] --> LB[Load balancer, TLS]
    LB --> Run[App service]
    Run --> RDS[(PostgreSQL)]
    Run --> SM[Secrets Manager / SSM]
```

## App responsibilities

| App | Owns |
| --- | --- |
| `accounts` | `CreditAccount` and its balance cache |
| `collateral` | `CollateralDeposit` |
| `cards` | `Card` |
| `ledger` | `LedgerAccount`, `JournalEntry`, `JournalLine` |
| `idempotency` | `IdempotencyRecord` |
| `audit` | `AuditEvent` |

Operations core (`backend/core/`) and dependency direction: see `docs/architecture.md` §3.
