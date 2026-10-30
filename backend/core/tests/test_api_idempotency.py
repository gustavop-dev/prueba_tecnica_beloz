"""Idempotency at the API (docs/idempotency.md, docs/testing.md §3)."""

import threading
import uuid
from unittest import mock

import pytest
from django.db import connection, transaction

from accounts import services as accounts
from core.reconciliation import find_mismatches
from core.tests.concurrency import run_concurrently
from core.tests.conftest import (
    DEPOSIT_MINOR,
    TOKENS,
    Api,
    error_code,
    open_account,
    provider_api,
    purchase,
    report_deposit,
    request_opening,
)
from ledger import services as ledger

KEY = "11111111-2222-4333-8444-555555555555"


def _purchase_entries(account_id):
    return [
        entry for entry in ledger.list_entries(uuid.UUID(account_id)) if entry.kind == "PURCHASE"
    ]


def _outstanding(account_id):
    return accounts.get_account(uuid.UUID(account_id)).outstanding_minor


@pytest.mark.django_db
def test_retry_with_same_key_and_body_replays_without_a_second_effect(customer_a, account_a):
    """Fails if a retried purchase is applied twice or answers differently (TEST-IDEM-1, INV-4)."""
    url = f"/cards/{account_a.card_id}/authorizations"
    first = customer_a.post(url, {"amount_minor": 1_000}, HTTP_IDEMPOTENCY_KEY=KEY)

    retry = customer_a.post(url, {"amount_minor": 1_000}, HTTP_IDEMPOTENCY_KEY=KEY)

    assert retry.status_code == first.status_code == 201
    assert retry.json() == first.json()
    assert retry["Idempotent-Replayed"] == "true"
    assert len(_purchase_entries(account_a.account_id)) == 1
    assert _outstanding(account_a.account_id) == 1_000


@pytest.mark.django_db
def test_same_key_with_a_different_body_is_rejected_without_effect(customer_a, account_a):
    """Fails if a reused key with another amount executes, e.g. 500 replayed as 200 (TEST-IDEM-2, ADR 0002)."""
    url = f"/accounts/{account_a.account_id}/repayments"
    purchase(customer_a, account_a.card_id, 5_000)
    customer_a.post(url, {"amount_minor": 200}, HTTP_IDEMPOTENCY_KEY=KEY)

    response = customer_a.post(url, {"amount_minor": 500}, HTTP_IDEMPOTENCY_KEY=KEY)

    assert response.status_code == 422
    assert error_code(response) == "IDEMPOTENCY_KEY_REUSED"
    assert _outstanding(account_a.account_id) == 4_800


@pytest.mark.django_db
def test_key_order_and_whitespace_do_not_make_a_different_request(customer_a, account_a):
    """Fails if the same logical body serialized differently is treated as another request (IDEM-9)."""
    url = f"/cards/{account_a.card_id}/authorizations"
    customer_a.client.post(
        url, '{"amount_minor": 700}', content_type="application/json", HTTP_IDEMPOTENCY_KEY=KEY
    )

    retry = customer_a.client.post(
        url, '{ "amount_minor" :700 }', content_type="application/json", HTTP_IDEMPOTENCY_KEY=KEY
    )

    assert retry.status_code == 201
    assert retry["Idempotent-Replayed"] == "true"
    assert _outstanding(account_a.account_id) == 700


@pytest.mark.django_db
def test_business_rejections_are_replayed_even_after_the_balance_changes(customer_a, account_a):
    """Fails if a retried rejected purchase is re-evaluated against a newer balance (IDEM-11)."""
    url = f"/cards/{account_a.card_id}/authorizations"
    purchase(customer_a, account_a.card_id, DEPOSIT_MINOR)
    rejected = customer_a.post(url, {"amount_minor": 5_000}, HTTP_IDEMPOTENCY_KEY=KEY)
    customer_a.post(f"/accounts/{account_a.account_id}/repayments", {"amount_minor": 50_000})

    retry = customer_a.post(url, {"amount_minor": 5_000}, HTTP_IDEMPOTENCY_KEY=KEY)

    assert rejected.status_code == retry.status_code == 422
    assert retry.json() == rejected.json()
    assert _outstanding(account_a.account_id) == DEPOSIT_MINOR - 50_000


@pytest.mark.django_db
def test_a_key_is_scoped_to_its_concrete_endpoint(customer_a, provider):
    """Fails if the same key and body on another account's endpoint is treated as a retry (IDEM-8)."""
    first = open_account(customer_a, provider)
    second = open_account(customer_a, provider)

    one = customer_a.post(
        f"/cards/{first.card_id}/authorizations", {"amount_minor": 300}, HTTP_IDEMPOTENCY_KEY=KEY
    )
    two = customer_a.post(
        f"/cards/{second.card_id}/authorizations", {"amount_minor": 300}, HTTP_IDEMPOTENCY_KEY=KEY
    )

    assert one.status_code == two.status_code == 201
    assert one.json()["authorization_id"] != two.json()["authorization_id"]
    assert _outstanding(first.account_id) == _outstanding(second.account_id) == 300


@pytest.mark.django_db
def test_a_key_is_scoped_to_its_principal(customer_a, customer_b):
    """Fails if one customer's key collides with another customer's identical request (IDEM-4)."""
    body = {"deposit_amount_minor": 1_000}

    one = customer_a.post("/accounts", body, HTTP_IDEMPOTENCY_KEY=KEY)
    two = customer_b.post("/accounts", body, HTTP_IDEMPOTENCY_KEY=KEY)

    assert one.status_code == two.status_code == 201
    assert one.json()["deposit"]["id"] != two.json()["deposit"]["id"]


