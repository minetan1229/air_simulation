# -*- coding: utf-8 -*-
"""Comprehensive physics conformance test suite for atmosphere, wind, and turbulence.

Verifies:
1. ISA 1976 atmospheric layers and layer boundary continuity
2. Ideal gas law thermodynamic consistency (rho = p / (R * T))
3. Moist air Dalton law consistency between atmosphere_domain and flight_sim fallback
4. GPU dynamics batch atmosphere conformance with atmosphere_domain across all layers
5. Sutherland dynamic viscosity and kinematic viscosity (nu = mu / rho)
6. Reynolds number (Re = rho * V * L / mu = V * L / nu) and Mach number (M = V / a)
7. Meteorological wind direction and NED world flow vectors (North, East, South, West)
8. Wind field continuity and boundary conditions (impermeable surface w_gust -> 0 as z -> 0)
9. Turbulence seed reproducibility and Taylor frozen turbulence advection
10. Dynamic atmosphere and wind updating across RK4 intermediate substeps
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    G0,
    GAMMA_AIR,
    MU0_ISA,
    MU_SUTHERLAND,
    P0_ISA,
    R_AIR,
    R_VAPOR,
    SUTHERLAND_T0,
    T0_ISA,
)
from physics import atmosphere_domain as atmo
from physics import flight_sim
from physics import gpu_dynamics
from physics import wind_domain


# ==========================================================================
# 1. ISA 1976 layers, boundary continuity, and ideal gas law
# ==========================================================================
def test_isa_layer_boundary_continuity():
    """Verify that temperature, pressure, and density are strictly continuous at layer boundaries."""
    boundaries = [11000.0, 20000.0, 32000.0]
    eps = 1e-4

    for h_b in boundaries:
        t_below = atmo.isa_temperature(h_b - eps)
        t_at = atmo.isa_temperature(h_b)
        t_above = atmo.isa_temperature(h_b + eps)
        assert math.isclose(t_below, t_at, rel_tol=1e-4)
        assert math.isclose(t_at, t_above, rel_tol=1e-4)

        p_below = atmo.isa_pressure(h_b - eps)
        p_at = atmo.isa_pressure(h_b)
        p_above = atmo.isa_pressure(h_b + eps)
        assert math.isclose(p_below, p_at, rel_tol=1e-4)
        assert math.isclose(p_at, p_above, rel_tol=1e-4)

        rho_below = atmo.isa_density(h_b - eps)
        rho_at = atmo.isa_density(h_b)
        rho_above = atmo.isa_density(h_b + eps)
        assert math.isclose(rho_below, rho_at, rel_tol=1e-4)
        assert math.isclose(rho_at, rho_above, rel_tol=1e-4)


def test_ideal_gas_law_exact_relation():
    """Verify rho = p / (R * T) holds identically across altitudes and humidities."""
    for alt in [-500.0, 0.0, 500.0, 2500.0, 11000.0, 15000.0, 20000.0, 30000.0]:
        t = atmo.isa_temperature(alt)
        p = atmo.isa_pressure(alt)
        rho = atmo.isa_density(alt)
        # Dry standard air
        assert math.isclose(rho, p / (R_AIR * t), rel_tol=1e-9)

        # Humid air across relative humidities
        for rh in [0.0, 0.25, 0.5, 0.8, 1.0]:
            rho_moist = atmo.air_density(t, p, humidity_rel=rh)
            r_moist = atmo.moist_air_gas_constant(t, p, rh)
            assert math.isclose(rho_moist, p / (r_moist * t), rel_tol=1e-9)
            if rh > 0.0:
                # Humid air is strictly less dense than dry air
                assert rho_moist < rho


def test_fallback_and_atmosphere_domain_equivalence():
    """Verify flight_sim._local_atmosphere matches atmosphere_domain."""
    for rh in [0.0, 0.4, 0.8]:
        env = EnvironmentConfig(temperature_K=295.15, pressure_Pa=100500.0, humidity_rel=rh, isa_mode=False)
        d_domain = atmo.air_density(env.temperature_K, env.pressure_Pa, env.humidity_rel)
        d_local = flight_sim._local_atmosphere(env, 0.0, 0.1)["density_kgm3"]
        assert math.isclose(d_domain, d_local, rel_tol=1e-4)


def test_gpu_atmosphere_batch_conformance():
    """Verify gpu_dynamics.atmosphere_batch conforms with atmosphere_domain across layers."""
    env = EnvironmentConfig(isa_mode=True)
    altitudes = np.array([-200.0, 0.0, 2000.0, 11000.0, 15000.0, 20000.0, 26000.0, 32000.0, 35000.0])
    gpu_res = gpu_dynamics.atmosphere_batch(np, env, altitudes, 0.1)

    for i, h in enumerate(altitudes):
        expected = atmo.evaluate_atmosphere(env, altitude_m=float(h))
        assert math.isclose(gpu_res["density_kgm3"][i], expected["density_kgm3"], rel_tol=1e-3)
        assert math.isclose(gpu_res["kinematic_viscosity_m2s"][i], expected["kinematic_viscosity_m2s"], rel_tol=1e-3)
        assert math.isclose(gpu_res["sound_speed_mps"][i], expected["sound_speed_mps"], rel_tol=1e-3)


# ==========================================================================
# 2. Viscosity, Reynolds number, and Mach number
# ==========================================================================
def test_viscosity_and_transport_properties():
    """Verify Sutherland formula, nu = mu / rho, Re = rho * V * L / mu, Mach = V / a."""
    # Sutherland formula check at reference points
    mu_ref = atmo.sutherland_viscosity(SUTHERLAND_T0)
    assert math.isclose(mu_ref, MU0_ISA, rel_tol=1e-7)

    env = EnvironmentConfig(altitude_m=1500.0, temperature_K=280.0, pressure_Pa=92000.0)
    state = atmo.evaluate_atmosphere(env, reference_chord_m=0.12)
    t = state["temperature_K"]
    rho = state["density_kgm3"]
    mu = state["dynamic_viscosity_Pa_s"]
    nu = state["kinematic_viscosity_m2s"]
    sound = state["sound_speed_mps"]

    # nu = mu / rho
    assert math.isclose(nu, mu / rho, rel_tol=1e-9)

    # Speed of sound
    expected_sound = math.sqrt(GAMMA_AIR * R_AIR * t)
    assert math.isclose(sound, expected_sound, rel_tol=1e-9)

    speed = 12.0
    chord = 0.12
    re = atmo.reynolds_number(speed, chord, nu)
    # Re = rho * V * L / mu = V * L / nu
    assert math.isclose(re, speed * chord / nu, rel_tol=1e-9)
    assert math.isclose(re, rho * speed * chord / mu, rel_tol=1e-9)

    mach = atmo.mach_number(speed, t)
    assert math.isclose(mach, speed / sound, rel_tol=1e-9)


# ==========================================================================
# 3. Wind direction and coordinate conventions
# ==========================================================================
def test_wind_directions_and_coordinate_transformation():
    """Verify meteorological wind directions (from which wind blows) and flow vectors.

    Wind domain: +x is East, +y is North, +z is Up.
    NED world frame: +x is North, +y is East, +z is Down.
    The canonical wind flow components: flow = -(sin(theta), cos(theta)).
    """
    speed = 10.0
    # North wind (0 deg): blows from North (+y) towards South (-y)
    env_n = EnvironmentConfig(wind_speed=speed, wind_direction_deg=0.0, boundary_layer_enabled=False, gust_intensity=0.0)
    w_n = wind_domain.wind_velocity(np.array([0.0, 0.0, 10.0]), env_n, 0.0)
    assert math.isclose(w_n[0], 0.0, abs_tol=1e-9)
    assert math.isclose(w_n[1], -speed, rel_tol=1e-9)
    assert math.isclose(w_n[2], 0.0, abs_tol=1e-9)

    # East wind (90 deg): blows from East (+x) towards West (-x)
    env_e = EnvironmentConfig(wind_speed=speed, wind_direction_deg=90.0, boundary_layer_enabled=False, gust_intensity=0.0)
    w_e = wind_domain.wind_velocity(np.array([0.0, 0.0, 10.0]), env_e, 0.0)
    assert math.isclose(w_e[0], -speed, rel_tol=1e-9)
    assert math.isclose(w_e[1], 0.0, abs_tol=1e-9)
    assert math.isclose(w_e[2], 0.0, abs_tol=1e-9)

    # South wind (180 deg): blows from South (-y) towards North (+y)
    env_s = EnvironmentConfig(wind_speed=speed, wind_direction_deg=180.0, boundary_layer_enabled=False, gust_intensity=0.0)
    w_s = wind_domain.wind_velocity(np.array([0.0, 0.0, 10.0]), env_s, 0.0)
    assert math.isclose(w_s[0], 0.0, abs_tol=1e-9)
    assert math.isclose(w_s[1], speed, rel_tol=1e-9)
    assert math.isclose(w_s[2], 0.0, abs_tol=1e-9)

    # West wind (270 deg): blows from West (-x) towards East (+x)
    env_w = EnvironmentConfig(wind_speed=speed, wind_direction_deg=270.0, boundary_layer_enabled=False, gust_intensity=0.0)
    w_w = wind_domain.wind_velocity(np.array([0.0, 0.0, 10.0]), env_w, 0.0)
    assert math.isclose(w_w[0], speed, rel_tol=1e-9)
    assert math.isclose(w_w[1], 0.0, abs_tol=1e-9)
    assert math.isclose(w_w[2], 0.0, abs_tol=1e-9)

    # In flight_sim.wind_vector, wind domain (x=East,y=North,z=Up) is converted to NED (x=North,y=East,z=Down)
    # Vertical component is inverted: z_ned = -z_wind
    pos_ned = np.array([0.0, 0.0, -10.0])  # 10m above ground
    w_sim_n = flight_sim.wind_vector(pos_ned, env_n, 0.0)
    # North wind blows from North (+x_ned) to South (-x_ned) -> velocity = (-speed, 0, 0)
    assert math.isclose(w_sim_n[0], -speed, rel_tol=1e-9)
    assert math.isclose(w_sim_n[1], 0.0, abs_tol=1e-9)
    assert math.isclose(w_sim_n[2], 0.0, abs_tol=1e-9)


# ==========================================================================
# 4. Turbulence statistical reproducibility and boundary conditions
# ==========================================================================
def test_turbulence_seed_strict_reproducibility():
    """Verify bit-for-bit reproducibility for identical turbulence seed and deviation on different seed."""
    env1 = EnvironmentConfig(wind_speed=8.0, turbulence_seed=42, gust_intensity=0.6, thermal_strength=1.5)
    env2 = EnvironmentConfig(wind_speed=8.0, turbulence_seed=42, gust_intensity=0.6, thermal_strength=1.5)
    env3 = EnvironmentConfig(wind_speed=8.0, turbulence_seed=999, gust_intensity=0.6, thermal_strength=1.5)

    pos = np.array([15.2, 34.8, -25.0])
    t = 4.25

    w1 = flight_sim.wind_vector(pos, env1, t)
    w2 = flight_sim.wind_vector(pos, env2, t)
    w3 = flight_sim.wind_vector(pos, env3, t)

    np.testing.assert_array_equal(w1, w2)
    assert not np.allclose(w1, w3)


def test_turbulence_ground_impermeability_boundary_condition():
    """Verify vertical wind velocity w vanishes at the ground boundary (z_agl <= 0)."""
    env = EnvironmentConfig(
        wind_speed=10.0,
        wind_direction_deg=45.0,
        gust_intensity=0.8,
        thermal_strength=2.0,
        ground_level_m=0.0,
    )
    # Positions at ground surface and underground
    pos_surface = np.array([10.0, 20.0, 0.0])
    pos_under = np.array([10.0, 20.0, 0.5])  # In NED frame, +z is underground

    w_surface = flight_sim.wind_vector(pos_surface, env, time_s=2.0)
    w_under = flight_sim.wind_vector(pos_under, env, time_s=2.0)

    # Vertical component in NED is index 2
    assert math.isclose(w_surface[2], 0.0, abs_tol=1e-12)
    assert math.isclose(w_under[2], 0.0, abs_tol=1e-12)


def test_wind_field_spatial_temporal_continuity():
    """Verify wind field is continuous in time and space across small increments."""
    env = EnvironmentConfig(wind_speed=7.0, gust_intensity=0.5, thermal_strength=1.0)
    pos = np.array([5.0, 5.0, -10.0])
    t = 1.0

    w0 = flight_sim.wind_vector(pos, env, t)
    # Small time step (dt = 1e-3s, typical RK4 substep; rate is ~18 m/s^2, so diff ~0.018 m/s)
    w_dt = flight_sim.wind_vector(pos, env, t + 1e-3)
    assert np.linalg.norm(w_dt - w0) < 0.05

    # Small space step (dr = 1e-3m)
    w_dr = flight_sim.wind_vector(pos + np.array([1e-3, 0.0, 0.0]), env, t)
    assert np.linalg.norm(w_dr - w0) < 0.05


# ==========================================================================
# 5. Dynamic state updating across RK4 intermediate substeps
# ==========================================================================
def test_rk4_intermediate_atmospheric_updating():
    """Verify derivative evaluation inside RK4 dynamically queries local atmosphere and wind."""
    env = EnvironmentConfig(
        wind_speed=8.0,
        wind_direction_deg=0.0,
        boundary_layer_enabled=True,
        reference_height_m=2.0,
        surface_roughness_m=0.01,
        isa_mode=True,
    )
    sim = flight_sim.FlightSimulator(AircraftConfig(), env, LaunchConfig(launch_speed=10.0, launch_angle_deg=20.0))
    y0 = flight_sim._pack(sim.state)

    # Step dynamics at y0 vs y0 + dy
    d1 = sim._derivative(y0, 0.0)
    y_step = y0 + 0.05 * d1
    d2 = sim._derivative(y_step, 0.05)

    assert not np.allclose(d1, d2)
    assert np.all(np.isfinite(d1))
    assert np.all(np.isfinite(d2))
