"""Milcy Falsification W2 Benchmark Tests.

Zero-placeholder pytest tests validating benchmark physics, origami pipeline,
anti-cheat penalties, and robustness evaluation parity.
"""

import math
import pytest
from typing import Any, Dict, List, Optional, Tuple

# Core interfaces and data structures
from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    ParametricOrigamiConfig,
    ParametricNoseSpec,
    ParametricKeelSpec,
    ParametricWingSpec,
    EvaluationMetric,
    FlightBenchmarkResult,
)

# Physics benchmark modules
from physics.benchmark.benchmark import _build_aircraft_config as physics_build_aircraft_config
from physics.benchmark.benchmark import evaluate_horizontal_glide as physics_evaluate_horizontal_glide
from physics.flight_sim import FlightSimulator, helmbold_cl_alpha
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    derived_properties,
)

# v3 modules
from v3.physics.benchmark import _build_aircraft_config as v3_build_aircraft_config
from v3.physics.benchmark import evaluate_horizontal_glide as v3_evaluate_horizontal_glide
from v3.origami.adapter import OrigamiAdapter, validate_tier0
from v3.origami.compiler import ParametricOrigamiCompiler

# Optimization robustness
from optimization.eval.robustness import RobustnessEvaluator


# ---------------------------------------------------------------------------
# Test 1: Ghost trailing fold & cm_alpha consistency
# ---------------------------------------------------------------------------
def test_f02_benchmark_ghost_trailing_fold_and_cm_alpha() -> None:
    """Test both benchmark implementations for CG position, static margin, and cm_alpha."""
    taper_ratio = 0.70
    chord = 0.20
    nominal_ac_from_nose = 0.55 * chord  # 0.11 m

    # Candidate A: Forward CG at 0.45c -> x_from_nose = 0.09, static_margin = +0.10
    geom_a = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=chord,
        aspect_ratio=2.16,
        cg_m=(0.11, 0.0, 0.005),  # nose_x - cg_x = 0.20 - 0.11 = 0.09 = 0.45 * 0.20
        keel_height_m=0.015,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
        planform_area_m2=0.015,
        planform_aspect_ratio=2.16,
        nose_tip_x_m=0.20,
    )

    # Candidate B: Aft CG at 0.65c -> x_from_nose = 0.13, static_margin = -0.10
    geom_b = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=chord,
        aspect_ratio=2.16,
        cg_m=(0.07, 0.0, 0.005),  # nose_x - cg_x = 0.20 - 0.07 = 0.13 = 0.65 * 0.20
        keel_height_m=0.015,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
        planform_area_m2=0.015,
        planform_aspect_ratio=2.16,
        nose_tip_x_m=0.20,
    )

    config = ParametricOrigamiConfig()
    cand_a = AircraftCandidate(
        candidate_id="test_fwd_cg",
        config=config,
        step_count=16,
        geometry=geom_a,
    )
    cand_b = AircraftCandidate(
        candidate_id="test_aft_cg",
        config=config,
        step_count=16,
        geometry=geom_b,
    )

    cl_alpha = helmbold_cl_alpha(2.16, taper_ratio)

    for build_fn in (physics_build_aircraft_config, v3_build_aircraft_config):
        # Candidate A: Forward CG
        ac_a = build_fn(cand_a)
        assert ac_a.fold_trailing_deg == 0.0
        assert ac_a.static_margin == pytest.approx(0.10, abs=1e-6)
        expected_cm_alpha_a = -0.10 * cl_alpha
        assert ac_a.cm_alpha == pytest.approx(expected_cm_alpha_a, abs=1e-6)
        assert ac_a.cg_offset_x == 0.0

        # Candidate B: Aft CG
        ac_b = build_fn(cand_b)
        assert ac_b.fold_trailing_deg == 0.0
        assert ac_b.static_margin == pytest.approx(-0.10, abs=1e-6)
        expected_cm_alpha_b = 0.10 * cl_alpha
        assert ac_b.cm_alpha == pytest.approx(expected_cm_alpha_b, abs=1e-6)
        assert ac_b.cm_alpha > 0.0
        assert ac_b.cg_offset_x == 0.0


