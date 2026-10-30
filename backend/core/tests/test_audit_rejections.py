"""Audit of rejected attempts (docs/audit.md §1): business and access rejections of authenticated
callers become `rejected` events; 400, 401 and 5xx do not."""

import io
import json
import logging
import uuid
from unittest import mock

import pytest
from django.db import DatabaseError

from audit import services as audit
from core.tests.conftest import (
    CUSTOMER_A,
    CUSTOMER_B,
    DEPOSIT_MINOR,
    OPERATOR,
    PROVIDER,
    Api,
    close,
    error_code,
    purchase,
    repay,
    report_deposit,
    request_opening,
)

pytestmark = pytest.mark.django_db

KEY = "55555555-4444-4333-8222-111111111111"


def _events(response):
    return audit.list_events(request_id=uuid.UUID(response["X-Request-ID"]))


def _rejection(response):
    (event,) = _events(response)
    assert event.outcome == "rejected"
    assert event.before_state is None
    assert event.after_state is None
    return event


@pytest.fixture
def security_lines():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s|%(code)s|%(path)s"))
    security = logging.getLogger("core.security")
    security.addHandler(handler)
    yield lambda: stream.getvalue().splitlines()
    security.removeHandler(handler)


def test_business_rejection_is_audited_with_actor_operation_and_attempt(customer_a, account_a):
    """Fails if a rejected purchase leaves no trail of who tried what, on which card, and why (AUD-1)."""
    response = purchase(customer_a, account_a.card_id, DEPOSIT_MINOR + 1)

    event = _rejection(response)
    assert response.status_code == 422
    assert (event.action, event.entity_type, str(event.entity_id)) == (
        "card.purchase",
        "card",
        account_a.card_id,
    )
    assert (event.actor_id, event.actor_role) == (CUSTOMER_A, "customer")
    assert event.error_code == "INSUFFICIENT_CREDIT"
    assert event.attempt == {"amount_minor": DEPOSIT_MINOR + 1}
    assert event.idempotency_key is not None


def _repay_without_debt(customer, account):
    return repay(customer, account.account_id, 1)


def _freeze_twice(customer, account):
    customer.post(f"/cards/{account.card_id}/freeze")
    return customer.post(f"/cards/{account.card_id}/freeze")


def _purchase_after_close(customer, account):
    close(customer, account.account_id)
    return purchase(customer, account.card_id, 1)


def _purchase_with_frozen_card(customer, account):
    customer.post(f"/cards/{account.card_id}/freeze")
    return purchase(customer, account.card_id, 1)


@pytest.mark.parametrize(
    ("rejected_call", "action", "code"),
    [
        (_repay_without_debt, "credit_account.repay", "PAYMENT_EXCEEDS_DEBT"),
        (_freeze_twice, "card.freeze", "INVALID_STATE_TRANSITION"),
        (_purchase_after_close, "card.purchase", "ACCOUNT_CLOSED"),
        (_purchase_with_frozen_card, "card.purchase", "CARD_NOT_ACTIVE"),
    ],
    ids=["payment-exceeds-debt", "invalid-transition", "account-closed", "card-not-active"],
)
def test_each_business_rejection_code_is_audited(
    customer_a, account_a, rejected_call, action, code
):
    """Fails if a domain rejection of an authenticated customer is not audited (AUD-1)."""
    response = rejected_call(customer_a, account_a)

    event = _rejection(response)
    assert (event.action, event.error_code) == (action, code)


def test_replayed_rejection_is_not_audited_twice(customer_a, account_a):
    """Fails if retrying a rejected request with the same key records the attempt again (INV-4)."""
    url = f"/cards/{account_a.card_id}/authorizations"
    customer_a.post(url, {"amount_minor": DEPOSIT_MINOR + 1}, HTTP_IDEMPOTENCY_KEY=KEY)

    replay = customer_a.post(url, {"amount_minor": DEPOSIT_MINOR + 1}, HTTP_IDEMPOTENCY_KEY=KEY)

    assert replay["Idempotent-Replayed"] == "true"
    assert _events(replay) == []
    rejected = [
        event
        for event in audit.list_events(entity_id=uuid.UUID(account_a.card_id))
        if event.outcome == "rejected"
    ]
    assert len(rejected) == 1


def test_idor_read_attempt_is_audited_against_the_target(customer_b, account_a):
    """Fails if customer B probing customer A's account leaves no trail (SEC-IDOR-1, AUD-1)."""
    response = customer_b.get(f"/accounts/{account_a.account_id}")

    event = _rejection(response)
    assert response.status_code == 404
    assert (event.actor_id, event.action, event.error_code) == (
        CUSTOMER_B,
        "credit_account.read",
        "NOT_FOUND",
    )
    assert str(event.entity_id) == account_a.account_id
    assert event.attempt == {}


def test_idor_write_attempt_is_audited_with_its_parameters(customer_b, account_a):
    """Fails if a foreign repayment attempt is not audited with the attempted amount (SEC-IDOR-1)."""
    response = repay(customer_b, account_a.account_id, 5_000)

    event = _rejection(response)
    assert (event.action, event.error_code, event.attempt) == (
        "credit_account.repay",
        "NOT_FOUND",
        {"amount_minor": 5_000},
    )


