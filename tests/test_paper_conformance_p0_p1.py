"""Regression tests for the paper-conformance audit (P0 + P1).

The audit (``PHYSICS_PAPER_CONFORMANCE_AUDIT.md``) found the project computing
the *same* airframe with three different physics models, three different
stress scales, three different wind sign conventions and four different
Helmbold expressions.  These tests pin the unified behaviour down:

* P0-1  the model tag names the path that produced a payload,
* P0-2  the stress index uses one single allowable-stress scale,
* P0-3  the meteorological wind convention is shared by every path,
* P0-4  the lift-curve slope has one single implementation,
* P0-5  a failed evaluation is reported as ``error`` and never as a 0 m flight,
* P1-6  the post-stall lift curve has no lift-free notch,
* P1-7  the separated drag follows Hoerner (1965) including ``C_D0 cos(a)``,
* P1-9  the passive morphing is geometric and does not scale the camber,
* P1-10 the published Polhamus relation is implemented.
"""

from __future__ import annotations

import math

import pytest

from physics import aero_domain, flight_sim, parallel_engine, stress_domain, wind_domain
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, as_dict
from physics.optimizer_domain import (
    INFEASIBLE_SCORE,
    evaluate_vector,
    is_infeasible_flight,
)
from physics.config_schema import OptimizerConfig

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _source(module_name: str) -> str:
    path = REPO_ROOT / "physics" / f"{module_name}.py"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# P0-1: the model tag names the path
# ---------------------------------------------------------------------------
def test_six_dof_path_is_tagged_rigid_body() -> None:
    case = {
        "aircraft": as_dict(AircraftConfig()),
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(LaunchConfig(launch_speed=8.0)),
        "duration_s": 0.2,
    }
    result = parallel_engine.evaluate_case(case)
    assert result["model"] == parallel_engine.RIGID_BODY_MODEL_TAG
    assert result["model_detail"] == "6dof_rk4"
    assert result["approximate"] is False


def test_reduced_paths_are_tagged_reduced_surrogate() -> None:
    surrogate = parallel_engine.evaluate_flight_surrogate({}, {}, {}, 1.0, 0.02)
    assert surrogate["model"] == parallel_engine.REDUCED_MODEL_TAG
    assert surrogate["model_detail"] == "analytical_glide_surrogate"
    assert surrogate["approximate"] is True
    assert surrogate["physics_verification_eligible"] is False

    ensemble = parallel_engine.gpu_ensemble_simulation(
        n_trajectories=4, settings={"duration_s": 0.1}, force_cpu=True
    )
    assert ensemble["model"] == parallel_engine.REDUCED_MODEL_TAG
    assert ensemble["model_detail"] == "gpu_ensemble_3dof"
    assert ensemble["approximate"] is True
    assert ensemble["physics_verification_eligible"] is False


def test_gpu_batch_path_is_tagged_reduced_surrogate() -> None:
    case = {
        "aircraft": as_dict(AircraftConfig()),
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(LaunchConfig(launch_speed=6.0)),
        "duration_s": 0.2,
    }
    # The CPU fallback of the GPU batch runs the full 6-DoF integrator, so it
    # must be tagged as such (claiming a reduced model would be a false
    # claim in the other direction).
    rows = parallel_engine.gpu_batch_evaluate_cases([case], force_cpu=True)
    assert len(rows) == 1
    assert rows[0]["model"] == parallel_engine.RIGID_BODY_MODEL_TAG

    info = parallel_engine.get_gpu_info()
    if not (info["available"] and info["backend"] == "cupy"):
        return
    # The CUDA path integrates the same 13-state rigid body as the reference
    # path, so it is a 6-DoF payload as well.
    gpu_rows = parallel_engine.gpu_batch_evaluate_cases([case])
    assert len(gpu_rows) == 1
    assert gpu_rows[0]["model"] == parallel_engine.RIGID_BODY_MODEL_TAG
    assert gpu_rows[0]["model_detail"] == "rigid_body_6dof_batch"


