"""Regression fixation of the release rotation produced by the hand model.

The release rotation of the 6-DoF path is the rigid-body rate the contact
impulse produces, ``w = I^-1 (r x F) dt eta`` with ``eta`` the documented
``throw_torque_efficiency``.  The implementation used to multiply the **pitch**
component of that rate by an unsourced ``0.10`` ("finger pinch guide effect"),
which silently scaled the initial condition of every flight by an order of
magnitude on one axis only.

The unsourced factor is forbidden by the repository protocol (root ``AGENTS.md``
section 1.5: values must be physical, not deliberately tuned; section 2:
no unsourced magic constants).  These tests pin the analytic identity so the
factor cannot come back, and they reconstruct the model from the *public*
configuration alone so the test is an independent re-derivation rather than a
restatement of the implementation.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from physics import flight_sim
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    derived_properties,
)


def _rebuild_release_rate(
    aircraft: AircraftConfig, launch: LaunchConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rebuild the grip point, the contact moment and the rigid-body rate.

    The rebuild uses only the public configuration, so it is independent of the
    internal ordering of :func:`physics.flight_sim.resolve_throw`.
    """
    derived = derived_properties(aircraft)
    mass = max(float(derived["mass_kg"]), 1.0e-9)
    mac = max(float(derived["mac_m"]), 1.0e-4)
    semispan = max(float(derived["wingspan_m"]), 1.0e-6) * 0.5

    force = abs(float(launch.throw_force_n))
    duration = abs(float(launch.throw_duration_s))
    pitch_tilt = math.radians(float(launch.throw_force_pitch_deg))
    yaw_tilt = math.radians(float(launch.throw_force_yaw_deg))
    direction = np.array(
        [
            math.cos(pitch_tilt) * math.cos(yaw_tilt),
            math.cos(pitch_tilt) * math.sin(yaw_tilt),
            -math.sin(pitch_tilt),
        ],
        dtype=np.float64,
    )
    force_body = force * direction

    grip = np.array(
        [
            float(launch.throw_grip_x) * mac + float(aircraft.cg_offset_x),
            float(launch.throw_grip_y) * semispan,
            float(launch.throw_grip_z),
        ],
        dtype=np.float64,
    )
    moment = np.cross(grip, force_body)

    inertia = np.diag(
        [
            max(float(derived["roll_inertia_kgm2"]), 1.0e-14),
            max(float(derived["pitch_inertia_kgm2"]), 1.0e-14),
            max(float(derived["yaw_inertia_kgm2"]), 1.0e-14),
        ]
    )
    inertia[0, 1] = inertia[1, 0] = float(derived.get("inertia_xy_kgm2", 0.0))
    inertia[0, 2] = inertia[2, 0] = float(derived.get("inertia_xz_kgm2", 0.0))
    inertia[1, 2] = inertia[2, 1] = float(derived.get("inertia_yz_kgm2", 0.0))

    efficiency = min(max(float(launch.throw_torque_efficiency), 0.0), 1.0)
    rate = np.linalg.solve(inertia, moment * duration * efficiency)
    assert mass > 0.0
    return grip, moment, rate


def _launch(**overrides: float) -> LaunchConfig:
    base = dict(
        throw_style="overhand",
        throw_speed_source="impulse",
        throw_force_n=0.05,
        throw_duration_s=0.05,
        throw_force_pitch_deg=0.0,
        throw_force_yaw_deg=0.0,
        throw_grip_x=0.0,
        throw_grip_y=0.0,
        throw_grip_z=0.015,
        throw_torque_efficiency=1.0,
        throw_wrist_spin_radps=0.0,
        throw_pitch_rate_radps=0.0,
        throw_yaw_rate_radps=0.0,
    )
    base.update(overrides)
    return LaunchConfig(**base)  # type: ignore[arg-type]


def test_release_pitch_rate_equals_the_analytic_rigid_body_rate():
    """The release pitch rate is ``I^-1 (r x F) dt eta`` -- no per-axis factor.

    The removed ``torque_rate[1] *= 0.10`` made the produced rate exactly one
    tenth of the analytic value, i.e. a relative error of 0.9.
    """
    aircraft = AircraftConfig()
    launch = _launch()
    solution = flight_sim.resolve_throw(launch, aircraft, EnvironmentConfig())

    grip, moment, analytic = _rebuild_release_rate(aircraft, launch)

    assert solution.grip_point_m == pytest.approx(tuple(grip), rel=1e-12, abs=1e-15)
    assert solution.moment_nm == pytest.approx(tuple(moment), rel=1e-12, abs=1e-15)

    produced = np.asarray(solution.torque_rate_radps, dtype=np.float64)
    assert produced[1] == pytest.approx(float(analytic[1]), rel=1e-12, abs=1e-15)
    # The whole rate vector has to follow the analytic solution, not only the
    # pitch component: a per-axis factor anywhere is the same defect.
    assert produced == pytest.approx(analytic, rel=1e-12, abs=1e-15)


def test_effective_release_rate_matches_the_analytic_rate_when_not_clamped():
    """Below the release ceiling the *effective* rate equals the analytic rate.

    The ceiling (``MAX_RELEASE_PITCH_RATE_RADPS``) is a documented guard rail
    on the 6-DoF integration, so the test first proves the analytic value is
    inside it and only then compares the clamped output.
    """
    aircraft = AircraftConfig()
    # A light push with the full hand efficiency, so the analytic rate stays
    # inside the release ceiling and the comparison is not saturated.
    launch = _launch(throw_force_n=0.02)
    _, _, analytic = _rebuild_release_rate(aircraft, launch)
    assert abs(float(analytic[1])) < flight_sim.MAX_RELEASE_PITCH_RATE_RADPS, (
        "the fixture must stay below the release ceiling, otherwise the test "
        "would compare a saturated value"
    )

    solution = flight_sim.resolve_throw(launch, aircraft, EnvironmentConfig())
    effective = np.asarray(solution.angular_rate_radps, dtype=np.float64)
    assert effective[1] == pytest.approx(float(analytic[1]), rel=1e-12, abs=1e-15)


def test_release_rate_is_not_scaled_by_a_tenth_on_the_pitch_axis():
    """Explicit guard against the reintroduction of the ``0.10`` factor.

    The ratio is compared against the analytic rate instead of a hard-coded
    value, so the test keeps its meaning if the moment model ever changes.
    """
    aircraft = AircraftConfig()
    launch = _launch(throw_force_pitch_deg=12.0, throw_grip_x=0.10)
    solution = flight_sim.resolve_throw(launch, aircraft, EnvironmentConfig())

    _, _, analytic = _rebuild_release_rate(aircraft, launch)
    produced = float(solution.torque_rate_radps[1])
    assert analytic[1] != 0.0
    assert produced / float(analytic[1]) == pytest.approx(1.0, rel=1e-12)


def test_direct_mode_does_not_apply_a_hand_rotation():
    """The neutral entry point stays neutral: no force, no grip, no rotation."""
    aircraft = AircraftConfig()
    launch = LaunchConfig(
        throw_style="direct",
        throw_speed_source="direct",
        launch_speed=5.0,
    )
    solution = flight_sim.resolve_throw(launch, aircraft, EnvironmentConfig())
    assert solution.torque_rate_radps == (0.0, 0.0, 0.0)
    assert solution.angular_rate_radps == (0.0, 0.0, 0.0)
