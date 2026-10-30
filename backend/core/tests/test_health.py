from unittest import mock

import pytest
from django.db import OperationalError


@pytest.mark.django_db
def test_health_returns_ok_when_database_is_reachable(client):
    """Fails if /health stops reporting a reachable PostgreSQL as healthy (OBS-4)."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_503_when_database_is_unreachable(client):
    """Fails if /health reports healthy while the database connection is down (OBS-4)."""
    with mock.patch(
        "core.observability.health.connection.cursor",
        side_effect=OperationalError("connection refused"),
    ):
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_health_rejects_non_get_methods(client):
    """Fails if /health starts accepting writes instead of being a read-only probe."""
    response = client.post("/health")

    assert response.status_code == 405
