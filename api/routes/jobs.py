"""Optimization and Auto-Investigation background job routes."""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Dict
from flask import Blueprint, Response, jsonify, request

from physics import auto_investigate, storage_domain
from physics.config_schema import (
    ALGORITHM_NAMES,
    AircraftConfig,
    DESIGN_VARIABLE_CATALOGUE,
    EnvironmentConfig,
    LaunchConfig,
    OBJECTIVE_DIRECTIONS,
    OptimizerConfig,
    as_dict,
    from_dict,
)
from physics.job_manager import JOB_MANAGER
from ..core import (
    RESULT_CACHE,
    RESULT_CACHE_LIMIT,
    RESULT_CACHE_LOCK,
    SESSION,
    BadRequest,
    as_float,
    body,
    domain,
    merge_overlay,
)

log = logging.getLogger("paper_airplane")
jobs_bp = Blueprint("jobs", __name__)


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
    merge_overlay(base, raw.get("aircraft"), "aircraft")
    env = as_dict(SESSION.environment)
    merge_overlay(env, raw.get("environment"), "environment")
    launch = as_dict(SESSION.launch)
    merge_overlay(launch, raw.get("launch"), "launch")

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


@jobs_bp.post("/api/v1/optimizer/run")
def optimizer_run():
    if JOB_MANAGER.active_count() >= 4:
        raise BadRequest("server busy: maximum active jobs (4) reached, please wait or cancel running jobs")
    payload = body()
    cfg = _optimizer_config(payload)

    optimizer = domain("optimizer_domain")
    dispatch = optimizer.ALGORITHM_DISPATCH
    total = max(1, int(cfg.max_evaluations))
    job = JOB_MANAGER.create(
        name=f"{cfg.algorithm} optimizer",
        total_evaluations=total,
        backend=domain("parallel_engine").backend_name(cfg.processes),
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


@jobs_bp.get("/api/v1/optimizer/status/<job_id>")
def optimizer_status(job_id: str):
    try:
        return jsonify(JOB_MANAGER.snapshot(job_id))
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc


@jobs_bp.post("/api/v1/optimizer/cancel/<job_id>")
def optimizer_cancel(job_id: str):
    try:
        JOB_MANAGER.request_cancel(job_id)
        record = JOB_MANAGER.cancel(job_id)
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc
    return jsonify(as_dict(record))


@jobs_bp.get("/api/v1/optimizer/result/<job_id>")
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


@jobs_bp.post("/api/v1/investigate/run")
@jobs_bp.post("/api/v1/jobs/investigate")
def investigate_run():
    """Start an auto-investigation job.

    ``/api/v1/jobs/investigate`` is the path mandated by SPECIFICATION 3.5;
    ``/api/v1/investigate/run`` is kept for the existing clients.
    """
    if JOB_MANAGER.active_count() >= 4:
        raise BadRequest("server busy: maximum active jobs (4) reached, please wait or cancel running jobs")
    payload = body()
    base_air = as_dict(SESSION.aircraft)
    merge_overlay(base_air, payload.get("aircraft"), "aircraft")
    env = as_dict(SESSION.environment)
    merge_overlay(env, payload.get("environment"), "environment")
    lau = as_dict(SESSION.launch)
    merge_overlay(lau, payload.get("launch"), "launch")

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


@jobs_bp.get("/api/v1/investigate/status/<job_id>")
def investigate_status(job_id: str):
    try:
        return jsonify(JOB_MANAGER.snapshot(job_id))
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc


@jobs_bp.post("/api/v1/investigate/cancel/<job_id>")
def investigate_cancel(job_id: str):
    try:
        JOB_MANAGER.request_cancel(job_id)
        record = JOB_MANAGER.cancel(job_id)
    except KeyError as exc:
        raise BadRequest(f"unknown job_id '{job_id}'") from exc
    return jsonify(as_dict(record))


@jobs_bp.get("/api/v1/investigate/result/<job_id>")
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


@jobs_bp.get("/api/v1/jobs")
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


@jobs_bp.post("/api/v1/jobs/join/<job_id>")
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