@pytest.mark.django_db
def test_duplicate_webhook_with_the_same_key_opens_one_account(customer_a, provider):
    """Fails if a retried provider report opens or books the deposit twice (TEST-IDEM-4, IDEM-13)."""
    opening = request_opening(customer_a)
    body = {"deposit_id": opening["deposit"]["id"], "outcome": "succeeded"}
    first = provider.post("/webhooks/payments", body, HTTP_IDEMPOTENCY_KEY=KEY)

    retry = provider.post("/webhooks/payments", body, HTTP_IDEMPOTENCY_KEY=KEY)

    assert retry.status_code == first.status_code == 200
    assert retry.json() == first.json()
    assert len(ledger.list_entries(uuid.UUID(opening["account_id"]))) == 1


@pytest.mark.django_db
def test_webhook_resent_with_a_new_key_is_stopped_by_the_state_machine(customer_a, provider):
    """Fails if a processed deposit is applied again when the report arrives with a new key (TEST-IDEM-5, IDEM-15)."""
    opening = request_opening(customer_a)
    report_deposit(provider, opening["deposit"]["id"])

    response = report_deposit(provider, opening["deposit"]["id"])

    assert response.status_code == 409
    assert error_code(response) == "DEPOSIT_NOT_PENDING"
    assert len(ledger.list_entries(uuid.UUID(opening["account_id"]))) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "IDEMPOTENCY_KEY_MISSING"),
        ({"HTTP_IDEMPOTENCY_KEY": "not-a-uuid"}, "IDEMPOTENCY_KEY_INVALID"),
    ],
    ids=["missing", "invalid"],
)
def test_post_without_a_valid_key_is_rejected_before_any_effect(
    customer_a, account_a, headers, code
):
    """Fails if a POST without a valid Idempotency-Key runs (TEST-IDEM-6, IDEM-1, IDEM-2)."""
    response = customer_a.client.post(
        f"/cards/{account_a.card_id}/authorizations",
        {"amount_minor": 100},
        format="json",
        **headers,
    )

    assert response.status_code == 400
    assert error_code(response) == code
    assert _outstanding(account_a.account_id) == 0


@pytest.mark.django_db
def test_a_technical_failure_is_not_stored_so_the_same_key_can_retry(customer_a, account_a):
    """Fails if a 5xx is stored and blocks the legitimate retry (IDEM-7)."""
    url = f"/cards/{account_a.card_id}/authorizations"
    with mock.patch("core.flows.operations.ledger.post_purchase", side_effect=RuntimeError("down")):
        failed = customer_a.post(url, {"amount_minor": 100}, HTTP_IDEMPOTENCY_KEY=KEY)

    retry = customer_a.post(url, {"amount_minor": 100}, HTTP_IDEMPOTENCY_KEY=KEY)

    assert failed.status_code == 500
    assert retry.status_code == 201
    assert "Idempotent-Replayed" not in retry
    assert _outstanding(account_a.account_id) == 100


@pytest.mark.django_db(transaction=True)
def test_same_key_sent_concurrently_has_exactly_one_effect():
    """Fails if concurrent requests with one key both execute or answer differently (TEST-IDEM-3, IDEM-10)."""
    customer, provider = Api(TOKENS["customer_a"][2]), provider_api()
    account = open_account(customer, provider)
    url = f"/cards/{account.card_id}/authorizations"

    def send():
        return Api(TOKENS["customer_a"][2]).post(
            url, {"amount_minor": 1_000}, HTTP_IDEMPOTENCY_KEY=KEY
        )

    outcomes = run_concurrently(*[send for _ in range(8)])

    responses = [outcome.value for outcome in outcomes]
    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["authorization_id"] for response in responses}) == 1
    assert sum(response.get("Idempotent-Replayed") == "true" for response in responses) == 7
    assert len(_purchase_entries(account.account_id)) == 1
    assert _outstanding(account.account_id) == 1_000
    assert find_mismatches() == []


@pytest.mark.django_db(transaction=True)
def test_lock_timeout_answers_503_and_the_same_key_succeeds_later(settings):
    """Fails if a request stuck behind a held lock waits forever or blocks its own retry (CON-9)."""
    settings.DB_LOCK_TIMEOUT_MS = 200
    customer = Api(TOKENS["customer_a"][2])
    account = open_account(customer, provider_api())
    url = f"/cards/{account.card_id}/authorizations"
    locked, release = threading.Event(), threading.Event()

    def hold_account_lock():
        try:
            with transaction.atomic():
                accounts.lock_account(uuid.UUID(account.account_id))
                locked.set()
                release.wait(10)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_account_lock, daemon=True)
    holder.start()
    locked.wait(10)
    try:
        timed_out = customer.post(url, {"amount_minor": 100}, HTTP_IDEMPOTENCY_KEY=KEY)
    finally:
        release.set()
        holder.join(10)
    retry = customer.post(url, {"amount_minor": 100}, HTTP_IDEMPOTENCY_KEY=KEY)

    assert timed_out.status_code == 503
    assert error_code(timed_out) == "LOCK_TIMEOUT"
    assert retry.status_code == 201
    assert _outstanding(account.account_id) == 100
