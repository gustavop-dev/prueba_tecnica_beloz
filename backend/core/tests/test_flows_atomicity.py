"""Each flow is all-or-nothing: a technical failure halfway leaves no partial state (MOD-4)."""

import uuid
from unittest import mock

import pytest

from accounts import services as accounts
from collateral import services as collateral
from core import flows
from core.reconciliation import find_mismatches
from core.tests.conftest import (
    CUSTOMER_A,
    DEPOSIT_MINOR,
    close,
    open_account,
    purchase,
    repay,
    report_deposit,
    request_opening,
)
from ledger import services as ledger

pytestmark = pytest.mark.django_db


class Boom(Exception):
    pass


def test_failure_while_confirming_leaves_the_deposit_pending_and_opens_nothing(customer_a):
    """Fails if a crash during confirmation leaves a half-opened account (BR-DEP-3, BR-DEP-5)."""
    opening = request_opening(customer_a)
    deposit_id = uuid.UUID(opening["deposit"]["id"])

    with mock.patch("core.flows.opening.cards.issue_card", side_effect=Boom), pytest.raises(Boom):
        flows.apply_provider_report(deposit_id=deposit_id, succeeded=True)

    assert collateral.get_deposit(deposit_id).status == "PENDING"
    with pytest.raises(accounts.AccountNotFound):
        accounts.get_account(uuid.UUID(opening["account_id"]))
    assert ledger.list_entries(uuid.UUID(opening["account_id"])) == []


def test_failure_while_booking_a_purchase_leaves_the_debt_unchanged(customer_a, provider):
    """Fails if a purchase raises the debt without its ledger entry (BR-PUR-1, CACHE-2)."""
    account = open_account(customer_a, provider)

    with (
        mock.patch("core.flows.operations.ledger.post_purchase", side_effect=Boom),
        pytest.raises(Boom),
    ):
        flows.authorize_purchase(
            owner_id=CUSTOMER_A, card_id=uuid.UUID(account.card_id), amount_minor=1_000
        )

    assert accounts.get_account(uuid.UUID(account.account_id)).outstanding_minor == 0
    assert find_mismatches() == []


def test_failure_while_closing_leaves_account_deposit_and_card_untouched(customer_a, provider):
    """Fails if a crash during closure leaves the account half closed (BR-CLO-5)."""
    account = open_account(customer_a, provider)
    purchase(customer_a, account.card_id, 30_000)

    with (
        mock.patch("core.flows.operations.cards.close_card", side_effect=Boom),
        pytest.raises(Boom),
    ):
        flows.close(owner_id=CUSTOMER_A, account_id=uuid.UUID(account.account_id))

    detail = customer_a.get(f"/accounts/{account.account_id}").json()
    assert detail["status"] == "ACTIVE"
    assert detail["outstanding_minor"] == 30_000
    assert detail["deposit"]["status"] == "CONFIRMED"
    assert detail["card"]["status"] == "ACTIVE"
    assert find_mismatches() == []


def test_full_lifecycle_keeps_cache_deposits_and_ledger_reconciled(customer_a, provider):
    """Fails if any step of open → purchase → repay → close leaves the cache out of sync (INV-1, CACHE-2)."""
    opening = request_opening(customer_a)
    report_deposit(provider, opening["deposit"]["id"])
    detail = customer_a.get(f"/accounts/{opening['account_id']}").json()

    purchase(customer_a, detail["card"]["id"], 40_000)
    repay(customer_a, opening["account_id"], 15_000)
    purchase(customer_a, detail["card"]["id"], 5_000)
    closure = close(customer_a, opening["account_id"]).json()

    assert closure["debt_at_closure_minor"] == 30_000
    assert closure["refund_minor"] == DEPOSIT_MINOR - 30_000
    assert find_mismatches() == []


def test_each_account_has_exactly_one_deposit_and_one_card(customer_a, provider):
    """Fails if an opened account gets more than one deposit or card (BR-ACC-2, BR-ACC-3)."""
    account = open_account(customer_a, provider)

    entries = customer_a.get(f"/accounts/{account.account_id}/ledger").json()["entries"]
    detail = customer_a.get(f"/accounts/{account.account_id}").json()

    assert [entry["kind"] for entry in entries] == ["DEPOSIT_CONFIRMED"]
    assert detail["deposit"]["id"] == account.deposit_id
    assert detail["card"]["id"] == account.card_id


def test_provider_reports_success_or_failure_through_the_webhook(customer_a, provider):
    """Fails if the provider endpoint cannot report both outcomes (BR-DEP-2)."""
    confirmed = request_opening(customer_a)
    failed = request_opening(customer_a)

    ok = report_deposit(provider, confirmed["deposit"]["id"], outcome="succeeded")
    ko = report_deposit(provider, failed["deposit"]["id"], outcome="failed")

    assert ok.json()["deposit"]["status"] == "CONFIRMED"
    assert ko.json()["deposit"]["status"] == "FAILED"
