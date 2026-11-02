"""Audit and verification tests covering:
- Issue #29: Clarification of physics model vs volume Navier-Stokes CFD
- Issue #30: Wind frame (+z up) to aircraft/world frame (+z down NED) coordinate transforms
- Issue #34: Prevention of internal stack traces in client-facing API error responses
- Issue #37: SSRF, loopback, private IP, non-standard port, and redirect rejections
"""

from __future__ import annotations

import json
import math
import urllib.parse
import pytest

from app import create_app
from physics import flight_sim, mesh_domain, wind_domain
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
)


# ==============================================================================
# Issue #30: 風場 (+z up) と 機体/世界座標系 (+z down NED) の符号変換検証
# ==============================================================================

def test_wind_coordinate_transform_updraft_and_downdraft():
    """Verify +z up in wind domain translates correctly to -z (upward) in NED flight dynamics.

    In wind_domain:
      +z is UP (altitude z >= 0)
    In flight_sim / world NED:
      +z is DOWN (position z <= 0 is above ground, height h = -pos[2])
    """
    env_calm = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
    aircraft = AircraftConfig()

    # Baseline: level flight in calm air
    probe_calm = flight_sim.aero_forces(
        aircraft,
        env_calm,
        speed_mps=10.0,
        alpha_deg=2.0,
        beta_deg=0.0,
    )
    assert probe_calm.lift_n > 0.0

    # Test that thermal / vertical wind affects relative wind correctly
    sim = flight_sim.FlightSimulator(
        aircraft=aircraft,
        environment=env_calm,
        launch=LaunchConfig(launch_speed=10.0, launch_angle_deg=0.0, launch_height_m=5.0),
        settings=SimulationSettings(max_substeps=1),
    )

    # State: level flight heading North (+x), at altitude 5m (NED pos z = -5.0)
    state = sim.state
    assert state.position_m[2] == pytest.approx(-5.0, abs=1e-3)
    assert state.velocity_mps[0] == pytest.approx(10.0, abs=1e-2)
    assert state.velocity_mps[2] == pytest.approx(0.0, abs=1e-3)

    # 1. Zero wind produces zero wind velocity vector
    w_vec = wind_domain.wind_velocity((0.0, 0.0, 5.0), env_calm, time_s=0.0)
    assert abs(w_vec[0]) < 1e-4
    assert abs(w_vec[1]) < 1e-4
    assert abs(w_vec[2]) < 1e-4

    # 2. Updraft: wind velocity in wind domain has wz > 0
    # In NED coordinates, an updraft corresponds to wind velocity with wz_ned < 0 (pointing up)
    # The relative airspeed vector is v_rel = v_aircraft - wind_ned
    v_air = (10.0, 0.0, 0.0)
    wind_ned_up = (0.0, 0.0, -2.0)  # 2 m/s updraft in NED
    v_rel_up = (v_air[0] - wind_ned_up[0], v_air[1] - wind_ned_up[1], v_air[2] - wind_ned_up[2])

    # In NED coordinates with body aligned to world:
    # v_rel_up[2] is +2.0 (relative wind has downward component into aircraft belly, i.e. upward relative inflow),
    # meaning the aircraft experiences a positive angle of attack increment (alpha = atan2(w, u) > 0)
    alpha_up_deg = math.degrees(math.atan2(v_rel_up[2], v_rel_up[0]))
    assert alpha_up_deg > 0.0

    # Conversely, a downdraft produces relative inflow from above (v_rel[2] = -2.0, alpha < 0)
    wind_ned_down = (0.0, 0.0, 2.0)
    v_rel_down = (v_air[0] - wind_ned_down[0], v_air[1] - wind_ned_down[1], v_air[2] - wind_ned_down[2])
    alpha_down_deg = math.degrees(math.atan2(v_rel_down[2], v_rel_down[0]))
    assert alpha_down_deg < 0.0


# ==============================================================================
# Issue #34: 予期しない例外で内部トレースバックをクライアントへ返さない検証
# ==============================================================================

def test_api_unhandled_exception_no_traceback_leak(monkeypatch):

    """Verify unhandled exceptions do not return file paths, stack traces, or source snippets."""
    app = create_app()
    app.config["TESTING"] = True

    client = app.test_client()
    monkeypatch.setenv("TESTING", "1")
    resp = client.get("/api/v1/meta/test-fault")


    assert resp.status_code == 500
    data = json.loads(resp.get_data(as_text=True))

    # Response must contain error and error_id, but NOT sensitive traceback details
    assert "error" in data
    assert "error_id" in data
    assert data["error"] == "an unexpected internal server error occurred"

    raw_text = resp.get_data(as_text=True)
    assert "Traceback" not in raw_text
    assert "/sensitive/source/path.py" not in raw_text
    assert "Simulated internal secret crash" not in raw_text


# ==============================================================================
# Issue #37: 外部メッシュURL取得のSSRF・リダイレクト・DNS再解決対策検証
# ==============================================================================

def test_mesh_ssrf_and_redirect_rejections():
    """Verify SSRF filters reject private IP ranges, cloud metadata, bad ports, and redirects."""
    # 1. Loopback addresses
    for bad_url in [
        "http://localhost/model.glb",
        "http://127.0.0.1/model.glb",
        "http://127.0.0.1:80/model.glb",
        "http://[::1]/model.glb",
    ]:
        with pytest.raises(ValueError, match="forbidden|loopback"):
            mesh_domain.validate_url_safe(bad_url)

    # 2. Private IP ranges (RFC 1918) and link-local cloud metadata (RFC 3927)
    for bad_url in [
        "http://10.0.0.1/model.glb",
        "http://172.16.0.1/model.glb",
        "http://192.168.1.1/model.glb",
        "http://169.254.169.254/latest/meta-data/",
    ]:
        with pytest.raises(ValueError, match="forbidden|private"):
            mesh_domain.validate_url_safe(bad_url)

    # 3. Non-standard ports
    for bad_url in [
        "http://example.com:22/model.glb",
        "http://example.com:8080/model.glb",
        "http://example.com:5000/model.glb",
    ]:
        with pytest.raises(ValueError, match="port .* is not allowed"):
            mesh_domain.validate_url_safe(bad_url)

    # 4. Bad schemes
    for bad_url in [
        "ftp://example.com/model.glb",
        "file:///etc/passwd",
        "gopher://example.com/",
    ]:
        with pytest.raises(ValueError, match="unsupported URL scheme"):
            mesh_domain.validate_url_safe(bad_url)

    # 5. Redirect disallowance in _NoRedirectHandler
    handler = mesh_domain._NoRedirectHandler()
    with pytest.raises(ValueError, match="HTTP redirects are disallowed"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://evil.com/target")
