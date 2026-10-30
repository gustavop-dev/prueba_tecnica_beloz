import pytest

from core.tests.conftest import error_code

pytestmark = pytest.mark.django_db

INVALID_AMOUNTS = [
    ("0", "zero"),
    ("-100", "negative"),
    ("10.0", "float-integral"),
    ("10.5", "float-fraction"),
    ('"1000"', "string"),
    ("true", "boolean"),
    ("null", "null"),
    ("[100]", "list"),
]


@pytest.mark.parametrize(
    ("raw", "case"), INVALID_AMOUNTS, ids=[case for _, case in INVALID_AMOUNTS]
)
def test_opening_rejects_amounts_that_are_not_positive_integer_cents(customer_a, raw, case):
    """Fails if a deposit amount other than a positive JSON integer is accepted (BR-MONEY-2..5)."""
    response = customer_a.post_raw("/accounts", f'{{"deposit_amount_minor": {raw}}}')

    assert response.status_code == 400
    assert error_code(response) == "VALIDATION_ERROR"
    assert "deposit_amount_minor" in response.json()["error"]["details"]


@pytest.mark.parametrize(
    ("raw", "case"), INVALID_AMOUNTS, ids=[case for _, case in INVALID_AMOUNTS]
)
def test_purchase_rejects_amounts_that_are_not_positive_integer_cents(
    customer_a, account_a, raw, case
):
    """Fails if a purchase amount other than a positive JSON integer is accepted (BR-MONEY-3..5)."""
    response = customer_a.post_raw(
        f"/cards/{account_a.card_id}/authorizations", f'{{"amount_minor": {raw}}}'
    )

    assert response.status_code == 400
    assert "amount_minor" in response.json()["error"]["details"]


@pytest.mark.parametrize(
    ("raw", "case"), INVALID_AMOUNTS, ids=[case for _, case in INVALID_AMOUNTS]
)
def test_repayment_rejects_amounts_that_are_not_positive_integer_cents(
    customer_a, account_a, raw, case
):
    """Fails if a repayment amount other than a positive JSON integer is accepted (BR-MONEY-3..5)."""
    response = customer_a.post_raw(
        f"/accounts/{account_a.account_id}/repayments", f'{{"amount_minor": {raw}}}'
    )

    assert response.status_code == 400
    assert "amount_minor" in response.json()["error"]["details"]


def test_amounts_have_no_business_maximum(customer_a, provider):
    """Fails if a large but valid amount is capped by a business maximum (BR-MONEY-6)."""
    response = customer_a.post("/accounts", {"deposit_amount_minor": 10**15})

    assert response.status_code == 201
    assert response.json()["deposit"]["amount_minor"] == 10**15


def test_every_amount_in_responses_is_usd_integer_cents(customer_a, account_a):
    """Fails if a response reports money in another currency or as a non-integer (BR-MONEY-1, BR-MONEY-2)."""
    body = customer_a.get(f"/accounts/{account_a.account_id}").json()

    assert body["currency"] == "USD"
    assert body["deposit"]["currency"] == "USD"
    assert type(body["credit_limit_minor"]) is int
    assert type(body["outstanding_minor"]) is int
    assert type(body["available_credit_minor"]) is int


@pytest.mark.parametrize(
    ("url_template", "body"),
    [
        ("/accounts", {"deposit_amount_minor": 100, "currency": "EUR"}),
        ("/cards/{card}/authorizations", {"amount_minor": 100, "merchant": "x"}),
        ("/accounts/{account}/repayments", {"amount_minor": 100, "note": "x"}),
        ("/accounts/{account}/close", {"force": True}),
    ],
    ids=["opening", "purchase", "repayment", "closure"],
)
def test_unknown_fields_are_rejected(customer_a, account_a, url_template, body):
    """Fails if an unknown body field is silently ignored (SEC-INPUT-3)."""
    url = url_template.format(card=account_a.card_id, account=account_a.account_id)

    response = customer_a.post(url, body)

    assert response.status_code == 400
    assert error_code(response) == "VALIDATION_ERROR"


def test_malformed_json_is_a_validation_error(customer_a):
    """Fails if a body that is not valid JSON is not rejected with the envelope (api.md §3)."""
    response = customer_a.post_raw("/accounts", "{not json")

    assert response.status_code == 400
    assert error_code(response) == "VALIDATION_ERROR"
