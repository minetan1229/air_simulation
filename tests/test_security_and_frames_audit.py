"""Audit and verification tests covering:
- Issue #29: Clarification of physics model vs volume Navier-Stokes CFD
- Issue #30: Wind frame (+z up) to aircraft/world frame (+z down NED) coordinate transforms
- Issue #34: Prevention of internal stack traces in client-facing API error responses
- Issue #37: SSRF, loopback, private IP, non-standard port, and redirect rejections
"""

from __future__ import annotations

import json
import urllib.parse
from unittest import mock

import numpy as np
import pytest

from app import create_app
from physics import flight_sim, mesh_domain, wind_domain
from physics.config_schema import EnvironmentConfig


# ==============================================================================
# Issue #30: 風場 (+z up) と 機体/世界座標系 (+z down NED) の符号変換検証
# ==============================================================================

def test_wind_coordinate_transform_updraft_and_downdraft(monkeypatch):
    """Verify the +z-up wind domain becomes a +z-down (NED) vector in flight_sim.

    In wind_domain:
      +z is UP (the ground plane sits at ``z = env.ground_level_m``)
    In flight_sim / world NED:
      +z is DOWN (the ground plane sits at ``z = -env.ground_level_m``)

    The hand-off therefore has to negate the vertical component
    (``w_NED = -w_wind``) and pass the *absolute* height above the world
    origin, not the height above ground.  This test drives the production
    conversion in :func:`physics.flight_sim.wind_vector`; the previous
    revision only evaluated arithmetic on hard-coded literals and therefore
    proved nothing about the implementation.
    """
    env = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.0, thermal_strength=0.0)

    observed: dict = {}

    def fake_wind_velocity(position, env, time_s=0.0):
        observed["position"] = np.asarray(position, dtype=float)
        observed["time_s"] = time_s
        return np.array([1.5, -2.5, 3.25])

    monkeypatch.setattr(wind_domain, "wind_velocity", fake_wind_velocity)

    out = flight_sim.wind_vector((10.0, 20.0, -30.0), env, time_s=2.5)

    # Horizontal components pass through unchanged.
    assert out[0] == pytest.approx(1.5)
    assert out[1] == pytest.approx(-2.5)
    # The vertical component is negated into the z-down world frame.
    assert out[2] == pytest.approx(-3.25)
    # The query point is converted into the wind domain's z-up axis.  The
    # absolute height (30 m) is passed, *not* the AGL height, so that the
    # domain can subtract ``ground_level_m`` exactly once.
    assert float(observed["position"][2]) == pytest.approx(30.0)
    assert float(observed["position"][0]) == pytest.approx(10.0)
    assert float(observed["position"][1]) == pytest.approx(20.0)
    assert observed["time_s"] == pytest.approx(2.5)


def test_wind_coordinate_transform_uses_absolute_height_above_origin():
    """The z-up query height must not have ``ground_level_m`` subtracted twice.

    ``flight_sim`` reports the AGL height as ``-z - ground_level_m`` and places
    the ground plane at ``z = -ground_level_m``.  ``wind_domain`` places its
    ground plane at ``z = +ground_level_m`` and derives the AGL height itself.
    Handing the domain an AGL height would shift the boundary layer, the
    ground clamp and the thermal plume by the airfield elevation.
    """
    env = EnvironmentConfig(
        wind_speed=5.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        ground_level_m=1000.0,
    )

    seen: dict = {}

    def fake_wind_velocity(position, env, time_s=0.0):
        seen["z_up"] = float(np.asarray(position, dtype=float)[2])
        return np.array([0.0, 0.0, 1.0])

    with mock.patch.object(wind_domain, "wind_velocity", fake_wind_velocity):
        flight_sim.wind_vector((0.0, 0.0, -30.0), env, time_s=0.0)

    # -z_ned = 30 m above the world origin.  The AGL height would be
    # 30 - 1000 = -970 m, which is not what the domain expects.
    assert seen["z_up"] == pytest.approx(30.0)


def test_wind_vector_is_inert_in_calm_air():
    """A calm atmosphere must produce the exact zero vector, with no upstream call."""
    env_calm = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)

    out = flight_sim.wind_vector((0.0, 0.0, -5.0), env_calm, time_s=0.0)

    assert out.shape == (3,)
    assert out[0] == 0.0
    assert out[1] == 0.0
    assert out[2] == 0.0


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
