"""Unit and integration tests for record-airframe domain and REST endpoints."""

import pytest
from app import app
from physics import records_domain


def test_list_presets_returns_all_catalogue_entries():
    presets = records_domain.list_presets()
    assert len(presets) >= 10
    keys = [p["key"] for p in presets]
    assert "unite_31s" in keys
    assert "toda_29_2s" in keys
    assert "blackburn_27_6s" in keys
    assert "ayoob_69_1m" in keys
    assert "firefly5_98m" in keys


def test_preset_payload_structure():
    payload = records_domain.preset_payload("ayoob_69_1m")
    assert payload["key"] == "ayoob_69_1m"
    assert "throw" in payload
    assert payload["throw"]["release_speed_mps"] > 20.0
    assert payload["throw"]["launch_angle_deg"] > 10.0
    assert "aircraft" in payload
    assert "environment" in payload
    assert "throw_rules" in payload


def test_apply_preset_merges_configs():
    merged = records_domain.apply_preset("toda_29_2s")
    aircraft = merged["aircraft"]
    launch = merged["launch"]
    env = merged["environment"]

    assert aircraft["mass_kg"] > 0
    assert launch["launch_angle_deg"] > 60.0  # vertical loft throw
    assert env["temperature_K"] > 250.0


def test_api_records_presets_get():
    client = app.test_client()
    resp = client.get("/api/v1/records/presets")
    assert resp.status_code == 200
    data = resp.json
    assert "presets" in data
    assert data["count"] >= 10


def test_api_records_preset_by_key():
    client = app.test_client()
    resp = client.get("/api/v1/records/presets/blackburn_27_6s")
    assert resp.status_code == 200
    data = resp.json
    assert data["key"] == "blackburn_27_6s"
    assert data["throw"]["throw_style"] == "record_loft"


def test_api_records_apply():
    client = app.test_client()
    resp = client.post("/api/v1/records/apply", json={"key": "ruble_88_3m"})
    assert resp.status_code == 200
    data = resp.json
    assert data["ok"] is True
    assert "aircraft" in data
    assert "launch" in data


def test_api_records_apply_throw_only():
    client = app.test_client()
    # First set a distinct aircraft mass
    resp1 = client.post("/api/v1/records/apply", json={"key": "ayoob_69_1m"})
    orig_mass = resp1.json["aircraft"]["mass_kg"]

    # Apply toda throw only
    resp2 = client.post("/api/v1/records/apply", json={"key": "toda_29_2s", "throw_only": True})
    assert resp2.status_code == 200
    # Mass should remain orig_mass
    assert resp2.json["aircraft"]["mass_kg"] == orig_mass
    # Launch angle should be Toda's loft pitch
    assert resp2.json["launch"]["launch_angle_deg"] > 70.0


def test_api_throw_techniques():
    client = app.test_client()
    resp = client.get("/api/v1/records/throw_techniques")
    assert resp.status_code == 200
    data = resp.json
    assert "throw_rules" in data
    assert len(data["throw_rules"]) > 0
