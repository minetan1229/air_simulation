"""Aerodynamics, wind field, and structural stress routes."""

from __future__ import annotations

import gzip
import json
from typing import Any, Dict
from flask import Blueprint, Response, jsonify, request

from physics.config_schema import AircraftConfig, EnvironmentConfig, as_dict
from ..core import (
    MAX_DENSITY,
    MAX_MACH,
    MAX_SPEED_MPS,
    SESSION,
    BadRequest,
    as_float,
    as_int,
    as_vec3,
    body,
    build,
    domain,
    merge_environment,
)

physics_bp = Blueprint("physics", __name__)


# ---------------------------------------------------------------------------
# Aerodynamics
# ---------------------------------------------------------------------------
@physics_bp.post("/api/v1/aerodynamics/calculate")
def aerodynamics_calculate():
    aero = domain("aero_domain")
    payload = body()
    cfg = build(AircraftConfig, payload.get("aircraft"), SESSION.aircraft)
    env = merge_environment(payload.get("environment"), SESSION.environment)

    speed = as_float(payload.get("speed_mps", 10.0), "speed_mps")
    if speed < 0.0:
        raise BadRequest("field 'speed_mps' must be >= 0")
    if speed > MAX_SPEED_MPS:
        raise BadRequest(f"field 'speed_mps' must be <= {MAX_SPEED_MPS:g} m/s")
    alpha = as_float(payload.get("alpha_deg", 0.0), "alpha_deg")
    beta = as_float(payload.get("beta_deg", 0.0), "beta_deg")
    mach = as_float(payload.get("mach", 0.0), "mach")
    if not (0.0 <= mach <= MAX_MACH):
        raise BadRequest(f"field 'mach' must lie within [0, {MAX_MACH:g}]")
    reynolds = payload.get("reynolds")
    reynolds = None if reynolds is None else as_float(reynolds, "reynolds")
    ground_height = as_float(payload.get("ground_height_m", 1.0), "ground_height_m")
    angular_rate = as_vec3(payload.get("angular_rate", (0.0, 0.0, 0.0)), "angular_rate")

    result = aero.evaluate_aero(
        cfg=cfg,
        env=env,
        speed_mps=speed,
        alpha_deg=alpha,
        beta_deg=beta,
        mach=mach,
        reynolds=reynolds,
        ground_height_m=ground_height,
        angular_rate=tuple(angular_rate),
    )
    payload_out = as_dict(result)
    payload_out["backend"] = "numpy"
    payload_out["aircraft"] = as_dict(cfg)
    return jsonify(payload_out)


@physics_bp.post("/api/v1/aerodynamics/polar")
def aerodynamics_polar():
    aero = domain("aero_domain")
    payload = body()
    cfg = build(AircraftConfig, payload.get("aircraft"), SESSION.aircraft)

    alpha_min = as_float(payload.get("alpha_min_deg", -8.0), "alpha_min_deg")
    alpha_max = as_float(payload.get("alpha_max_deg", 20.0), "alpha_max_deg")
    if alpha_max <= alpha_min:
        raise BadRequest("'alpha_max_deg' must be greater than 'alpha_min_deg'")
    points = as_int(payload.get("points", 57), "points")
    if points < 2 or points > 2001:
        raise BadRequest("'points' must be between 2 and 2001")
    reynolds = as_float(payload.get("reynolds", 1.0e4), "reynolds")

    result = aero.drag_polar(
        cfg=cfg,
        alpha_min_deg=alpha_min,
        alpha_max_deg=alpha_max,
        points=points,
        reynolds=reynolds,
    )
    out = dict(result)
    out["backend"] = "numpy"
    return jsonify(out)


# ---------------------------------------------------------------------------
# Atmosphere
# ---------------------------------------------------------------------------
def _atmosphere_payload(env: EnvironmentConfig, chord: float) -> Dict[str, Any]:
    atmosphere = domain("atmosphere_domain")
    data = dict(atmosphere.evaluate_atmosphere(env, reference_chord_m=chord))
    data.setdefault("backend", "numpy")
    data["environment"] = as_dict(env)
    return data


@physics_bp.post("/api/v1/environment/atmosphere")
def environment_atmosphere():
    env = merge_environment(body(), SESSION.environment)
    SESSION.set_environment(env)
    derived = domain("aero_domain").derived_properties(SESSION.aircraft)
    return jsonify(_atmosphere_payload(env, derived["ref_length_m"]))


@physics_bp.get("/api/v1/environment/current")
def environment_current():
    derived = domain("aero_domain").derived_properties(SESSION.aircraft)
    return jsonify(_atmosphere_payload(SESSION.environment, derived["ref_length_m"]))


@physics_bp.post("/api/v1/environment/config")
def environment_config():
    """Persist the airframe / launch / integrator configuration."""
    payload = body()
    if not payload:
        return jsonify(SESSION.current_snapshot())
    with SESSION.lock:
        if "aircraft" in payload:
            from physics.config_schema import AircraftConfig, LaunchConfig, SimulationSettings
            SESSION.set_aircraft(build(AircraftConfig, payload["aircraft"]))
        if "environment" in payload:
            SESSION.set_environment(
                merge_environment(payload["environment"], SESSION.environment)
            )
        if "launch" in payload:
            from physics.config_schema import LaunchConfig
            SESSION.set_launch(build(LaunchConfig, payload["launch"]))
        if "simulation" in payload:
            from physics.config_schema import SimulationSettings
            SESSION.set_settings(build(SimulationSettings, payload["simulation"]))
        return jsonify(SESSION.current_snapshot())


