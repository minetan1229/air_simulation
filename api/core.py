"""Core helpers, session state, exceptions, and domain registry for the API."""

from __future__ import annotations

import importlib
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
    """Merge a partial environment update onto ``base`` and clamp the result."""
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
                        if self.simulator._history.maxlen != cfg.ring_buffer_capacity:
                            from collections import deque
                            old_items = list(self.simulator._history)
                            self.simulator._history = deque(old_items, maxlen=cfg.ring_buffer_capacity)
                except Exception:  # pragma: no cover
                    self.simulator = None

    def simulator_instance(self) -> Any:
        with self.lock:
            if self.simulator is None:
                flight = domain("flight_sim")
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
