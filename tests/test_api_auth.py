"""Tests for API authentication and authorization middleware."""

from __future__ import annotations

import os
import pytest
from app import create_app


@pytest.fixture
def app_with_api_key(monkeypatch):
    test_key = "secure-test-token-xyz-123456"
    monkeypatch.setenv("API_KEY", test_key)
    app = create_app()
    app.config["TESTING"] = True
    return app, test_key


def test_auth_optional_when_api_key_not_configured(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        # Protected route should allow access when no API_KEY configured in environment
        resp = client.post("/api/v1/simulation/reset")
        assert resp.status_code == 200


def test_auth_blocks_unauthorized_requests(app_with_api_key):
    app, _ = app_with_api_key
    with app.test_client() as client:
        resp = client.post("/api/v1/simulation/reset")
        assert resp.status_code == 401
        data = resp.get_json()
        assert "Unauthorized" in data["error"]
        assert data["status"] == 401


def test_auth_rejects_query_parameter_api_key(app_with_api_key):
    """Query parameter api_key is forbidden for security to avoid URL leakage in logs/proxies."""
    app, key = app_with_api_key
    with app.test_client() as client:
        resp = client.post(f"/api/v1/simulation/reset?api_key={key}")
        assert resp.status_code == 401
        data = resp.get_json()
        assert "Unauthorized" in data["error"]


def test_auth_rejects_invalid_tokens(app_with_api_key):
    app, _ = app_with_api_key
    with app.test_client() as client:
        # Invalid Bearer token
        resp1 = client.post(
            "/api/v1/simulation/reset",
            headers={"Authorization": "Bearer wrong-token-value"},
        )
        assert resp1.status_code == 401

        # Invalid X-API-Key
        resp2 = client.post(
            "/api/v1/simulation/reset",
            headers={"X-API-Key": "wrong-token-value"},
        )
        assert resp2.status_code == 401


def test_auth_accepts_valid_bearer_token(app_with_api_key):
    app, key = app_with_api_key
    with app.test_client() as client:
        resp = client.post(
            "/api/v1/simulation/reset",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert resp.status_code == 200


def test_auth_accepts_valid_x_api_key(app_with_api_key):
    app, key = app_with_api_key
    with app.test_client() as client:
        resp = client.post(
            "/api/v1/simulation/reset",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200


def test_public_routes_exempt_from_auth(app_with_api_key):
    app, _ = app_with_api_key
    with app.test_client() as client:
        # UI & health
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz/ready").status_code == 200

        # Meta & schemas
        assert client.get("/api/v1/meta/health").status_code == 200
        assert client.get("/api/v1/meta/schema").status_code == 200
        assert client.get("/api/v1/meta/physics-references").status_code == 200


def test_system_gpu_exempt_but_force_gpu_render_protected(app_with_api_key):
    """Ensure read-only GPU diagnostics is public, but force-gpu-render mutation requires auth."""
    app, key = app_with_api_key
    with app.test_client() as client:
        # /api/v1/system/gpu is exempt
        resp_gpu = client.get("/api/v1/system/gpu")
        assert resp_gpu.status_code == 200

        # /api/v1/system/force-gpu-render is protected
        resp_force_unauth = client.post("/api/v1/system/force-gpu-render", json={"force": True})
        assert resp_force_unauth.status_code == 401

        # /api/v1/system/force-gpu-render succeeds with valid auth
        resp_force_auth = client.post(
            "/api/v1/system/force-gpu-render",
            json={"force": True},
            headers={"X-API-Key": key},
        )
        assert resp_force_auth.status_code == 200
