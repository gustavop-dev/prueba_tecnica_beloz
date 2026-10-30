"""Permission matrix per view (backend-test-coverage) and object ownership (SEC-IDOR-1..3)."""

import uuid

import pytest

from core.tests.conftest import Api, error_code, open_account, purchase

pytestmark = pytest.mark.django_db

ACCOUNT_SCOPED = [
    ("get", "/accounts/{account}", None, 200),
    ("get", "/accounts/{account}/ledger", None, 200),
    ("post", "/accounts/{account}/repayments", {"amount_minor": 1}, 422),
    ("post", "/accounts/{account}/close", None, 200),
    ("post", "/cards/{card}/authorizations", {"amount_minor": 1}, 201),
    ("post", "/cards/{card}/freeze", None, 200),
    # The owner reaches the state machine: an ACTIVE card cannot be unfrozen.
    ("post", "/cards/{card}/unfreeze", None, 409),
]
ACCOUNT_SCOPED_IDS = [
    "detail",
    "ledger",
    "repayment",
    "closure",
    "purchase",
    "freeze",
    "unfreeze",
]


def _call(api: Api, method, template, body, account):
    url = template.format(account=account.account_id, card=account.card_id)
    return api.post(url, body) if method == "post" else api.get(url)


def _error_without_request_id(response):
    error = dict(response.json()["error"])
    error.pop("request_id")
    return error


@pytest.mark.parametrize(
    ("method", "template", "body", "owner_status"), ACCOUNT_SCOPED, ids=ACCOUNT_SCOPED_IDS
)
def test_anonymous_caller_is_unauthenticated(
    anonymous, account_a, method, template, body, owner_status
):
    """Fails if an endpoint answers without credentials (SEC-AUTH-4, TEST-SEC-5)."""
    response = _call(anonymous, method, template, body, account_a)

    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"
    assert response["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("method", "template", "body", "owner_status"), ACCOUNT_SCOPED, ids=ACCOUNT_SCOPED_IDS
)
@pytest.mark.parametrize("role_fixture", ["provider", "operator"])
def test_non_customer_roles_are_forbidden(
    request, account_a, role_fixture, method, template, body, owner_status
):
    """Fails if the provider or operator role can use customer endpoints (SEC-ROLE-1, TEST-SEC-6)."""
    api = request.getfixturevalue(role_fixture)

    response = _call(api, method, template, body, account_a)

    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


@pytest.mark.parametrize(
    ("method", "template", "body", "owner_status"), ACCOUNT_SCOPED, ids=ACCOUNT_SCOPED_IDS
)
def test_owner_is_served(customer_a, account_a, method, template, body, owner_status):
    """Fails if the owner cannot use the endpoints of their own account (BR-OWN-1)."""
    response = _call(customer_a, method, template, body, account_a)

    assert response.status_code == owner_status


@pytest.mark.parametrize(
    ("method", "template", "body", "owner_status"), ACCOUNT_SCOPED, ids=ACCOUNT_SCOPED_IDS
)
def test_another_customer_gets_not_found(
    customer_a, customer_b, account_a, method, template, body, owner_status
):
    """Fails if customer B can read or operate customer A's account or card (SEC-IDOR-1, TEST-SEC-4)."""
    response = _call(customer_b, method, template, body, account_a)

    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


@pytest.mark.parametrize(
    ("method", "template", "body", "owner_status"), ACCOUNT_SCOPED, ids=ACCOUNT_SCOPED_IDS
)
def test_foreign_resource_is_indistinguishable_from_a_missing_one(
    customer_b, account_a, method, template, body, owner_status
):
    """Fails if a 404 for someone else's resource differs from a 404 for a missing one (SEC-IDOR-2)."""
    missing = type(account_a)(
        account_id=str(uuid.uuid4()), deposit_id=str(uuid.uuid4()), card_id=str(uuid.uuid4())
    )

    foreign = _call(customer_b, method, template, body, account_a)
    absent = _call(customer_b, method, template, body, missing)

    assert foreign.status_code == absent.status_code == 404
    assert _error_without_request_id(foreign) == _error_without_request_id(absent)


