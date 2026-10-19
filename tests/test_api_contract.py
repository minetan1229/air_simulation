"""API Contract Tests for SPECIFICATION §3 (Comprehensive REST API Specification).

Verifies registration, route connectivity, and robust 400 validation across all 29 endpoints.
"""

from __future__ import annotations

import json
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ==============================================================================
# SPEC §3 Route Registration Check (All 29 Endpoints)
# ==============================================================================
SPEC_ENDPOINTS = [
    # 3.1 Meta API (4)
    ("GET", "/api/v1/meta/health"),
    ("GET", "/api/v1/meta/schema"),
    ("GET", "/api/v1/meta/physics-references"),
    ("GET", "/api/v1/meta/test-fault"),
    # 3.2 Physics API (6)
    ("POST", "/api/v1/aerodynamics/calculate"),
    ("POST", "/api/v1/aerodynamics/polar"),
    ("POST", "/api/v1/environment/atmosphere"),
    ("GET", "/api/v1/environment/current"),
    ("POST", "/api/v1/wind/field"),
    ("POST", "/api/v1/stress/analysis"),
    # 3.3 Simulation API (3)
    ("POST", "/api/v1/simulation/step"),
    ("POST", "/api/v1/simulation/reset"),
    ("POST", "/api/v1/simulation/batch"),
    # 3.4 Mesh API (4)
    ("POST", "/api/v1/mesh/import"),
    ("GET", "/api/v1/mesh/models"),
    ("POST", "/api/v1/mesh/select"),
    ("GET", "/api/v1/mesh/metrics/<model_id>"),
    # 3.5 Jobs & Optimization API (6)
    ("POST", "/api/v1/optimizer/run"),
    ("GET", "/api/v1/optimizer/status/<job_id>"),
    ("POST", "/api/v1/optimizer/cancel/<job_id>"),
    ("GET", "/api/v1/optimizer/result/<job_id>"),
    ("POST", "/api/v1/jobs/investigate"),
    ("GET", "/api/v1/jobs"),
    # 3.6 Records & Data API (6)
    ("GET", "/api/v1/records/presets"),
    ("GET", "/api/v1/records/preset/<key>"),
    ("GET", "/api/v1/records/guinness"),
    ("POST", "/api/v1/data/export"),
    ("POST", "/api/v1/data/import"),
    ("GET", "/api/v1/records/errors"),
]


def test_spec_endpoints_count():
    assert len(SPEC_ENDPOINTS) == 29


def test_spec_endpoints_registered(client):
    app = client.application
    rules = [rule.rule for rule in app.url_map.iter_rules()]

    for method, path in SPEC_ENDPOINTS:
        rule_pattern = path.replace("<model_id>", "<model_id>").replace("<job_id>", "<job_id>").replace("<key>", "<key>")
        assert rule_pattern in rules, f"Expected endpoint {method} {path} to be registered"


