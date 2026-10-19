"""Compare the vectorised 6-DoF batch with the reference 6-DoF path.

The GPU batch used to integrate a different model (3-DoF point mass, explicit
Euler, artificial clamps) and reported a wing-root stress 1261x away from the
6-DoF path.  This tool runs *both* on the same launch and prints the
comparison, so the remaining difference is visible instead of hidden.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from physics import flight_sim, gpu_dynamics
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig


def _state_vector(launch, env, aircraft) -> np.ndarray:
    state = flight_sim.initial_state(launch, env, aircraft)
    return np.concatenate(
        (
            np.asarray(state.position_m, dtype=np.float64),
            np.asarray(state.velocity_mps, dtype=np.float64),
            np.asarray(state.quaternion, dtype=np.float64),
            np.asarray(state.angular_velocity_radps, dtype=np.float64),
        )
    )


def run_case(name: str, aircraft, env, launch, duration: float = 6.0) -> None:
    settings = flight_sim.SimulationSettings(
        dt_max=2.0e-2,
        dt_min=1.0e-5,
        max_substeps=64,
        duration_s=duration,
        record_interval_s=2.0e-2,
        time_scale=1.0,
        adaptive=True,
        collision_enabled=True,
        restitution=0.15,
        ring_buffer_capacity=4096,
    )
    simulator = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    simulator.simulate(duration_s=duration, record=True)
    samples = simulator.history()
    reference = flight_sim.summarize_flight(simulator)

    params = gpu_dynamics.build_params(np, [aircraft], env)
    initial = _state_vector(launch, env, aircraft)[np.newaxis, :]
    batch = gpu_dynamics.simulate_batch(np, params, initial, duration, 2.0e-2)

    ref_stress = float(reference["max_stress_pa"])
    gpu_stress = float(np.asarray(batch["peak_stress_pa"])[0])
    ref_index = float(reference["max_stress_index"])
    gpu_index = float(np.asarray(batch["peak_stress_index"])[0])
    ratio = gpu_stress / ref_stress if ref_stress > 0.0 else float("nan")
    ref_g = max(float(s.g_load) for s in samples) if samples else float("nan")
    gpu_g = float(np.asarray(batch["peak_load_factor"])[0])
    peak_sample = max(samples, key=lambda s: float(s.max_stress_pa)) if samples else None
    peak_info = (
        f"t={float(peak_sample.time_s):.3f}s g={float(peak_sample.g_load):.3f} "
        f"alt={float(peak_sample.altitude_m):.3f} landed={bool(peak_sample.landed)}"
        if peak_sample is not None
        else "n/a"
    )
    batch_status = (
        "crashed"
        if bool(np.asarray(batch["crashed"])[0])
        else ("landed" if bool(np.asarray(batch["landed"])[0]) else "flying")
    )
    print(
        f"[{name}]\n"
        f"  status        6dof={reference['status']:<10s} batch={batch_status:<10s}\n"
        f"  max_stress_pa 6dof={ref_stress:12.4f}  batch={gpu_stress:12.4f}  ratio={ratio:8.3f}\n"
        f"  stress_index  6dof={ref_index:12.6f}  batch={gpu_index:12.6f}\n"
        f"  peak_g_load   6dof={ref_g:12.4f}  batch={gpu_g:12.4f}\n"
        f"  6dof peak at  {peak_info}\n"
        f"  distance_m    6dof={float(reference['distance_m']):12.4f}  batch={float(np.asarray(batch['distance_m'])[0]):12.4f}\n"
        f"  flight_time   6dof={float(reference['flight_time_s']):12.4f}  batch={float(np.asarray(batch['flight_time_s'])[0]):12.4f}"
    )


def main() -> int:
    env = EnvironmentConfig()
    cases = [
        ("default", AircraftConfig(), LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)),
        ("fast", AircraftConfig(), LaunchConfig(launch_speed=12.0, launch_angle_deg=5.0, launch_height_m=1.5)),
        ("elevon", AircraftConfig(elevon_deg=5.0), LaunchConfig(launch_speed=9.0, launch_angle_deg=2.0, launch_height_m=1.2)),
        ("dihedral", AircraftConfig(dihedral_deg=8.0), LaunchConfig(launch_speed=9.0, launch_angle_deg=0.0, launch_height_m=1.2)),
        ("windy", AircraftConfig(), LaunchConfig(launch_speed=9.0, launch_angle_deg=0.0, launch_height_m=1.2)),
    ]
    for name, aircraft, launch in cases:
        case_env = env
        if name == "windy":
            case_env = EnvironmentConfig(wind_speed=4.0, wind_direction_deg=45.0)
        run_case(name, aircraft, case_env, launch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