# ---------------------------------------------------------------------------
# P0-2: one stress scale
# ---------------------------------------------------------------------------
def test_stress_index_uses_the_airframe_allowable() -> None:
    cfg = AircraftConfig()
    allowable = stress_domain.paper_allowable_stress(cfg.paper_gsm, cfg.paper_stiffness)
    assert allowable > 0.0

    field = stress_domain.stress_field(cfg, dynamic_pressure_pa=120.0, load_factor=2.0)
    expected = field["max_stress_pa"] / allowable
    assert field["max_stress_index"] == pytest.approx(expected, rel=1e-6)
    # The heat map is a utilisation map, so 1.0 means "at the failure limit".
    assert max(max(row) for row in field["stress_index"]) == pytest.approx(
        min(1.0, expected), rel=1e-6
    )
    # The load pattern is published separately, so the index keeps its meaning.
    assert "stress_shape" in field


def test_flight_sim_fallback_uses_the_same_allowable() -> None:
    cfg = AircraftConfig()
    assert flight_sim.allowable_stress_pa(cfg) == pytest.approx(
        stress_domain.paper_allowable_stress(cfg.paper_gsm, cfg.paper_stiffness), rel=1e-9
    )


def test_parallel_engine_no_longer_uses_a_second_stress_scale() -> None:
    source = _source("parallel_engine")
    assert "_STRESS_LIMIT_PA" not in source
    assert "1.2e5" not in source


# ---------------------------------------------------------------------------
# P0-3: the wind convention is shared
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("direction_deg", [0.0, 45.0, 90.0, 180.0, 270.0, 359.0])
def test_wind_components_match_the_meteorological_convention(direction_deg: float) -> None:
    env = EnvironmentConfig(wind_speed=5.0, wind_direction_deg=direction_deg)
    flow_x, flow_y = wind_domain.wind_flow_components(direction_deg)
    unit = wind_domain.wind_direction_unit(env)
    assert unit[0] == pytest.approx(flow_x)
    assert unit[1] == pytest.approx(flow_y)
    assert unit[2] == 0.0
    assert math.hypot(flow_x, flow_y) == pytest.approx(1.0)
    # "From" bearing 0 deg is a north wind: the flow runs towards -y.
    if direction_deg == 0.0:
        assert (flow_x, flow_y) == pytest.approx((0.0, -1.0))
    if direction_deg == 90.0:
        assert (flow_x, flow_y) == pytest.approx((-1.0, 0.0))


def test_reference_path_uses_the_shared_wind_vector() -> None:
    env = EnvironmentConfig(wind_speed=4.0, wind_direction_deg=0.0)
    # z-down world frame: 5 m above the ground plane.
    position = (0.0, 0.0, -5.0)
    vector = flight_sim.wind_vector(position, env, 0.0)
    height = -position[2] - env.ground_level_m
    expected = wind_domain.wind_velocity([0.0, 0.0, height], env, 0.0)
    assert float(vector[0]) == pytest.approx(float(expected[0]), abs=1e-9)
    assert float(vector[1]) == pytest.approx(float(expected[1]), abs=1e-9)
    # The wind domain is z-up, the flight dynamics world frame is z-down.
    assert float(vector[2]) == pytest.approx(-float(expected[2]), abs=1e-9)


def test_gpu_paths_do_not_rotate_the_wind() -> None:
    # Every path builds its flow vector from the shared meteorological
    # convention (flow = -(sin(theta), cos(theta))); a rotated convention
    # (cos/sin) would send a north wind to the east.
    for module_name in ("parallel_engine", "gpu_dynamics"):
        assert "wind_flow_components" in _source(module_name)
    source = _source("parallel_engine")
    assert "wx = -w_speed * xp.sin(w_dir_rad)" in source
    assert "wy = -w_speed * xp.cos(w_dir_rad)" in source
    # The rotated form (a 90 deg turn of the whole vector) must not come
    # back: it would send a north wind to the east.
    assert "wx = -w_speed * xp.cos(w_dir_rad)" not in source
    assert "wy = -w_speed * xp.sin(w_dir_rad)" not in source