# ---------------------------------------------------------------------------
# Wind field
# ---------------------------------------------------------------------------
@physics_bp.post("/api/v1/wind/field")
def wind_field():
    wind = domain("wind_domain")
    payload = body()

    bounds_min_raw = payload.get("bounds_min", [-2.0, -2.0, -0.5])
    bounds_max_raw = payload.get("bounds_max", [2.0, 2.0, 1.5])
    if not (isinstance(bounds_min_raw, (list, tuple)) and len(bounds_min_raw) == 3):
        raise BadRequest("'bounds_min' must be a 3-element list")
    if not (isinstance(bounds_max_raw, (list, tuple)) and len(bounds_max_raw) == 3):
        raise BadRequest("'bounds_max' must be a 3-element list")
    bounds_min = [as_float(v, f"bounds_min[{i}]") for i, v in enumerate(bounds_min_raw)]
    bounds_max = [as_float(v, f"bounds_max[{i}]") for i, v in enumerate(bounds_max_raw)]
    if any(b <= a for a, b in zip(bounds_min, bounds_max)):
        raise BadRequest("every entry of 'bounds_max' must exceed 'bounds_min'")

    resolution = payload.get("resolution", 16)
    if isinstance(resolution, (list, tuple)):
        if len(resolution) != 3:
            raise BadRequest("'resolution' list must hold exactly 3 integers")
        resolution = [as_int(v, "resolution") for v in resolution]
        if any(v < 2 or v > 128 for v in resolution):
            raise BadRequest("'resolution' list must hold 3 integers in [2, 128]")
        total_grid = resolution[0] * resolution[1] * resolution[2]
    else:
        resolution = as_int(resolution, "resolution")
        if resolution < 2 or resolution > 64:
            raise BadRequest("'resolution' must be an int in [2, 64] or a 3-list")
        total_grid = resolution ** 3

    if total_grid > 262144:
        raise BadRequest(f"total grid points ({total_grid}) exceeds limit of 262144")

    time_s = as_float(payload.get("time_s", 0.0), "time_s")
    backend = str(payload.get("backend", "auto"))
    if backend not in ("auto", "cupy", "torch", "numpy"):
        raise BadRequest("'backend' must be one of auto, cupy, torch, numpy")

    env = merge_environment(payload.get("environment"), SESSION.environment)
    data = dict(
        wind.wind_field(
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            resolution=resolution,
            env=env,
            time_s=time_s,
            backend=backend,
        )
    )
    data.setdefault("backend", wind.active_backend())

    # Check if client accepts gzip or requested compact binary encoding
    accept_encoding = request.headers.get("Accept-Encoding", "")
    if "gzip" in accept_encoding and not request.args.get("raw"):
        json_bytes = json.dumps(data).encode("utf-8")
        # Only compress if payload is large enough (> 1KB)
        if len(json_bytes) > 1024:
            compressed = gzip.compress(json_bytes, compresslevel=6)
            resp = Response(compressed, mimetype="application/json")
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Content-Length"] = str(len(compressed))
            return resp

    return jsonify(data)


# ---------------------------------------------------------------------------
# Stress Analysis
# ---------------------------------------------------------------------------
@physics_bp.post("/api/v1/stress/analysis")
def stress_analysis():
    stress = domain("stress_domain")
    payload = body()
    cfg = build(AircraftConfig, payload.get("aircraft"), SESSION.aircraft)
    env = merge_environment(payload.get("environment"), SESSION.environment)

    speed = as_float(payload.get("speed_mps", 10.0), "speed_mps")
    if speed < 0.0:
        raise BadRequest("field 'speed_mps' must be >= 0")
    if speed > MAX_SPEED_MPS:
        raise BadRequest(f"field 'speed_mps' must be <= {MAX_SPEED_MPS:g} m/s")
    density = as_float(payload.get("density_kgm3", 1.225), "density_kgm3")
    if density <= 0.0:
        raise BadRequest("field 'density_kgm3' must be > 0")
    if density > MAX_DENSITY:
        raise BadRequest(f"field 'density_kgm3' must be <= {MAX_DENSITY:g} kg/m3")
    spanwise = as_int(payload.get("spanwise", 24), "spanwise")
    chordwise = as_int(payload.get("chordwise", 16), "chordwise")
    if not (2 <= spanwise <= 256 and 2 <= chordwise <= 256):
        raise BadRequest("'spanwise' and 'chordwise' must be within [2, 256]")
    beta_deg = as_float(payload.get("beta_deg", 0.0), "beta_deg")
    load = payload.get("load_factor")
    load = None if load is None else as_float(load, "load_factor")

    backend = str(payload.get("backend", "auto"))
    data = dict(
        stress.analyse(
            cfg=cfg,
            env=env,
            speed_mps=speed,
            density_kgm3=density,
            load_factor=load,
            spanwise=spanwise,
            chordwise=chordwise,
            beta_deg=beta_deg,
            backend=backend,
        )
    )
    return jsonify(data)
