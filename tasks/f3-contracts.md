# F3 Contracts — Concurrency and Idempotency

> Written by the orchestrator. The `idempotency` subagent receives §1 of `tasks/f1-contracts.md` (common conventions, still valid) plus §1 of this file. §2 is the orchestrator's part.

## 1. `idempotency` app — docs/idempotency.md, ADR 0002, INV-4

### Model `IdempotencyRecord`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `principal_id` | UUID | The authenticated caller (customer or provider). |
| `endpoint` | `CharField(max_length=255)` | HTTP method + concrete path, e.g. `POST /accounts/<uuid>/repayments` (IDEM-8). |
| `key` | UUID | The `Idempotency-Key` header. |
| `request_hash` | `BinaryField` | SHA-256 of the canonical body, exactly 32 bytes (`CHECK octet_length(request_hash) = 32`). |
| `response_status` | `PositiveSmallIntegerField`, nullable | Set by `complete`. |
| `response_body` | `JSONField`, nullable | Set by `complete`. |
| `created_at`, `completed_at` | datetime / nullable datetime | |

Constraints and guards:

| ID | Guard |
| --- | --- |
| IDEM-4 | `UNIQUE (principal_id, endpoint, key)`, named so the core can rely on it. |
| IDEM-7 | Deferred constraint trigger (`DEFERRABLE INITIALLY DEFERRED`): a record can only commit with `response_status`, `response_body` and `completed_at` set. A record is never visible to another transaction half-done. |
| — | `response_status` between 100 and 599 when set; `completed_at` set ⇔ `response_status` set. |
| — | Completed records are immutable: a `BEFORE UPDATE` trigger rejects any update when `OLD.response_status IS NOT NULL`; a `BEFORE DELETE` trigger always rejects; `TRUNCATE` is rejected for non-superusers, exactly like LED-4. Records never expire (IDEM-12). |

### Services

```python
@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: Any                 # JSON-compatible

@dataclass(frozen=True, slots=True)
class ClaimResult:
    record_id: UUID | None    # set when this call claimed the key and must execute the operation
    replay: StoredResponse | None   # set when the key was already completed with the same request

def request_fingerprint(body: Any) -> bytes
    # SHA-256 of the canonical JSON of `body`: json.dumps(body, sort_keys=True, separators=(",", ":"),
    # ensure_ascii=False).encode("utf-8"). Returns 32 bytes (IDEM-5, IDEM-9).

def claim(*, principal_id: UUID, endpoint: str, key: UUID, request_hash: bytes) -> ClaimResult
    # Must run inside transaction.atomic and be the FIRST write of that transaction (IDEM-10).
    # INSERT ... ON CONFLICT (principal_id, endpoint, key) DO NOTHING RETURNING id (raw SQL or
    # equivalent). PostgreSQL makes a concurrent insert of the same key WAIT until the first
    # transaction ends: if it committed, the insert does nothing and the committed record is read
    # back; if it rolled back, the insert succeeds.
    # - inserted                   → ClaimResult(record_id=<id>, replay=None)
    # - existing, same hash        → ClaimResult(record_id=None, replay=StoredResponse(...))
    # - existing, different hash   → raise KeyReusedWithDifferentRequest (IDEM behavior table)

def complete(record_id: UUID, *, status_code: int, body: Any) -> None
    # Same transaction as `claim`; stores the response and completed_at (IDEM-6, IDEM-7).
```

Exceptions: `IdempotencyError`, `KeyReusedWithDifferentRequest`.

`claim` and `complete` require an open transaction (`RuntimeError` otherwise) and validate their arguments (`ValueError`: non-UUID ids, `request_hash` not 32 bytes, `status_code` outside 100–599, empty endpoint).

### Mandatory tests

- Fingerprint: deterministic; key order and whitespace do not change it; any value change does (e.g. `{"amount_minor": 500}` vs `200` — the ADR 0002 example); 32 bytes.
- `claim` → `complete` → second `claim` with the same hash replays the stored response exactly; with a different hash raises `KeyReusedWithDifferentRequest`; another principal or endpoint with the same key is a new claim (IDEM-4, IDEM-8).
- Guards with raw SQL: duplicate `(principal_id, endpoint, key)` rejected; a record committed without response rejected at commit (`transaction=True` test, or `SET CONSTRAINTS ALL IMMEDIATE`); update of a completed record rejected; delete rejected; truncate rejected for a non-superuser role.
- **Real concurrency** (`transaction=True`, threads, one connection each): thread 1 claims and holds its transaction open; thread 2 claims the same key and must block; thread 1 completes and commits; thread 2 then receives the replay. And the rollback variant: thread 1 rolls back, thread 2 claims successfully.

## 2. Orchestrator (`core`)

- Idempotent `POST` wrapper in `core/api`: auth → permission → `Idempotency-Key` header check (`IDEMPOTENCY_KEY_MISSING` / `IDEMPOTENCY_KEY_INVALID`, 400, no effect) → one `transaction.atomic()` that sets `lock_timeout` (CON-9), claims (`IDEMPOTENCY_KEY_REUSED` 422 on different body), runs the view, stores every `< 500` response including business rejections (IDEM-11), and commits. `>= 500` rolls everything back, so the client can retry with the same key. Replays carry `Idempotent-Replayed: true`.
- `LockNotAvailable` (SQLSTATE `55P03`) → `LOCK_TIMEOUT` 503.
- Tests: TEST-CON-1..4, TEST-IDEM-1..6, TEST-PROP-1..2 (Hypothesis stateful machine over the API with retries).