# ==============================================================================
# §3.1 Meta API Tests
# ==============================================================================
def test_meta_health(client):
    resp = client.get("/api/v1/meta/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "gpu" in data
    assert "domains" in data


def test_meta_schema(client):
    resp = client.get("/api/v1/meta/schema")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "aircraft" in data
    assert "environment" in data
    assert "launch" in data


def test_meta_physics_references(client):
    resp = client.get("/api/v1/meta/physics-references")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "references" in data
    assert len(data["references"]) >= 5


def test_meta_test_fault(client, monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    resp = client.get("/api/v1/meta/test-fault")
    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
    assert "error_id" in data


# ==============================================================================
# §3.2 Physics API Tests
# ==============================================================================
def test_aerodynamics_calculate(client):
    resp = client.post("/api/v1/aerodynamics/calculate", json={"speed_mps": 12.0, "alpha_deg": 3.0})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "lift_n" in data
    assert "drag_n" in data

    # 400 on negative speed
    bad_resp = client.post("/api/v1/aerodynamics/calculate", json={"speed_mps": -5.0})
    assert bad_resp.status_code == 400


def test_aerodynamics_polar(client):
    resp = client.post("/api/v1/aerodynamics/polar", json={"speed_mps": 10.0, "alpha_min_deg": -5.0, "alpha_max_deg": 15.0})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "alpha_deg" in data
    assert "cl" in data
    assert "cd" in data


def test_environment_atmosphere(client):
    resp = client.post("/api/v1/environment/atmosphere", json={"altitude_m": 100.0})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "density_kgm3" in data


def test_environment_current(client):
    resp = client.get("/api/v1/environment/current")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "density_kgm3" in data


def test_wind_field(client):
    resp = client.post("/api/v1/wind/field", json={"points": [[0.0, 0.0, 10.0]]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "vectors" in data


def test_stress_analysis(client):
    resp = client.post("/api/v1/stress/analysis", json={"speed_mps": 10.0})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "dynamic_pressure_pa" in data


# ==============================================================================
# §3.3 Simulation API Tests
# ==============================================================================
def test_simulation_reset_and_step(client):
    resp_reset = client.post("/api/v1/simulation/reset")
    assert resp_reset.status_code == 200

    resp_step = client.post("/api/v1/simulation/step", json={"dt": 0.02})
    assert resp_step.status_code == 200
    data = resp_step.get_json()
    assert "position_m" in data
    assert "velocity_mps" in data


def test_simulation_batch(client):
    resp = client.post("/api/v1/simulation/batch", json={"cases": [{"launch": {"launch_speed": 10.0}}]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "results" in data


# ==============================================================================
# §3.4 Mesh API Tests
# ==============================================================================
def test_mesh_models_and_metrics(client):
    resp = client.get("/api/v1/mesh/models")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "models" in data

    # 400 for unknown model
    resp_bad = client.get("/api/v1/mesh/metrics/nonexistent_model_id")
    assert resp_bad.status_code == 400


def test_mesh_import_bad_data(client):
    resp = client.post("/api/v1/mesh/import", json={})
    assert resp.status_code == 400


def test_mesh_select_bad_id(client):
    resp = client.post("/api/v1/mesh/select", json={"model_id": "nonexistent"})
    assert resp.status_code == 400


# ==============================================================================
# §3.5 Jobs & Optimization API Tests (including merge_overlay)
# ==============================================================================
def test_jobs_list(client):
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "jobs" in data


def test_optimizer_run_merge_overlay_rejections(client):
    # Overlay is not a JSON object -> 400 Bad Request
    resp = client.post(
        "/api/v1/optimizer/run",
        json={"objectives": ["max_flight_time"], "aircraft": "invalid_string"},
    )
    assert resp.status_code == 400
    assert "'aircraft' must be a JSON object" in resp.get_json()["error"]

    resp2 = client.post(
        "/api/v1/optimizer/run",
        json={"objectives": ["max_flight_time"], "environment": 12345},
    )
    assert resp2.status_code == 400
    assert "'environment' must be a JSON object" in resp2.get_json()["error"]


def test_investigate_alias_and_overlay_rejections(client):
    # Verify alias /api/v1/jobs/investigate exists and rejects invalid overlay
    resp = client.post(
        "/api/v1/jobs/investigate",
        json={"aircraft": "not_an_object"},
    )
    assert resp.status_code == 400
    assert "'aircraft' must be a JSON object" in resp.get_json()["error"]


def test_optimizer_job_routes_not_found(client):
    assert client.get("/api/v1/optimizer/status/fake_job").status_code == 400
    assert client.post("/api/v1/optimizer/cancel/fake_job").status_code == 400
    assert client.get("/api/v1/optimizer/result/fake_job").status_code == 400


# ==============================================================================
# §3.6 Records & Data API Tests
# ==============================================================================
def test_records_presets(client):
    resp = client.get("/api/v1/records/presets")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "presets" in data
    assert data["count"] > 0


def test_records_preset_alias(client):
    resp = client.get("/api/v1/records/preset/toda_29_2s")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["key"] == "toda_29_2s"
    assert "Toda" in data["name"]


def test_records_guinness_benchmark_and_verify(client):
    # Default returns benchmark JSON immediately without computing 60s matrix
    resp = client.get("/api/v1/records/guinness")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["key"] == "toda_29_2s"
    assert "achievement" in data


def test_data_export_import(client):
    resp = client.post("/api/v1/data/export", json={"kind": "config", "format": "json"})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "payload" in payload

    # Import exported config bundle
    bundle_data = json.loads(payload["payload"])
    resp_imp = client.post("/api/v1/data/import", json={"bundle": bundle_data})
    assert resp_imp.status_code == 200
    assert resp_imp.get_json()["ok"] is True


def test_records_errors_alias(client):
    resp = client.get("/api/v1/records/errors")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "errors" in data
    assert "statistics" in data
    # Check that public response sanitized absolute paths in statistics
    log_files = data["statistics"]["log_files"]
    assert "/" not in log_files["text"] and "\\" not in log_files["text"]


def test_mesh_ssrf_multihop_and_cloud_metadata():
    from physics import mesh_domain

    # Blocked hostnames (GCP / AWS / Azure metadata)
    with pytest.raises(ValueError, match="forbidden|blocked"):
        mesh_domain.validate_url_safe("http://metadata.google.internal/computeMetadata/v1/")

    with pytest.raises(ValueError, match="forbidden|blocked|private"):
        mesh_domain.validate_url_safe("http://169.254.169.254/latest/meta-data/")

    import urllib.request

    # Safe multi-hop redirect handler
    handler = mesh_domain._SafeRedirectHandler(max_hops=5)

    # Rejection of unsafe hop
    with pytest.raises(ValueError, match="forbidden|blocked|private"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/evil.glb")

    # Exceeding max hops
    dummy_req = urllib.request.Request("http://example.com/init.glb")
    strict_handler = mesh_domain._SafeRedirectHandler(max_hops=2)
    with pytest.raises(ValueError, match="too many redirects"):
        for i in range(3):
            strict_handler.redirect_request(dummy_req, None, 302, "Found", {}, f"http://example.com/hop{i}.glb")

