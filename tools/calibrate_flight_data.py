"""Real-world Flight Data Calibration Tool for Paper Airplane 6-DoF Simulator.

This tool identifies and calibrates aerodynamic and structural parameters
(e.g., cd0, oswald_e, elevon_deg, aeroelastic_flex, polhamus_kv) from measured
flight trajectory data (high-speed camera, video tracking, IMU, or optical logs).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    as_dict,
    from_dict,
)
from physics.flight_sim import FlightSimulator

log = logging.getLogger(__name__)


# Parameter bounds and default scales for optimization
PARAM_SPECS: Dict[str, Dict[str, Any]] = {
    "cd0": {"bounds": (0.005, 0.12), "default": 0.025, "scale": 0.01},
    "oswald_e": {"bounds": (0.10, 0.98), "default": 0.75, "scale": 0.1},
    "elevon_deg": {"bounds": (-15.0, 15.0), "default": 0.0, "scale": 1.0},
    "aeroelastic_flex": {"bounds": (0.0, 1.0), "default": 0.0, "scale": 0.1},
    "polhamus_kv": {"bounds": (0.0, math.pi), "default": 0.0, "scale": 0.5},
    "cL0": {"bounds": (-0.1, 0.4), "default": 0.0, "scale": 0.05},
}


def load_flight_data(path: Path) -> List[Dict[str, float]]:
    """Load measured flight trajectory from CSV or JSON.

    Required minimum columns: time_s and (z_m or altitude_m), plus (x_m or speed_mps).
    """
    ext = path.suffix.lower()
    records: List[Dict[str, float]] = []
    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            records = [{k: float(v) for k, v in row.items()} for row in data]
        elif isinstance(data, dict) and "samples" in data:
            records = [{k: float(v) for k, v in row.items()} for row in data["samples"]]
        else:
            raise ValueError(f"Unexpected JSON format in {path}")
    else:  # Assume CSV
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                parsed_row: Dict[str, float] = {}
                for k, v in row.items():
                    if k is None or v is None:
                        continue
                    try:
                        parsed_row[k.strip()] = float(v.strip())
                    except ValueError:
                        continue
                if parsed_row:
                    records.append(parsed_row)

    if not records:
        raise ValueError(f"No trajectory samples loaded from {path}")

    # Standardize column names
    standardized: List[Dict[str, float]] = []
    for r in records:
        t = r.get("time_s", r.get("time", r.get("t", 0.0)))
        x = r.get("x_m", r.get("x", r.get("distance_m", r.get("dist", 0.0))))
        y = r.get("y_m", r.get("y", 0.0))
        alt = r.get("altitude_m", r.get("alt_m", r.get("altitude", -r.get("z_m", -r.get("z", 0.0)))))
        speed = r.get("speed_mps", r.get("speed", r.get("velocity", 0.0)))
        standardized.append({
            "time_s": float(t),
            "x_m": float(x),
            "y_m": float(y),
            "altitude_m": float(alt),
            "speed_mps": float(speed),
        })
    standardized.sort(key=lambda item: item["time_s"])
    return standardized


def run_candidate_sim(
    aircraft: AircraftConfig,
    env: EnvironmentConfig,
    launch: LaunchConfig,
    duration_s: float,
    dt_max: float = 0.01,
) -> List[Dict[str, float]]:
    """Run simulator and return sampled time series."""
    settings = SimulationSettings(
        dt_min=0.001,
        dt_max=dt_max,
        record_interval_s=0.02,
        duration_s=duration_s + 0.1,
        collision_enabled=True,
    )
    sim = FlightSimulator(aircraft, env, launch, settings)
    sim.simulate(duration_s=duration_s)
    samples = sim.history()
    out: List[Dict[str, float]] = []
    for s in samples:
        out.append({
            "time_s": float(s.time_s),
            "x_m": float(s.x_m),
            "y_m": float(s.y_m),
            "altitude_m": float(s.altitude_m),
            "speed_mps": float(s.speed_mps),
        })
    return out


def evaluate_trajectory_loss(
    sim_data: List[Dict[str, float]],
    target_data: List[Dict[str, float]],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Compute Root Mean Squared Error (RMSE) between simulated and measured tracks."""
    if not sim_data or not target_data:
        return 1.0e6

    w = weights or {"altitude": 2.0, "x": 1.0, "speed": 0.5}
    sim_t = np.array([p["time_s"] for p in sim_data])
    target_t = np.array([p["time_s"] for p in target_data])

    t_max = min(sim_t[-1], target_t[-1])
    if t_max <= 0.05:
        return 1.0e6

    # Interpolate target points that fall within simulated time
    mask = target_t <= t_max
    if not np.any(mask):
        return 1.0e6

    eval_t = target_t[mask]
    target_alt = np.array([p["altitude_m"] for p in target_data])[mask]
    target_x = np.array([p["x_m"] for p in target_data])[mask]
    target_speed = np.array([p["speed_mps"] for p in target_data])[mask]

    sim_alt = np.interp(eval_t, sim_t, [p["altitude_m"] for p in sim_data])
    sim_x = np.interp(eval_t, sim_t, [p["x_m"] for p in sim_data])
    sim_speed = np.interp(eval_t, sim_t, [p["speed_mps"] for p in sim_data])

    alt_rmse = float(np.sqrt(np.mean((sim_alt - target_alt) ** 2)))
    x_rmse = float(np.sqrt(np.mean((sim_x - target_x) ** 2)))
    speed_rmse = float(np.sqrt(np.mean((sim_speed - target_speed) ** 2)) if np.any(target_speed > 0) else 0.0)

    total_loss = w["altitude"] * alt_rmse + w["x"] * x_rmse + w["speed"] * speed_rmse
    # Penalty if flight time was cut short due to premature crash
    sim_flight_time = sim_t[-1]
    target_flight_time = target_t[-1]
    if sim_flight_time < target_flight_time * 0.8:
        total_loss += 5.0 * (target_flight_time - sim_flight_time)

    return total_loss


