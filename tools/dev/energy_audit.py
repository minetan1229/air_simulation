"""Unpowered-glide mechanical-energy audit (Issue #131).

Decomposes the mechanical energy into translational, rotational,
gravitational (and fluid added-mass) parts and separates the instantaneous
work rates (aerodynamic force, aerodynamic moment, apparent-mass
Coriolis/Munk terms) so that a rise of the body-only profile can be
classified:

* model  -- a work rate is positive (energy injected into the body),
* diagnostic -- the body-only profile omits the fluid added-mass kinetic
  energy ``1/2 * m_z * w^2`` while the dynamics carries it via
  ``m_eff_z = m + m_added_z`` and the Munk moment,
* numeric -- the rise shrinks when ``dt`` is refined.

Usage::

    python tools/energy_audit.py [--dt-max 0.01,0.005] [--duration 1.0]

The default configuration is the *trimmed glider*
(``elevon_deg=6, fold_trailing_deg=0``) launched high enough to glide
for the full duration.  The bare ``AircraftConfig()`` default
(``elevon=2, fold=15``) is a dart that trims at negative lift and is
therefore *not* a glide; it aborts the original one-off energy test
after ~0.4 s (ground crash), which made that test vacuous.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
)
from physics.flight_sim import FlightSimulator
from physics.geometry_utils import body_to_world, world_to_body


def build_sim(dt_max: float, duration_s: float) -> FlightSimulator:
    aircraft = AircraftConfig(
        mass_kg=0.0035,
        wing_area_m2=0.018,
        wingspan_m=0.22,
        fidelity_mode="improved_medium",
        dynamic_damping_enabled=True,
        apparent_inertia_enabled=True,
        lsb_hysteresis_enabled=True,
        elevon_deg=6.0,
        fold_trailing_deg=0.0,
    )
    env = EnvironmentConfig(
        wind_speed=0.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        turbulence_scale=0.0,
        ground_level_m=-50.0,
    )
    launch = LaunchConfig(
        launch_speed=6.5, launch_angle_deg=2.0, launch_height_m=20.0
    )
    settings = SimulationSettings(
        dt_min=0.001,
        dt_max=dt_max,
        record_interval_s=0.02,
        duration_s=duration_s,
        collision_enabled=False,
    )
    return FlightSimulator(aircraft, env, launch, settings)


def audit(dt_max: float, duration_s: float) -> dict:
    sim = build_sim(dt_max, duration_s)
    mass = float(sim._mass)
    g = 9.80665
    g_world = np.array([0.0, 0.0, g], dtype=np.float64)
    # Added-mass fluid kinetic energy reference (matches aero_kernel).
    mz = math.pi / 4.0 * 1.225 * 0.018 * float(sim._chord)
    rows = []
    steps = int(duration_s / 0.02)
    for _ in range(steps):
        sim.step(dt=0.02)
        state = sim.state
        core = sim._last_dynamics_core
        v = np.asarray(state.velocity_mps, dtype=np.float64)
        om = np.asarray(state.angular_velocity_radps, dtype=np.float64)
        e_trans = 0.5 * mass * float(np.dot(v, v))
        e_rot = 0.5 * float(om @ (sim._inertia @ om))
        e_grav = mass * g * (-float(state.position_m[2]))
        e_body = e_trans + e_rot + e_grav
        vb = world_to_body(np.asarray(state.quaternion), v)
        e_fluid = 0.5 * mz * float(vb[2]) ** 2
        d = sim._diagnostics()
        f_pow = m_pow = f_app = m_app = None
        if core is not None:
            fb = core["force_body"]
            mb = core["moment_body"]
            vw = core["velocity"]
            q = core["quaternion"]
            q_body = np.asarray(om, dtype=np.float64)
            g_body = world_to_body(q, g_world)
            aero_fb = np.asarray(fb, dtype=np.float64) - mass * g_body
            aero_fw = body_to_world(q, aero_fb)
            f_pow = float(np.dot(aero_fw, vw))
            m_pow = float(np.dot(mb, q_body))
            # Apparent terms as in physics.aero_kernel.compute_apparent_forces.
            u, w = float(vb[0]), float(vb[2])
            f_app_vec = np.array(
                [q_body[1] * mz * w, -q_body[0] * mz * w, 0.0]
            )
            m_app_vec = np.array([-float(vb[1]) * mz * w, u * mz * w, 0.0])
            f_app = float(np.dot(body_to_world(q, f_app_vec), vw))
            m_app = float(np.dot(m_app_vec, q_body))
        rows.append(
            dict(
                t=float(state.time_s),
                alpha=float(d["alpha_deg"]),
                speed=float(d["speed_mps"]),
                e_trans=e_trans,
                e_rot=e_rot,
                e_grav=e_grav,
                e_body=e_body,
                e_fluid=e_fluid,
                e_total=e_body + e_fluid,
                f_pow=f_pow,
                m_pow=m_pow,
                f_app=f_app,
                m_app=m_app,
            )
        )
    jumps_body = [rows[i]["e_body"] - rows[i - 1]["e_body"] for i in range(1, len(rows))]
    jumps_total = [rows[i]["e_total"] - rows[i - 1]["e_total"] for i in range(1, len(rows))]
    return dict(
        rows=rows,
        dt_max=dt_max,
        flight_time=rows[-1]["t"] if rows else 0.0,
        max_jump_body=max(jumps_body) if jumps_body else 0.0,
        max_jump_total=max(jumps_total) if jumps_total else 0.0,
        viol_body=sum(1 for j in jumps_body if j > 1.0e-4),
        viol_total=sum(1 for j in jumps_total if j > 1.0e-4),
        e_body_0=rows[0]["e_body"] if rows else 0.0,
        e_body_end=rows[-1]["e_body"] if rows else 0.0,
        e_total_0=rows[0]["e_total"] if rows else 0.0,
        e_total_end=rows[-1]["e_total"] if rows else 0.0,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dt-max", default="0.01,0.005", help="comma-separated dt_max values")
    ap.add_argument("--duration", type=float, default=1.0)
    args = ap.parse_args()
    results = []
    for dt in [float(x) for x in args.dt_max.split(",") if x.strip()]:
        r = audit(dt, args.duration)
        results.append(r)
        print(
            f"dt_max={dt:g} t={r['flight_time']:.2f}s "
            f"E_body {r['e_body_0']:.6f}->{r['e_body_end']:.6f} "
            f"max_jump_body={r['max_jump_body']:.3e} viol={r['viol_body']} "
            f"max_jump_total={r['max_jump_total']:.3e} viol={r['viol_total']}"
        )
        # Power signs over the whole glide: every collected work rate.
        fps = [x["f_pow"] for x in r["rows"] if x["f_pow"] is not None]
        mps = [x["m_pow"] for x in r["rows"] if x["m_pow"] is not None]
        print(
            f"  force power: max={max(fps):.4f} W min={min(fps):.4f} W "
            f"(aerodynamic force must dissipate)"
        )
        print(
            f"  moment power: max={max(mps):.5f} W min={min(mps):.5f} W "
            f"(static pitch moment may inject rotational energy by design)"
        )
        for row in r["rows"][:: max(1, len(r["rows"]) // 6)]:
            print(
                f"   t={row['t']:.2f} alpha={row['alpha']:.2f} "
                f"Etr={row['e_trans']:.5f} Erot={row['e_rot']:.6f} "
                f"Egrav={row['e_grav']:.5f} Efluid={row['e_fluid']:.6f} "
                f"F={row['f_pow']:.4f} M={row['m_pow']:.5f} "
                f"Fapp={row['f_app']:.4f} Mapp={row['m_app']:.5f}"
            )
    if len(results) == 2:
        print(
            f"convergence: |E_body(dt={results[0]['dt_max']:g}) - "
            f"E_body(dt={results[1]['dt_max']:g})| at end = "
            f"{abs(results[0]['e_body_end'] - results[1]['e_body_end']):.3e} J"
        )
    ok = all(r["viol_body"] == 0 and r["viol_total"] == 0 for r in results)
    print("classification: " + ("no body/total energy increase found" if ok else "energy increase present -- see power table above"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
