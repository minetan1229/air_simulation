"""Tests for energy conservation and physical constraints during ground contact,
specifically focusing on wingtip strike scenarios to prevent perpetual bouncing
and non-physical energy creation.
"""

from __future__ import annotations

import numpy as np
import pytest

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    SimulationSettings,
    StateVector,
)
from physics.flight_sim import FlightSimulator, compute_ground_stations
from physics.geometry_utils import quaternion_from_euler


def _total_kinetic_energy(sim: FlightSimulator, state: StateVector) -> float:
    """Calculate total kinetic energy (translational + rotational) of the aircraft."""
    m = float(sim.aircraft.mass_kg)
    v = np.asarray(state.velocity_mps, dtype=np.float64)
    w = np.asarray(state.angular_velocity_radps, dtype=np.float64)
    e_trans = 0.5 * m * float(np.dot(v, v))
    e_rot = 0.5 * float(w @ sim._inertia @ w)
    return e_trans + e_rot


def test_wingtip_strike_energy_conservation():
    """Ensure that a wingtip strike does not generate energy out of nowhere (E_post <= E_pre).

    In the unfixed implementation, an asymmetric wingtip contact applies fixed
    angular velocity increments (e.g. +2.0 rad/s roll, -1.5 rad/s yaw) regardless
    of impact kinetic energy, causing E_post > E_pre for lightweight/high-inertia
    aircraft at low vertical impact speed (e.g. vz = 0.5 m/s).
    """
    cfg = AircraftConfig(
        mass_kg=0.001,
        roll_inertia_kgm2=5.0e-4,
        yaw_inertia_kgm2=5.0e-4,
    )
    settings = SimulationSettings(restitution=0.8)
    sim = FlightSimulator(cfg, EnvironmentConfig(ground_level_m=0.0), settings=settings)

    # Roll aircraft by 30 deg so right wingtip strikes ground before CG or other stations
    q = quaternion_from_euler(30.0, -5.0, 0.0)
    z_cg = -0.04
    stations = compute_ground_stations((0.0, 0.0, z_cg), tuple(q), sim.environment, sim._derived)
    assert stations["altitude_cg"] > 0.0, "CG must be above ground"
    assert stations["alt_rtip"] <= 0.0, "Right wingtip must be in ground contact"
    assert stations["alt_ltip"] > 0.0, "Left wingtip must be in air"

    vz = 0.5
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, vz),
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    e_pre = _total_kinetic_energy(sim, state)
    assert e_pre > 0.0

    next_state = sim._apply_ground_contact(state, None)
    e_post = _total_kinetic_energy(sim, next_state)

    # Physical law: Inelastic collision cannot create energy. E_post <= E_pre must hold.
    assert e_post <= e_pre + 1.0e-12, (
        f"Energy non-conservation detected on wingtip strike: "
        f"E_post ({e_post:.6e} J) > E_pre ({e_pre:.6e} J), ratio={e_post / e_pre:.2f}"
    )


def test_wingtip_strike_physical_dissipation_bound():
    """Ensure energy after wingtip collision does not exceed the physical dissipation limit:
    E_post <= E_pre * max(restitution^2, 0.5^2).
    """
    cfg = AircraftConfig(
        mass_kg=0.003,
        roll_inertia_kgm2=2.0e-4,
        yaw_inertia_kgm2=2.0e-4,
    )
    restitution = 0.4
    settings = SimulationSettings(restitution=restitution)
    sim = FlightSimulator(cfg, EnvironmentConfig(ground_level_m=0.0), settings=settings)

    q = quaternion_from_euler(30.0, -5.0, 0.0)
    z_cg = -0.04
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, 0.5),
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    e_pre = _total_kinetic_energy(sim, state)
    next_state = sim._apply_ground_contact(state, None)
    e_post = _total_kinetic_energy(sim, next_state)

    allowed_ratio = max(restitution**2, 0.5**2)
    assert e_post <= e_pre * allowed_ratio + 1.0e-12, (
        f"Collision dissipation violated: E_post={e_post:.6e} J exceeded allowed limit "
        f"{e_pre * allowed_ratio:.6e} J (ratio={e_post / e_pre:.2f}, max_allowed={allowed_ratio:.2f})"
    )


def test_no_double_rebound_on_wingtip_strike():
    """Ensure that wingtip strike rebound speed is not overwritten by main body rebound."""
    restitution = 0.8
    settings = SimulationSettings(restitution=restitution)
    sim = FlightSimulator(AircraftConfig(), EnvironmentConfig(ground_level_m=0.0), settings=settings)

    q = quaternion_from_euler(30.0, -5.0, 0.0)
    z_cg = -0.04
    vz = 0.6
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, vz),
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    next_state = sim._apply_ground_contact(state, None)
    # Wingtip contact restricts restitution to at most 0.5 (rebound vz magnitude <= 0.5 * vz = 0.3)
    # Main body contact would set new_vz = -restitution * vz = -0.48.
    rebound_vz = abs(next_state.velocity_mps[2])
    assert rebound_vz <= 0.5 * vz + 1.0e-6, (
        f"Wingtip strike rebound speed {rebound_vz} exceeded wingtip limit {0.5 * vz}; "
        f"double-rebound calculation overwrote wingtip rebound."
    )
