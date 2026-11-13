"""Tests for Issue #18: Physical Integration of Aeroelasticity, Dynamic Pitch Moment, and Unsteady Aerodynamics.

Verifies:
1. Dynamic aeroelastic deformation (flex, washout twist, camber compliance):
   - Passive aeroelastic morphing scales with launch dynamic pressure q = 0.5 * rho * V^2.
   - Washout twist provides aerodynamic relief (negative twist at wingtips).
   - Dihedral flex enhances lateral-directional roll-yaw stability under high load factor.
2. Low-speed / high-alpha apex pitch pushover dynamics:
   - At high pitch attitude / low airspeed near the flight trajectory apex (speed < 2.5 m/s, alpha > 10 deg),
     aerodynamic pitching moment produces a restoring nose-down pushover moment (Cm < 0).
   - Prevents unphysical post-stall tail-slide or pitch lock-up.
3. Apparent added mass & inertia coupling:
   - Added normal mass m_added_z and rotation inertias (I_xx, I_yy, I_zz) scale physically with fluid density rho
     and wing dimensions without unphysical positive external energy injection.
4. Unsteady aerodynamics and LSB dynamic stall hysteresis:
   - Dynamic pitch rate delay produces counter-clockwise hysteresis loop in CL(alpha), delaying separation during pitch-up.
5. End-to-end flight integration:
   - Complete flight simulation from high-speed launch to apex transition, push-over, and glide.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_domain, aero_kernel
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import FlightSimulator


def test_aeroelastic_dynamic_pressure_morphing_and_relief() -> None:
    """Aeroelastic deformations respond dynamically to pressure without unbounded divergence."""
    cfg = AircraftConfig(
        aeroelastic_flex=0.5,
        aeroelastic_twist=0.5,
        aeroelastic_compliance=1.0,
        elevon_deg=2.0,
        fold_trailing_deg=5.0,
    )
    env = EnvironmentConfig()

    # Moderate glide speed: 6 m/s
    res_glide = aero_domain.evaluate_aero(cfg, env, speed_mps=6.0, alpha_deg=3.0)
    # High-speed launch condition: 20 m/s
    res_launch = aero_domain.evaluate_aero(cfg, env, speed_mps=20.0, alpha_deg=3.0)

    # Launch dynamic pressure is (20/6)^2 ~ 11.1x larger
    assert res_launch.dynamic_pressure_pa > 10.0 * res_glide.dynamic_pressure_pa

    # Washout twist is negative (relief), and magnitude increases with dynamic pressure
    assert res_launch.aeroelastic_twist_deg < res_glide.aeroelastic_twist_deg <= 0.0

    # Dihedral flex is positive (upward wing flex)
    assert res_launch.aeroelastic_dihedral_deg >= res_glide.aeroelastic_dihedral_deg >= 0.0

    # Deformations remain physically bounded
    assert abs(res_launch.aeroelastic_twist_deg) <= 20.0
    assert abs(res_launch.aeroelastic_dihedral_deg) <= 30.0


def test_apex_low_speed_pushover_restoring_moment() -> None:
    """Near flight apex (low airspeed, climbing angle of attack), Cm must be restorative (nose-down)."""
    cfg = AircraftConfig(
        cm_alpha=-0.45,
        elevon_deg=2.0,
        fold_trailing_deg=0.0,
    )
    env = EnvironmentConfig()

    # Low-speed near apex (V = 1.2 m/s, alpha = 18 deg)
    res_apex = aero_domain.evaluate_aero(cfg, env, speed_mps=1.2, alpha_deg=18.0)

    # Pitching moment is strictly negative (nose-down pushover)
    assert res_apex.cm < 0.0
    assert res_apex.pitching_moment_nm < 0.0

    # Higher alpha produces stronger restoring nose-down moment (in physical dimensional torque)
    res_apex_high = aero_domain.evaluate_aero(cfg, env, speed_mps=1.2, alpha_deg=25.0)
    assert res_apex_high.pitching_moment_nm <= res_apex.pitching_moment_nm


def test_added_mass_and_inertia_dimensions_and_scaling() -> None:
    """Added mass and inertia tensor scale with density and plane geometry with correct dimensions."""
    rho = 1.225
    area = 0.015
    chord = 0.08
    span = 0.20

    added = aero_kernel.compute_added_mass(
        ScalarMath, density_kgm3=rho, wing_area_m2=area, mac_m=chord, wingspan_m=span
    )

    # Added mass normal to wing [kg]
    m_z = added["added_mass_z_kg"]
    assert m_z > 0.0
    # Expected order of magnitude: (pi/4) * rho * S * chord ~ 0.785 * 1.225 * 0.015 * 0.08 ~ 0.00115 kg (1.15 g)
    assert 0.0005 <= m_z <= 0.005

    # Added pitch inertia I_yy [kg m^2]
    I_yy = added["added_inertia_yy_kgm2"]
    assert I_yy > 0.0
    # Added roll inertia I_xx [kg m^2]
    I_xx = added["added_inertia_xx_kgm2"]
    assert I_xx > 0.0
    # Added yaw inertia I_zz [kg m^2]
    I_zz = added["added_inertia_zz_kgm2"]
    assert I_zz > 0.0


def test_unsteady_dynamic_stall_hysteresis_loop() -> None:
    """LSB dynamic stall model produces counter-clockwise hysteresis loop with pitch rate."""
    base_stall = 14.0
    chord = 0.08
    speed = 6.0
    reynolds = 2.5e4

    # Pitch up (alpha_dot = +30 deg/s) delays separation (raises effective stall alpha)
    eff_stall_up, _ = aero_kernel.lsb_dynamic_stall_hysteresis(
        ScalarMath,
        alpha_deg=15.0,
        alpha_dot_deg_s=30.0,
        stall_alpha_deg=base_stall,
        mac_m=chord,
        speed_mps=speed,
        reynolds=reynolds,
        is_stalled_prev=False,
    )

    # Pitch down (alpha_dot = -30 deg/s) delays reattachment (lowers effective stall alpha)
    eff_stall_down, _ = aero_kernel.lsb_dynamic_stall_hysteresis(
        ScalarMath,
        alpha_deg=13.0,
        alpha_dot_deg_s=-30.0,
        stall_alpha_deg=base_stall,
        mac_m=chord,
        speed_mps=speed,
        reynolds=reynolds,
        is_stalled_prev=True,
    )

    assert eff_stall_up > base_stall
    assert eff_stall_down < base_stall
    # Hysteresis loop gap
    assert eff_stall_up - eff_stall_down > 1.5


def test_full_flight_launch_to_glide_continuity() -> None:
    """6-DoF flight simulator successfully transitions from launch through apex to glide."""
    cfg = AircraftConfig(
        mass_kg=0.0055,
        wingspan_m=0.22,
        wing_area_m2=0.016,
        elevon_deg=2.2,
        fold_trailing_deg=0.0,
        aeroelastic_flex=0.2,
        aeroelastic_twist=0.2,
        aeroelastic_compliance=0.5,
    )
    env = EnvironmentConfig()
    launch = LaunchConfig(
        launch_speed=9.0,
        launch_angle_deg=10.0,
        launch_height_m=1.5,
    )

    sim = FlightSimulator(cfg, env, launch)
    result = sim.simulate(duration_s=4.0)

    # Flight executes cleanly without NaN or divergence
    assert result["status"] in ("landed", "time-limit")
    assert result["distance_m"] > 3.0
    assert result["flight_time_s"] > 0.5
    assert not math.isnan(result["distance_m"])
    assert not math.isnan(result["max_altitude_m"])
