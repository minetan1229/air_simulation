"""Iteration 2 Part 1 falsification tests for heterogeneous batch, grid archive, and Nelder-Mead trim."""

from dataclasses import replace
import numpy as np
import pytest

from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    BehaviorDescriptor,
    EvaluationMetric,
    FlightBenchmarkResult,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from geometry.v3_origami.adapter import OrigamiAdapter
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
from physics import flight_sim, gpu_dynamics
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


def test_i2_01_heterogeneous_batch_munk_masked_by_apparent_active() -> None:
    cfg_a = AircraftConfig(
        added_mass_enabled=True,
        apparent_inertia_enabled=True,
        munk_coupling_enabled=False,
        aeroelastic_compliance=0.0,
        aeroelastic_flex=0.0,
        aeroelastic_twist=0.0,
        unsteady_aero_enabled=False,
        dynamic_damping_enabled=False,
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.20,
    )
    cfg_b = AircraftConfig(
        added_mass_enabled=False,
        apparent_inertia_enabled=False,
        munk_coupling_enabled=True,
        aeroelastic_compliance=0.0,
        aeroelastic_flex=0.0,
        aeroelastic_twist=0.0,
        unsteady_aero_enabled=False,
        dynamic_damping_enabled=False,
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.20,
    )

    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0, gravity=0.0)
    launch = LaunchConfig(launch_speed=7.0, launch_angle_deg=8.0, launch_height_m=1.5)
    settings = SimulationSettings()

    y = np.array(
        [0.0, 0.0, -2.0, 6.0, 1.5, 0.8, 1.0, 0.0, 0.0, 0.0, 0.4, 0.3, 0.2], dtype=np.float64
    )

    sim_a = flight_sim.FlightSimulator(cfg_a, env, launch, settings)
    sim_b = flight_sim.FlightSimulator(cfg_b, env, launch, settings)

    c_cpu_a = sim_a._compute_dynamics(y, 0.0)
    c_cpu_b = sim_b._compute_dynamics(y, 0.0)

    params = gpu_dynamics.build_params(np, [cfg_a, cfg_b], env)
    y_batch = np.stack([y, y], axis=0)
    w_gpu = gpu_dynamics.wind_batch(np, params, y_batch[:, :3], 0.0)
    c_gpu = gpu_dynamics._evaluate_state(np, params, y_batch, w_gpu)

    np.testing.assert_allclose(
        c_cpu_a["acceleration"], c_gpu["acceleration"][0], rtol=1e-10, atol=1e-10
    )
    np.testing.assert_allclose(
        c_cpu_a["angular_acceleration"], c_gpu["angular_acceleration"][0], rtol=1e-10, atol=1e-10
    )
    np.testing.assert_allclose(
        c_cpu_b["acceleration"], c_gpu["acceleration"][1], rtol=1e-10, atol=1e-10
    )
    np.testing.assert_allclose(
        c_cpu_b["angular_acceleration"], c_gpu["angular_acceleration"][1], rtol=1e-10, atol=1e-10
    )

    m_vec = np.array([sim_b._mass, sim_b._mass, sim_b._mass], dtype=np.float64)
    I_eff = sim_b._inertia

    v_b = y[3:6]
    omega = y[10:13]
    ext_power = float(np.dot(v_b, c_cpu_b["force_body"]) + np.dot(omega, c_cpu_b["moment_body"]))

    a_b = c_gpu["acceleration"][1]
    alpha_b = c_gpu["angular_acceleration"][1]
    dv_b = a_b - np.cross(omega, v_b)
    dT_trans = float(np.dot(v_b * m_vec, dv_b))
    dT_rot = float(np.dot(omega, I_eff @ alpha_b + np.cross(omega, I_eff @ omega)))

    assert abs((dT_trans + dT_rot) - ext_power) < 1e-11


