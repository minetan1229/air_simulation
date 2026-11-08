"""Diagnostic harness: compare the vectorised kernel with the scalar domain.

Run directly (``python tools/check_aero_kernel_parity.py``) to print the
largest disagreement for every coefficient over a sweep of states.  The same
sweep is asserted in ``tests/test_aero_kernel_parity.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from physics import aero_domain, aero_kernel
from physics.config_schema import AircraftConfig, EnvironmentConfig, derived_properties

ALPHAS = [-180.0, -120.0, -90.0, -60.0, -45.0, -41.0, -30.0, -20.0, -14.0,
          -5.0, 0.0, 2.0, 5.0, 14.0, 20.0, 30.0, 41.0, 45.0, 60.0, 90.0, 120.0, 180.0]
BETAS = [-90.0, -30.0, -5.0, 0.0, 5.0, 30.0, 90.0]
SPEEDS = [0.0, 0.5, 2.0, 2.9, 3.1, 8.0, 18.0, 30.0]


def _configs() -> list[tuple[AircraftConfig, EnvironmentConfig]]:
    base = AircraftConfig()
    env = EnvironmentConfig()
    out = [(base, env)]
    out.append((AircraftConfig(elevon_deg=6.0, cm0=0.02, cm_alpha=-0.8), env))
    out.append((AircraftConfig(aileron_deg=4.0, rudder_deg=-3.0, dihedral_deg=6.0), env))
    out.append((AircraftConfig(polhamus_kv=0.4, polhamus_kp=2.0), env))
    out.append((AircraftConfig(stall_alpha_deg=9.0, cl_max=0.7, taper_ratio=0.2), env))
    out.append((AircraftConfig(step_trip_ratio=0.05, paper_stiffness=0.8), env))
    out.append((base, EnvironmentConfig(lift_scale=0.8, drag_scale=1.2)))
    out.append((base, EnvironmentConfig(ground_effect_enabled=False, compressibility_enabled=False)))
    return out


def main() -> int:
    worst: dict[str, float] = {}
    checked = 0
    for cfg, env in _configs():
        params = aero_kernel.params_from_config(cfg, env)
        geom = derived_properties(cfg)
        chord = float(geom["ref_length_m"])
        for alpha in ALPHAS:
            for beta in BETAS:
                for speed in SPEEDS:
                    air = aero_domain.atmosphere_state(env, speed, reference_chord_m=chord)
                    reynolds = speed * chord / float(air["kinematic_viscosity_m2s"])
                    mach = speed / float(air["sound_speed_mps"])
                    scalar = aero_domain.evaluate_aero(
                        cfg, env, speed, alpha, beta, mach, reynolds, 1.0, (0.3, -0.2, 0.15)
                    )
                    vector = aero_kernel.evaluate_coefficients(
                        np,
                        params,
                        alpha_deg=alpha,
                        beta_deg=beta,
                        p_rate=0.3,
                        q_rate=-0.2,
                        r_rate=0.15,
                        speed=speed,
                        reynolds=reynolds,
                        mach=mach,
                        ground_height_m=1.0,
                        density=float(air["density_kgm3"]),
                    )
                    for key, value in (
                        ("cl", scalar.cl),
                        ("cd", scalar.cd),
                        ("cy", scalar.cy),
                        ("cm", scalar.cm),
                        ("cl_roll", scalar.cl_roll),
                        ("cm_roll", scalar.cm_roll),
                        ("cn_roll", scalar.cn_roll),
                        ("lift_n", scalar.lift_n),
                        ("drag_n", scalar.drag_n),
                        ("side_n", scalar.side_n),
                        ("pitching_nm", scalar.pitching_moment_nm),
                        ("rolling_nm", scalar.rolling_moment_nm),
                        ("yawing_nm", scalar.yawing_moment_nm),
                        ("stall_margin", scalar.stall_margin),
                    ):
                        got = float(np.asarray(vector[key]))
                        scale = max(abs(float(value)), 1.0e-9)
                        error = abs(got - float(value)) / scale
                        if error > worst.get(key, 0.0) or key not in worst:
                            worst[key] = error
                            if error > 1.0e-6:
                                print(
                                    f"  {key}: rel={error:.3e} domain={float(value):.8g} "
                                    f"kernel={got:.8g} alpha={alpha} beta={beta} v={speed}"
                                )
                    checked += 1
    print(f"checked {checked} states")
    for key, value in sorted(worst.items(), key=lambda item: -item[1]):
        print(f"{key:24s} max relative difference = {value:.3e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