def calibrate_parameters(
    target_data: List[Dict[str, float]],
    base_aircraft: AircraftConfig,
    base_env: Optional[EnvironmentConfig] = None,
    base_launch: Optional[LaunchConfig] = None,
    param_names: Sequence[str] = ("cd0", "oswald_e", "elevon_deg"),
    maxiter: int = 40,
) -> Dict[str, Any]:
    """Optimize aerodynamic parameters to minimize discrepancy with target data."""
    env = base_env or EnvironmentConfig()
    duration = float(target_data[-1]["time_s"])

    # If launch not given, deduce initial conditions from first data points
    if base_launch is None:
        p0 = target_data[0]
        init_speed = p0["speed_mps"]
        if init_speed <= 0.0 and len(target_data) > 1:
            dt0 = target_data[1]["time_s"] - p0["time_s"]
            dx0 = target_data[1]["x_m"] - p0["x_m"]
            init_speed = max(dx0 / max(dt0, 1.0e-3), 5.0)
        init_height = max(p0["altitude_m"], 0.2)
        launch = LaunchConfig(launch_speed=init_speed, launch_height_m=init_height)
    else:
        launch = base_launch

    names = [p for p in param_names if p in PARAM_SPECS]
    if not names:
        raise ValueError(f"None of requested parameters {param_names} recognized in PARAM_SPECS")

    lows = np.array([PARAM_SPECS[name]["bounds"][0] for name in names])
    highs = np.array([PARAM_SPECS[name]["bounds"][1] for name in names])
    x0_raw = np.array([float(getattr(base_aircraft, name, PARAM_SPECS[name]["default"])) for name in names])
    u0 = np.clip((x0_raw - lows) / (highs - lows), 0.0, 1.0)

    history: List[Dict[str, Any]] = []

    def objective_u(u: np.ndarray) -> float:
        u_clamped = np.clip(u, 0.0, 1.0)
        x_val = lows + u_clamped * (highs - lows)
        updates = {names[i]: float(x_val[i]) for i in range(len(names))}
        candidate = AircraftConfig(**{**as_dict(base_aircraft), **updates})
        try:
            sim_data = run_candidate_sim(candidate, env, launch, duration)
            loss = evaluate_trajectory_loss(sim_data, target_data)
        except Exception as ex:
            log.debug("Sim evaluation error in calibration: %s", ex)
            loss = 1.0e5
        history.append({"params": updates, "loss": loss})
        return loss

    initial_loss = objective_u(u0)

    try:
        from scipy.optimize import minimize
        u_bounds = [(0.0, 1.0) for _ in names]
        res = minimize(
            objective_u,
            u0,
            method="Nelder-Mead",
            bounds=u_bounds,
            options={"maxiter": maxiter, "xatol": 1.0e-3, "fatol": 1.0e-3},
        )
        opt_u = np.clip(res.x, 0.0, 1.0)
        opt_x = lows + opt_u * (highs - lows)
        final_loss = float(res.fun)
    except ImportError:
        opt_x = x0_raw
        final_loss = initial_loss

    calibrated_params = {names[i]: float(opt_x[i]) for i in range(len(names))}
    calibrated_aircraft = AircraftConfig(**{**as_dict(base_aircraft), **calibrated_params})

    # Run final simulation for telemetry
    final_sim = run_candidate_sim(calibrated_aircraft, env, launch, duration)

    return {
        "initial_params": {names[i]: float(x0_raw[i]) for i in range(len(names))},
        "initial_loss": float(initial_loss),
        "calibrated_params": calibrated_params,
        "calibrated_aircraft": as_dict(calibrated_aircraft),
        "final_loss": float(final_loss),
        "iterations": len(history),
        "sim_trajectory": final_sim,
    }


