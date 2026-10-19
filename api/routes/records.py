"""Data import/export, records presets, Guinness simulation, and error logging routes."""

from __future__ import annotations

import logging
from flask import Blueprint, Response, jsonify, request

from physics import storage_domain
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    as_dict,
    from_dict,
)
from physics.error_domain import ERROR_LOGGER
from ..core import SESSION, BadRequest, NotFound, api_error, as_float, body

log = logging.getLogger("paper_airplane")
records_bp = Blueprint("records", __name__)


# ---------------------------------------------------------------------------
# Data export / import
# ---------------------------------------------------------------------------
@records_bp.post("/api/v1/data/export")
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
    elif kind == "ensemble":
        ensemble_data = payload.get("data")
        if ensemble_data is None:
            raise BadRequest("ensemble 'data' payload is required for kind='ensemble'")
        text, mimetype, filename = storage_domain.export_ensemble_data(ensemble_data, fmt)
    else:
        raise BadRequest("'kind' must be one of flight, optimizer, config, ensemble")

    if request.args.get("download") in ("1", "true", "yes"):
        return Response(
            text, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    return jsonify({"filename": filename, "mimetype": mimetype, "payload": text})


@records_bp.post("/api/v1/data/import")
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
# Record presets & throw techniques
# ---------------------------------------------------------------------------
@records_bp.get("/api/v1/records/presets")
def get_record_presets():
    from physics import records_domain
    category = request.args.get("category")
    presets = records_domain.list_presets(category=category)
    return jsonify({"presets": presets, "count": len(presets)})


@records_bp.get("/api/v1/records/presets/<key>")
def get_record_preset(key: str):
    from physics import records_domain
    try:
        data = records_domain.preset_payload(key)
    except KeyError:
        raise NotFound(f"Unknown preset key: '{key}'")
    return jsonify(data)


@records_bp.post("/api/v1/records/apply")
def apply_record_preset():
    from physics import records_domain
    payload = body()
    key = payload.get("key")
    if not key:
        raise BadRequest("'key' is required")
    apply_throw_only = bool(payload.get("throw_only", False))
    apply_aircraft_only = bool(payload.get("aircraft_only", False))
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


@records_bp.get("/api/v1/records/throw_techniques")
def get_throw_techniques():
    from physics import records_domain
    rules = [dict(rule) for rule in records_domain.THROW_RULES]
    comp_rules = records_domain.competition_rules()
    return jsonify({"throw_rules": rules, "competition_rules": comp_rules})


@records_bp.post("/api/v1/guinness/simulate")
def guinness_simulate():
    """Fly a Guinness world record preset in isolation without polluting user session."""
    from physics import records_domain
    payload = body()
    key = payload.get("key")
    if not key:
        raise BadRequest("'key' is required")
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


@records_bp.get("/api/v1/guinness/verification")
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
# Error Logging & Diagnostics Endpoints
# ---------------------------------------------------------------------------
@records_bp.get("/api/v1/errors")
def list_errors():
    """Retrieve error history with optional level, category, and text filtering.

    The payload is the redacted view: stack traces and client addresses stay
    in the server log (SPECIFICATION 4.1).
    """
    limit = int(request.args.get("limit", 100))
    limit = max(1, min(limit, 500))
    level = request.args.get("level")
    category = request.args.get("category")
    search = request.args.get("search")
    logs = ERROR_LOGGER.get_logs(
        limit=limit, level=level, category=category, search=search, public=True
    )
    return jsonify({
        "errors": logs,
        "count": len(logs),
        "statistics": ERROR_LOGGER.public_statistics(),
    })


@records_bp.post("/api/v1/errors/report")
def report_client_error():
    """Receive client-side (frontend JavaScript/WebGL) errors and persist them.

    The client controls every field, so each one is bounded and validated
    before it reaches the audit log; otherwise a caller could inject arbitrary
    content into the operator's log files.
    """
    payload = body()
    if not isinstance(payload, dict):
        return api_error("request body must be a JSON object", 400)

    message = str(payload.get("message", "Frontend error"))[:2000]
    level = str(payload.get("level", "ERROR")).upper()
    if level not in ("ERROR", "WARNING", "CRITICAL", "INFO", "DEBUG"):
        return api_error("level must be one of ERROR, WARNING, CRITICAL, INFO, DEBUG", 400)
    category = str(payload.get("category", "frontend"))[:64]
    details = payload.get("details", {})
    if not isinstance(details, dict):
        return api_error("details must be a JSON object", 400)
    details = {str(k)[:64]: str(v)[:500] for k, v in list(details.items())[:20]}
    traceback_str = payload.get("stack") or payload.get("traceback")
    if traceback_str is not None:
        traceback_str = str(traceback_str)[:4000]

    try:
        req_path = payload.get("url") or (request.path if request else None)
        client_ip = request.remote_addr if request else None
    except Exception:
        req_path, client_ip = None, None

    status_code = payload.get("status_code", 400)
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        status_code = 400

    rec = ERROR_LOGGER.record_error(
        message=message,
        level=level,
        category=category,
        traceback_str=traceback_str,
        details=details,
        path=req_path,
        method="CLIENT",
        status_code=status_code,
        client_ip=client_ip,
    )
    return jsonify({"ok": True, "error_id": rec.id})


@records_bp.get("/api/v1/errors/stats")
def error_stats():
    """Get error metrics and log file statuses (without the server paths)."""
    return jsonify(ERROR_LOGGER.public_statistics())


@records_bp.post("/api/v1/errors/clear")
def clear_error_buffer():
    """Clear the in-memory error ring buffer (retaining disk logs)."""
    ERROR_LOGGER.clear()
    return jsonify({"ok": True, "cleared": True})