@pytest.mark.parametrize(
    ("caller", "actor_id", "role"),
    [("provider_bearer_only", PROVIDER, "payment_provider"), ("operator", OPERATOR, "operator")],
)
def test_role_denial_is_audited(request, account_a, caller, actor_id, role):
    """Fails if a wrong-role call (403) leaves no trail (SEC-ROLE-1, AUD-1)."""
    api = request.getfixturevalue(caller)

    response = api.get(f"/accounts/{account_a.account_id}")

    event = _rejection(response)
    assert response.status_code == 403
    assert (event.actor_id, event.actor_role, event.action, event.error_code) == (
        actor_id,
        role,
        "credit_account.read",
        "FORBIDDEN",
    )


def test_role_denial_before_any_entity_exists_has_no_entity(operator):
    """Fails if an opening request denied by role is not audited without an entity (AUD-1)."""
    response = operator.post("/accounts", {"deposit_amount_minor": 1_000})

    event = _rejection(response)
    assert (event.action, event.entity_id, event.error_code) == (
        "collateral_deposit.request_opening",
        None,
        "FORBIDDEN",
    )
    assert event.attempt == {"deposit_amount_minor": 1_000}


def test_provider_report_for_a_processed_deposit_is_audited(customer_a, provider):
    """Fails if a duplicate provider report with a new key leaves no trail (IDEM-15, AUD-1)."""
    opening = request_opening(customer_a)
    report_deposit(provider, opening["deposit"]["id"])

    response = report_deposit(provider, opening["deposit"]["id"])

    event = _rejection(response)
    assert (event.actor_id, event.actor_role, event.action, event.error_code) == (
        PROVIDER,
        "payment_provider",
        "collateral_deposit.report",
        "DEPOSIT_NOT_PENDING",
    )
    assert str(event.entity_id) == opening["deposit"]["id"]
    assert event.attempt == {"outcome": "succeeded"}


def test_reused_idempotency_key_is_audited(customer_a, account_a):
    """Fails if reusing a key for a different request is not audited (IDEM behavior table, AUD-1)."""
    repayment_url = f"/accounts/{account_a.account_id}/repayments"
    purchase(customer_a, account_a.card_id, 5_000)
    customer_a.post(repayment_url, {"amount_minor": 100}, HTTP_IDEMPOTENCY_KEY=KEY)

    response = customer_a.post(repayment_url, {"amount_minor": 200}, HTTP_IDEMPOTENCY_KEY=KEY)

    event = _rejection(response)
    assert (event.action, event.error_code, event.attempt) == (
        "credit_account.repay",
        "IDEMPOTENCY_KEY_REUSED",
        {"amount_minor": 200},
    )


def test_validation_errors_are_not_audited(customer_a, account_a):
    """Fails if a malformed request (400) produces an audit event (operator decision: noise)."""
    response = customer_a.post_raw(
        f"/cards/{account_a.card_id}/authorizations", '{"amount_minor": 10.5}'
    )

    assert response.status_code == 400
    assert _events(response) == []


def test_technical_failures_are_not_audited(customer_a, account_a):
    """Fails if a rolled-back 5xx leaves a rejection event (AUD-2)."""
    with mock.patch("core.flows.operations.ledger.post_purchase", side_effect=RuntimeError("down")):
        response = purchase(customer_a, account_a.card_id, 1_000)

    assert response.status_code == 500
    assert _events(response) == []


@pytest.mark.parametrize(
    "credentials",
    [{}, {"HTTP_AUTHORIZATION": "Bearer unknown-token-123456"}],
    ids=["missing-token", "unknown-token"],
)
def test_unauthenticated_attempts_are_logged_not_written(account_a, security_lines, credentials):
    """Fails if a 401 writes to the database or is not logged as a security event (operator decision)."""
    api = Api()
    api.client.credentials(**credentials)

    response = api.get(f"/accounts/{account_a.account_id}")

    assert response.status_code == 401
    assert _events(response) == []
    assert security_lines() == [
        f"security.rejected|UNAUTHENTICATED|/accounts/{account_a.account_id}"
    ]


def test_invalid_webhook_signature_is_logged_without_the_signature(customer_a, security_lines):
    """Fails if a forged webhook is written to the database or its signature reaches the log (OBS-3)."""
    opening = request_opening(customer_a)
    forger = Api()

    response = forger.client.post(
        "/webhooks/payments",
        json.dumps({"deposit_id": opening["deposit"]["id"], "outcome": "succeeded"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        HTTP_X_WEBHOOK_SIGNATURE="t=1,v1=" + "ab" * 32,
    )

    assert response.status_code == 401
    assert _events(response) == []
    assert security_lines() == ["security.rejected|INVALID_SIGNATURE|/webhooks/payments"]
    assert "ab" * 32 not in "\n".join(security_lines())


def test_failure_to_record_a_read_rejection_keeps_the_answer(customer_b, account_a, caplog):
    """Fails if a broken audit write turns a 404 into a 500 instead of being logged (AUD-1)."""
    with mock.patch(
        "core.api.rejections.audit.record_rejection", side_effect=DatabaseError("audit down")
    ):
        response = customer_b.get(f"/accounts/{account_a.account_id}")

    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"
    assert "audit.rejection_not_recorded" in caplog.messages


def test_successful_mutations_keep_the_succeeded_outcome(customer_a, account_a):
    """Fails if a mutation event loses its succeeded outcome or gains rejection fields (AUD-1)."""
    response = purchase(customer_a, account_a.card_id, 1_000)

    (event,) = _events(response)
    assert (event.outcome, event.error_code, event.attempt) == ("succeeded", None, None)
    assert event.after_state["outstanding_minor"] == 1_000
