"""Authentication middleware behaviour.

The API key is optional by default (the local browser UI does not send one),
so these tests pin down exactly *what* is protected when an operator does
configure ``API_KEY``.
"""

from __future__ import annotations

import pytest

from app import create_app


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret-test-key")
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def test_protected_endpoint_requires_a_key(client):
    response = client.get("/api/v1/environment/current")
    assert response.status_code == 401
    assert "error_id" in response.get_json()


def test_bearer_token_is_accepted(client):
    response = client.get(
        "/api/v1/environment/current", headers={"Authorization": "Bearer s3cret-test-key"}
    )
    assert response.status_code == 200


def test_api_key_header_is_accepted(client):
    response = client.get("/api/v1/environment/current", headers={"X-API-Key": "s3cret-test-key"})
    assert response.status_code == 200


def test_a_wrong_key_is_rejected(client):
    response = client.get(
        "/api/v1/environment/current", headers={"Authorization": "Bearer wrong-key"}
    )
    assert response.status_code == 401


def test_the_key_is_not_accepted_from_the_query_string(client):
    """A credential in the query string leaks into logs, history and Referer."""
    response = client.get("/api/v1/environment/current?api_key=s3cret-test-key")
    assert response.status_code == 401


def test_read_only_diagnostics_stay_public(client):
    for path in (
        "/api/v1/meta/health",
        "/api/v1/meta/schema",
        "/api/v1/meta/physics-references",
        "/api/v1/system/gpu",
    ):
        response = client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"


def test_state_changing_system_endpoint_stays_protected(client):
    response = client.post("/api/v1/system/force-gpu-render", json={})
    assert response.status_code == 401