def _make_metric(stability: float = 0.70) -> EvaluationMetric:
    return EvaluationMetric(
        speed_mps=10.0,
        flight_time_s=5.0,
        max_ld_ratio=8.0,
        steady_glide_fraction=0.6,
        stability=stability,
        distance_m=50.0,
        max_altitude_m=2.0,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
        bounce_count=0,
        max_abs_roll_deg=5.0,
        ground_skim_fraction=0.05,
    )


def _make_valid_candidate(candidate_id: str = "valid") -> AircraftCandidate:
    geometry = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.20,
        chord_m=0.10,
        aspect_ratio=2.5,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.010,
        keel_length_m=0.050,
        keel_layers=4,
        step_count=12,
        planform_area_m2=0.012,
        planform_aspect_ratio=3.0,
        penetrations=0,
        fold_valid=True,
        thickness_limit_ok=True,
        flat_foldable_ok=True,
        nose_tip_x_m=0.1485,
    )
    descriptor = BehaviorDescriptor(
        wing_loading_kg_m2=0.4167,
        static_stability=0.65,
        aspect_ratio=2.5,
    )
    benchmark = FlightBenchmarkResult(
        low_speed=_make_metric(0.75),
        mid_speed=_make_metric(0.60),
        composite_score=5.0,
        valid=True,
        rejection_reason="",
    )
    config = ParametricOrigamiConfig()
    return AircraftCandidate(
        candidate_id=candidate_id,
        config=config,
        step_count=12,
        geometry=geometry,
        descriptor=descriptor,
        benchmark=benchmark,
        metadata={"tier0_valid": True},
    )


def test_i2_07_grid_archive_rejects_nan_none_negative_fields() -> None:
    grid = MAPElitesGridArchive(
        bins=(4, 4, 4),
        ranges=((0.12, 0.28), (0.25, 0.95), (0.45, 1.10)),
    )

    overrides_list = [
        {"penetrations": -1},
        {"penetrations": None},
        {"keel_layers": float("nan")},
        {"keel_layers": None},
        {"keel_height_m": None},
        {"keel_length_m": None},
    ]

    for overrides in overrides_list:
        cand = _make_valid_candidate(f"invalid-{len(overrides)}")
        cand.geometry = replace(cand.geometry, **overrides)
        cand.metadata = {"tier0_valid": True}

        assert grid._is_candidate_valid(cand) is False
        assert grid.add_candidate(cand) is False


def test_i2_08_nelder_mead_descriptor_uses_planform_true() -> None:
    config = ParametricOrigamiConfig(wing=ParametricWingSpec(dihedral_deg=5.0))
    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(config)

    class SteppedBenchmark:
        def __init__(self) -> None:
            self.call_count = 0

        def evaluate_candidate(self, candidate: AircraftCandidate) -> FlightBenchmarkResult:
            self.call_count += 1
            if self.call_count == 1:
                return FlightBenchmarkResult(
                    low_speed=_make_metric(0.75),
                    mid_speed=_make_metric(0.60),
                    composite_score=1.0,
                    valid=True,
                    rejection_reason="",
                )
            return FlightBenchmarkResult(
                low_speed=_make_metric(0.75),
                mid_speed=_make_metric(0.60),
                composite_score=3.0,
                valid=True,
                rejection_reason="",
            )

    bench = SteppedBenchmark()
    optimizer = NelderMeadTrimOptimizer(adapter=adapter, benchmark=bench, max_iter=2)
    res = optimizer.optimize_trim(cand)

    assert res.optimized_score == pytest.approx(3.0)
    assert cand.descriptor is not None

    expected = cand.geometry.compute_behavior_descriptor(stability=0.60, use_planform=True)

    assert cand.descriptor.wing_loading_kg_m2 == pytest.approx(
        expected.wing_loading_kg_m2, rel=1e-12, abs=1e-12
    )
    assert cand.descriptor.static_stability == pytest.approx(
        expected.static_stability, rel=1e-12, abs=1e-12
    )
    assert cand.descriptor.aspect_ratio == pytest.approx(
        expected.aspect_ratio, rel=1e-12, abs=1e-12
    )
