"""Structured JSON logs (docs/audit.md §2)."""

import io
import json
import logging
import uuid

import pytest

from core.observability import context
from core.observability.json_logging import JsonFormatter
from core.tests.conftest import TOKENS, WEBHOOK_SECRET, purchase


@pytest.fixture
def log_lines():
    """Capture every log record of a test as the JSON lines the service would print."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.INFO)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    root.removeHandler(handler)
    root.setLevel(previous_level)


def test_formatter_emits_one_json_object_with_the_standard_fields():
    """Fails if a log line is not a JSON object with timestamp, level, logger, message and request id (OBS-1)."""
    record = logging.makeLogRecord(
        {
            "name": "core.test",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "hello",
            "status": 201,
        }
    )
    request_id = uuid.uuid4()
    token = context.start(request_id)
    try:
        line = JsonFormatter().format(record)
    finally:
        context.end(token)

    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "core.test"
    assert payload["message"] == "hello"
    assert payload["request_id"] == str(request_id)
    assert payload["status"] == 201
    assert "timestamp" in payload


def test_request_id_is_null_outside_a_request():
    """Fails if a log line outside HTTP invents a request id (OBS-2)."""
    record = logging.makeLogRecord({"msg": "background"})

    assert json.loads(JsonFormatter().format(record))["request_id"] is None


@pytest.mark.django_db
def test_every_request_logs_a_completed_line_with_its_request_id(customer_a, account_a, log_lines):
    """Fails if a request is not logged as JSON with method, path, status, duration and its request id (OBS-1, OBS-2)."""
    response = purchase(customer_a, account_a.card_id, 1_000)

    completed = [line for line in log_lines() if line["message"] == "request.completed"]
    assert completed[-1]["request_id"] == response["X-Request-ID"]
    assert completed[-1]["method"] == "POST"
    assert completed[-1]["path"] == f"/cards/{account_a.card_id}/authorizations"
    assert completed[-1]["status"] == 201
    assert completed[-1]["duration_ms"] >= 0


@pytest.mark.django_db
def test_logs_never_contain_tokens_or_signatures(customer_a, provider, account_a, log_lines):
    """Fails if a bearer token, the webhook secret or a signature reaches the logs (OBS-3, SEC-CARD-3)."""
    purchase(customer_a, account_a.card_id, 1_000)
    customer_a.get(f"/accounts/{account_a.account_id}")

    output = json.dumps(log_lines())

    assert TOKENS["customer_a"][2] not in output
    assert TOKENS["provider"][2] not in output
    assert WEBHOOK_SECRET not in output
    assert "v1=" not in output
