"""Property-based tests (docs/testing.md §6): invariants hold after every step of random sequences.

The state machine drives the real API — purchases, repayments, closure, card freeze/unfreeze and
retries with a reused Idempotency-Key — and keeps a reference model of the expected debt. After every
step it checks INV-1, INV-2, INV-5, CACHE-2 and the reference model.
"""

import json
import uuid

import pytest
from django.db import transaction
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from cards import services as cards
from core.reconciliation import find_account_mismatches
from core.tests.conftest import TOKENS, Api, open_account, provider_api

DEPOSIT_MINOR = 10_000
SETTLED = {"RELEASED", "PARTIALLY_APPLIED", "APPLIED"}


class AccountLifecycle(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.customer = Api(TOKENS["customer_a"][2])
        opened = open_account(self.customer, provider_api(), DEPOSIT_MINOR)
        self.account_id = opened.account_id
        self.card_id = opened.card_id
        self.debt = 0  # reference model of the outstanding debt
        self.closed = False
        self.frozen = False
        self.last_post = None  # (url, body, key, status, json) of the last POST

    # -- helpers ---------------------------------------------------------------------------------

    def _post(self, url, body, key=None):
        key = key or str(uuid.uuid4())
        response = self.customer.post(url, body, HTTP_IDEMPOTENCY_KEY=key)
        self.last_post = (url, body, key, response.status_code, response.json())
        return response

    def _expected_rejection(self, amount, *, purchase):
        if self.closed:
            return 409, "ACCOUNT_CLOSED"
        if purchase and self.frozen:
            return 409, "CARD_NOT_ACTIVE"
        if purchase and amount > DEPOSIT_MINOR - self.debt:
            return 422, "INSUFFICIENT_CREDIT"
        if not purchase and amount > self.debt:
            return 422, "PAYMENT_EXCEEDS_DEBT"
        return None

    def _check(self, response, rejection, success_status):
        if rejection is None:
            assert response.status_code == success_status, response.json()
            return True
        assert (response.status_code, response.json()["error"]["code"]) == rejection
        return False

    # -- rules -----------------------------------------------------------------------------------

    @rule(amount=st.integers(min_value=1, max_value=2 * DEPOSIT_MINOR))
    def purchase(self, amount):
        rejection = self._expected_rejection(amount, purchase=True)
        response = self._post(f"/cards/{self.card_id}/authorizations", {"amount_minor": amount})
        if self._check(response, rejection, 201):
            self.debt += amount

    @rule(amount=st.integers(min_value=1, max_value=2 * DEPOSIT_MINOR))
    def repay(self, amount):
        rejection = self._expected_rejection(amount, purchase=False)
        response = self._post(f"/accounts/{self.account_id}/repayments", {"amount_minor": amount})
        if self._check(response, rejection, 201):
            self.debt -= amount

    @rule()
    def close(self):
        rejection = (409, "ACCOUNT_CLOSED") if self.closed else None
        response = self._post(f"/accounts/{self.account_id}/close", {})
        if self._check(response, rejection, 200):
            body = response.json()
            assert body["debt_at_closure_minor"] == self.debt
            assert body["refund_minor"] == DEPOSIT_MINOR - self.debt
            self.debt = 0
            self.closed = True

    @precondition(lambda self: not self.closed)
    @rule()
    def toggle_freeze(self):
        with transaction.atomic():
            toggle = cards.unfreeze_card if self.frozen else cards.freeze_card
            toggle(uuid.UUID(self.card_id))
        self.frozen = not self.frozen

    @precondition(lambda self: self.last_post is not None)
    @rule()
    def retry_last_request(self):
        """A retry with the same key and body replays the first answer and changes nothing (INV-4)."""
        url, body, key, status, payload = self.last_post
        response = self.customer.post(url, body, HTTP_IDEMPOTENCY_KEY=key)
        assert response.status_code == status
        assert response.json() == payload
        assert response["Idempotent-Replayed"] == "true"

    @precondition(lambda self: self.last_post is not None)
    @rule()
    def reuse_key_with_a_different_body(self):
        """The same key with another body is rejected before running anything (IDEM behavior table)."""
        url, body, key, _, _ = self.last_post
        response = self.customer.post(url, {**body, "tampered": True}, HTTP_IDEMPOTENCY_KEY=key)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # -- invariants ------------------------------------------------------------------------------

    @invariant()
    def balances_stay_within_bounds_and_match_the_model(self):
        detail = self.customer.get(f"/accounts/{self.account_id}").json()
        assert 0 <= detail["outstanding_minor"] <= detail["credit_limit_minor"]  # INV-1
        assert detail["outstanding_minor"] == self.debt
        assert detail["available_credit_minor"] == detail["credit_limit_minor"] - self.debt

    @invariant()
    def every_entry_balances_and_the_cache_matches_the_ledger(self):
        entries = self.customer.get(f"/accounts/{self.account_id}/ledger").json()["entries"]
        assert {sum(line["amount_minor"] for line in entry["lines"]) for entry in entries} == {0}
        assert find_account_mismatches(uuid.UUID(self.account_id)) == []  # CACHE-2

    @invariant()
    def only_valid_state_combinations_exist(self):
        detail = self.customer.get(f"/accounts/{self.account_id}").json()
        combination = (detail["status"], detail["deposit"]["status"], detail["card"]["status"])
        if self.closed:
            assert combination[0] == "CLOSED" and combination[2] == "CLOSED"
            assert combination[1] in SETTLED
            deposit = detail["deposit"]
            assert deposit["applied_minor"] + deposit["refunded_minor"] == DEPOSIT_MINOR
        else:
            expected_card = "INACTIVE" if self.frozen else "ACTIVE"
            assert combination == ("ACTIVE", "CONFIRMED", expected_card)


@pytest.mark.django_db
def test_invariants_hold_through_random_account_lifecycles():
    """Fails if any random sequence of operations and retries breaks an invariant or the debt model (TEST-PROP-1)."""
    run_state_machine_as_test(AccountLifecycle)


INVALID_AMOUNTS = st.one_of(
    st.integers(max_value=0),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=12),
    st.booleans(),
    st.none(),
    st.lists(st.integers(), max_size=2),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)


@pytest.mark.django_db
def test_invalid_amounts_are_always_rejected_without_effect(customer_a, account_a):
    """Fails if any non-positive or non-integer amount is accepted or changes the debt (TEST-PROP-2, BR-MONEY-3..5)."""
    url = f"/cards/{account_a.card_id}/authorizations"

    @given(value=INVALID_AMOUNTS)
    def check(value):
        response = customer_a.post_raw(url, json.dumps({"amount_minor": value}))

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    check()

    detail = customer_a.get(f"/accounts/{account_a.account_id}").json()
    assert detail["outstanding_minor"] == 0
