"""Independent inertia and force-axis checks for the physics repair."""
import numpy as np
import pytest

from physics.config_schema import AircraftConfig, derived_properties
from physics.geometry_utils import body_inertia_tensor, inertia_tensor_box, wind_force_to_body


def test_rectangular_default_inertia_matches_uniform_sheet():
    cfg = AircraftConfig(taper_ratio=1.0)
    d = derived_properties(cfg)
    thickness = cfg.paper_gsm * 1e-3 / 800.0
    expected = inertia_tensor_box(cfg.mass_kg, cfg.wing_area_m2 / cfg.wingspan_m,
                                  cfg.wingspan_m, thickness)
    actual = np.diag([d['roll_inertia_kgm2'], d['pitch_inertia_kgm2'], d['yaw_inertia_kgm2']])
    assert actual == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize('taper', [0.05, 0.4, 0.7, 1.0])
def test_tapered_default_inertia_is_realizable(taper):
    d = derived_properties(AircraftConfig(taper_ratio=taper))
    values = np.array([d['roll_inertia_kgm2'], d['pitch_inertia_kgm2'], d['yaw_inertia_kgm2']])
    assert np.all(values > 0)
    assert 2 * values.max() <= values.sum() + 1e-18


@pytest.mark.parametrize('velocity', [(6, 2, 3), (-6, 1, 2), (0, 4, 0), (0, 0, 5)])
def test_wind_axes_lift_and_side_do_no_work(velocity):
    v = np.array(velocity, dtype=float)
    lift = wind_force_to_body(v, 0, 0, 2)
    side = wind_force_to_body(v, 0, 3, 0)
    assert float(lift @ v) == pytest.approx(0, abs=1e-12)
    assert float(side @ v) == pytest.approx(0, abs=1e-12)
    drag = wind_force_to_body(v, 4, 0, 0)
    assert float(drag @ v) == pytest.approx(-4 * np.linalg.norm(v), rel=1e-12)
    assert np.linalg.norm(lift) == pytest.approx(2)
    assert np.linalg.norm(side) == pytest.approx(3)


def test_zero_flow_has_no_force_axis():
    assert np.array_equal(wind_force_to_body((0, 0, 0), 3, 2, 1), np.zeros(3))


def test_impossible_inertia_override_is_rejected():
    cfg = AircraftConfig(roll_inertia_kgm2=3e-5, pitch_inertia_kgm2=1e-5, yaw_inertia_kgm2=1e-5)
    with pytest.raises(ValueError, match='triangle'):
        body_inertia_tensor(derived_properties(cfg))


def test_off_diagonal_body_inertia_retains_axis_information():
    cfg = AircraftConfig(roll_inertia_kgm2=2e-5, pitch_inertia_kgm2=3e-5,
                         yaw_inertia_kgm2=4e-5, inertia_xy_kgm2=-1e-6)
    tensor = body_inertia_tensor(derived_properties(cfg))
    assert tensor[0, 1] == tensor[1, 0] == -1e-6


def test_clamp_ranges_repairs_triangle_inequality():
    cfg = AircraftConfig(roll_inertia_kgm2=3e-5, pitch_inertia_kgm2=1e-5, yaw_inertia_kgm2=1e-5)
    clamped = cfg.clamp_ranges()
    # Clamped config must be accepted by body_inertia_tensor
    tensor = body_inertia_tensor(derived_properties(clamped))
    principal = np.linalg.eigvalsh(tensor)
    assert principal[-1] <= principal[0] + principal[1] + 1e-12 * principal[-1]


def test_partial_inertia_override_preserves_triangle_inequality():
    # Only pitch is overridden (e.g. from fuselage length)
    cfg_pitch = AircraftConfig(pitch_inertia_kgm2=5e-5)
    tensor_p = body_inertia_tensor(derived_properties(cfg_pitch))
    principal_p = np.linalg.eigvalsh(tensor_p)
    assert principal_p[-1] <= principal_p[0] + principal_p[1] + 1e-12 * principal_p[-1]

    # Only roll is overridden (e.g. from span)
    cfg_roll = AircraftConfig(roll_inertia_kgm2=5e-5)
    tensor_r = body_inertia_tensor(derived_properties(cfg_roll))
    principal_r = np.linalg.eigvalsh(tensor_r)
    assert principal_r[-1] <= principal_r[0] + principal_r[1] + 1e-12 * principal_r[-1]


def test_flight_simulator_gracefully_handles_invalid_inertia():
    from physics.config_schema import EnvironmentConfig
    from physics.flight_sim import FlightSimulator
    # Even if an impossible inertia is passed directly into FlightSimulator, it safely recovers
    bad_cfg = AircraftConfig(roll_inertia_kgm2=3e-5, pitch_inertia_kgm2=1e-5, yaw_inertia_kgm2=1e-5)
    sim = FlightSimulator(aircraft=bad_cfg, environment=EnvironmentConfig())
    assert sim._inertia is not None
    principal = np.linalg.eigvalsh(sim._inertia)
    assert principal[-1] <= principal[0] + principal[1] + 1e-12 * principal[-1]

