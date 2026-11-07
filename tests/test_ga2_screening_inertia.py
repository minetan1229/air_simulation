"""Tests for Stage 1 screening aircraft moments of inertia (Issue #83).

Verifies that _screening_aircraft and build_screening_aircraft calculate
axis-specific moments of inertia reflecting span and chord mass distribution,
satisfy perpendicular axis theorem / triangle inequality, and exhibit shape-dependent
rotational dynamics.
"""

from __future__ import annotations

import math
import pytest

from physics.config_schema import AircraftConfig, derived_properties
from physics.ga2.runner import EvolutionRunner
from physics.ga2.scaling import ScalingProfile
from physics.ga2.stage1 import build_screening_aircraft


def test_screening_aircraft_axis_specific_inertias() -> None:
    """_screening_aircraft must compute shape-dependent, axis-specific inertias."""
    runner = EvolutionRunner(profile=ScalingProfile.small_long().with_overrides(population_size=8))

    # Elongated aircraft (dart-like: high chord, narrow span)
    span = 0.10
    area = 0.02
    mass = 0.005
    chord = area / span  # 0.20 m

    record = {
        "stage0": {
            "span_m": span,
            "wing_area_m2": area,
            "mass_kg": mass,
        }
    }

    cfg = runner._screening_aircraft(record)

    expected_ixx = mass * span * span / 12.0
    expected_iyy = mass * chord * chord / 12.0
    expected_izz = expected_ixx + expected_iyy

    assert math.isclose(cfg.roll_inertia_kgm2, expected_ixx, rel_tol=1e-5)
    assert math.isclose(cfg.pitch_inertia_kgm2, expected_iyy, rel_tol=1e-5)
    assert math.isclose(cfg.yaw_inertia_kgm2, expected_izz, rel_tol=1e-5)

    # Check that pitch inertia is strictly greater than roll inertia for chord > span
    assert cfg.pitch_inertia_kgm2 > cfg.roll_inertia_kgm2
    # Verify pitch-to-roll inertia ratio scales with (chord/span)^2
    assert math.isclose(
        cfg.pitch_inertia_kgm2 / cfg.roll_inertia_kgm2,
        (chord / span) ** 2,
        rel_tol=1e-5,
    )
    # Perpendicular axis theorem / triangle inequality
    assert math.isclose(cfg.yaw_inertia_kgm2, cfg.roll_inertia_kgm2 + cfg.pitch_inertia_kgm2, rel_tol=1e-5)
    assert cfg.yaw_inertia_kgm2 <= cfg.roll_inertia_kgm2 + cfg.pitch_inertia_kgm2


def test_screening_aircraft_glider_aspect_ratio() -> None:
    """Glider aircraft (wide span, small chord) must have roll inertia > pitch inertia."""
    runner = EvolutionRunner(profile=ScalingProfile.small_long().with_overrides(population_size=8))

    span = 0.30
    area = 0.015
    mass = 0.005
    chord = area / span  # 0.05 m

    record = {
        "stage0": {
            "span_m": span,
            "wing_area_m2": area,
            "mass_kg": mass,
        }
    }

    cfg = runner._screening_aircraft(record)

    assert cfg.roll_inertia_kgm2 > cfg.pitch_inertia_kgm2
    assert math.isclose(
        cfg.pitch_inertia_kgm2 / cfg.roll_inertia_kgm2,
        (chord / span) ** 2,
        rel_tol=1e-5,
    )


def test_consistency_with_build_screening_aircraft() -> None:
    """runner._screening_aircraft and stage1.build_screening_aircraft must agree."""
    runner = EvolutionRunner(profile=ScalingProfile.small_long().with_overrides(population_size=8))
    runner.paper_gsm = 80.0

    record = {
        "stage0": {
            "span_m": 0.16,
            "wing_area_m2": 0.024,
            "mass_kg": 0.004,
        }
    }

    cfg_runner = runner._screening_aircraft(record)
    cfg_stage1 = build_screening_aircraft(record, paper_gsm=80.0)

    assert math.isclose(cfg_runner.roll_inertia_kgm2, cfg_stage1.roll_inertia_kgm2, rel_tol=1e-6)
    assert math.isclose(cfg_runner.pitch_inertia_kgm2, cfg_stage1.pitch_inertia_kgm2, rel_tol=1e-6)
    assert math.isclose(cfg_runner.yaw_inertia_kgm2, cfg_stage1.yaw_inertia_kgm2, rel_tol=1e-6)


def test_consistency_with_derived_properties() -> None:
    """For a rectangular thin sheet (taper=1), derived_properties and screening inertia agree."""
    runner = EvolutionRunner(profile=ScalingProfile.small_long().with_overrides(population_size=8))
    runner.paper_gsm = 0.0  # Zero thickness approximation

    span = 0.20
    area = 0.02
    mass = 0.005
    record = {
        "stage0": {
            "span_m": span,
            "wing_area_m2": area,
            "mass_kg": mass,
        }
    }

    cfg = runner._screening_aircraft(record)

    # Create canonical config for rectangular wing with same properties
    canonical_cfg = AircraftConfig(
        wingspan_m=span,
        wing_area_m2=area,
        mass_kg=mass,
        taper_ratio=1.0,
        paper_gsm=0.0,
    )
    derived = derived_properties(canonical_cfg)

    assert math.isclose(cfg.roll_inertia_kgm2, derived["roll_inertia_kgm2"], rel_tol=1e-5)
    assert math.isclose(cfg.pitch_inertia_kgm2, derived["pitch_inertia_kgm2"], rel_tol=1e-5)
    assert math.isclose(cfg.yaw_inertia_kgm2, derived["yaw_inertia_kgm2"], rel_tol=1e-5)


def test_rotational_dynamics_shape_dependence() -> None:
    """Rotational pitch and roll angular response depends strongly on geometry."""
    runner = EvolutionRunner(profile=ScalingProfile.small_long().with_overrides(population_size=8))

    # Narrow span dart: span 0.10, chord 0.20
    dart_record = {"stage0": {"span_m": 0.10, "wing_area_m2": 0.02, "mass_kg": 0.005}}
    dart_cfg = runner._screening_aircraft(dart_record)

    # Wide span glider: span 0.20, chord 0.10
    glider_record = {"stage0": {"span_m": 0.20, "wing_area_m2": 0.02, "mass_kg": 0.005}}
    glider_cfg = runner._screening_aircraft(glider_record)

    # Both have the same mass (0.005) and area (0.02).
    # Applied pitch torque tau_y produces angular acceleration alpha_y = tau_y / I_yy
    # Applied roll torque tau_x produces angular acceleration alpha_x = tau_x / I_xx
    tau = 1e-4  # N*m

    dart_alpha_pitch = tau / dart_cfg.pitch_inertia_kgm2
    dart_alpha_roll = tau / dart_cfg.roll_inertia_kgm2

    glider_alpha_pitch = tau / glider_cfg.pitch_inertia_kgm2
    glider_alpha_roll = tau / glider_cfg.roll_inertia_kgm2

    # Dart has high chord -> high pitch inertia -> low pitch accel, but quick roll response
    assert dart_alpha_roll > dart_alpha_pitch
    # Glider has high span -> high roll inertia -> low roll accel, but quick pitch response
    assert glider_alpha_pitch > glider_alpha_roll
    # Cross comparison
    assert dart_alpha_roll > glider_alpha_roll
    assert glider_alpha_pitch > dart_alpha_pitch