# ---------------------------------------------------------------------------
# Test 2: Legacy v3 package divergence
# ---------------------------------------------------------------------------
def test_f04_legacy_v3_package_divergence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test v3.origami.adapter, v3.origami.compiler, and v3.physics.benchmark divergence."""
    # (a) v3.origami.adapter
    # Monkeypatch fold_genome to raise RuntimeError
    import v3.origami.adapter as adapter_module

    def fold_genome_boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("fold boom")

    with monkeypatch.context() as m:
        m.setattr(adapter_module, "fold_genome", fold_genome_boom)

        adapter = OrigamiAdapter()
        cand = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="boom")
        assert cand.geometry is None
        assert cand.metadata.get("fold_failed") is True
        assert cand.metadata.get("tier0_valid") is False

    # Validate validate_tier0 rejects various failure modes
    geom_penetration = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
        penetrations=1,
    )
    cand_penetration = AircraftCandidate(
        candidate_id="pen",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=geom_penetration,
    )
    valid, reasons = validate_tier0(cand_penetration)
    assert valid is False
    assert any("penetration" in r.lower() for r in reasons)

    geom_thickness = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
        thickness_limit_ok=False,
    )
    cand_thickness = AircraftCandidate(
        candidate_id="thick",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=geom_thickness,
    )
    valid, reasons = validate_tier0(cand_thickness)
    assert valid is False

    geom_flat = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
        flat_foldable_ok=False,
    )
    cand_flat = AircraftCandidate(
        candidate_id="flat",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=geom_flat,
    )
    valid, reasons = validate_tier0(cand_flat)
    assert valid is False

    geom_fold_invalid = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
        fold_valid=False,
    )
    cand_fold_invalid = AircraftCandidate(
        candidate_id="fold_invalid",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=geom_fold_invalid,
    )
    valid, reasons = validate_tier0(cand_fold_invalid)
    assert valid is False

    # Patch detect_penetrations to return a hit
    def detect_penetrations_hit(*args: Any, **kwargs: Any) -> List[Dict[str, int]]:
        return [{"face_a": 0, "face_b": 1}]

    with monkeypatch.context() as m:
        m.setattr(adapter_module, "detect_penetrations", detect_penetrations_hit)
        geom_detect = AirframeGeometry(
            mass_kg=0.005,
            wing_area_m2=0.015,
            wingspan_m=0.18,
            chord_m=0.12,
            aspect_ratio=2.16,
            cg_m=(0.08, 0.0, 0.005),
            keel_height_m=0.015,
            keel_length_m=0.06,
            keel_layers=4,
            step_count=16,
        )
        cand_detect = AircraftCandidate(
            candidate_id="detect",
            config=ParametricOrigamiConfig(),
            step_count=16,
            geometry=geom_detect,
            raw_genome=type("obj", (object,), {"faces": []})(),
        )
        valid, reasons = validate_tier0(cand_detect)
        assert valid is False

    # Test high GSM thickness limit failure
    config_high_gsm = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6, nose_length_ratio=0.20),
        paper_gsm=300.0,
    )
    adapter2 = OrigamiAdapter()
    cand_high_gsm = adapter2.compile_candidate(config_high_gsm, candidate_id="high_gsm")
    assert cand_high_gsm.geometry is not None
    assert cand_high_gsm.geometry.thickness_limit_ok is False
    assert cand_high_gsm.metadata.get("tier0_valid") is False

    # (b) v3.origami.compiler
    compiler = ParametricOrigamiCompiler()

    # Test wingspan_ratio effect on wing_u
    config_wide = ParametricOrigamiConfig(wing=ParametricWingSpec(wingspan_ratio=0.55))
    config_narrow = ParametricOrigamiConfig(wing=ParametricWingSpec(wingspan_ratio=0.88))

    steps_wide = compiler.compile_steps(config_wide)
    steps_narrow = compiler.compile_steps(config_narrow)

    wing_l_wide = next(s for s in steps_wide if s.set_group == "wing_l")
    wing_l_narrow = next(s for s in steps_narrow if s.set_group == "wing_l")

    # wing_u = 0.30 + 0.18 * span_ratio, so larger span_ratio -> larger wing_u
    assert wing_l_narrow.crease_line[0][0] > wing_l_wide.crease_line[0][0]

    # Test elevon mountain/valley
    config_elevon_up = ParametricOrigamiConfig(wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=5.0))
    config_elevon_down = ParametricOrigamiConfig(wing=ParametricWingSpec(elevon_left_deg=-5.0, elevon_right_deg=-5.0))
    config_elevon_diff = ParametricOrigamiConfig(wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=-3.0))

    steps_up = compiler.compile_steps(config_elevon_up)
    steps_down = compiler.compile_steps(config_elevon_down)
    steps_diff = compiler.compile_steps(config_elevon_diff)

    elevon_l_up = next(s for s in steps_up if s.set_group == "elevon_l")
    elevon_l_down = next(s for s in steps_down if s.set_group == "elevon_l")
    elevon_l_diff = next(s for s in steps_diff if s.set_group == "elevon_l")
    elevon_r_diff = next(s for s in steps_diff if s.set_group == "elevon_r")

    # Positive elevon_deg -> trailing edge up -> mountain fold (mountain=True)
    # Negative elevon_deg -> trailing edge down -> valley fold (mountain=False)
    assert elevon_l_up.mountain is True
    assert elevon_l_down.mountain is False

    # Differential elevon
    assert elevon_l_diff.mountain is True
    assert elevon_r_diff.mountain is False
    assert elevon_l_diff.fold_angle_deg != elevon_r_diff.fold_angle_deg

    # (c) v3.physics.benchmark
    geom_c = AirframeGeometry(
        mass_kg=0.006,
        wing_area_m2=0.030,
        wingspan_m=0.18,
        chord_m=0.20,
        aspect_ratio=1.08,
        cg_m=(0.14, 0.0, 0.005),  # nose_tip_x_m=0.25, x_from_nose = 0.11 = 0.55 * 0.20
        keel_height_m=0.020,
        keel_length_m=0.060,
        keel_layers=4,
        step_count=16,
        planform_area_m2=0.020,
        planform_aspect_ratio=1.62,
        nose_tip_x_m=0.25,
    )
    config_c = ParametricOrigamiConfig()
    cand_c = AircraftCandidate(
        candidate_id="test_c",
        config=config_c,
        step_count=16,
        geometry=geom_c,
    )

    ac = v3_build_aircraft_config(cand_c)
    assert ac.wing_area_m2 == pytest.approx(0.020)
    assert ac.aspect_ratio == pytest.approx(1.62)
    assert ac.static_margin == pytest.approx(0.0, abs=1e-6)
    assert ac.roll_inertia_kgm2 > 0.0

    # Monkeypatch FlightSimulator to capture settings
    captured_settings: Dict[str, Any] = {}

    class DummySim:
        def __init__(self, aircraft: AircraftConfig, environment: EnvironmentConfig, launch: LaunchConfig, settings: Optional[SimulationSettings] = None) -> None:
            captured_settings["settings"] = settings
            self._aircraft = aircraft
            self._environment = environment
            self._launch = launch

        def simulate(self, duration_s: float) -> None:
            pass

        def metrics(self) -> Dict[str, Any]:
            return {
                "flight_time_s": 1.0,
                "distance_m": 5.0,
                "max_altitude_m": 1.5,
                "max_ld_ratio": 5.0,
                "crashed": False,
                "diverged": False,
                "landed": True,
                "status": "landed",
            }

        def history(self) -> List[Any]:
            return []

    monkeypatch.setattr("v3.physics.benchmark.FlightSimulator", DummySim)

    v3_evaluate_horizontal_glide(ac, speed_mps=6.0, duration_s=1.0, dt_max=0.005)
    assert captured_settings.get("settings") is not None
    assert captured_settings["settings"].dt_max == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# Test 3: Anti-cheat penalties wired to benchmark
# ---------------------------------------------------------------------------
def test_f17_antichat_penalties_wired_to_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test anti-cheat penalties (bounce, roll, ground skim) are wired to benchmark."""
    # (a) Real FlightSimulator flight to verify metrics exist
    ac = AircraftConfig(
        keel_height_m=0.015,
        dihedral_deg=3.0,
        elevon_deg=2.0,
        fold_trailing_deg=0.0,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
    launch = LaunchConfig(
        launch_speed=6.0,
        launch_angle_deg=-5.0,
        launch_roll_deg=30.0,
        launch_height_m=0.3,
    )
    settings = SimulationSettings(duration_s=1.0, dt_max=0.02)

    sim = FlightSimulator(aircraft=ac, environment=env, launch=launch, settings=settings)
    sim.simulate(duration_s=1.0)
    m = sim.metrics()

    assert "bounce_count" in m
    assert "max_abs_roll_deg" in m
    assert "ground_skim_fraction" in m
    assert m["max_abs_roll_deg"] >= 25.0

    # (b) Monkeypatch FlightSimulator for both benchmark modules
    for benchmark_module_name in ("physics.benchmark.benchmark", "v3.physics.benchmark"):
        if benchmark_module_name == "physics.benchmark.benchmark":
            import physics.benchmark.benchmark as bm
        else:
            import v3.physics.benchmark as bm

        captured_metrics: List[Dict[str, Any]] = []

        class CapturingSim:
            def __init__(self, aircraft: AircraftConfig, environment: EnvironmentConfig, launch: LaunchConfig, settings: Optional[SimulationSettings] = None) -> None:
                self._aircraft = aircraft
                self._environment = environment
                self._launch = launch
                self._settings = settings

            def simulate(self, duration_s: float) -> None:
                pass

            def metrics(self) -> Dict[str, Any]:
                if len(captured_metrics) == 0:
                    # Clean flight
                    return {
                        "flight_time_s": 4.0,
                        "distance_m": 20.0,
                        "max_ld_ratio": 8.0,
                        "bounce_count": 0,
                        "max_abs_roll_deg": 5.0,
                        "ground_skim_fraction": 0.05,
                        "crashed": False,
                        "diverged": False,
                        "stalled": False,
                        "landed": True,
                        "status": "landed",
                    }
                else:
                    # Hacked flight
                    return {
                        "flight_time_s": 4.0,
                        "distance_m": 20.0,
                        "max_ld_ratio": 8.0,
                        "bounce_count": 3,
                        "max_abs_roll_deg": 110.0,
                        "ground_skim_fraction": 0.90,
                        "crashed": False,
                        "diverged": False,
                        "stalled": False,
                        "landed": True,
                        "status": "landed",
                    }

            def history(self) -> List[Any]:
                return []

        monkeypatch.setattr(bm, "FlightSimulator", CapturingSim)

        ac_test = AircraftConfig()
        clean = bm.evaluate_horizontal_glide(ac_test, speed_mps=6.0, duration_s=1.0, dt_max=0.02)
        captured_metrics.append({})
        hacked = bm.evaluate_horizontal_glide(ac_test, speed_mps=6.0, duration_s=1.0, dt_max=0.02)

        assert hacked.bounce_count == 3
        assert hacked.max_abs_roll_deg == pytest.approx(110.0)
        assert hacked.ground_skim_fraction == pytest.approx(0.90)
        assert hacked.deterministic_score < clean.deterministic_score / 10.0


# ---------------------------------------------------------------------------
# Test 4: Benchmark robustness parity and wind log
# ---------------------------------------------------------------------------
def test_f25_benchmark_robustness_parity_and_wind_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test benchmark robustness parity between implementations and wind logging."""
    # (a) Verify keel_height_m propagation
    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.042,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
    )
    config = ParametricOrigamiConfig()
    cand = AircraftCandidate(
        candidate_id="keel_test",
        config=config,
        step_count=16,
        geometry=geom,
    )

    ac_physics = physics_build_aircraft_config(cand)
    ac_v3 = v3_build_aircraft_config(cand)

    assert ac_physics.keel_height_m == pytest.approx(0.042)
    assert ac_v3.keel_height_m == pytest.approx(0.042)

    # (b) Monkeypatch optimization.eval.robustness.FlightSimulator
    import optimization.eval.robustness as robustness_module

    recorded_calls: List[Dict[str, Any]] = []

    class StubSimulator:
        def __init__(self, aircraft: AircraftConfig, environment: EnvironmentConfig, launch: LaunchConfig, settings: Optional[SimulationSettings] = None, *args: Any, **kwargs: Any) -> None:
            self._aircraft = aircraft
            self._environment = environment
            self._launch = launch
            recorded_calls.append({
                "environment": environment,
                "settings": settings,
            })

        def simulate(self, duration_s: float) -> None:
            pass

        def metrics(self) -> Dict[str, Any]:
            return {
                "flight_time_s": 1.5,
                "distance_m": 6.0,
                "crashed": False,
                "diverged": False,
                "stalled": False,
            }

    monkeypatch.setattr(robustness_module, "FlightSimulator", StubSimulator)

    evaluator = RobustnessEvaluator(n_samples=10, duration_s=1.0, dt_max=0.005)
    report = evaluator.evaluate_candidate(cand)

    # Verify every recorded call has settings with dt_max
    for call in recorded_calls:
        assert call["environment"] is not None
        assert call["settings"] is not None and call["settings"].dt_max == pytest.approx(0.005)

    # Check Dryden runs 8 and 9 (the Monte Carlo ones after the 8 fixed scenarios)
    # Fixed scenarios: 4 wind directions * 2 seeds = 8 runs (indices 0-7)
    # Monte Carlo starts at index 8
    assert len(report.details["dryden_runs"]) >= 10

    # These should have wind_direction_deg matching the environment passed to FlightSimulator
    for idx in (8, 9):
        assert report.details["dryden_runs"][idx]["wind_direction_deg"] == pytest.approx(recorded_calls[idx]["environment"].wind_direction_deg)


# ---------------------------------------------------------------------------
# Test 5: Robustness survival requires valid glide
# ---------------------------------------------------------------------------
def test_f26_robustness_survival_requires_valid_glide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test robustness evaluator requires valid glide for non-zero scores."""
    import optimization.eval.robustness as robustness_module

    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.06,
        keel_layers=4,
        step_count=16,
    )
    config = ParametricOrigamiConfig()
    cand = AircraftCandidate(
        candidate_id="survival_test",
        config=config,
        step_count=16,
        geometry=geom,
    )

    call_count = [0]

    class FlightMetricsStub:
        def __init__(self, metrics: Dict[str, Any]) -> None:
            self._metrics = metrics
            call_count[0] += 1

        def metrics(self) -> Dict[str, Any]:
            return self._metrics

    def make_stub(metrics: Dict[str, Any]):
        class StubSim:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self._stub = FlightMetricsStub(metrics)

            def simulate(self, duration_s: float) -> None:
                pass

            def metrics(self) -> Dict[str, Any]:
                return self._stub.metrics()

        return StubSim

    # Case 1: Short flight time (0.3s) and short distance (0.5m) -> overall = 0.0
    monkeypatch.setattr(robustness_module, "FlightSimulator", make_stub({
        "flight_time_s": 0.3,
        "distance_m": 0.5,
        "crashed": False,
        "diverged": False,
        "stalled": False,
    }))
    call_count[0] = 0
    evaluator = RobustnessEvaluator(n_samples=8, duration_s=2.0)
    report = evaluator.evaluate_candidate(cand)
    assert report.dryden_survival_rate == 0.0
    assert report.jitter_robustness_score == 0.0
    assert report.high_pitch_recovery_rate == 0.0
    assert report.overall_robustness_score == 0.0

    # Case 2: Short distance (1.5s, 1.0m) -> overall = 0.0
    monkeypatch.setattr(robustness_module, "FlightSimulator", make_stub({
        "flight_time_s": 1.5,
        "distance_m": 1.0,
        "crashed": False,
        "diverged": False,
        "stalled": False,
    }))
    call_count[0] = 0
    evaluator = RobustnessEvaluator(n_samples=8, duration_s=2.0)
    report = evaluator.evaluate_candidate(cand)
    assert report.overall_robustness_score == 0.0

    # Case 3: Short time (0.5s, 5.0m) -> overall = 0.0
    monkeypatch.setattr(robustness_module, "FlightSimulator", make_stub({
        "flight_time_s": 0.5,
        "distance_m": 5.0,
        "crashed": False,
        "diverged": False,
        "stalled": False,
    }))
    call_count[0] = 0
    evaluator = RobustnessEvaluator(n_samples=8, duration_s=2.0)
    report = evaluator.evaluate_candidate(cand)
    assert report.overall_robustness_score == 0.0

    # Case 4: Valid glide (1.5s, 6.0m) -> all scores = 1.0
    monkeypatch.setattr(robustness_module, "FlightSimulator", make_stub({
        "flight_time_s": 1.5,
        "distance_m": 6.0,
        "crashed": False,
        "diverged": False,
        "stalled": False,
    }))
    call_count[0] = 0
    evaluator = RobustnessEvaluator(n_samples=8, duration_s=2.0)
    report = evaluator.evaluate_candidate(cand)
    assert report.dryden_survival_rate == pytest.approx(1.0)
    assert report.jitter_robustness_score == pytest.approx(1.0)
    assert report.high_pitch_recovery_rate == pytest.approx(1.0)
    assert report.overall_robustness_score == pytest.approx(1.0)
