# ADR 0007 — Money as integer cents, ratios in basis points

- Status: **accepted**
- Related: [business-rules.md](../business-rules.md) (BR-MONEY-1..6, BR-LIM-1..5)

## Context

Binary floating point cannot represent most decimal amounts exactly (`0.1 + 0.2 != 0.3`). In accounting, accumulated rounding errors become unexplainable differences. The collateral ratio is a fraction too.

## Decision

- Every amount is an `int` number of cents with the `_minor` suffix, stored as `BIGINT`. Floats and decimals are forbidden in every layer, including tests.
- The API accepts amounts only as JSON integers: `10.0`, `"1000"`, `true` and `null` are rejected (in Python `bool` is an `int`, so the check is on the exact type).
- The collateral ratio is an integer in basis points (`10000 = 1.0`), and the limit is `deposit × ratio_bps // 10000`: multiply first, then floor, so nothing is rounded mid-way and the limit never exceeds what the ratio allows.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| `Decimal` / `NUMERIC` | Exact, but easy to mix with floats in Python, slower, and unnecessary for a single currency with two decimals. |
| Float with rounding at the edges | Rounding errors still accumulate inside computations. |
| A money library | One more dependency for what is integer arithmetic. |

## Consequences

- Tests fail if any model declares a `FloatField` or `DecimalField`, or if a `*_minor` field is not a `BigIntegerField`.
- Hypothesis checks the limit formula is the exact floor for random deposits and ratios.
- Supporting other currencies later means adding a currency column and per-currency minor units; amounts stay integers.
