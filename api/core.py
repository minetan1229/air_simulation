"""Core helpers, session state, exceptions, and domain registry for the API."""

from __future__ import annotations

import importlib
import json
import logging
import threading
from typing import Any, Dict, Optional, Tuple

from flask import Response, jsonify, request
from werkzeug.exceptions import HTTPException

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    as_dict,
    from_dict,
)
from physics.atmosphere_domain import isa_pressure, isa_temperature
from physics.error_domain import ERROR_LOGGER

log = logging.getLogger("paper_airplane")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ServiceUnavailable(RuntimeError):
    """Raised when an optional backend module cannot be imported."""


class BadRequest(ValueError):
    """Raised by handlers for any client-side input problem."""


class NotFound(KeyError):
    """Raised when an entity or preset is not found."""


# ---------------------------------------------------------------------------
# Lazy domain access
# ---------------------------------------------------------------------------
_DOMAINS: Dict[str, Any] = {}


def domain(name: str) -> Any:
    """Import a domain module on first use so a missing optional dependency
    never breaks the whole application."""
    if name in _DOMAINS:
        return _DOMAINS[name]

    try:
        module = importlib.import_module(f"physics.{name}")
    except Exception as exc:  # pragma: no cover - surfaced as HTTP 503
        log.error("domain '%s' unavailable: %s", name, exc)
        raise ServiceUnavailable(f"physics domain '{name}' is unavailable: {exc}") from exc
    _DOMAINS[name] = module
    return module


# ---------------------------------------------------------------------------
# Error response helper
# ---------------------------------------------------------------------------
def api_error(
    message: str,
    status: int = 400,
    exc: Optional[BaseException] = None,
    traceback_str: Optional[str] = None,
    category: str = "api",
    level: str = "WARNING",
) -> Tuple[Response, int]:
    try:
        req_path = request.path if request else None
        req_method = request.method if request else None
        client_ip = request.remote_addr if request else None
    except Exception:
        req_path, req_method, client_ip = None, None, None

    rec = ERROR_LOGGER.record_error(
        message=message,
        level=level,
        category=category,
        exc=exc,
        traceback_str=traceback_str,
        path=req_path,
        method=req_method,
        status_code=status,
        client_ip=client_ip,
    )
    return jsonify({
        "error": str(message),
        "status": status,
        "timestamp": rec.timestamp,
        "error_id": rec.id,
    }), status


# ---------------------------------------------------------------------------
# Request parsing helpers
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


MAX_SPEED_MPS = 1500.0
MAX_MACH = 0.99
MAX_DENSITY = 50.0


def build(cfg_cls: Any, payload: Optional[Dict[str, Any]], base: Any = None) -> Any:
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


