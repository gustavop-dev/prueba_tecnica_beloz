"""`request_fingerprint`: SHA-256 of the canonical request body (IDEM-5, IDEM-9, ADR 0002)."""

import hashlib
import json

import pytest

from idempotency.services import request_fingerprint

# The same logical request serialized by different clients: key order and whitespace differ.
EQUIVALENT_SERIALIZATIONS = {
    "key order": ('{"amount_minor": 500, "card_id": "c1"}', '{"card_id":"c1","amount_minor":500}'),
    "whitespace": ('{"amount_minor":500}', '{\n  "amount_minor" :\t500\n}'),
    "nested key order": (
        '{"merchant": {"name": "Shop", "mcc": "5411"}, "amount_minor": 1}',
        '{"amount_minor": 1, "merchant": {"mcc": "5411", "name": "Shop"}}',
    ),
    "unicode escapes": ('{"merchant": "Caf\\u00e9"}', '{"merchant": "Café"}'),
}

# Pairs of different requests: any change of value, type or shape must change the hash.
DIFFERENT_BODIES = {
    "amount 500 vs 200 (ADR 0002)": ({"amount_minor": 500}, {"amount_minor": 200}),
    "int vs string": ({"amount_minor": 500}, {"amount_minor": "500"}),
    "extra key": ({"amount_minor": 500}, {"amount_minor": 500, "card_id": "c1"}),
    "nested value": ({"merchant": {"name": "Shop"}}, {"merchant": {"name": "Shop2"}}),
    "list order": ({"ids": [1, 2]}, {"ids": [2, 1]}),
    "null vs missing": ({"amount_minor": 500, "note": None}, {"amount_minor": 500}),
    "empty object vs null": ({}, None),
    "accent": ({"merchant": "Café"}, {"merchant": "Cafe"}),
}


def test_fingerprint_is_the_sha256_of_the_canonical_json():
    """Fails if the fingerprint is not SHA-256 over sorted, compact UTF-8 JSON (IDEM-5, IDEM-9):
    the core and stored records would disagree on what "the same request" is."""
    body = {"merchant": "Café", "amount_minor": 500}

    fingerprint = request_fingerprint(body)

    assert fingerprint == hashlib.sha256('{"amount_minor":500,"merchant":"Café"}'.encode()).digest()
    assert len(fingerprint) == 32


def test_fingerprint_is_deterministic():
    """Fails if hashing the same body twice gives different results, which would turn every retry
    into a `422` key reuse instead of a replay (IDEM-5)."""
    first = request_fingerprint({"amount_minor": 500, "card_id": "c1"})
    second = request_fingerprint({"amount_minor": 500, "card_id": "c1"})

    assert first == second


@pytest.mark.parametrize(
    ("first", "second"), EQUIVALENT_SERIALIZATIONS.values(), ids=EQUIVALENT_SERIALIZATIONS.keys()
)
def test_fingerprint_ignores_key_order_and_whitespace(first, second):
    """Fails if two serializations of the same logical body hash differently, so a client retry
    re-serialized by another library would be rejected as a key reuse (IDEM-9)."""
    assert request_fingerprint(json.loads(first)) == request_fingerprint(json.loads(second))


@pytest.mark.parametrize(
    ("first", "second"), DIFFERENT_BODIES.values(), ids=DIFFERENT_BODIES.keys()
)
def test_fingerprint_changes_with_any_value_change(first, second):
    """Fails if two different requests share a fingerprint: a key reused for a payment of 500 would
    replay the response of a payment of 200 (ADR 0002, IDEM-5)."""
    assert request_fingerprint(first) != request_fingerprint(second)


def test_fingerprint_accepts_lone_surrogates_from_json_escapes():
    """Fails if a body with a lone surrogate (a valid `\\ud800` JSON escape) crashes the hash, turning
    a client input into a server error on every retry (IDEM-9)."""
    body = json.loads('{"note": "\\ud800"}')

    fingerprint = request_fingerprint(body)

    assert fingerprint == hashlib.sha256(b'{"note":"\xed\xa0\x80"}').digest()
