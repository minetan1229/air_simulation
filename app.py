"""Flask REST API for the 3D paper-airplane simulator.

Integrates every physics domain behind the endpoints declared in AGENTS.md
section 10 / prompt.md section 7.  All handlers follow the same contract:

* a malformed or missing field yields HTTP 400 with a descriptive JSON body;
* an unexpected failure yields HTTP 500 with the traceback written to the
  server log;
* every successful response is JSON and carries the ``backend`` field that
  actually executed the computation (``"numpy"`` when no GPU is present).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import struct
import subprocess
import threading
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from physics.config_schema import (
    ALGORITHM_NAMES,
    AIRCRAFT_SCHEMA,
    DESIGN_VARIABLE_CATALOGUE,
    ENVIRONMENT_SCHEMA,
    LAUNCH_SCHEMA,
    LaunchConfig,
    OBJECTIVE_DIRECTIONS,
    OptimizerConfig,
    SimulationSettings,
    AircraftConfig,
    EnvironmentConfig,
    as_dict,
    from_dict,
    utc_now_iso,
)
from physics import auto_investigate
from physics import job_manager as job_manager_module
from physics import mesh_domain
from physics import storage_domain
from physics.job_manager import JOB_MANAGER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
)
log = logging.getLogger("paper_airplane")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024


# Pre-warm GPU kernels in background on app load
def _kick_gpu_warmup():
    try:
        from physics import parallel_engine
        threading.Thread(target=parallel_engine.warmup_gpu, daemon=True).start()
    except Exception:
        pass


_kick_gpu_warmup()


# ---------------------------------------------------------------------------
# Lazy domain access
# ---------------------------------------------------------------------------
_DOMAINS: Dict[str, Any] = {}


def _domain(name: str) -> Any:
    """Import a domain module on first use so a missing optional dependency
    never breaks the whole application."""
    if name in _DOMAINS:
        return _DOMAINS[name]
    import importlib

    try:
        module = importlib.import_module(f"physics.{name}")
    except Exception as exc:  # pragma: no cover - surfaced as HTTP 503
        log.error("domain '%s' unavailable: %s", name, exc)
        raise ServiceUnavailable(f"physics domain '{name}' is unavailable: {exc}") from exc
    _DOMAINS[name] = module
    return module


class ServiceUnavailable(RuntimeError):
    """Raised when an optional backend module cannot be imported."""


class BadRequest(ValueError):
    """Raised by handlers for any client-side input problem."""


class NotFound(KeyError):
    """Raised when an entity or preset is not found."""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
def _error(message: str, status: int = 400) -> Tuple[Response, int]:
    return jsonify({"error": str(message), "status": status, "timestamp": utc_now_iso()}), status


@app.errorhandler(BadRequest)
def _handle_bad_request(exc: BadRequest):
    return _error(exc, 400)


@app.errorhandler(NotFound)
def _handle_not_found(exc: NotFound):
    return _error(str(exc), 404)


@app.errorhandler(ServiceUnavailable)
def _handle_unavailable(exc: ServiceUnavailable):
    return _error(exc, 503)


@app.errorhandler(404)
def _handle_404(_exc):
    return _error("resource not found", 404)


@app.errorhandler(405)
def _handle_405(_exc):
    return _error("method not allowed for this endpoint", 405)


@app.errorhandler(ValueError)
def _handle_value_error(exc: ValueError):
    """A domain raising ValueError means the payload was not usable.

    Logged at WARNING so a genuine internal bug is still visible, but reported
    to the client as 400 -- never as a 500.
    """
    log.warning("ValueError on %s: %s", request.path, exc)
    return _error(f"invalid input: {exc}", 400)


@app.errorhandler(OverflowError)
def _handle_overflow(exc: OverflowError):
    log.warning("OverflowError on %s: %s", request.path, exc)
    return _error(f"numeric overflow for the given input: {exc}", 400)


@app.errorhandler(ZeroDivisionError)
def _handle_zero_division(exc: ZeroDivisionError):
    log.warning("ZeroDivisionError on %s: %s", request.path, exc)
    return _error(f"degenerate input caused a division by zero: {exc}", 400)


@app.errorhandler(HTTPException)
def _handle_http_exception(exc: HTTPException):
    code = exc.code or 500
    return _error(exc.description or str(exc), code)


@app.errorhandler(Exception)
def _handle_unexpected(exc: Exception):
    log.error("unhandled error on %s: %s\n%s", request.path, exc, traceback.format_exc())
    return _error("an unexpected internal server error occurred", 500)


@app.after_request
def _add_security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: https://api.iconify.design; "
        "connect-src 'self' ws: wss:;"
    )
    if "Access-Control-Allow-Origin" not in response.headers:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, DELETE"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def _handle_options(path: str = ""):
    resp = Response(status=204)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp



# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def body() -> Dict[str, Any]:
    """Parse the JSON body, tolerating an absent or non-object payload."""
    if not request.data:
        return {}
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as exc:
        raise BadRequest(f"request body is not valid JSON: {exc}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise BadRequest("request body must be a JSON object")
    return payload


def require(payload: Dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise BadRequest(f"missing required field '{key}'")
    return payload[key]


def as_float(value: Any, key: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"field '{key}' must be a number, got {value!r}") from exc
    if out != out or out in (float("inf"), float("-inf")):
        raise BadRequest(f"field '{key}' must be finite")
    return out


def as_int(value: Any, key: str) -> int:
    """Parse an integer field, rejecting floats with a fractional part."""
    if isinstance(value, bool):
        return int(value)
    try:
        as_float(value, key)
    except BadRequest as exc:
        raise BadRequest(f"field '{key}' must be an integer, got {value!r}") from exc
    out = int(float(value))
    if float(value) != out:
        raise BadRequest(f"field '{key}' must be a whole number, got {value!r}")
    return out


def as_vec3(value: Any, key: str) -> Tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise BadRequest(f"field '{key}' must be a 3-element list of numbers")
    return tuple(as_float(v, f"{key}[{i}]") for i, v in enumerate(value))  # type: ignore[return-value]


#: Physical sanity ceilings.  Values beyond these overflow the double-precision
#: dynamic-pressure term (``0.5*rho*V**2``) and raise instead of returning a
#: finite answer, so they are rejected as bad input rather than 500-ing.
MAX_SPEED_MPS = 1500.0
MAX_MACH = 0.99
MAX_DENSITY = 50.0


def build(cfg_cls, payload: Optional[Dict[str, Any]], base: Any = None):
    """Merge a partial payload onto ``base`` (or the class defaults)."""
    if payload is not None and not isinstance(payload, dict):
        raise BadRequest(f"{cfg_cls.__name__} payload must be a JSON object")
    current = as_dict(base) if base is not None else {}
    if payload:
        current.update(payload)
    try:
        obj = from_dict(cfg_cls, current, strict=False)
        if hasattr(obj, "clamp_ranges"):
            obj = obj.clamp_ranges()
        return obj
    except Exception as exc:
        raise BadRequest(f"invalid {cfg_cls.__name__} payload: {exc}") from exc


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
class Session:
    """Server-side simulation state shared by the step/reset endpoints."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.aircraft = AircraftConfig()
        self.environment = EnvironmentConfig()
        self.launch = LaunchConfig()
        self.settings = SimulationSettings()
        self.simulator: Any = None
        self.mesh_model_id: str = ""
        self.selected_mesh: Any = None

    # -- configuration ---------------------------------------------------
    def set_aircraft(self, cfg: AircraftConfig) -> None:
        with self.lock:
            self.aircraft = cfg
            self.simulator = None

    def set_environment(self, cfg: EnvironmentConfig) -> None:
        with self.lock:
            self.environment = cfg
            if self.simulator is not None:
                try:
                    self.simulator.environment = cfg
                    if hasattr(self.simulator, "_refresh_derived"):
                        self.simulator._refresh_derived()
                except Exception:  # pragma: no cover
                    self.simulator = None

    def set_launch(self, cfg: LaunchConfig) -> None:
        with self.lock:
            self.launch = cfg
            self.simulator = None

    def set_settings(self, cfg: SimulationSettings) -> None:
        with self.lock:
            self.settings = cfg
            if self.simulator is not None:
                try:
                    self.simulator.settings = cfg
                    if hasattr(self.simulator, "_history") and hasattr(self.simulator._history, "maxlen"):
                        # Rebuild history ring buffer capacity if changed
                        if self.simulator._history.maxlen != cfg.ring_buffer_capacity:
                            from collections import deque
                            old_items = list(self.simulator._history)
                            self.simulator._history = deque(old_items, maxlen=cfg.ring_buffer_capacity)
                except Exception:  # pragma: no cover
                    self.simulator = None

    # -- simulator -------------------------------------------------------
    def simulator_instance(self):
        with self.lock:
            if self.simulator is None:
                flight = _domain("flight_sim")
                self.simulator = flight.FlightSimulator(
                    aircraft=self.aircraft,
                    environment=self.environment,
                    launch=self.launch,
                    settings=self.settings,
                )
            return self.simulator

    def reset(self) -> Any:
        with self.lock:
            sim = self.simulator_instance()
            return sim.reset(launch=self.launch)

    def current_snapshot(self) -> Dict[str, Any]:
        # Configuration objects are replaced atomically by the setters.  A
        # read-only snapshot must not wait behind a long-running flight.
        return {
            "aircraft": as_dict(self.aircraft),
            "environment": as_dict(self.environment),
            "launch": as_dict(self.launch),
            "simulation": as_dict(self.settings),
            "selected_mesh_model_id": self.mesh_model_id,
        }


