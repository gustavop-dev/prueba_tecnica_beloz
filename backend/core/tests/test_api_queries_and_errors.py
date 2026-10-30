import uuid
from unittest import mock

import pytest

from core.tests.conftest import DEPOSIT_MINOR, error_code, purchase, repay

pytestmark = pytest.mark.django_db


def test_account_detail_reports_status_limit_debt_available_deposit_and_card(customer_a, account_a):
    """Fails if GET /accounts/{id} omits or miscomputes a documented field (api.md §2)."""
    purchase(customer_a, account_a.card_id, 30_000)

    body = customer_a.get(f"/accounts/{account_a.account_id}").json()

    assert body["id"] == account_a.account_id
    assert body["status"] == "ACTIVE"
    assert body["credit_limit_minor"] == DEPOSIT_MINOR
    assert body["outstanding_minor"] == 30_000
    assert body["available_credit_minor"] == DEPOSIT_MINOR - 30_000
    assert body["deposit"]["id"] == account_a.deposit_id
    assert body["deposit"]["amount_minor"] == DEPOSIT_MINOR
    assert body["card"]["id"] == account_a.card_id


def test_card_is_exposed_only_by_its_last_four_digits(customer_a, account_a):
    """Fails if the API exposes anything about the card beyond id, last 4 and status (SEC-CARD-1..3)."""
    body = customer_a.get(f"/accounts/{account_a.account_id}").json()

    assert set(body["card"]) == {"id", "last4", "status"}


def test_ledger_lists_entries_oldest_first_and_each_entry_balances(customer_a, account_a):
    """Fails if the ledger view is out of order or shows an entry whose lines do not sum to 0 (INV-2)."""
    purchase(customer_a, account_a.card_id, 30_000)
    repay(customer_a, account_a.account_id, 10_000)

    entries = customer_a.get(f"/accounts/{account_a.account_id}/ledger").json()["entries"]

    assert [entry["kind"] for entry in entries] == ["DEPOSIT_CONFIRMED", "PURCHASE", "REPAYMENT"]
    assert [sum(line["amount_minor"] for line in entry["lines"]) for entry in entries] == [0, 0, 0]


def test_malformed_uuid_in_the_path_is_a_json_not_found(customer_a):
    """Fails if an unroutable URL returns Django's HTML page instead of the error envelope (api.md §3)."""
    response = customer_a.get("/accounts/not-a-uuid")

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert error_code(response) == "NOT_FOUND"


def test_wrong_method_is_rejected_with_the_envelope(customer_a, account_a):
    """Fails if an unsupported method is not answered with the error envelope."""
    response = customer_a.client.delete(f"/accounts/{account_a.account_id}")

    assert response.status_code == 405
    assert error_code(response) == "METHOD_NOT_ALLOWED"


def test_unexpected_errors_become_internal_error_without_details(customer_a, account_a):
    """Fails if an unexpected exception leaks internals instead of INTERNAL_ERROR (api.md §4)."""
    with mock.patch(
        "core.api.views.flows.get_account_view",
        side_effect=RuntimeError("database password=secret"),
    ):
        response = customer_a.get(f"/accounts/{account_a.account_id}")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.content.decode()


def test_every_response_carries_a_generated_request_id(customer_a, account_a):
    """Fails if a response has no X-Request-ID or the error envelope does not echo it (OBS-2)."""
    response = customer_a.get(f"/accounts/{uuid.uuid4()}")

    assert uuid.UUID(response["X-Request-ID"])
    assert response.json()["error"]["request_id"] == response["X-Request-ID"]


def test_a_valid_incoming_request_id_is_propagated(customer_a, account_a):
    """Fails if a valid caller-provided X-Request-ID is replaced (OBS-2)."""
    request_id = str(uuid.uuid4())

    response = customer_a.get(f"/accounts/{account_a.account_id}", HTTP_X_REQUEST_ID=request_id)

    assert response["X-Request-ID"] == request_id


def test_an_invalid_incoming_request_id_is_replaced(customer_a, account_a):
    """Fails if an arbitrary caller string is trusted as a request id (OBS-2)."""
    response = customer_a.get(
        f"/accounts/{account_a.account_id}", HTTP_X_REQUEST_ID="<script>alert(1)</script>"
    )

    assert response["X-Request-ID"] != "<script>alert(1)</script>"
    assert uuid.UUID(response["X-Request-ID"])
