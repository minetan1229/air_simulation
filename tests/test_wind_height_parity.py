"""Regression test for the wind height convention of the batch path.

:mod:`physics.wind_domain` evaluates the atmospheric boundary layer at the
**height above the ground plane**, which it forms itself as
``z_up - ground_level_m`` from the third component of the query point.  The
query point therefore has to be the z-up coordinate of the position, i.e.
``z_up = -z_ned`` for the world NED frame, and both paths that sample the
wind -- the 6-DoF reference path (:func:`physics.flight_sim.wind_vector`) and
the batched path (:func:`physics.gpu_dynamics.wind_batch`) -- have to convert
their world position the same way.

The defect this file guards against: the batch path converted the position
with ``ground_level_m - z_ned`` and therefore sampled the boundary layer at
the altitude above the *world origin* instead of at the height above the
ground plane.  On a plateau (``ground_level_m != 0``) the two paths flew in
different air masses -- a 35 % difference of the wind speed at 5 m above the
ground plane -- which breaks the CPU/GPU parity the genetic search depends
on.  With the default ``ground_level_m == 0`` the bug is invisible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics import flight_sim, gpu_dynamics, wind_domain  # noqa: E402
from physics.config_schema import AircraftConfig, EnvironmentConfig  # noqa: E402


def plateau_environment() -> EnvironmentConfig:
    """A windy scene whose ground plane is 100 m above the world origin."""
    return EnvironmentConfig(
        wind_speed=8.0,
        wind_direction_deg=45.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        turbulence_scale=0.0,
        ground_level_m=100.0,
    )


def test_the_batch_path_samples_the_wind_at_the_height_above_the_ground_plane() -> None:
    """The batch path and the reference path have to see the same air mass.

    The point is 5 m above the ground plane (105 m above the world origin),
    so the three possible conventions -- the height above the ground plane,
    the altitude above the world origin and the z-up coordinate of the point
    -- give three different wind speeds and only one of them is right.
    """
    env = plateau_environment()
    position = np.array([0.0, 0.0, -105.0])

    reference = flight_sim.wind_vector((0.0, 0.0, -105.0), env, 0.0)

    params = gpu_dynamics.build_params(np, [AircraftConfig()], env)
    batch = np.asarray(
        gpu_dynamics.wind_batch(np, params, position[np.newaxis, :], 0.0)
    )[0]

    assert batch == pytest.approx(reference, rel=1.0e-12, abs=1.0e-12)

    def solver_at(z_up: float) -> np.ndarray:
        return wind_domain.velocity_components(
            np.array([[0.0, 0.0, z_up]]), env, 0.0, "numpy"
        )[0]

    # The solver forms the height above the ground plane itself, so the
    # caller passes the z-up coordinate of the point.
    expected = solver_at(-position[2])
    assert batch == pytest.approx(
        np.array([expected[0], expected[1], -expected[2]]), rel=1.0e-12, abs=1.0e-12
    )

    # The two wrong conversions are excluded: the height above the ground
    # plane (the ground level would be subtracted twice) and the altitude
    # above the world origin (the wind would be sampled in the wrong air).
    at_ground_plane = solver_at(env.ground_level_m - position[2])
    at_world_origin = solver_at(env.ground_level_m + position[2])
    assert float(np.linalg.norm(batch)) != pytest.approx(
        float(np.linalg.norm(at_ground_plane)), rel=1.0e-6
    )
    assert float(np.linalg.norm(batch)) != pytest.approx(
        float(np.linalg.norm(at_world_origin)), rel=1.0e-6
    )


def test_the_batch_path_follows_the_reference_path_over_the_whole_scene() -> None:
    """The two paths have to agree at every height, not only at one point."""
    env = plateau_environment()
    params = gpu_dynamics.build_params(np, [AircraftConfig()], env)

    # From 1 m to 200 m above the ground plane.
    altitudes_m = np.array([1.0, 5.0, 25.0, 100.0, 200.0])
    positions = np.stack(
        (
            np.zeros_like(altitudes_m),
            np.zeros_like(altitudes_m),
            -(env.ground_level_m + altitudes_m),
        ),
        axis=-1,
    )

    batch = np.asarray(gpu_dynamics.wind_batch(np, params, positions, 0.0))
    for index, position in enumerate(positions):
        reference = flight_sim.wind_vector(tuple(position), env, 0.0)
        assert batch[index] == pytest.approx(reference, rel=1.0e-12, abs=1.0e-12)


def test_the_batch_path_is_unchanged_for_a_sea_level_scene() -> None:
    """With the default ground level the two conventions coincide."""
    env = EnvironmentConfig(
        wind_speed=8.0,
        wind_direction_deg=45.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        turbulence_scale=0.0,
    )
    params = gpu_dynamics.build_params(np, [AircraftConfig()], env)
    position = np.array([0.0, 0.0, -12.0])

    batch = np.asarray(
        gpu_dynamics.wind_batch(np, params, position[np.newaxis, :], 0.0)
    )[0]
    reference = flight_sim.wind_vector((0.0, 0.0, -12.0), env, 0.0)
    assert batch == pytest.approx(reference, rel=1.0e-12, abs=1.0e-12)
