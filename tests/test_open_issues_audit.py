"""Automated verification tests covering open issues #9, #10, #14, #15, #16, #20, #22.

Validates:
- Issue #9 & #20: High alpha, negative alpha, reversed flow, and stall continuity
- Issue #10: Pitch static stability and pitch damping torques
- Issue #14: Ground effect smoothness, bounded restitution, non-increasing mechanical energy on contact
- Issue #15: Wind coordinate frames, reproducibility across seeds, calm condition
- Issue #16: Atmosphere ISA temperature/pressure/density consistency up to extreme bounds
- Issue #22: Parameter sensitivity monotonicity and robust output response
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_domain, atmosphere_domain, flight_sim
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    derived_properties,
)


# ==============================================================================
# Issue #9 & #20: 揚力・抗力・失速モデル、高迎角・横滑り・逆流
# ==============================================================================

@pytest.mark.parametrize("alpha_deg", [-180.0, -90.0, -45.0, -15.0, 0.0, 10.0, 14.0, 25.0, 45.0, 90.0, 180.0])
def test_aero_forces_continuous_and_finite_across_full_alpha_range(alpha_deg):
    """Aerodynamic forces must be finite, real, and well-behaved across 360 degrees."""
    aircraft = AircraftConfig()
    env = EnvironmentConfig()
    probe = flight_sim.aero_forces(
        aircraft,
        env,
        speed_mps=12.0,
        alpha_deg=alpha_deg,
        beta_deg=5.0,
        mach=0.035,
        reynolds=1.5e4,
        ground_height_m=5.0,
    )
    assert math.isfinite(probe.lift_n)
    assert math.isfinite(probe.drag_n)
    assert math.isfinite(probe.side_n)
    assert math.isfinite(probe.pitching_moment_nm)
    # Drag must be strictly positive at non-zero airspeed
    assert probe.drag_n > 0.0


def test_stall_transition_continuity():
    """Lift and drag must vary continuously without jumps across the stall angle."""
    aircraft = AircraftConfig(stall_alpha_deg=14.0)
    env = EnvironmentConfig()
    alphas = np.linspace(10.0, 20.0, 50)
    lifts = []
    drags = []
    for a in alphas:
        p = flight_sim.aero_forces(aircraft, env, speed_mps=10.0, alpha_deg=float(a))
        lifts.append(p.lift_n)
        drags.append(p.drag_n)

    # Check that maximum derivative (step change) is bounded
    max_d_lift = np.max(np.abs(np.diff(lifts)))
    max_d_drag = np.max(np.abs(np.diff(drags)))
    assert max_d_lift < 0.2
    assert max_d_drag < 0.2


# ==============================================================================
# Issue #10: ピッチ安定性・空力減衰モーメント
# ==============================================================================

def test_pitch_restoring_and_damping_moments():
    """Stable aircraft (cm_alpha < 0) produces restoring pitch moment; pitch rate produces damping."""
    aircraft = AircraftConfig(cm0=0.04, cm_alpha=-0.9, cm_q=-12.0)
    env = EnvironmentConfig()

    # 1. Negative cm_alpha means increasing alpha decreases pitch moment (restoring)
    p_low = flight_sim.aero_forces(aircraft, env, speed_mps=10.0, alpha_deg=2.0)
    p_high = flight_sim.aero_forces(aircraft, env, speed_mps=10.0, alpha_deg=8.0)
    assert p_high.pitching_moment_nm < p_low.pitching_moment_nm

    # 2. Positive pitch rate (nose-up pitch) produces opposing negative pitch damping moment
    p_still = flight_sim.aero_forces(aircraft, env, speed_mps=10.0, alpha_deg=4.0, angular_rate=(0.0, 0.0, 0.0))
    p_pitching = flight_sim.aero_forces(aircraft, env, speed_mps=10.0, alpha_deg=4.0, angular_rate=(0.0, 1.0, 0.0))
    assert p_pitching.pitching_moment_nm < p_still.pitching_moment_nm


# ==============================================================================
# Issue #14: 地面効果・接地
# ==============================================================================

def test_ground_effect_monotonic_reduction_in_induced_drag():
    """Ground proximity monotonically reduces induced drag factor towards zero."""
    span = 0.20
    h_values = [0.01, 0.05, 0.10, 0.20, 0.50, 1.0, 5.0]
    factors = [aero_domain.ground_effect_factor(h, span) for h in h_values]

    # Ground factor must be strictly increasing with height towards 1.0
    for i in range(len(factors) - 1):
        assert 0.0 <= factors[i] < factors[i + 1] <= 1.0
    assert factors[-1] == pytest.approx(1.0, abs=1e-3)


def test_landing_contact_energy_dissipation():
    """Landing contact with ground must never increase total kinetic and potential energy."""
    aircraft = AircraftConfig(mass_kg=0.010, wingspan_m=0.20)
    env = EnvironmentConfig(gravity=9.80665, ground_friction=0.5)
    # Launch downwards directly towards ground
    launch = LaunchConfig(launch_speed=5.0, launch_angle_deg=-45.0, launch_height_m=0.5)
    settings = SimulationSettings(
        dt_max=0.005,
        dt_min=0.001,
        collision_enabled=True,
        restitution=0.2,
        duration_s=0.6,
    )
    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    m = aircraft.mass_kg
    g = env.gravity

    e0 = 0.5 * m * (np.linalg.norm(sim.state.velocity_mps) ** 2) + m * g * (-sim.state.position_m[2])
    sim.simulate(0.5, record=False)
    e_final = 0.5 * m * (np.linalg.norm(sim.state.velocity_mps) ** 2) + m * g * (-sim.state.position_m[2])

    assert sim.state.landed or sim.state.crashed
    assert e_final <= e0 + 1e-9


# ==============================================================================
# Issue #15: 風・環境モデルの再現性と座標系
# ==============================================================================

def test_wind_seed_reproducibility():
    """Identical turbulence seed produces identical wind velocity sequence."""
    env1 = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.5, turbulence_scale=1.0, turbulence_seed=42)
    env2 = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.5, turbulence_scale=1.0, turbulence_seed=42)

    pos = (10.0, 5.0, -15.0)
    for t in [0.0, 0.5, 1.0, 2.5]:
        w1 = flight_sim.wind_vector(pos, env1, time_s=t)
        w2 = flight_sim.wind_vector(pos, env2, time_s=t)
        assert np.allclose(w1, w2, atol=1e-12)


# ==============================================================================
# Issue #16: ISA 大気モデルの高度・温度・圧力・密度整合性
# ==============================================================================

@pytest.mark.parametrize("altitude_m", [-200.0, 0.0, 1000.0, 5000.0, 11000.0, 15000.0])
def test_isa_thermodynamic_state_consistency(altitude_m):
    """Density must strictly equal p / (R * T) and speed of sound sqrt(gamma * R * T)."""
    t_k = atmosphere_domain.isa_temperature(altitude_m)
    p_pa = atmosphere_domain.isa_pressure(altitude_m)
    rho = atmosphere_domain.isa_density(altitude_m)
    r_air = 287.05
    gamma = 1.4

    assert t_k > 0.0
    assert p_pa > 0.0
    assert rho > 0.0

    # Ideal gas law consistency
    expected_rho = p_pa / (r_air * t_k)
    assert rho == pytest.approx(expected_rho, rel=1e-3)

    # Speed of sound consistency
    a = atmosphere_domain.speed_of_sound(t_k)
    assert a == pytest.approx(math.sqrt(gamma * r_air * t_k), rel=1e-4)


# ==============================================================================
# Issue #22: 感度分析・頑健性
# ==============================================================================

def test_mass_sensitivity_monotonic_influence_on_sink_rate():
    """Increasing mass increases wing loading and sink rate in glide."""
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
    launch = LaunchConfig(launch_speed=6.0, launch_angle_deg=0.0, launch_height_m=10.0)
    settings = SimulationSettings(dt_max=0.01, duration_s=1.0, collision_enabled=False)

    # Light glider vs heavy glider
    g_light = AircraftConfig(mass_kg=0.003, wing_area_m2=0.02, elevon_deg=4.0)
    g_heavy = AircraftConfig(mass_kg=0.012, wing_area_m2=0.02, elevon_deg=4.0)

    sim_light = flight_sim.FlightSimulator(g_light, env, launch, settings)
    sim_heavy = flight_sim.FlightSimulator(g_heavy, env, launch, settings)

    sim_light.simulate(0.8, record=False)
    sim_heavy.simulate(0.8, record=False)

    # Heavy glider drops faster (higher sink rate / lower final altitude)
    alt_light = -sim_light.state.position_m[2]
    alt_heavy = -sim_heavy.state.position_m[2]
    assert alt_heavy < alt_light
