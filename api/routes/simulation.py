"""Simulation step, reset, launch, history, batch, and ensemble endpoints."""

from __future__ import annotations

import logging
import threading
from flask import Blueprint, jsonify

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    as_dict,
    utc_now_iso,
)
from ..core import (
    SESSION,
    SIMULATION_LOCK,
    BadRequest,
    as_float,
    as_int,
    body,
    build,
    domain,
)

log = logging.getLogger("paper_airplane")
sim_bp = Blueprint("simulation", __name__)

SIMULATION_LAUNCH_SEMAPHORE = threading.BoundedSemaphore(value=4)


@sim_bp.post("/api/v1/simulation/step")
def simulation_step():
    flight = domain("flight_sim")
    payload = body()
    dt = payload.get("dt")
    if dt is not None:
        dt = as_float(dt, "dt")
        if not (1.0e-5 <= dt <= 2.0e-1):
            raise BadRequest("'dt' must lie within [1e-5, 2e-1]")
    substeps = payload.get("substeps")
    substeps = None if substeps is None else as_int(substeps, "substeps")
    if substeps is not None and not (1 <= substeps <= 256):
        raise BadRequest("'substeps' must lie within [1, 256]")

    # Optional multi-step execution to stream 10-60 simulation steps in one round-trip
    n_steps = payload.get("n_steps")
    n_steps = 1 if n_steps is None else as_int(n_steps, "n_steps")
    if not (1 <= n_steps <= 240):
        raise BadRequest("'n_steps' must lie within [1, 240]")

    # The shared simulator is mutated by the step itself, so the whole
    # request runs under the single session mutex.  Holding it here is what
    # keeps a concurrent /simulation/launch from interleaving its frames into
    # this trajectory (and vice versa).
    with SIMULATION_LOCK:
        simulator = SESSION.simulator_for_request(payload)
        if n_steps == 1:
            result = simulator.step(dt=dt, substeps=substeps)
            out = dict(result)
        else:
            steps_list = []
            for _ in range(n_steps):
                res = simulator.step(dt=dt, substeps=substeps)
                steps_list.append(res)
                if res.get("terminated", False):
                    break
            last = steps_list[-1]
            out = dict(last)
            out["steps"] = steps_list
            out["n_steps"] = len(steps_list)

    gpu_info = domain("parallel_engine").get_gpu_info()
    if gpu_info.get("available"):
        out.setdefault("backend", gpu_info["backend"])
        out.setdefault("device_name", gpu_info.get("device_name"))
    else:
        out.setdefault("backend", "numpy")
    return jsonify(out)


@sim_bp.post("/api/v1/simulation/reset")
def simulation_reset():
    payload = body()
    with SIMULATION_LOCK:
        SESSION.apply_payload(payload)
        SESSION.simulator = None
        state = SESSION.reset_state()
    return jsonify(
        {
            "state": as_dict(state),
            **SESSION.current_snapshot(),
            "backend": "numpy",
            "timestamp": utc_now_iso(),
        }
    )


@sim_bp.post("/api/v1/simulation/launch")
def simulation_launch():
    """Execute a complete flight trajectory from release until landing."""
    acquired = SIMULATION_LAUNCH_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        raise BadRequest("server busy: maximum concurrent simulations reached, please retry")
    try:
        payload = body()
        # The launch integrates the *shared* simulator, so it must hold the
        # very same mutex that /simulation/step holds.  A concurrent step
        # would otherwise advance the same state machine in the middle of the
        # integration loop and mix its frames into the recorded trajectory.
        with SIMULATION_LOCK:
            simulator = SESSION.simulator_for_request(payload)
            simulator.reset(launch=SESSION.launch)
            duration_s = as_float(payload.get("duration_s", 25.0), "duration_s")
            if not (0.1 <= duration_s <= 120.0):
                raise BadRequest("field 'duration_s' must lie within [0.1, 120.0]")
            aircraft = SESSION.aircraft
            environment = SESSION.environment
            launch = SESSION.launch
            settings = SESSION.settings

            summary = simulator.simulate(duration_s=duration_s, record=True)
            samples = [as_dict(s) for s in simulator.history()]

        parallel = domain("parallel_engine")
        gpu_info = parallel.get_gpu_info()
        sample_trajectories = []
        ensemble_data = None
        backend_name_str = summary.get("backend", "numpy")
        device_name_str = None

        should_ensemble = bool(payload.get("include_ensemble") or payload.get("ensemble"))
        if should_ensemble and gpu_info.get("available") and gpu_info.get("backend") == "cupy":
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
    finally:
        SIMULATION_LAUNCH_SEMAPHORE.release()


@sim_bp.get("/api/v1/simulation/history")
def simulation_history():
    with SIMULATION_LOCK:
        simulator = SESSION.simulator
        if simulator is None:
            raise BadRequest("no simulation has been started; POST /api/v1/simulation/reset first")
        samples = [as_dict(s) for s in simulator.history()]
        metrics = simulator.metrics()
    return jsonify({"samples": samples, "metrics": metrics, "backend": "numpy"})


@sim_bp.get("/api/v1/simulation/history/<int:index>")
def simulation_history_frame(index: int):
    with SIMULATION_LOCK:
        simulator = SESSION.simulator
        if simulator is None:
            raise BadRequest("no simulation has been started")
        sample = simulator.seek_frame(index)
    if sample is None:
        raise BadRequest(f"frame {index} is outside the ring buffer")
    return jsonify({"sample": as_dict(sample), "backend": "numpy"})


@sim_bp.post("/api/v1/simulation/batch")
def simulation_batch():
    parallel = domain("parallel_engine")
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


@sim_bp.post("/api/v1/simulation/ensemble")
def simulation_ensemble():
    parallel = domain("parallel_engine")
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
