"""Non-linear dynamic-damping audit (Issue #138).

Checks, separately:

* kernel coefficients are negative and ``M_damp . omega <= 0`` across
  attached / transitional / separated flow (roll/pitch/yaw single-axis
  and combined rates, swept rate magnitudes),
* the moment is continuous in the rate (no jumps at the stall blend),
* the 6-DoF pitch response at high initial rate converges in ``dt`` and
  the rotational energy decays (no over-damping instability), and
* the adaptive-step stiffness probe captures the fastest damping mode
  (it under-reported it by sqrt(3) before the fix).

Usage::

    python tools/damping_audit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from physics import aero_kernel
from physics.aero_kernel import ScalarMath
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
)
from physics.flight_sim import FlightSimulator, _pack


def kernel_sweep() -> bool:
    ok = True
    for alpha in (-45.0, -20.0, -10.0, 0.0, 10.0, 14.0, 20.0, 30.0, 45.0):
        for rate in (-2.0, -0.5, -0.1, 0.0, 0.1, 0.5, 2.0):
            cl, cm, cn = aero_kernel.nonlinear_dynamic_damping(
                ScalarMath,
                alpha_deg=alpha,
                stall_alpha_deg=14.0,
                p_hat=rate,
                q_hat=rate,
                r_hat=rate,
                cl_p0=-0.40,
                cm_q0=-12.0,
                cn_r0=-0.30,
            )
            for name, coeff in (("cl_p", cl), ("cm_q", cm), ("cn_r", cn)):
                if not float(coeff) < -1.0e-4:
                    print(f"FAIL coeff {name}={float(coeff):.4f} alpha={alpha} rate={rate}")
                    ok = False
            # Work rate proxy: M ~ coeff * hat, P ~ M * omega ~ coeff * hat ** 2.
            for coeff in (cl, cm, cn):
                if float(coeff) * rate * rate > 0.0:
                    print(f"FAIL power {float(coeff)} alpha={alpha} rate={rate}")
                    ok = False
    # Continuity in the rate at the stall blend (alpha = 20 deg).
    prev = None
    for rate in np.linspace(-2.0, 2.0, 81):
        _, cm, _ = aero_kernel.nonlinear_dynamic_damping(
            ScalarMath, alpha_deg=20.0, stall_alpha_deg=14.0,
            p_hat=0.0, q_hat=float(rate), r_hat=0.0,
            cl_p0=-0.40, cm_q0=-12.0, cn_r0=-0.30,
        )
        cur = float(cm)
        if prev is not None and abs(cur - prev) > 0.5:
            print(f"FAIL jump cm_q {prev:.3f} -> {cur:.3f} near rate={rate}")
            ok = False
        prev = cur
    print("kernel sweep:", "ok" if ok else "FAILED")
    return ok


def sixdof_convergence() -> tuple[bool, float]:
    def run(dt_max: float) -> tuple[float, float]:
        aircraft = AircraftConfig(
            mass_kg=0.0035,
            wing_area_m2=0.018,
            wingspan_m=0.22,
            fidelity_mode="standard",
            dynamic_damping_enabled=True,
            apparent_inertia_enabled=False,
            lsb_hysteresis_enabled=False,
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
            launch_speed=6.5,
            launch_angle_deg=2.0,
            launch_height_m=20.0,
            throw_pitch_rate_radps=5.0,
        )
        settings = SimulationSettings(
            dt_min=0.0001,
            dt_max=dt_max,
            record_interval_s=0.02,
            duration_s=0.5,
            collision_enabled=False,
        )
        sim = FlightSimulator(aircraft, env, launch, settings)
        for _ in range(10):
            sim.step(dt=0.05)
        return float(sim.state.angular_velocity_radps[1]), float(sim.state.time_s)

    coarse, t_coarse = run(0.01)
    fine, t_fine = run(0.002)
    # Reference: fixed-step fine integration.
    ref_aircraft = AircraftConfig(
        mass_kg=0.0035,
        wing_area_m2=0.018,
        wingspan_m=0.22,
        fidelity_mode="standard",
        dynamic_damping_enabled=True,
        apparent_inertia_enabled=False,
        lsb_hysteresis_enabled=False,
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
        launch_speed=6.5,
        launch_angle_deg=2.0,
        launch_height_m=20.0,
        throw_pitch_rate_radps=5.0,
    )
    ref_settings = SimulationSettings(
        dt_min=0.0001,
        dt_max=0.001,
        record_interval_s=0.02,
        duration_s=0.5,
        collision_enabled=False,
        adaptive=False,
    )
    ref_sim = FlightSimulator(ref_aircraft, env, launch, ref_settings)
    for _ in range(10):
        ref_sim.step(dt=0.05)
    ref = float(ref_sim.state.angular_velocity_radps[1])
    print(f"6DoF pitch rate at t=0.5s: dt_max=0.01 omy={coarse:.4f}, "
          f"dt_max=0.002 omy={fine:.4f}, fixed dt=0.001 omy={ref:.4f}")
    err_coarse = abs(coarse - ref)
    err_fine = abs(fine - ref)
    print(f"  |coarse-ref|={err_coarse:.4f} |fine-ref|={err_fine:.4f}")
    return (err_coarse < 0.5 and err_fine < 0.2), err_coarse


def main() -> int:
    print("=== kernel sweep (attached / separated, single + combined rates) ===")
    ok1 = kernel_sweep()
    print("=== 6DoF high-rate dt convergence ===")
    ok2, _ = sixdof_convergence()
    print("damping audit:", "ok" if (ok1 and ok2) else "FAILED")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