SESSION = Session()

#: In-process store of optimiser results, keyed by job_id, so
#: ``GET /api/v1/optimizer/result/<job_id>`` can serve the full payload.
RESULT_CACHE: Dict[str, Dict[str, Any]] = {}
RESULT_CACHE_LIMIT = 32
RESULT_CACHE_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------
@app.get("/api/v1/meta/schemas")
def meta_schemas():
    """Single source of truth for the UI sliders, objective list and ranges."""
    return jsonify(
        {
            "aircraft": AIRCRAFT_SCHEMA,
            "environment": ENVIRONMENT_SCHEMA,
            "launch": LAUNCH_SCHEMA,
            "objectives": [
                {"name": name, "direction": direction}
                for name, direction in OBJECTIVE_DIRECTIONS.items()
            ],
            "algorithms": list(ALGORITHM_NAMES),
            "design_variables": [as_dict(v) for v in DESIGN_VARIABLE_CATALOGUE],
            "design_variable_catalogue": [as_dict(v) for v in DESIGN_VARIABLE_CATALOGUE],
            "backend": "numpy",
        }
    )


@app.get("/api/v1/meta/health")
def meta_health():
    domains = {}
    for name in (
        "aero_domain",
        "atmosphere_domain",
        "wind_domain",
        "stress_domain",
        "mesh_domain",
        "flight_sim",
        "parallel_engine",
        "optimizer_domain",
        "job_manager",
        "storage_domain",
    ):
        try:
            _domain(name)
            domains[name] = "ok"
        except ServiceUnavailable as exc:
            domains[name] = str(exc)
    gpu_info = _domain("parallel_engine").get_gpu_info()
    return jsonify({
        "status": "ok",
        "domains": domains,
        "gpu": gpu_info,
        "timestamp": utc_now_iso(),
    })


@app.get("/api/v1/system/gpu")
def system_gpu():
    parallel = _domain("parallel_engine")
    return jsonify(parallel.get_gpu_info())