def generate_synthetic_data(
    aircraft: AircraftConfig,
    env: EnvironmentConfig,
    launch: LaunchConfig,
    duration_s: float = 3.0,
    noise_std: float = 0.0,
) -> List[Dict[str, float]]:
    """Generate ground-truth trajectory with optional measurement noise."""
    sim_data = run_candidate_sim(aircraft, env, launch, duration_s)
    if noise_std <= 0.0:
        return sim_data

    rng = np.random.default_rng(42)
    noisy: List[Dict[str, float]] = []
    for p in sim_data:
        noisy.append({
            "time_s": p["time_s"],
            "x_m": p["x_m"] + float(rng.normal(0.0, noise_std)),
            "y_m": p["y_m"] + float(rng.normal(0.0, noise_std * 0.5)),
            "altitude_m": max(p["altitude_m"] + float(rng.normal(0.0, noise_std * 0.5)), 0.0),
            "speed_mps": max(p["speed_mps"] + float(rng.normal(0.0, noise_std)), 0.0),
        })
    return noisy


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate 6-DoF aerodynamic parameters from flight data")
    parser.add_argument("--input", "-i", type=Path, help="Path to measured flight data (CSV or JSON)")
    parser.add_argument("--aircraft", "-a", type=Path, help="Base aircraft config JSON")
    parser.add_argument("--output", "-o", type=Path, help="Output calibrated aircraft config JSON")
    parser.add_argument("--parameters", "-p", type=str, default="cd0,oswald_e,elevon_deg",
                        help="Comma-separated parameter names to calibrate")
    parser.add_argument("--maxiter", type=int, default=50, help="Maximum optimizer iterations")
    parser.add_argument("--self-test", action="store_true", help="Run self-test with synthetic trajectory")

    args = parser.parse_args(argv)

    if args.self_test:
        print("=== Running Calibration Self-Test ===")
        true_aircraft = AircraftConfig(cd0=0.035, oswald_e=0.68, elevon_deg=2.5)
        env = EnvironmentConfig()
        launch = LaunchConfig(launch_speed=8.5, launch_height_m=1.3)
        synth_data = generate_synthetic_data(true_aircraft, env, launch, duration_s=2.5)

        # Perturbed initial guess
        guess_aircraft = AircraftConfig(cd0=0.020, oswald_e=0.85, elevon_deg=0.0)
        res = calibrate_parameters(
            synth_data,
            base_aircraft=guess_aircraft,
            base_env=env,
            base_launch=launch,
            param_names=["cd0", "oswald_e", "elevon_deg"],
            maxiter=40,
        )
        print(f"Initial loss: {res['initial_loss']:.4f}")
        print(f"Final loss:   {res['final_loss']:.4f}")
        print("Target parameters:     cd0=0.035, oswald_e=0.68, elevon_deg=2.5")
        print(f"Calibrated parameters: cd0={res['calibrated_params']['cd0']:.4f}, "
              f"oswald_e={res['calibrated_params']['oswald_e']:.4f}, "
              f"elevon_deg={res['calibrated_params']['elevon_deg']:.2f}")
        assert res["final_loss"] < res["initial_loss"], "Calibration did not reduce error"
        print("=== Self-Test PASSED ===")
        return 0

    if not args.input:
        parser.error("--input is required unless --self-test is specified")

    data = load_flight_data(args.input)
    base_aircraft = AircraftConfig()
    if args.aircraft and args.aircraft.exists():
        base_aircraft = from_dict(AircraftConfig, json.loads(args.aircraft.read_text(encoding="utf-8")))

    param_names = [p.strip() for p in args.parameters.split(",") if p.strip()]
    res = calibrate_parameters(data, base_aircraft, param_names=param_names, maxiter=args.maxiter)

    print("=== Calibration Complete ===")
    print(f"Initial Loss: {res['initial_loss']:.4f}")
    print(f"Final Loss:   {res['final_loss']:.4f}")
    for k, v in res["calibrated_params"].items():
        print(f"  {k}: {res['initial_params'][k]} -> {v:.4f}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(res["calibrated_aircraft"], indent=2), encoding="utf-8")
        print(f"Saved calibrated aircraft to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
