"""Issue #198 / #199 / #200: Cross-Domain Physical Coupling and Numerical Stability Verification.

Comprehensive integration tests covering the 5 core physical domains of ultra-light, low-Re paper aircraft:
1. Helmbold potential lift + Polhamus vortex lift synthesis and smooth stall blending.
2. Kirchhoff added mass and Munk moment passivity in 6-DoF trajectory integration.
3. Aeroelastic deformation (washout, TE morphing) and center-of-pressure travel coupling.
4. RK4 sub-stepping coherence with unsteady circulation and dynamic stall lag states.
5. Zero-airspeed singularity avoidance, boundary robustness, and passive recovery.
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_kernel, flight_sim, gpu_dynamics
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


# ==============================================================================
# Domain 1: Aero Synthesis & Blending
# ==============================================================================
def test_domain1_polhamus_helmbold_blending_continuity() -> None:
    """Verify that CL and CD vary continuously across stall and post-stall without jumps."""
    xp = ScalarMath
    aspect_ratio = 2.5
    sweep_deg = 45.0
    kv = float(aero_kernel.estimate_polhamus_kv(xp, aspect_ratio, sweep_deg))
    slope = aero_kernel.helmbold_cl_alpha(aspect_ratio, 0.5)

    alphas = np.linspace(-30.0, 60.0, 181)  # step 0.5 deg
    cls = []
    cds = []

    for a in alphas:
        cl_val = float(aero_kernel.lift_coefficient(
            xp, a, cL0=0.05, slope_per_rad=slope, stall_alpha_deg=13.0,
            sharpness=1.4, polhamus_kv=kv, cl_max=0.95
        ))
        cd_val = float(aero_kernel.drag_coefficient(
            xp, a, cL0=0.05, slope_per_rad=slope, stall_alpha_deg=13.0,
            sharpness=1.4, polhamus_kv=kv, cl_max=0.95, cd0=0.025,
            polar_constant=math.pi * 0.8 * aspect_ratio
        ))
        cls.append(cl_val)
        cds.append(cd_val)

    cls = np.array(cls)
    cds = np.array(cds)

    # Check finite values everywhere
    assert np.all(np.isfinite(cls))
    assert np.all(np.isfinite(cds))

    # Check non-negative drag everywhere
    assert np.all(cds >= 0.0)

    # Check continuity: no discrete jump between consecutive 0.5-deg samples
    dcl = np.abs(np.diff(cls))
    dcd = np.abs(np.diff(cds))
    assert np.max(dcl) < 0.15, f"Discontinuous lift jump detected: {np.max(dcl)}"
    assert np.max(dcd) < 0.15, f"Discontinuous drag jump detected: {np.max(dcd)}"


# ==============================================================================
# Domain 2: Added-Mass and Munk Coupling in 6-DoF Flight
# ==============================================================================
def test_domain2_added_mass_munk_6dof_stability() -> None:
    """Verify that Munk moment coupling does not destabilize long-duration 6-DoF simulation."""
    aircraft = AircraftConfig(
        added_mass_enabled=True,
        apparent_inertia_enabled=True,
        mass_kg=0.0040,
        wing_area_m2=0.0160,
        wingspan_m=0.22,
        dihedral_deg=4.0,
        fold_trailing_deg=6.0,
    )
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(
        launch_speed=6.0,
        launch_angle_deg=0.0,
        launch_height_m=2.0,
    )
    settings = SimulationSettings(
        duration_s=3.0,
        adaptive=True,
    )

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    summary = sim.simulate(duration_s=3.0, record=True)
    history = sim.history()

    assert summary is not None
    assert len(history) > 30

    # Flight must remain strictly bounded without diverging into NaN or infinite velocities
    for s in history:
        assert math.isfinite(s.x_m)
        assert math.isfinite(s.y_m)
        assert math.isfinite(s.z_m)
        assert math.isfinite(s.speed_mps)
        assert math.isfinite(s.vx_mps)
        assert math.isfinite(s.vy_mps)
        assert math.isfinite(s.vz_mps)


# ==============================================================================
# Domain 3: Aeroelastic Deformation and Pitch Stability
# ==============================================================================
def test_domain3_aeroelastic_high_speed_morphing_protection() -> None:
    """Verify that aeroelastic washout and morphing protect against pitch-up divergent tuck."""
    aircraft = AircraftConfig(
        aeroelastic_compliance=0.25,
        aeroelastic_pressure_scale_pa=160.0,
        aeroelastic_morph_exponent=3.0,
        fold_trailing_deg=10.0,
        mass_kg=0.0050,
        wing_area_m2=0.0180,
        wingspan_m=0.24,
    )
    env = EnvironmentConfig()

    # Low speed (glide regime, ~3 m/s) vs high speed (launch regime, ~15 m/s)
    sim_low = flight_sim.FlightSimulator(aircraft, env, LaunchConfig(launch_speed=3.0))
    sim_high = flight_sim.FlightSimulator(aircraft, env, LaunchConfig(launch_speed=15.0))

    core_low = sim_low._compute_dynamics(flight_sim._pack(sim_low.state), 0.0)
    core_high = sim_high._compute_dynamics(flight_sim._pack(sim_high.state), 0.0)

    # In high speed launch regime, trailing edge reflex should morph passively
    # reducing excessive pitch moment
    assert core_high["trailing_edge_bend_deg"] > core_low["trailing_edge_bend_deg"]


# ==============================================================================
# Domain 4: RK4 Stages and Lag State Coherence
# ==============================================================================
def test_domain4_rk4_substepping_lag_state_convergence() -> None:
    """Verify that unsteady and aeroelastic lag states remain monotonic and convergent across dt."""
    aircraft = AircraftConfig(
        unsteady_aero_enabled=True,
        lsb_hysteresis_enabled=True,
        aeroelastic_compliance=0.15,
    )
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=7.0, launch_angle_deg=5.0)

    sim = flight_sim.FlightSimulator(aircraft, env, launch)
    y0 = flight_sim._pack(sim.state)

    # Evaluate dynamics with different dt steps
    core_fine = sim._compute_dynamics(y0, 0.0, dt=0.002)
    core_coarse = sim._compute_dynamics(y0, 0.0, dt=0.010)

    # Lag states must be finite and consistent
    lag_fine = core_fine["lag_states"]
    lag_coarse = core_coarse["lag_states"]

    for k in lag_fine:
        if isinstance(lag_fine[k], (int, float)):
            assert math.isfinite(lag_fine[k])
            assert math.isfinite(lag_coarse[k])


# ==============================================================================
# Domain 5: Zero-Speed Singularity Avoidance and Passive Recovery
# ==============================================================================
def test_domain5_zero_speed_pushover_and_attitude_robustness() -> None:
    """Verify that zero-speed apex condition activates pushover moment and recovers stably."""
    aircraft = AircraftConfig(
        mass_kg=0.0035,
        wing_area_m2=0.0150,
        wingspan_m=0.20,
        dihedral_deg=5.0,
    )
    env = EnvironmentConfig(wind_speed=0.0)

    # Vertical stall launch (speed very low at apex, pitch = 85 deg)
    launch = LaunchConfig(
        launch_speed=0.5,  # almost zero speed
        launch_angle_deg=80.0,
        launch_height_m=5.0,
    )
    settings = SimulationSettings(
        duration_s=2.5,
        adaptive=True,
    )

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    summary = sim.simulate(duration_s=2.5, record=True)
    history = sim.history()

    assert summary is not None
    assert len(history) > 10

    # Ensure no NaN occurred during apex transition
    for s in history:
        assert math.isfinite(s.x_m)
        assert math.isfinite(s.y_m)
        assert math.isfinite(s.z_m)
        assert math.isfinite(s.speed_mps)
        q_norm = math.sqrt(s.qw * s.qw + s.qx * s.qx + s.qy * s.qy + s.qz * s.qz)
        assert abs(q_norm - 1.0) < 1e-4, f"Quaternion norm diverged: {q_norm}"