@app.post("/api/v1/system/force-gpu-render")
def force_gpu_render():
    """Configure Windows DirectX UserGpuPreferences to force discrete high-performance GPU (RTX 4060)
    for browsers, and optionally launch or re-launch browser with high-performance flags.
    Restricted to loopback address and explicit environment variable.
    """
    if request.remote_addr not in ("127.0.0.1", "::1", "localhost"):
        return _error("forbidden: endpoint restricted to local loopback", 403)
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if origin and not any(origin.startswith(h) for h in ("http://127.0.0.1:", "http://localhost:", "https://127.0.0.1:", "https://localhost:")):
        return _error("forbidden: cross-origin request disallowed", 403)
    if not os.environ.get("PAPER_ALLOW_SYSTEM_CONFIG") and os.environ.get("ENV") == "production":
        return _error("system configuration endpoint is disabled in production", 403)

    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "configure")  # "configure" | "launch_chrome" | "launch_edge"

    registered = []
    error_msg = None

    try:
        import winreg
        key_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        browser_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            browser_paths.extend([
                os.path.join(local_app, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(local_app, r"Microsoft\Edge\Application\msedge.exe"),
            ])

        for bp in browser_paths:
            if os.path.exists(bp):
                winreg.SetValueEx(key, bp, 0, winreg.REG_SZ, "GpuPreference=2;AutoHDREnable=0;")
                registered.append(bp)
        winreg.CloseKey(key)
    except Exception as ex:
        error_msg = str(ex)

    launched = False
    if action in ("launch_chrome", "launch_edge"):
        target_exe = (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if action == "launch_chrome"
            else r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        )
        if os.path.exists(target_exe):
            flags = [
                target_exe,
                "--force_high_performance_gpu",
                "--gpu-preference=high-performance",
                "--enable-gpu-rasterization",
                "--ignore-gpu-blocklist",
                "http://127.0.0.1:5000/",
            ]
            try:
                subprocess.Popen(flags)
                launched = True
            except Exception as ex:
                error_msg = str(ex)

    return jsonify({
        "status": "success" if not error_msg else "partial_error",
        "registered_paths": registered,
        "launched": launched,
        "error": error_msg,
        "gpu_preference": 2,
        "preference_name": "High Performance (NVIDIA dGPU)",
    })


# ---------------------------------------------------------------------------
# 1. Aerodynamics
# ---------------------------------------------------------------------------
@app.post("/api/v1/aerodynamics/calculate")
def aerodynamics_calculate():
    aero = _domain("aero_domain")
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


@app.post("/api/v1/aerodynamics/polar")
def aerodynamics_polar():
    aero = _domain("aero_domain")
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
# 2. Environment / atmosphere
# ---------------------------------------------------------------------------
def _atmosphere_payload(env: EnvironmentConfig, chord: float) -> Dict[str, Any]:
    atmosphere = _domain("atmosphere_domain")
    data = dict(atmosphere.evaluate_atmosphere(env, reference_chord_m=chord))
    data.setdefault("backend", "numpy")
    data["environment"] = as_dict(env)
    return data


def merge_environment(payload: Optional[Dict[str, Any]], base: EnvironmentConfig) -> EnvironmentConfig:
    """Merge a partial environment update onto ``base`` and clamp the result.

    Supplying ``temperature_K`` or ``pressure_Pa`` implicitly switches
    ``isa_mode`` off unless the caller states it explicitly, so a UI slider
    move always reaches the next integration step instead of being silently
    overridden by the ISA profile (prompt.md 2.1).
    """
    payload = dict(payload or {})
    merged = as_dict(base)
    if not payload.get("isa_mode") and (
        "temperature_K" in payload or "pressure_Pa" in payload
    ):
        payload["isa_mode"] = False
    merged.update(payload)
    try:
        env = from_dict(EnvironmentConfig, merged, strict=False)
    except Exception as exc:
        raise BadRequest(f"invalid EnvironmentConfig payload: {exc}") from exc
    return env.clamp_ranges()


@app.post("/api/v1/environment/atmosphere")
def environment_atmosphere():
    env = merge_environment(body(), SESSION.environment)
    SESSION.set_environment(env)
    derived = _domain("aero_domain").derived_properties(SESSION.aircraft)
    return jsonify(_atmosphere_payload(env, derived["ref_length_m"]))


@app.get("/api/v1/environment/current")
def environment_current():
    derived = _domain("aero_domain").derived_properties(SESSION.aircraft)
    return jsonify(_atmosphere_payload(SESSION.environment, derived["ref_length_m"]))


@app.post("/api/v1/environment/config")
def environment_config():
    """Persist the airframe / launch / integrator configuration."""
    payload = body()
    if not payload:
        return jsonify(SESSION.current_snapshot())
    with SESSION.lock:
        if "aircraft" in payload:
            SESSION.set_aircraft(build(AircraftConfig, payload["aircraft"]))
        if "environment" in payload:
            SESSION.set_environment(
                merge_environment(payload["environment"], SESSION.environment)
            )
        if "launch" in payload:
            SESSION.set_launch(build(LaunchConfig, payload["launch"]))
        if "simulation" in payload:
            SESSION.set_settings(build(SimulationSettings, payload["simulation"]))
        return jsonify(SESSION.current_snapshot())


# ---------------------------------------------------------------------------
# 3. Wind field
# ---------------------------------------------------------------------------
@app.post("/api/v1/wind/field")
def wind_field():
    wind = _domain("wind_domain")
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
    return jsonify(data)


# ---------------------------------------------------------------------------
# 4. Structural stress
# ---------------------------------------------------------------------------
@app.post("/api/v1/stress/analysis")
def stress_analysis():
    stress = _domain("stress_domain")
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


# ---------------------------------------------------------------------------
# 5. Simulation control
# ---------------------------------------------------------------------------
@app.post("/api/v1/simulation/step")
def simulation_step():
    flight = _domain("flight_sim")
    payload = body()
    dt = payload.get("dt")
    if dt is not None:
        dt = as_float(dt, "dt")
        if not (1.0e-5 <= dt <= 2.0e-1):
            raise BadRequest("'dt' must lie within [1e-5, 2e-1]")
    substeps = payload.get("substeps")
    substeps = None if substeps is None else as_int(substeps, "substeps")
    if substeps is not None and not (1 <= substeps <= 4096):
        raise BadRequest("'substeps' must lie within [1, 4096]")

    with SESSION.lock:
        if payload.get("aircraft"):
            SESSION.set_aircraft(build(AircraftConfig, payload["aircraft"]))
        if payload.get("environment"):
            SESSION.set_environment(
                merge_environment(payload["environment"], SESSION.environment)
            )
        if payload.get("launch"):
            SESSION.set_launch(build(LaunchConfig, payload["launch"]))
        if payload.get("simulation"):
            SESSION.set_settings(build(SimulationSettings, payload["simulation"]))

        simulator = SESSION.simulator_instance()
        result = simulator.step(dt=dt, substeps=substeps)
    out = dict(result)
    gpu_info = _domain("parallel_engine").get_gpu_info()
    if gpu_info.get("available"):
        out.setdefault("backend", gpu_info["backend"])
        out.setdefault("device_name", gpu_info.get("device_name"))
    else:
        out.setdefault("backend", "numpy")
    return jsonify(out)


@app.post("/api/v1/simulation/reset")
def simulation_reset():
    payload = body()
    with SESSION.lock:
        if payload.get("aircraft"):
            SESSION.set_aircraft(build(AircraftConfig, payload["aircraft"]))
        if payload.get("environment"):
            SESSION.set_environment(
                merge_environment(payload["environment"], SESSION.environment)
            )
        if payload.get("launch"):
            SESSION.set_launch(build(LaunchConfig, payload["launch"]))
        if payload.get("simulation"):
            SESSION.set_settings(build(SimulationSettings, payload["simulation"]))
        SESSION.simulator = None
        state = SESSION.reset()
    return jsonify(
        {
            "state": as_dict(state),
            **SESSION.current_snapshot(),
            "backend": "numpy",
            "timestamp": utc_now_iso(),
        }
    )


@app.post("/api/v1/simulation/launch")
def simulation_launch():
    """Execute a complete flight trajectory from release until landing.

    Precomputes all samples so the frontend transport dock operates as a pure
    video-like player (scrub, rewind, play, pause) without ad-hoc background steps.
    """
    payload = body()
    with SESSION.lock:
        if payload.get("aircraft"):
            SESSION.set_aircraft(build(AircraftConfig, payload["aircraft"]))
        if payload.get("environment"):
            SESSION.set_environment(
                merge_environment(payload["environment"], SESSION.environment)
            )
        if payload.get("launch"):
            SESSION.set_launch(build(LaunchConfig, payload["launch"]))
        if payload.get("simulation"):
            SESSION.set_settings(build(SimulationSettings, payload["simulation"]))

        SESSION.simulator = None
        simulator = SESSION.simulator_instance()
        simulator.reset(launch=SESSION.launch)
        duration_s = as_float(payload.get("duration_s", 25.0), "duration_s")
        if not (0.1 <= duration_s <= 120.0):
            raise BadRequest("field 'duration_s' must lie within [0.1, 120.0]")
        aircraft = SESSION.aircraft
        environment = SESSION.environment
        launch = SESSION.launch
        settings = SESSION.settings

    # Keep the session available for configuration edits while integrating.
    summary = simulator.simulate(duration_s=duration_s, record=True)
    samples = [as_dict(s) for s in simulator.history()]

    # GPU-accelerated ensemble Monte Carlo on RTX 4060
    parallel = _domain("parallel_engine")
    gpu_info = parallel.get_gpu_info()
    sample_trajectories = []
    ensemble_data = None
    backend_name_str = summary.get("backend", "numpy")
    device_name_str = None

    should_ensemble = bool(payload.get("include_ensemble") or payload.get("ensemble"))
    if should_ensemble and gpu_info.get("available") and gpu_info.get("backend") == "cupy":
        try:
            ensemble_res = parallel.gpu_ensemble_simulation(
                aircraft=aircraft,
                environment=environment,
                launch=launch,
                settings=settings,
                n_trajectories=128,
                max_sample_tracks=64,
            )
            if ensemble_res and "sample_trajectories" in ensemble_res:
                sample_trajectories = ensemble_res["sample_trajectories"]
                ensemble_data = ensemble_res
                backend_name_str = f"cupy ({gpu_info.get('device_name', 'RTX 4060')})"
                device_name_str = gpu_info.get("device_name")
        except Exception as exc:
            log.warning("GPU ensemble simulation fallback: %s", exc)

    return jsonify(
        {
            "metrics": summary,
            "samples": samples,
            "sample_trajectories": sample_trajectories,
            "ensemble": ensemble_data,
            "total_frames": len(samples),
            "backend": backend_name_str,
            "device_name": device_name_str,
            "gpu_available": bool(gpu_info.get("available")),
            "timestamp": utc_now_iso(),
        }
    )


@app.get("/api/v1/simulation/history")
def simulation_history():
    with SESSION.lock:
        simulator = SESSION.simulator
        if simulator is None:
            raise BadRequest("no simulation has been started; POST /api/v1/simulation/reset first")
        samples = [as_dict(s) for s in simulator.history()]
        metrics = simulator.metrics()
    return jsonify({"samples": samples, "metrics": metrics, "backend": "numpy"})


@app.get("/api/v1/simulation/history/<int:index>")
def simulation_history_frame(index: int):
    with SESSION.lock:
        simulator = SESSION.simulator
        if simulator is None:
            raise BadRequest("no simulation has been started")
        sample = simulator.seek_frame(index)
    if sample is None:
        raise BadRequest(f"frame {index} is outside the ring buffer")
    return jsonify({"sample": as_dict(sample), "backend": "numpy"})


@app.post("/api/v1/simulation/batch")
def simulation_batch():
    parallel = _domain("parallel_engine")
    payload = body()
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BadRequest("'cases' must be a non-empty list of configurations")
    if len(cases) > 512:
        raise BadRequest("'cases' may contain at most 512 entries")
    normalised = []
    total_steps = 0
    MAX_BATCH_TOTAL_STEPS = 500_000
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise BadRequest(f"cases[{index}] must be a JSON object")
        duration_s = as_float(case.get("duration_s", 5.0), f"cases[{index}].duration_s")
        if not (0.01 <= duration_s <= 120.0):
            raise BadRequest(f"cases[{index}].duration_s must lie within [0.01, 120.0]")
        dt_max = as_float(case.get("dt_max", 0.02), f"cases[{index}].dt_max")
        if not (1.0e-4 <= dt_max <= 1.0):
            raise BadRequest(f"cases[{index}].dt_max must lie within [1e-4, 1.0]")
        total_steps += int(duration_s / dt_max)
        if total_steps > MAX_BATCH_TOTAL_STEPS:
            raise BadRequest(f"batch total computation steps exceed limit of {MAX_BATCH_TOTAL_STEPS}")
        normalised.append(
            {
                "aircraft": build(AircraftConfig, case.get("aircraft"), SESSION.aircraft).__dict__,
                "environment": build(
                    EnvironmentConfig, case.get("environment"), SESSION.environment
                ).clamp_ranges().__dict__,
                "launch": build(LaunchConfig, case.get("launch"), SESSION.launch).__dict__,
                "duration_s": duration_s,
                "dt_max": dt_max,
            }
        )
    processes_raw = payload.get("processes", 0)
    if processes_raw is None:
        processes = 0
    else:
        processes = as_int(processes_raw, "processes")
        if not (0 <= processes <= 64):
            raise BadRequest("'processes' must lie within [0, 64]")
    results = parallel.parallel_evaluate(normalised, processes=processes)
    return jsonify(
        {
            "count": len(results),
            "results": results,
            "backend": parallel.backend_name(processes),
        }
    )


@app.post("/api/v1/simulation/ensemble")
def simulation_ensemble():
    parallel = _domain("parallel_engine")
    payload = body()
    aircraft = build(AircraftConfig, payload.get("aircraft"), SESSION.aircraft)
    environment = build(EnvironmentConfig, payload.get("environment"), SESSION.environment).clamp_ranges()
    launch = build(LaunchConfig, payload.get("launch"), SESSION.launch)
    settings = build(SimulationSettings, payload.get("settings"), SESSION.settings)

    n_trajectories = int(payload.get("n_trajectories", 100))
    noise_config = payload.get("noise_config")
    force_cpu = bool(payload.get("force_cpu", False))
    random_seed = payload.get("random_seed", 42)
    max_sample_tracks = int(payload.get("max_sample_tracks", n_trajectories))
    if random_seed is not None:
        random_seed = int(random_seed)

    result = parallel.gpu_ensemble_simulation(
        aircraft=aircraft,
        environment=environment,
        launch=launch,
        settings=settings,
        n_trajectories=n_trajectories,
        noise_config=noise_config,
        force_cpu=force_cpu,
        random_seed=random_seed,
        max_sample_tracks=max_sample_tracks,
    )
    return jsonify(result)



# ---------------------------------------------------------------------------
# 6. Mesh import
# ---------------------------------------------------------------------------
@app.post("/api/v1/mesh/import")
def mesh_import():
    try:
        return _mesh_import_inner()
    except BadRequest:
        raise
    except (ValueError, KeyError, UnicodeDecodeError, IndexError, struct.error) as exc:
        # Parsers reject malformed payloads with ValueError; that is a client
        # input problem, not a server fault.
        raise BadRequest(f"could not import model: {exc}") from exc


def _mesh_import_inner():
    payload: Dict[str, Any] = {}
    if request.files:
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            raise BadRequest("multipart upload must contain a 'file' field")
        data = uploaded.read()
        filename = secure_filename(uploaded.filename) or "model.glb"
        metrics = mesh_domain.MESH_LIBRARY.import_model_bytes(
            data=data,
            filename=filename,
            scale_hint=request.form.get("scale_hint", "m"),
            cg_mode=request.form.get("cg_mode", "volume"),
            paper_gsm=float(request.form.get("paper_gsm", 100.0)),
        )
    else:
        payload = body()
        if "url" in payload and payload["url"]:
            url = str(payload["url"])
            if not url.lower().startswith(("http://", "https://")):
                raise BadRequest("'url' must start with http:// or https://")
            metrics = mesh_domain.MESH_LIBRARY.import_model_url(
                url=url,
                scale_hint=str(payload.get("scale_hint", "m")),
                cg_mode=str(payload.get("cg_mode", "volume")),
                paper_gsm=float(payload.get("paper_gsm", 100.0)),
                overrides=payload.get("overrides"),
            )
        else:
            data = payload.get("data_base64")
            if not data:
                raise BadRequest("provide a multipart 'file', a 'url', or 'data_base64'")
            import base64

            try:
                raw = base64.b64decode(data, validate=True)
            except Exception as exc:
                raise BadRequest(f"'data_base64' is not valid base64: {exc}") from exc
            metrics = mesh_domain.MESH_LIBRARY.import_model_bytes(
                data=raw,
                filename=str(payload.get("filename", "model.glb")),
                scale_hint=str(payload.get("scale_hint", "m")),
                cg_mode=str(payload.get("cg_mode", "volume")),
                paper_gsm=float(payload.get("paper_gsm", 100.0)),
                overrides=payload.get("overrides"),
            )
    with SESSION.lock:
        SESSION.mesh_model_id = metrics.model_id
        SESSION.selected_mesh = metrics
        SESSION.set_aircraft(mesh_domain.MESH_LIBRARY.to_aircraft_config(metrics.model_id, SESSION.aircraft))
    return jsonify(as_dict(metrics))


@app.get("/api/v1/mesh/models")
def mesh_models():
    models = mesh_domain.MESH_LIBRARY.list_models()
    selected = mesh_domain.MESH_LIBRARY.selected()
    return jsonify(
        {
            "models": models,
            "count": len(models),
            "selected": selected.model_id if selected else None,
        }
    )


@app.post("/api/v1/mesh/select")
def mesh_select():
    payload = body()
    model_id = str(require(payload, "model_id"))
    try:
        metrics = mesh_domain.MESH_LIBRARY.select(model_id)
    except KeyError as exc:
        raise BadRequest(f"unknown model_id '{model_id}'") from exc
    with SESSION.lock:
        SESSION.mesh_model_id = metrics.model_id
        SESSION.selected_mesh = metrics
        SESSION.set_aircraft(mesh_domain.MESH_LIBRARY.to_aircraft_config(model_id, SESSION.aircraft))
    return jsonify(as_dict(metrics))


@app.get("/api/v1/mesh/metrics/<model_id>")
def mesh_metrics(model_id: str):
    try:
        metrics = mesh_domain.MESH_LIBRARY.get_metrics(model_id)
    except KeyError as exc:
        raise BadRequest(f"unknown model_id '{model_id}'") from exc
    return jsonify(as_dict(metrics))


@app.post("/api/v1/mesh/overrides/<model_id>")
def mesh_overrides(model_id: str):
    payload = body()
    overrides = payload.get("overrides", payload)
    if not isinstance(overrides, dict):
        raise BadRequest("'overrides' must be a JSON object")
    try:
        metrics = mesh_domain.MESH_LIBRARY.apply_overrides(model_id, overrides)
    except KeyError as exc:
        raise BadRequest(f"unknown model_id '{model_id}'") from exc
    return jsonify(as_dict(metrics))


# ---------------------------------------------------------------------------
# 7. Optimizer
# ---------------------------------------------------------------------------
def _optimizer_config(payload: Dict[str, Any]) -> OptimizerConfig:
    raw = dict(payload)
    objectives: list[Dict[str, Any]] = raw.get("objectives") or []
    names: list[str] = []
    weights: Dict[str, float] = {}
    if isinstance(objectives, list):
        for item in objectives:
            if isinstance(item, str):
                names.append(item)
                weights.setdefault(item, 1.0)
            elif isinstance(item, dict):
                name = str(item.get("name", ""))
                if name:
                    names.append(name)
                    weights[name] = float(item.get("weight", 1.0))
    elif isinstance(objectives, dict):
        for name, weight in objectives.items():
            names.append(str(name))
            weights[str(name)] = float(weight)

    raw_weights = raw.get("weights")
    if isinstance(raw_weights, dict):
        for k, v in raw_weights.items():
            try:
                weights[str(k)] = float(v)
            except (ValueError, TypeError):
                pass

    unknown = [n for n in names if n not in OBJECTIVE_DIRECTIONS]
    if unknown:
        raise BadRequest(f"unknown objective(s): {', '.join(unknown)}")
    if not names:
        raise BadRequest("at least one objective is required")

    algorithm = str(raw.get("algorithm", "differential-evolution"))
    if algorithm not in ALGORITHM_NAMES:
        raise BadRequest(f"'algorithm' must be one of {', '.join(ALGORITHM_NAMES)}")

    dvars_raw = raw.get("design_variables")
    if dvars_raw is None:
        dvars = [v.name for v in DESIGN_VARIABLE_CATALOGUE]
    elif not dvars_raw:
        raise BadRequest("at least one design variable must be selected")
    else:
        dvars = list(dvars_raw)

    base = as_dict(SESSION.aircraft)
    base.update(raw.get("aircraft", {}) or {})
    env = as_dict(SESSION.environment)
    env.update(raw.get("environment", {}) or {})
    launch = as_dict(SESSION.launch)
    launch.update(raw.get("launch", {}) or {})

    variable_bounds = raw.get("variable_bounds") or {}
    if not isinstance(variable_bounds, dict):
        raise BadRequest("'variable_bounds' must be a JSON object")
    for key, bounds in variable_bounds.items():
        if not (isinstance(bounds, (list, tuple)) and len(bounds) == 2):
            raise BadRequest(f"variable_bounds['{key}'] must be a [low, high] pair")
        if float(bounds[0]) > float(bounds[1]):
            raise BadRequest(f"variable_bounds['{key}'] low must not exceed high")

    try:
        cfg = OptimizerConfig(
            objectives=names,
            weights=weights,
            algorithm=algorithm,
            design_variables=dvars,
            variable_bounds={k: (float(v[0]), float(v[1])) for k, v in variable_bounds.items()},
            max_evaluations=int(raw.get("max_evaluations", 200)),
            population_size=int(raw.get("population_size", 40)),
            seed=int(raw.get("seed", 7)),
            processes=int(raw.get("processes", 0)),
            sim_duration_s=float(raw.get("sim_duration_s", 6.0)),
            dt_max=float(raw.get("dt_max", 0.02)),
            aircraft=from_dict(AircraftConfig, base, strict=False),
            environment=from_dict(EnvironmentConfig, env, strict=False).clamp_ranges(),
            launch=from_dict(LaunchConfig, launch, strict=False),
        )
    except BadRequest:
        raise
    except Exception as exc:
        raise BadRequest(f"invalid optimizer configuration: {exc}") from exc

    if cfg.max_evaluations < 1 or cfg.max_evaluations > 5000:
        raise BadRequest("'max_evaluations' must be within [1, 5000]")
    if cfg.design_variables:
        try:
            cfg.resolved_variables()
        except KeyError as exc:
            raise BadRequest(f"unknown design variable: {exc}") from exc
    return cfg


@app.post("/api/v1/optimizer/run")
def optimizer_run():
    if JOB_MANAGER.active_count() >= 4:
        raise BadRequest("server busy: maximum active jobs (4) reached, please wait or cancel running jobs")
    payload = body()
    cfg = _optimizer_config(payload)

    optimizer = _domain("optimizer_domain")
    dispatch = optimizer.ALGORITHM_DISPATCH
    total = max(1, int(cfg.max_evaluations))
    job = JOB_MANAGER.create(
        name=f"{cfg.algorithm} optimizer",
        total_evaluations=total,
        backend=_domain("parallel_engine").backend_name(cfg.processes),
        objectives=list(cfg.objectives),
        algorithm=cfg.algorithm,
        model_id=SESSION.mesh_model_id,
    )
    JOB_MANAGER.start(job.job_id)
    cfg_dict = as_dict(cfg)

    def progress_cb(evaluated: int, _total: int, _best: float) -> None:
        try:
            JOB_MANAGER.update(job.job_id, evaluated, total_evaluations=total)
        except KeyError:
            pass

    def cancel_cb() -> bool:
        return JOB_MANAGER.is_cancel_requested(job.job_id)

    def worker() -> None:
        try:
            runner = dispatch[cfg_dict["algorithm"]]
            result = runner(
                optimizer.from_dict(OptimizerConfig, cfg_dict), progress_cb, cancel_cb
            )
            result["job_id"] = job.job_id
            with RESULT_CACHE_LOCK:
                RESULT_CACHE[job.job_id] = result
                while len(RESULT_CACHE) > RESULT_CACHE_LIMIT:
                    RESULT_CACHE.pop(next(iter(RESULT_CACHE)))
            JOB_MANAGER.finish(job.job_id, message=f"{result['evaluations']} evaluations")
        except Exception as exc:
            log.error("optimizer job %s failed: %s\n%s", job.job_id, exc, traceback.format_exc())
            try:
                JOB_MANAGER.fail(job.job_id, str(exc))
            except KeyError:
                pass

    thread = threading.Thread(target=worker, name=f"optimizer-{job.job_id}", daemon=True)
    JOB_MANAGER.register_thread(job.job_id, thread)
    thread.start()
    return jsonify(as_dict(JOB_MANAGER.get(job.job_id))), 202


@app.get("/api/v1/optimizer/status/<job_id>")
def optimizer_status(job_id: str):
    try:
        return jsonify(JOB_MANAGER.snapshot(job_id))
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc


@app.post("/api/v1/optimizer/cancel/<job_id>")
def optimizer_cancel(job_id: str):
    try:
        JOB_MANAGER.request_cancel(job_id)
        record = JOB_MANAGER.cancel(job_id)
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc
    return jsonify(as_dict(record))


@app.get("/api/v1/optimizer/result/<job_id>")
def optimizer_result(job_id: str):
    try:
        record = JOB_MANAGER.get(job_id)
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc
    if record.status not in ("completed", "cancelled"):
        raise BadRequest(f"job '{job_id}' is {record.status}; no result available yet")

    with RESULT_CACHE_LOCK:
        payload = RESULT_CACHE.get(job_id)
    if payload is None:
        payload = {
            "job_id": job_id,
            "algorithm": record.algorithm,
            "status": record.status,
            "message": record.message,
            "note": "result payload is no longer retained in process; re-run to regenerate",
        }
    fmt = str(request.args.get("format", "json")).lower()
    if fmt == "csv":
        text, mimetype, filename = storage_domain.export_optimizer_result(payload, "csv")
        return Response(text, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"})
    if fmt != "json":
        raise BadRequest("'format' must be json or csv")
    return jsonify(payload)


# ---------------------------------------------------------------------------
# 7b. Auto Investigate / Tuning
# ---------------------------------------------------------------------------
@app.post("/api/v1/investigate/run")
def investigate_run():
    if JOB_MANAGER.active_count() >= 4:
        raise BadRequest("server busy: maximum active jobs (4) reached, please wait or cancel running jobs")
    payload = body()
    base_air = as_dict(SESSION.aircraft)
    base_air.update(payload.get("aircraft", {}) or {})
    env = as_dict(SESSION.environment)
    env.update(payload.get("environment", {}) or {})
    lau = as_dict(SESSION.launch)
    lau.update(payload.get("launch", {}) or {})

    aircraft_cfg = from_dict(AircraftConfig, base_air, strict=False)
    environment_cfg = from_dict(EnvironmentConfig, env, strict=False).clamp_ranges()
    launch_cfg = from_dict(LaunchConfig, lau, strict=False)

    target_metric = str(payload.get("target_metric", "distance")).lower()
    if target_metric not in ("distance", "flight_time"):
        raise BadRequest("'target_metric' must be 'distance' or 'flight_time'")

    total_evals = 100
    job = JOB_MANAGER.create(
        name=f"Auto-investigate ({target_metric})",
        total_evaluations=total_evals,
        backend="auto_investigate",
        objectives=[f"max_{target_metric}"],
        algorithm="sensitivity_and_focused_tuning",
        model_id=SESSION.mesh_model_id,
    )
    JOB_MANAGER.start(job.job_id)

    def progress_cb(pct: int, total: int, msg: str) -> None:
        if JOB_MANAGER.is_cancelled(job.job_id) or JOB_MANAGER.is_cancel_requested(job.job_id):
            raise RuntimeError(f"Job {job.job_id} cancelled")
        try:
            JOB_MANAGER.update(job.job_id, pct, total_evaluations=total, message=msg)
        except KeyError:
            pass

    def worker() -> None:
        try:
            res = auto_investigate.investigate_airplane(
                aircraft=aircraft_cfg,
                environment=environment_cfg,
                launch=launch_cfg,
                target_metric=target_metric,
                progress_callback=progress_cb,
            )
            res_dict = as_dict(res)
            res_dict["job_id"] = job.job_id
            with RESULT_CACHE_LOCK:
                RESULT_CACHE[job.job_id] = res_dict
                while len(RESULT_CACHE) > RESULT_CACHE_LIMIT:
                    RESULT_CACHE.pop(next(iter(RESULT_CACHE)))
            JOB_MANAGER.finish(job.job_id, message=f"Completed auto-investigation: {res.summary.get('primary_issue', 'OK')}")
        except Exception as exc:
            try:
                if JOB_MANAGER.is_cancelled(job.job_id) or JOB_MANAGER.is_cancel_requested(job.job_id):
                    JOB_MANAGER.cancel(job.job_id)
                    return
            except KeyError:
                return
            log.error("investigation job %s failed: %s\n%s", job.job_id, exc, traceback.format_exc())
            try:
                JOB_MANAGER.fail(job.job_id, str(exc))
            except KeyError:
                pass

    thread = threading.Thread(target=worker, name=f"investigate-{job.job_id}", daemon=True)
    JOB_MANAGER.register_thread(job.job_id, thread)
    thread.start()
    return jsonify(as_dict(JOB_MANAGER.get(job.job_id))), 202


@app.get("/api/v1/investigate/status/<job_id>")
def investigate_status(job_id: str):
    try:
        return jsonify(JOB_MANAGER.snapshot(job_id))
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc


@app.post("/api/v1/investigate/cancel/<job_id>")
def investigate_cancel(job_id: str):
    try:
        JOB_MANAGER.request_cancel(job_id)
        record = JOB_MANAGER.cancel(job_id)
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc
    return jsonify(as_dict(record))


@app.get("/api/v1/investigate/result/<job_id>")
def investigate_result(job_id: str):
    try:
        record = JOB_MANAGER.get(job_id)
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc
    if record.status not in ("completed", "cancelled"):
        raise BadRequest(f"job '{job_id}' is {record.status}; no result available yet")

    with RESULT_CACHE_LOCK:
        payload = RESULT_CACHE.get(job_id)
    if payload is None:
        raise BadRequest(f"result for job '{job_id}' is not in memory")
    return jsonify(payload)



@app.get("/api/v1/jobs")
def jobs_list():
    status = request.args.get("status")
    records = JOB_MANAGER.list_jobs(status=status)
    return jsonify(
        {
            "jobs": [JOB_MANAGER.snapshot(r.job_id) for r in records],
            "count": len(records),
            "active": JOB_MANAGER.active_count(),
        }
    )


@app.post("/api/v1/jobs/join/<job_id>")
def jobs_join(job_id: str):
    timeout_raw = request.args.get("timeout")
    if timeout_raw is None or str(timeout_raw).strip() == "":
        timeout = 10.0
    else:
        timeout = min(max(as_float(timeout_raw, "timeout"), 0.0), 30.0)
    try:
        finished = JOB_MANAGER.join(job_id, timeout)
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc
    snap = JOB_MANAGER.snapshot(job_id)
    snap["joined"] = finished
    return jsonify(snap)


# ---------------------------------------------------------------------------
# 8. Data export / import
# ---------------------------------------------------------------------------
@app.post("/api/v1/data/export")
def data_export():
    payload = body()
    kind = str(payload.get("kind", "flight")).lower()
    fmt = str(payload.get("format", "csv")).lower()
    if fmt not in ("csv", "json"):
        raise BadRequest("'format' must be csv or json")

    if kind == "flight":
        samples = payload.get("samples")
        if samples is None:
            with SESSION.lock:
                if SESSION.simulator is None:
                    raise BadRequest(
                        "no simulation history available; start one or pass 'samples' explicitly"
                    )
                samples = [as_dict(s) for s in SESSION.simulator.history()]
        if not isinstance(samples, list):
            raise BadRequest("'samples' must be a list of flight-log records")
        text, mimetype, filename = storage_domain.export_flight_log(samples, fmt)
    elif kind == "optimizer":
        result = payload.get("result")
        if result is None:
            raise BadRequest("an optimizer 'result' payload is required for kind='optimizer'")
        text, mimetype, filename = storage_domain.export_optimizer_result(result, fmt)
    elif kind == "config":
        bundle = payload.get("bundle") or SESSION.current_snapshot()
        text, mimetype, filename = storage_domain.export_config(bundle, fmt)
    else:
        raise BadRequest("'kind' must be one of flight, optimizer, config")

    if request.args.get("download") in ("1", "true", "yes"):
        return Response(
            text, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    return jsonify({"filename": filename, "mimetype": mimetype, "payload": text})


@app.post("/api/v1/data/import")
def data_import():
    payload = body()
    bundle = payload.get("bundle", payload)
    if not isinstance(bundle, dict):
        raise BadRequest("'bundle' must be a JSON object")
    try:
        normalised = storage_domain.validate_config_bundle(bundle)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc

    with SESSION.lock:
        SESSION.set_aircraft(from_dict(AircraftConfig, normalised["aircraft"], strict=False))
        SESSION.set_environment(
            from_dict(EnvironmentConfig, normalised["environment"], strict=False).clamp_ranges()
        )
        SESSION.set_launch(from_dict(LaunchConfig, normalised["launch"], strict=False))
        SESSION.set_settings(from_dict(SimulationSettings, normalised["simulation"], strict=False))
        snapshot = SESSION.current_snapshot()
    return jsonify({"ok": True, "bundle": snapshot})


# ---------------------------------------------------------------------------
# 8b. Record presets & throw techniques
# ---------------------------------------------------------------------------
@app.get("/api/v1/records/presets")
def get_record_presets():
    from physics import records_domain
    category = request.args.get("category")
    presets = records_domain.list_presets(category=category)
    return jsonify({"presets": presets, "count": len(presets)})


@app.get("/api/v1/records/presets/<key>")
def get_record_preset(key: str):
    from physics import records_domain
    try:
        data = records_domain.preset_payload(key)
    except KeyError:
        raise NotFound(f"Unknown preset key: '{key}'")
    return jsonify(data)


@app.post("/api/v1/records/apply")
def apply_record_preset():
    from physics import records_domain
    payload = body()
    key = payload.get("key")
    if not key:
        raise BadRequest("'key' is required")
    apply_throw_only = bool(payload.get("throw_only", False))
    apply_aircraft_only = bool(payload.get("aircraft_only", False))
    # calibrated defaults to True for full presets, but False when isolating a throw technique
    calibrated = bool(payload.get("calibrated", not apply_throw_only))
    flight_mode = payload.get("flight_mode") or "full_flight"

    with SESSION.lock:
        try:
            entry = records_domain.preset(key)
        except KeyError:
            raise NotFound(f"Unknown preset key: '{key}'")

        try:
            new_launch = entry.launch_config(SESSION.launch, calibrated=calibrated, flight_mode=flight_mode)
        except ValueError as exc:
            raise BadRequest(str(exc))
        if not apply_throw_only:
            SESSION.set_aircraft(entry.aircraft_config(SESSION.aircraft))
        if not apply_aircraft_only:
            SESSION.set_launch(new_launch)
            SESSION.set_environment(entry.environment_config(SESSION.environment))

        SESSION.simulator = None
        state = SESSION.reset()
        snapshot = SESSION.current_snapshot()

    chosen_throw, selection = entry.flight_selection(calibrated=calibrated, flight_mode=flight_mode)
    return jsonify({
        "ok": True,
        "key": key,
        "preset_name": entry.name,
        "calibrated": calibrated,
        "flight_mode": SESSION.launch.flight_mode,
        "flight_selection": selection,
        "throw": as_dict(chosen_throw),
        "state": state,
        **snapshot,
    })


@app.get("/api/v1/records/throw_techniques")
def get_throw_techniques():
    from physics import records_domain
    rules = [dict(rule) for rule in records_domain.THROW_RULES]
    comp_rules = records_domain.competition_rules()
    return jsonify({"throw_rules": rules, "competition_rules": comp_rules})


@app.post("/api/v1/guinness/simulate")
def guinness_simulate():
    """Fly a Guinness world record preset in isolation without polluting user session."""
    from physics import records_domain
    payload = body()
    key = payload.get("key")
    if not key:
        raise BadRequest("'key' is required")
    # By default in isolated guinness system, use the authentic published values (raw, uncalibrated)
    calibrated = bool(payload.get("calibrated", False))
    flight_mode = payload.get("flight_mode")
    duration_s = as_float(payload.get("duration_s", 60.0), "duration_s")

    try:
        result = records_domain.simulate_preset(
            key,
            duration_s=duration_s,
            calibrated=calibrated,
            flight_mode=flight_mode,
        )
    except KeyError:
        raise NotFound(f"Unknown record preset key: '{key}'")
    except Exception as exc:
        raise BadRequest(f"Guinness simulation error: {exc}")

    return jsonify({"ok": True, **result})


@app.get("/api/v1/guinness/verification")
def guinness_verification():
    """Get the verification matrix of all Guinness world records side by side."""
    from physics import records_domain
    category = request.args.get("category")
    keys = None
    if category:
        presets = records_domain.list_presets(category=category)
        keys = [p["key"] for p in presets]
    report = records_domain.verification_report(keys=keys)
    return jsonify(report)



# ---------------------------------------------------------------------------
# 9. UI entry point
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    from flask import render_template

    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
def create_app() -> Flask:
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper airplane CFD simulator API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    # Pre-warm GPU CUDA kernels in background so the first user flight has 0ms compile latency
    try:
        parallel = _domain("parallel_engine")
        threading.Thread(target=parallel.warmup_gpu, daemon=True).start()
    except Exception as exc:
        log.warning("GPU warmup thread skipped: %s", exc)

    log.info("starting on http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
