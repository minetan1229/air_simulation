"""Diagnose the launch-state aerodynamic difference between the two paths."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from physics import aero_domain, flight_sim, gpu_dynamics
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig


def main() -> int:
    aircraft = AircraftConfig()
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)

    state = flight_sim.initial_state(launch, env, aircraft)
    q = np.asarray(state.quaternion, dtype=np.float64)
    v = np.asarray(state.velocity_mps, dtype=np.float64)

    core = flight_sim.FlightSimulator(aircraft, env, launch, flight_sim.SimulationSettings())._compute_dynamics(
        flight_sim._pack(state), 0.0
    )
    aero = core["aero"]
    print("6dof : alpha=%.4f beta=%.4f speed=%.4f cl=%.6f cd=%.6f" % (
        core["alpha_deg"], core["beta_deg"], core["speed"], aero.cl, aero.cd))
    print("6dof : force_body=%s  g_load=%.4f" % (np.round(core["force_body"], 8), core["g_load"]))
    print("6dof : q_dyn=%.6f  mach=%.6f re=%.4f" % (
        core["aero"].dynamic_pressure_pa, core["mach"], core["reynolds"]))

    params = gpu_dynamics.build_params(np, [aircraft], env)
    y = np.concatenate(
        (
            np.asarray(state.position_m, dtype=np.float64),
            v,
            q,
            np.asarray(state.angular_velocity_radps, dtype=np.float64),
        )
    )[np.newaxis, :]
    gpu = gpu_dynamics._evaluate_state(np, params, y, gpu_dynamics.wind_batch(np, params, y[:, 0:3], 0.0))
    print("batch: alpha=%.4f beta=%.4f speed=%.4f" % (
        float(np.asarray(gpu["alpha"])[0]), float(np.asarray(gpu["beta"])[0]),
        float(np.asarray(gpu["speed"])[0])))
    print("batch: g_load=%.4f  q_dyn=%.6f" % (
        float(np.asarray(gpu["g_load"])[0]), float(np.asarray(gpu["dynamic_pressure"])[0])))

    print("state  : position=%s" % (np.round(np.asarray(state.position_m, dtype=float), 6),))
    print("state  : velocity=%s" % (np.round(v, 6),))
    print("state  : quaternion=%s" % (np.round(q, 6),))
    rot = gpu_dynamics.quaternion_to_matrix_batch(np, q[np.newaxis, :])[0]
    print("rot    :\n%s" % np.round(rot, 6))
    print("body_rel(6dof-style) = %s" % np.round(rot.T @ v, 6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