def test_another_customer_cannot_change_the_victims_balances(customer_a, customer_b, account_a):
    """Fails if a rejected IDOR attempt still changes the victim's money, account or card (SEC-IDOR-1, TEST-SEC-4)."""
    purchase(customer_a, account_a.card_id, 5_000)

    purchase(customer_b, account_a.card_id, 1_000)
    customer_b.post(f"/accounts/{account_a.account_id}/repayments", {"amount_minor": 5_000})
    customer_b.post(f"/cards/{account_a.card_id}/freeze")
    customer_b.post(f"/accounts/{account_a.account_id}/close")

    detail = customer_a.get(f"/accounts/{account_a.account_id}").json()
    assert detail["outstanding_minor"] == 5_000
    assert detail["status"] == "ACTIVE"
    assert detail["card"]["status"] == "ACTIVE"


def test_account_ids_are_uuids(account_a):
    """Fails if account identifiers become enumerable integers (SEC-IDOR-3)."""
    assert uuid.UUID(account_a.account_id).version == 4


@pytest.mark.parametrize(
    ("caller", "status", "code"),
    [
        ("anonymous", 401, "UNAUTHENTICATED"),
        ("provider", 403, "FORBIDDEN"),
        ("operator", 403, "FORBIDDEN"),
    ],
)
def test_opening_requires_the_customer_role(request, caller, status, code):
    """Fails if anyone but a customer can request an account opening (SEC-ROLE-3)."""
    api = request.getfixturevalue(caller)

    response = api.post("/accounts", {"deposit_amount_minor": 1_000})

    assert response.status_code == status
    assert error_code(response) == code


@pytest.mark.parametrize("caller", ["anonymous", "customer_a", "operator", "provider_bearer_only"])
def test_webhook_accepts_only_a_provider_signature(request, customer_a, caller):
    """Fails if anyone can confirm a deposit without the provider's signature, bearer tokens included (SEC-HOOK-5, TEST-SEC-6)."""
    deposit_id = customer_a.post("/accounts", {"deposit_amount_minor": 1_000}).json()["deposit"][
        "id"
    ]
    api = request.getfixturevalue(caller)

    response = api.post("/webhooks/payments", {"deposit_id": deposit_id, "outcome": "succeeded"})

    assert response.status_code == 401
    assert error_code(response) == "INVALID_SIGNATURE"
    assert response["WWW-Authenticate"] == "Signature"


@pytest.mark.parametrize(
    "header",
    ["Bearer unknown-token-123456", "Bearer", "Basic dXNlcjpwYXNz", "Bearer a b"],
    ids=["unknown-token", "no-token", "wrong-scheme", "extra-parts"],
)
def test_invalid_credentials_are_unauthenticated(account_a, header):
    """Fails if a malformed or unknown Authorization header is accepted (SEC-AUTH-4, TEST-SEC-5)."""
    api = Api()
    api.client.credentials(HTTP_AUTHORIZATION=header)

    response = api.get(f"/accounts/{account_a.account_id}")

    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


def test_one_user_can_hold_several_isolated_accounts(customer_a, provider):
    """Fails if a second account of the same user shares limit, debt or card with the first (BR-OWN-1..3)."""
    first = open_account(customer_a, provider, 50_000)
    second = open_account(customer_a, provider, 20_000)

    purchase(customer_a, first.card_id, 40_000)
    first_detail = customer_a.get(f"/accounts/{first.account_id}").json()
    second_detail = customer_a.get(f"/accounts/{second.account_id}").json()

    assert first.card_id != second.card_id
    assert first_detail["outstanding_minor"] == 40_000
    assert second_detail["outstanding_minor"] == 0
    assert second_detail["available_credit_minor"] == 20_000


def test_debt_on_one_account_cannot_be_spent_from_another(customer_a, provider):
    """Fails if credit of one account can cover a purchase on another account of the same user (BR-OWN-2)."""
    first = open_account(customer_a, provider, 50_000)
    open_account(customer_a, provider, 50_000)

    response = purchase(customer_a, first.card_id, 50_001)

    assert response.status_code == 422
    assert error_code(response) == "INSUFFICIENT_CREDIT"