# ---------------------------------------------------------------------------
# P0-4: one Helmbold implementation
# ---------------------------------------------------------------------------
def test_lift_slope_has_a_single_implementation() -> None:
    cfg = AircraftConfig()
    derived = stress_domain.derived_properties(cfg)
    aspect = float(derived["aspect_ratio"])
    taper = float(derived["taper_ratio"])
    canonical = aero_domain.helmbold_cl_alpha(aspect, taper)
    assert flight_sim.helmbold_cl_alpha(aspect, taper) == pytest.approx(canonical, rel=1e-12)
    assert stress_domain.lift_curve_slope(cfg, derived) == pytest.approx(canonical, rel=1e-12)
    # Helmbold (1942) / Raymer eq. 12.10 form: 2 pi AR / (2 + sqrt(AR^2(1+lam) + 4AR/pi))
    expected = 2.0 * math.pi * aspect / (
        2.0 + math.sqrt(aspect * aspect * (1.0 + taper) + 4.0 * aspect / math.pi)
    )
    assert canonical == pytest.approx(expected, rel=1e-12)


def test_no_module_keeps_a_local_helmbold_copy() -> None:
    for module_name in ("parallel_engine", "stress_domain"):
        source = _source(module_name)
        assert "aspect_ratio**2 + 4.0" not in source
        assert "2.0 + math.sqrt(aspect_ratio ** 2 + 4.0)" not in source


# ---------------------------------------------------------------------------
# P0-5: a failed evaluation is an error, not a 0 m flight
# ---------------------------------------------------------------------------
def test_evaluate_flight_reports_a_broken_configuration_as_error() -> None:
    broken = {"aircraft": {"mass_kg": "not-a-number"}, "environment": {}, "launch": {}}
    payload = flight_sim.evaluate_flight(broken["aircraft"], broken["environment"], broken["launch"], 0.1)
    assert payload["status"] == "error"
    assert payload["error"] == "ValueError"
    assert payload["error_id"]
    # A broken design must never look like a legitimate 0 m flight.
    assert payload["distance_m"] == 0.0
    assert payload["status"] != "flying"


def test_optimizer_treats_an_error_as_infeasible() -> None:
    cfg = OptimizerConfig()
    assert is_infeasible_flight({"status": "error"}) is True
    assert is_infeasible_flight({"status": "failed"}) is True
    assert is_infeasible_flight({"status": "landed"}) is False
    assert INFEASIBLE_SCORE < -1.0e6

    # The score of an infeasible evaluation must not compete with a real one.
    variables = cfg.resolved_variables()
    vector = [float(variable.low) for variable in variables]
    result = evaluate_vector(vector, cfg)
    assert result["status"] in ("flying", "landed", "crashed", "timeout", "error", "failed")
    if result["status"] in ("error", "failed"):
        assert result["score"] == pytest.approx(INFEASIBLE_SCORE)
        assert result["infeasible"] is True
    else:
        assert result["infeasible"] is False


# ---------------------------------------------------------------------------
# P1-6: no lift-free notch behind the stall
# ---------------------------------------------------------------------------
def test_post_stall_lift_has_no_zero_notch() -> None:
    cfg = AircraftConfig()
    for alpha in (38.0, 41.0, 43.0, 45.0, 50.0):
        cl = aero_domain.lift_coefficient(alpha, cfg)
        assert cl > 0.05, f"C_L collapsed to {cl} at alpha={alpha}"
    # The separated plate carries the Hoerner normal force, not zero.
    hoerner = 2.0 * math.sin(math.radians(41.0)) * math.cos(math.radians(41.0)) ** 2
    assert hoerner == pytest.approx(0.7474, abs=1e-3)
    assert aero_domain.lift_coefficient(41.0, cfg) > 0.2


def test_post_stall_lift_is_odd_in_alpha() -> None:
    # cL0 = 0 removes the (legitimately even) camber term, so the whole
    # lift curve has to be odd: the airframe stalls at the same incidence
    # in both directions.
    cfg = AircraftConfig(cL0=0.0)
    for alpha in (5.0, 20.0, 41.0, 60.0):
        assert aero_domain.lift_coefficient(alpha, cfg) == pytest.approx(
            -aero_domain.lift_coefficient(-alpha, cfg), rel=1e-9
        )


