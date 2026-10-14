"""Unit tests for the Auto-Investigate domain."""

import pytest
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, as_dict
from physics.auto_investigate import (
    ADJUSTABLE_VARIABLES,
    diagnose_flight,
    generate_advice,
    investigate_airplane,
    run_focused_tuning,
    run_sensitivity_scan,
)


def test_adjustable_variables_freeze_mass_and_sheet():
    """Verify that immutable parameters are strictly excluded from adjustable variables."""
    forbidden = {
        "mass_kg",
        "paper_gsm",
        "paper_stiffness",
        "sheet_area_m2",
        "wing_area_m2",
        "wingspan_m",
        "taper_ratio",
        "aspect_ratio",
        "shape_flag",
    }
    names = {v.name for v in ADJUSTABLE_VARIABLES}
    intersection = forbidden & names
    assert not intersection, f"Forbidden immutable variables found in adjustable set: {intersection}"


def test_diagnose_flight_nose_dive():
    """Verify diagnosis detects severe nose dive with negative elevon and aft zero trim."""
    air = AircraftConfig(elevon_deg=-5.0, cL0=0.02, cm0=-0.05)
    lau = LaunchConfig(launch_speed=12.0, launch_angle_deg=-2.0)
    env = EnvironmentConfig()

    result = investigate_airplane(
        air, env, lau, target_metric="distance", skip_scan=True, skip_tuning=True
    )
    diag = result.diagnosis
    assert diag["issue_code"] in ("NOSE_DIVE", "LOW_GLIDE", "STALL_DIVE")
    assert "headline_ja" in diag
    assert len(result.advice_list) > 0


def test_focused_tuning_preserves_immutable_aircraft_properties():
    """Verify that multi-variable tuning only alters adjustable parameters, keeping mass etc. untouched."""
    original_mass = 0.0055
    original_gsm = 120.0
    original_span = 0.22

    air = AircraftConfig(
        mass_kg=original_mass,
        paper_gsm=original_gsm,
        wingspan_m=original_span,
        elevon_deg=0.5,
        dihedral_deg=3.0,
    )
    env = EnvironmentConfig()
    lau = LaunchConfig(launch_speed=12.0, launch_angle_deg=12.0)

    rec_air, rec_lau, rec_summary, rec_samples = run_focused_tuning(
        air, env, lau, target_metric="distance", max_evaluations=8
    )

    # Strictly immutable
    assert rec_air.mass_kg == pytest.approx(original_mass)
    assert rec_air.paper_gsm == pytest.approx(original_gsm)
    assert rec_air.wingspan_m == pytest.approx(original_span)

    # Tuned outputs exist and simulation succeeded
    assert len(rec_samples) > 0
    assert "flight_time_s" in rec_summary


def test_sensitivity_scan_ranks_variables():
    """Verify sensitivity scan computes valid impact percentages."""
    air = AircraftConfig(elevon_deg=2.0, dihedral_deg=5.0)
    env = EnvironmentConfig()
    lau = LaunchConfig(launch_speed=12.0, launch_angle_deg=10.0)

    sens = run_sensitivity_scan(air, env, lau, target_metric="distance", sample_steps=2)
    assert len(sens) == len(ADJUSTABLE_VARIABLES)
    # Ranked descending
    for i in range(len(sens) - 1):
        assert sens[i].impact_score_pct >= sens[i + 1].impact_score_pct
    # Top variable has valid curve points
    top = sens[0]
    assert len(top.curve) >= 2


def test_full_investigate_pipeline():
    """Verify complete investigation output schema."""
    air = AircraftConfig()
    env = EnvironmentConfig()
    lau = LaunchConfig()

    res = investigate_airplane(
        air, env, lau, target_metric="distance", sample_steps=2, max_tune_evaluations=5
    )
    assert res.target_metric == "distance"
    assert "metrics" in res.baseline
    assert "metrics" in res.recommended
    assert "advice_list" in as_dict(res)
    assert "summary" in as_dict(res)
    assert res.summary["primary_issue"] != ""


def test_investigate_api_endpoints():
    """Verify Flask REST endpoints for investigation."""
    from app import app

    client = app.test_client()

    # 1. Trigger run
    resp = client.post(
        "/api/v1/investigate/run",
        json={"target_metric": "distance"},
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert "job_id" in data
    job_id = data["job_id"]

    # 2. Check status
    st_resp = client.get(f"/api/v1/investigate/status/{job_id}")
    assert st_resp.status_code == 200
    st_data = st_resp.get_json()
    assert st_data["job_id"] == job_id

    # 3. Cancel job
    cancel_resp = client.post(f"/api/v1/investigate/cancel/{job_id}")
    assert cancel_resp.status_code == 200
    cancel_data = cancel_resp.get_json()
    assert cancel_data["status"] in ("cancelled", "running", "completed")

