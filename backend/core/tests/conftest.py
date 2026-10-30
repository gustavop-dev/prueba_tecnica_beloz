import uuid
from dataclasses import dataclass

import pytest
from rest_framework.test import APIClient

CUSTOMER_A = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
CUSTOMER_B = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")
PROVIDER = uuid.UUID("cccccccc-0000-4000-8000-00000000000c")
OPERATOR = uuid.UUID("dddddddd-0000-4000-8000-00000000000d")

TOKENS = {
    "customer_a": ("customer", CUSTOMER_A, "test-customer-a-token"),
    "customer_b": ("customer", CUSTOMER_B, "test-customer-b-token"),
    "provider": ("payment_provider", PROVIDER, "test-provider-token-0"),
    "operator": ("operator", OPERATOR, "test-operator-token-0"),
}

DEPOSIT_MINOR = 100_000


@pytest.fixture(autouse=True)
def auth_tokens(settings):
    settings.AUTH_TOKENS = ",".join(
        f"{role}:{principal_id}:{token}" for role, principal_id, token in TOKENS.values()
    )


class Api:
    """API client for one caller. Every POST carries a fresh Idempotency-Key (API-3)."""

    def __init__(self, token: str | None = None):
        self.client = APIClient()
        if token:
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def post(self, url, body=None, **headers):
        headers.setdefault("HTTP_IDEMPOTENCY_KEY", str(uuid.uuid4()))
        return self.client.post(url, {} if body is None else body, format="json", **headers)

    def post_raw(self, url, raw: str):
        return self.client.post(
            url, raw, content_type="application/json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )

    def get(self, url, **headers):
        return self.client.get(url, **headers)


@pytest.fixture
def customer_a():
    return Api(TOKENS["customer_a"][2])


@pytest.fixture
def customer_b():
    return Api(TOKENS["customer_b"][2])


@pytest.fixture
def provider():
    return Api(TOKENS["provider"][2])


@pytest.fixture
def operator():
    return Api(TOKENS["operator"][2])


@pytest.fixture
def anonymous():
    return Api()


@dataclass(frozen=True)
class OpenedAccount:
    account_id: str
    deposit_id: str
    card_id: str


def request_opening(customer: Api, amount_minor: int = DEPOSIT_MINOR) -> dict:
    response = customer.post("/accounts", {"deposit_amount_minor": amount_minor})
    assert response.status_code == 201, response.json()
    return response.json()


def report_deposit(provider: Api, deposit_id: str, outcome: str = "succeeded"):
    return provider.post("/webhooks/payments", {"deposit_id": deposit_id, "outcome": outcome})


def open_account(customer: Api, provider: Api, amount_minor: int = DEPOSIT_MINOR) -> OpenedAccount:
    opening = request_opening(customer, amount_minor)
    report = report_deposit(provider, opening["deposit"]["id"])
    assert report.status_code == 200, report.json()
    detail = customer.get(f"/accounts/{opening['account_id']}").json()
    return OpenedAccount(
        account_id=opening["account_id"],
        deposit_id=opening["deposit"]["id"],
        card_id=detail["card"]["id"],
    )


def purchase(customer: Api, card_id: str, amount_minor: int):
    return customer.post(f"/cards/{card_id}/authorizations", {"amount_minor": amount_minor})


def repay(customer: Api, account_id: str, amount_minor: int):
    return customer.post(f"/accounts/{account_id}/repayments", {"amount_minor": amount_minor})


def close(customer: Api, account_id: str):
    return customer.post(f"/accounts/{account_id}/close")


def error_code(response) -> str:
    return response.json()["error"]["code"]


@pytest.fixture
def account_a(customer_a, provider) -> OpenedAccount:
    return open_account(customer_a, provider)