# ---------------------------------------------------------------------------
# P1-7: Hoerner separated drag
# ---------------------------------------------------------------------------
def test_separated_drag_contains_the_hoerner_parasite_term() -> None:
    cfg = AircraftConfig()
    reynolds = 5.0e4
    cd0 = aero_domain.parasite_drag_coefficient(cfg, reynolds)
    alpha = 30.0
    value = aero_domain.separated_pressure_drag_coefficient(alpha, cfg, reynolds)
    hoerner = 2.0 * math.sin(math.radians(alpha)) ** 2 + cd0 * math.cos(math.radians(alpha))
    # The transition weight is 1 well behind the stall angle.
    assert value == pytest.approx(hoerner, rel=1e-6)
    # The old model reached only 0.10 at 30 deg (a factor 5 too small).
    assert value > 0.4


def test_reverse_flow_does_not_produce_negative_drag() -> None:
    cfg = AircraftConfig()
    value = aero_domain.separated_pressure_drag_coefficient(170.0, cfg, 5.0e4)
    assert value >= 0.0


# ---------------------------------------------------------------------------
# P1-9: the morphing is geometric
# ---------------------------------------------------------------------------
def test_morphing_does_not_scale_the_camber_or_the_zero_lift_moment() -> None:
    source = _source("flight_sim")
    assert "effective_cm0 = float(self.aircraft.cm0) * morph_factor" not in source
    assert "effective_cL0 = float(self.aircraft.cL0) * morph_factor" not in source


def test_morphing_parameters_are_configurable() -> None:
    cfg = AircraftConfig(
        aeroelastic_compliance=1.0,
        aeroelastic_morph_exponent=2.0,
        aeroelastic_pressure_scale_pa=250.0,
    )
    clamped = cfg.clamp_ranges()
    assert clamped.aeroelastic_morph_exponent == pytest.approx(2.0)
    assert clamped.aeroelastic_pressure_scale_pa == pytest.approx(250.0)
    # The values are honoured by the GPU path as well.
    source = _source("parallel_engine")
    assert "aeroelastic_morph_exponent" in source
    assert "aeroelastic_pressure_scale_pa" in source


# ---------------------------------------------------------------------------
# P1-10: the published Polhamus relation
# ---------------------------------------------------------------------------
def test_published_polhamus_relation_is_implemented() -> None:
    kp = 2.0 * math.pi
    kv = 0.8
    cfg = AircraftConfig(polhamus_kp=kp, polhamus_kv=kv, cL0=0.0)
    alpha = 6.0
    expected = kp * math.sin(math.radians(alpha)) * math.cos(math.radians(alpha)) ** 2 + kv * (
        math.sin(math.radians(alpha)) ** 2
    ) * math.cos(math.radians(alpha))
    assert aero_domain.lift_coefficient(alpha, cfg) == pytest.approx(expected, rel=1e-6)
    assert aero_domain.polhamus_lift_coefficient(alpha, cfg) == pytest.approx(expected, rel=1e-6)


def test_polhamus_term_keeps_its_own_sign_past_90_deg() -> None:
    kp = 2.0 * math.pi
    kv = 0.8
    # ``sin^2(a) cos(a)`` is negative beyond 90 deg: the vortex suction has
    # to *subtract* lift there.  Forcing the sign of alpha (the old
    # ``copysign``) returned +0.375 K_v instead of -0.375 K_v.
    with_vortex = aero_domain.polhamus_lift_coefficient(120.0, AircraftConfig(polhamus_kp=kp, polhamus_kv=kv))
    without_vortex = aero_domain.polhamus_lift_coefficient(120.0, AircraftConfig(polhamus_kp=kp, polhamus_kv=0.0))
    expected_kv = kv * math.sin(math.radians(120.0)) ** 2 * math.cos(math.radians(120.0))
    assert expected_kv < 0.0
    assert with_vortex - without_vortex == pytest.approx(expected_kv, rel=1e-9)
    assert with_vortex < without_vortex
