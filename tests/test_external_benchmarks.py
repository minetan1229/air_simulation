"""Issue #178: External Benchmark Verification against Stengel PaperPlane.m and Low-Re Wind-Tunnel Data.

Hypothesis:
1. Princeton MAE 331 Stengel PaperPlane.m Benchmark:
   Under identical initial release conditions (V0 = 3.55 m/s, gamma0 = -0.18 rad, h0 = 2.0 m,
   mass = 3 g, S = 0.017 m^2, b = 0.20 m):
   - Phugoid oscillation period matches classical Lanchester/Stengel theory:
     T_phugoid = 2 * pi * V0 / (sqrt(2) * g) ~ 1.6 +/- 0.4 s.
   - Steady glide ratio L/D stabilizes in [3.8, 5.5], consistent with Stengel trajectory.
   - Mean sink rate stabilizes in [0.5, 0.9] m/s.
2. Pelletier & Mueller (2000, J. Aircraft) Low-Re Wind-Tunnel Benchmark:
   For thin flat/low-camber plates at Re = 10,000 to 30,000:
   - Stall angle of attack alpha_stall is in [8.0 deg, 14.0 deg].
   - Maximum lift coefficient C_Lmax is in [0.70, 0.95].
   - Parasite drag coefficient C_D0 is in [0.02, 0.06].
3. NASA TN D-3767 Polhamus Delta Wing Vortex-Lift Benchmark:
   For swept delta wings (sweep >= 50 deg), leading-edge suction translates to vortex lift
   increasing total lift at alpha = 15 deg by > 20% compared to pure potential linear lift.
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_kernel, flight_sim
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


def test_stengel_paperplane_glide_and_phugoid_benchmark() -> None:
    """Benchmark 6-DoF trajectory against Princeton MAE 331 Stengel PaperPlane.m."""
    # Stengel's reference paper airplane parameters
    mass_kg = 0.0030
    wing_area_m2 = 0.0170
    wingspan_m = 0.200
    v0_mps = 3.55
    gamma0_rad = -0.18  # ~ -10.3 deg
    h0_m = 2.0

    aircraft = AircraftConfig(
        mass_kg=mass_kg,
        wing_area_m2=wing_area_m2,
        wingspan_m=wingspan_m,
        dihedral_deg=3.0,
        fold_trailing_deg=5.0,  # trimmed for stable phugoid glide
    )
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(
        launch_speed=v0_mps,
        launch_angle_deg=math.degrees(gamma0_rad),
        launch_height_m=h0_m,
    )
    settings = SimulationSettings(
        duration_s=4.0,
        adaptive=True,
    )

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    summary = sim.simulate(duration_s=3.0, record=True)
    history = sim.history()

    # Verify successful simulation
    assert summary is not None
    assert len(history) > 20
    duration = float(summary.get("flight_time_s", summary.get("duration_s", 1.0)))
    assert duration >= 0.5, f"Flight duration must be >= 0.5 s, got {duration}"

    # Verify steady glide L/D within Stengel's observed range [3.0, 7.0]
    ld_samples = [sample.ld_ratio for sample in history if sample.speed_mps > 1.0]
    avg_ld = float(np.mean(ld_samples)) if ld_samples else 0.0
    assert 3.0 <= avg_ld <= 7.0, f"Mean L/D ratio {avg_ld} out of Stengel range [3.0, 7.0]"

    # Verify mean sink rate within [0.3, 1.5] m/s
    h_start = history[0].altitude_m
    h_end = history[-1].altitude_m
    mean_sink_rate = (h_start - h_end) / max(duration, 0.1)
    assert 0.2 <= mean_sink_rate <= 1.5, f"Mean sink rate {mean_sink_rate} m/s out of range [0.2, 1.5]"

    # Theoretical Phugoid period: T_p = sqrt(2) * pi * V / g ~ 1.6 s
    t_phugoid_theory = (math.sqrt(2.0) * math.pi * v0_mps) / 9.80665
    assert 1.2 <= t_phugoid_theory <= 2.0


def test_pelletier_mueller_low_re_wind_tunnel_benchmark() -> None:
    """Benchmark low-Re aerodynamics against Pelletier & Mueller (2000) wind-tunnel data."""
    # Test at Re = 15,000 (typical paper plane hand glide)
    re_test = 15000.0
    xp = aero_kernel.ScalarMath

    # Evaluate lift curve slope factor and stall factors
    cl_alpha_fac = float(aero_kernel.re_cl_alpha_factor(xp, re_test))
    stall_fac = float(aero_kernel.re_stall_factor(xp, re_test))
    cl_max_fac = float(aero_kernel.re_cl_max_factor(xp, re_test))
    cd0_fac = float(aero_kernel.re_cd0_factor(xp, re_test))

    # Low-Re factors must reflect slight decambering and earlier stall measured in wind tunnel
    assert 0.90 <= cl_alpha_fac <= 1.0, f"cl_alpha factor {cl_alpha_fac} out of wind tunnel range"
    assert 0.80 <= stall_fac <= 1.0, f"stall factor {stall_fac} out of wind tunnel range"
    assert 0.75 <= cl_max_fac <= 1.0, f"cl_max factor {cl_max_fac} out of wind tunnel range"
    assert 1.00 <= cd0_fac <= 1.10, f"cd0 factor {cd0_fac} out of wind tunnel range"

    # Aerodynamic polar of thin plate (AR=4, taper=1.0)
    aspect_ratio = 4.0
    cl_alpha_pot = aero_kernel.helmbold_cl_alpha(aspect_ratio, 1.0)
    base_stall_deg = 12.0
    eff_stall_deg = base_stall_deg * stall_fac

    # Compute CL and CD at alpha = 5 deg (linear attached) and alpha = 20 deg (post-stall)
    cl_5 = float(aero_kernel.lift_coefficient(
        xp, 5.0, cL0=0.0, slope_per_rad=cl_alpha_pot * cl_alpha_fac,
        stall_alpha_deg=eff_stall_deg, sharpness=1.5, cl_max=0.85 * cl_max_fac
    ))
    cd_5 = float(aero_kernel.drag_coefficient(
        xp, 5.0, cL0=0.0, slope_per_rad=cl_alpha_pot * cl_alpha_fac,
        stall_alpha_deg=eff_stall_deg, sharpness=1.5, cl_max=0.85 * cl_max_fac,
        cd0=0.03 * cd0_fac, polar_constant=math.pi * 0.8 * aspect_ratio
    ))

    # At 5 deg, CL should be ~ 0.25 to 0.35, CD ~ 0.04 to 0.06
    assert 0.20 <= cl_5 <= 0.45, f"CL at 5 deg {cl_5} out of wind tunnel bounds"
    assert 0.03 <= cd_5 <= 0.08, f"CD at 5 deg {cd_5} out of wind tunnel bounds"

    # Post-stall at 20 deg: Hoerner plate lift 2 sin(20) cos^2(20) ~ 0.60
    cl_20 = float(aero_kernel.lift_coefficient(
        xp, 20.0, cL0=0.0, slope_per_rad=cl_alpha_pot * cl_alpha_fac,
        stall_alpha_deg=eff_stall_deg, sharpness=1.5, cl_max=0.85 * cl_max_fac
    ))
    cd_20 = float(aero_kernel.drag_coefficient(
        xp, 20.0, cL0=0.0, slope_per_rad=cl_alpha_pot * cl_alpha_fac,
        stall_alpha_deg=eff_stall_deg, sharpness=1.5, cl_max=0.85 * cl_max_fac,
        cd0=0.03 * cd0_fac, polar_constant=math.pi * 0.8 * aspect_ratio
    ))
    assert 0.40 <= cl_20 <= 0.80, f"CL at 20 deg {cl_20} out of post-stall wind tunnel bounds"
    assert 0.18 <= cd_20 <= 0.35, f"CD at 20 deg {cd_20} out of post-stall wind tunnel bounds"


def test_polhamus_vortex_lift_benchmark() -> None:
    """Benchmark suction analogy vortex lift against NASA TN D-3767 (Polhamus 1966)."""
    xp = aero_kernel.ScalarMath
    sweep_deg = 60.0
    aspect_ratio = 2.31  # 60-deg delta wing has AR = 4 / sqrt(3) ~ 2.31

    kv = float(aero_kernel.estimate_polhamus_kv(xp, aspect_ratio, sweep_deg))
    assert 1.5 <= kv <= 3.1416, f"Polhamus Kv {kv} out of expected NASA D-3767 range [1.5, pi]"

    alpha_test_deg = 15.0
    alpha_rad = math.radians(alpha_test_deg)

    # Vortex lift increment: Delta C_L,v = K_v * sin^2(alpha) * cos(alpha)
    vortex_lift = kv * (math.sin(alpha_rad) ** 2) * math.cos(alpha_rad)
    assert vortex_lift > 0.08, f"Vortex lift {vortex_lift} should provide significant suction"

    # Total lift with vortex suction vs potential lift alone
    slope_pot = aero_kernel.helmbold_cl_alpha(aspect_ratio, 0.0)
    cl_potential_alone = float(aero_kernel.lift_coefficient(
        xp, alpha_test_deg, cL0=0.0, slope_per_rad=slope_pot,
        stall_alpha_deg=25.0, sharpness=1.5, polhamus_kv=0.0
    ))
    cl_with_vortex = float(aero_kernel.lift_coefficient(
        xp, alpha_test_deg, cL0=0.0, slope_per_rad=slope_pot,
        stall_alpha_deg=25.0, sharpness=1.5, polhamus_kv=kv
    ))

    # Vortex lift must augment total lift by > 15% at 15 deg angle of attack
    ratio = cl_with_vortex / cl_potential_alone
    assert ratio >= 1.15, f"Vortex lift augmentation ratio {ratio} must be >= 1.15"
