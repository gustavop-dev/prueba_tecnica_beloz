# ADR 0002 — Idempotency keys with a SHA-256 request fingerprint

- Status: **accepted**
- Related: [idempotency.md](../idempotency.md), [invariants.md](../invariants.md) (INV-4)

## Context

Clients and the payment provider retry requests after timeouts or network errors. Without protection, a retried purchase or payment would be applied twice.

A retry must be recognized as the same request, and a reused key with different content must be detected instead of being treated as a retry.

## Decision

- Every `POST` that moves money or changes state carries a client-generated UUID in the `Idempotency-Key` header.
- An idempotency record, unique on `(principal, endpoint, key)`, stores a SHA-256 hash of the request body and the response returned.
- The record is written in the same transaction as the effect.
- A request with a known key and the same hash returns the stored response; a different hash is rejected.

### Why a hash and why SHA-256

The system does not need to compare whole bodies, only a fingerprint that tells whether two requests are the same. SHA-256 provides:

- **Determinism:** the same content always produces the same hash.
- **No practical collisions:** two different bodies do not produce the same hash. A collision would be a money bug: a key reused for a payment of 500 could be taken as a retry of a payment of 200 and replay the wrong response.
- **Fixed size:** 32 bytes regardless of the body size, easy to store and index.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Store and compare the full body | Same guarantee, but more storage and slower comparison for no benefit. |
| MD5 or SHA-1 | Known practical collisions. |
| Non-cryptographic hashes (CRC32, xxHash) | Fast, but no collision resistance. |
| SHA-512, SHA-3, BLAKE2/3 | Equally valid. The choice among them is indifferent for this use; SHA-256 is the most widely supported. |

## Consequences

- Retries are safe: the effect is applied once and every retry gets the same response.
- Because the record and the effect share a transaction, a crash never leaves an effect without its record or a record without its effect.
- Clients must generate a new key per logical operation and reuse it only for retries of that same operation.