def merge_environment(payload: Optional[Dict[str, Any]], base: EnvironmentConfig) -> EnvironmentConfig:
    """Merge a partial environment update onto ``base`` and clamp the result.

    When ``isa_mode`` is true the temperature/pressure sliders are disabled:
    their values are derived from the ISA profile at ``altitude_m`` regardless
    of what the payload (or the previous session state) carried, so an
    ``isa_mode=True`` payload can never force a non-ISA temperature or pressure
    into the session.
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
    if env.isa_mode:
        temperature = isa_temperature(
            env.altitude_m, env.sea_level_temperature_K, env.lapse_rate_K_per_m
        )
        pressure = isa_pressure(
            env.altitude_m,
            env.sea_level_pressure_Pa,
            env.sea_level_temperature_K,
            env.lapse_rate_K_per_m,
        )
        env = EnvironmentConfig(
            **{**as_dict(env), "temperature_K": temperature, "pressure_Pa": pressure}
        )
    return env.clamp_ranges()


def merge_overlay(base: Dict[str, Any], overlay: Any, field: str) -> Dict[str, Any]:
    """Merge an overlay dict onto base dict, rejecting non-dict overlay values with 400 BadRequest."""
    out = dict(base)
    if overlay is None:
        return out
    if not isinstance(overlay, dict):
        raise BadRequest(f"'{field}' must be a JSON object")
    out.update(overlay)
    return out


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
#: Process-wide mutex that guards the shared simulation state.
#:
#: ``SESSION.simulator`` is a single :class:`~physics.flight_sim.FlightSimulator`
#: instance shared by every client, and the launch endpoint integrates it with
#: ``simulator.simulate()`` -- a loop that mutates the airframe state and appends
#: to the history ring buffer.  ``FlightSimulator._lock`` only protects the
#: history snapshot, not the integration itself, so a concurrent ``/simulation/step``
#: would advance the same state machine in the middle of the launch loop and mix
#: its frames into the recorded trajectory.
#:
#: Every endpoint that touches the shared state therefore has to hold this one
#: mutex.  It is an ``RLock`` because a few call sites (``Session.set_*``) keep
#: their historical self-locking behaviour, but the endpoints in
#: ``api/routes/simulation.py`` acquire it exactly once and then call the
#: lock-free helpers below, so no endpoint nests a second acquisition.
SIMULATION_LOCK = threading.RLock()


class Session:
    """Server-side simulation state shared by the step/reset endpoints."""

    def __init__(self) -> None:
        self.lock = SIMULATION_LOCK
        self.aircraft = AircraftConfig()
        self.environment = EnvironmentConfig()
        self.launch = LaunchConfig()
        self.settings = SimulationSettings()
        self.simulator: Any = None
        self.mesh_model_id: str = ""
        self.selected_mesh: Any = None

    # -- lock-free primitives ------------------------------------------------
    # The caller must already hold SIMULATION_LOCK.

    def _apply_aircraft(self, cfg: AircraftConfig) -> None:
        self.aircraft = cfg
        self.simulator = None

    def _apply_environment(self, cfg: EnvironmentConfig) -> None:
        self.environment = cfg
        if self.simulator is not None:
            try:
                self.simulator.environment = cfg
                if hasattr(self.simulator, "_refresh_derived"):
                    self.simulator._refresh_derived()
            except Exception:  # pragma: no cover
                self.simulator = None

    def _apply_launch(self, cfg: LaunchConfig) -> None:
        self.launch = cfg
        self.simulator = None

    def _apply_settings(self, cfg: SimulationSettings) -> None:
        self.settings = cfg
        if self.simulator is not None:
            try:
                self.simulator.settings = cfg
                if hasattr(self.simulator, "_history") and hasattr(self.simulator._history, "maxlen"):
                    if self.simulator._history.maxlen != cfg.ring_buffer_capacity:
                        from collections import deque
                        old_items = list(self.simulator._history)
                        self.simulator._history = deque(old_items, maxlen=cfg.ring_buffer_capacity)
            except Exception:  # pragma: no cover
                self.simulator = None

    def _simulator_instance(self) -> Any:
        if self.simulator is None:
            flight = domain("flight_sim")
            self.simulator = flight.FlightSimulator(
                aircraft=self.aircraft,
                environment=self.environment,
                launch=self.launch,
                settings=self.settings,
            )
        return self.simulator

    def apply_payload(self, payload: Dict[str, Any]) -> None:
        """Apply a request payload to the shared configuration.

        The caller **must** hold :data:`SIMULATION_LOCK`.  No lock is acquired
        here, so a request that already owns the mutex cannot deadlock on a
        re-entrant acquisition of the same mutex.
        """
        if payload.get("aircraft"):
            self._apply_aircraft(build(AircraftConfig, payload["aircraft"]))
        if payload.get("environment"):
            self._apply_environment(
                merge_environment(payload["environment"], self.environment)
            )
        if payload.get("launch"):
            self._apply_launch(build(LaunchConfig, payload["launch"]))
        if payload.get("simulation"):
            self._apply_settings(build(SimulationSettings, payload["simulation"]))

    def simulator_for_request(self, payload: Dict[str, Any]) -> Any:
        """Apply ``payload`` and return the shared simulator.

        The caller **must** hold :data:`SIMULATION_LOCK`; no lock is acquired
        here.  The payload is applied before the simulator is handed out, so the
        returned instance always matches the configuration it was built from.
        """
        self.apply_payload(payload)
        return self._simulator_instance()

    def reset_state(self) -> Any:
        """Reset the shared simulator back to the release state.

        The caller **must** hold :data:`SIMULATION_LOCK`; no lock is acquired
        here.
        """
        sim = self._simulator_instance()
        return sim.reset(launch=self.launch)

    # -- self-locking public API --------------------------------------------

    def set_aircraft(self, cfg: AircraftConfig) -> None:
        with self.lock:
            self._apply_aircraft(cfg)

    def set_environment(self, cfg: EnvironmentConfig) -> None:
        with self.lock:
            self._apply_environment(cfg)

    def set_launch(self, cfg: LaunchConfig) -> None:
        with self.lock:
            self._apply_launch(cfg)

    def set_settings(self, cfg: SimulationSettings) -> None:
        with self.lock:
            self._apply_settings(cfg)

    def simulator_instance(self) -> Any:
        with self.lock:
            return self._simulator_instance()

    def reset(self) -> Any:
        with self.lock:
            return self.reset_state()

    def current_snapshot(self) -> Dict[str, Any]:
        return {
            "aircraft": as_dict(self.aircraft),
            "environment": as_dict(self.environment),
            "launch": as_dict(self.launch),
            "simulation": as_dict(self.settings),
            "selected_mesh_model_id": self.mesh_model_id,
        }


SESSION = Session()

RESULT_CACHE: Dict[str, Dict[str, Any]] = {}
RESULT_CACHE_LIMIT = 32
RESULT_CACHE_LOCK = threading.RLock()
#: Byte ceiling for the optimizer / investigation result cache.  The oldest
#: entries are evicted first once either the entry count or the byte total
#: exceeds its limit, so a runaway optimiser cannot pin the process in RAM.
RESULT_CACHE_BYTES_LIMIT = 64 * 1024 * 1024
_RESULT_CACHE_BYTES: Dict[str, int] = {}


def result_cache_put(job_id: str, result: Dict[str, Any]) -> None:
    """Insert ``result`` into :data:`RESULT_CACHE`, evicting the oldest entries
    while the entry count or the total byte size exceeds its limit."""
    size = len(json.dumps(result, ensure_ascii=False, default=str))
    with RESULT_CACHE_LOCK:
        RESULT_CACHE[job_id] = result
        _RESULT_CACHE_BYTES[job_id] = size
        total_bytes = sum(_RESULT_CACHE_BYTES.values())
        while (
            len(RESULT_CACHE) > RESULT_CACHE_LIMIT
            or total_bytes > RESULT_CACHE_BYTES_LIMIT
        ):
            oldest = next(iter(RESULT_CACHE))
            RESULT_CACHE.pop(oldest, None)
            total_bytes -= _RESULT_CACHE_BYTES.pop(oldest, 0)
