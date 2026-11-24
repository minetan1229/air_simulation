"""Rigorous audit & regression test suite for the milcy (v3) pipeline."""
from __future__ import annotations

from typing import List, Tuple
import pytest

from core.config_schema import AircraftConfig, EnvironmentConfig, derived_properties
from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    BehaviorDescriptor,
    EvaluationMetric,
    FlightBenchmarkResult,
    ParametricKeelSpec,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from geometry.origami_domain import FoldedFace
from geometry.v3_origami.adapter import (
    OrigamiAdapter,
    detect_strict_self_intersections,
    validate_tier0,
)
from geometry.v3_origami.compiler import (
    ParametricOrigamiCompiler,
    check_vertex_kawasaki_maekawa,
    gallivan_min_fold_length_m,
    paper_thickness_m,
)
from geometry.v3_origami.mcts import MCTSNode, MCTSOrigamiGenerator
from optimization.eval.robustness import RobustnessEvaluator
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
from physics.ground_contact import compute_ground_stations


def _make_metric(
    speed_mps: float = 6.0,
    flight_time_s: float = 4.0,
    max_ld_ratio: float = 8.0,
    steady_glide_fraction: float = 0.85,
    stability: float = 0.85,
    crashed: bool = False,
    diverged: bool = False,
    bounce_count: int = 0,
    max_abs_roll_deg: float = 5.0,
    ground_skim_fraction: float = 0.0,
) -> EvaluationMetric:
    return EvaluationMetric(
        speed_mps=speed_mps,
        flight_time_s=flight_time_s,
        max_ld_ratio=max_ld_ratio,
        steady_glide_fraction=steady_glide_fraction,
        stability=stability,
        distance_m=20.0,
        max_altitude_m=1.6,
        landed=not crashed,
        crashed=crashed,
        diverged=diverged,
        status="crashed" if crashed else "landed",
        bounce_count=bounce_count,
        max_abs_roll_deg=max_abs_roll_deg,
        ground_skim_fraction=ground_skim_fraction,
    )


def test_stage1_tier0_rejects_penetration_and_x_crossing() -> None:
    """Detect 3D X-crossing panels and reject penetrations in Tier 0."""
    face_a = FoldedFace(
        fid=0,
        poly3d=[(-0.05, 0.0, -0.02), (0.05, 0.0, -0.02), (0.05, 0.0, 0.02), (-0.05, 0.0, 0.02)],
        normal=(0.0, 1.0, 0.0),
        front=(0.0, 1.0, 0.0),
        level=0,
        group="wing_l",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.004,
    )
    face_b = FoldedFace(
        fid=1,
        poly3d=[(0.0, -0.05, -0.01), (0.0, 0.05, -0.01), (0.0, 0.05, 0.01), (0.0, -0.05, 0.01)],
        normal=(1.0, 0.0, 0.0),
        front=(1.0, 0.0, 0.0),
        level=0,
        group="wing_r",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.002,
    )
    hits = detect_strict_self_intersections([face_a, face_b], adjacency=[])
    assert len(hits) == 1
    assert hits[0]["type"] == "edge_piercing_face"

    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.014,
        keel_length_m=0.060,
        keel_layers=4,
        step_count=20,
        planform_area_m2=0.015,
        planform_aspect_ratio=2.16,
        penetrations=2,
    )
    cand = AircraftCandidate(
        candidate_id="penetrating-cand",
        config=ParametricOrigamiConfig(),
        step_count=20,
        geometry=geom,
    )
    valid, reasons = validate_tier0(cand)
    assert valid is False
    assert any("Self-intersection" in r for r in reasons)


def test_stage1_fold_failure_never_passes_tier0_via_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When fold_genome raises an error, adapter must NOT return a fake passing geometry."""
    import geometry.v3_origami.adapter as adapter_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("degenerate crease geometry")

    monkeypatch.setattr(adapter_mod, "fold_genome", _boom)
    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="broken-fold")
    assert cand.geometry is None
    assert cand.metadata["fold_failed"] is True
    assert cand.metadata["tier0_valid"] is False
    assert any("Fold engine failed" in r for r in cand.metadata["tier0_reasons"])


def test_stage1_gallivan_thickness_and_kawasaki_maekawa_enforcement() -> None:
    """Verify Kawasaki/Maekawa theorems and Gallivan paper-thickness bend limit."""
    k_ok, m_ok, err = check_vertex_kawasaki_maekawa([60.0, 120.0, 120.0, 60.0], [1, 1, 1, -1])
    assert k_ok is True
    assert m_ok is True
    assert err == pytest.approx(0.0, abs=1e-6)

    k_bad, m_bad, err_bad = check_vertex_kawasaki_maekawa([50.0, 130.0, 110.0, 70.0], [1, 1, -1, -1])
    assert k_bad is False
    assert m_bad is False
    assert err_bad == pytest.approx(20.0, abs=1e-6)

    t_heavy = paper_thickness_m(300.0)
    assert gallivan_min_fold_length_m(t_heavy, 6) > 0.20 * 0.297

    heavy_cfg = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6, taper_ratio=0.65, nose_length_ratio=0.20),
        paper_gsm=300.0,
    )
    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(heavy_cfg, candidate_id="too-thick")
    assert cand.geometry is not None
    assert cand.geometry.thickness_limit_ok is False
    assert cand.metadata["tier0_valid"] is False
    assert any("Gallivan" in r for r in cand.metadata["tier0_reasons"])


def test_stage1_compiler_preserves_elevon_sign_and_keel_lock() -> None:
    """Verify compiler preserves signed elevon sense and emits keel lock creases."""
    compiler = ParametricOrigamiCompiler()
    cfg_up = ParametricOrigamiConfig(
        keel=ParametricKeelSpec(has_lock_fold=True),
        wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=5.0),
    )
    cfg_down = ParametricOrigamiConfig(
        keel=ParametricKeelSpec(has_lock_fold=False),
        wing=ParametricWingSpec(elevon_left_deg=-5.0, elevon_right_deg=-5.0),
    )
    steps_up = compiler.compile_steps(cfg_up)
    steps_down = compiler.compile_steps(cfg_down)

    elev_up = [s for s in steps_up if s.set_group == "elevon_l"][0]
    elev_down = [s for s in steps_down if s.set_group == "elevon_l"][0]
    assert elev_up.mountain is True
    assert elev_down.mountain is False

    assert any(s.set_group == "keel_lock_l" for s in steps_up)
    assert not any(s.set_group == "keel_lock_l" for s in steps_down)


def test_stage1_mcts_bidirectional_preserves_wing_fields_and_deduplicates() -> None:
    """Verify MCTS expands bidirectionally, preserves wing fields, and deduplicates states."""
    base_cfg = ParametricOrigamiConfig(
        wing=ParametricWingSpec(
            wingspan_ratio=0.72,
            dihedral_deg=4.0,
            elevon_left_deg=4.2,
            elevon_right_deg=4.2,
            winglet_angle_deg=62.0,
            has_winglet=True,
        )
    )
    gen = MCTSOrigamiGenerator(seed=7)
    root = MCTSNode(config=base_cfg)
    visited = set()
    gen._expand_node(root, visited)
    descs = {c.action_desc for c in root.children}
    assert "nose_more_folds" in descs and "nose_fewer_folds" in descs
    assert "wing_higher_aspect" in descs and "wing_lower_aspect" in descs
    assert "wing_more_dihedral" in descs and "wing_less_dihedral" in descs

    for child in root.children:
        assert child.config.wing.elevon_left_deg == pytest.approx(4.2)
        assert child.config.wing.winglet_angle_deg == pytest.approx(62.0)

    count_before = len(root.children)
    gen._expand_node(root, visited)
    assert len(root.children) == count_before


def test_stage2_archive_rejects_crashed_invalid_and_oob_candidates() -> None:
    """Archive must reject crashed, invalid, out-of-bounds, and mid-speed unstable candidates."""
    grid = MAPElitesGridArchive(
        bins=(5, 5, 5),
        ranges=((0.10, 0.20), (0.60, 0.95), (0.20, 0.30)),
    )
    crashed_cand = AircraftCandidate(
        candidate_id="crashed-01",
        config=ParametricOrigamiConfig(),
        step_count=20,
        descriptor=BehaviorDescriptor(0.15, 0.80, 0.25),
        benchmark=FlightBenchmarkResult(
            low_speed=_make_metric(crashed=True),
            mid_speed=_make_metric(crashed=True),
            composite_score=0.0,
            valid=False,
        ),
    )
    assert grid.add_candidate(crashed_cand) is False
    assert len(grid.cells) == 0

    oob_cand = AircraftCandidate(
        candidate_id="oob-01",
        config=ParametricOrigamiConfig(),
        step_count=20,
        descriptor=BehaviorDescriptor(5.0, 0.80, 9.0),
        benchmark=FlightBenchmarkResult(
            low_speed=_make_metric(),
            mid_speed=_make_metric(speed_mps=10.0),
            composite_score=5.0,
            valid=True,
        ),
    )
    assert grid.add_candidate(oob_cand) is False
    assert len(grid.cells) == 0

    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.035,
        wingspan_m=0.095,
        chord_m=0.12,
        aspect_ratio=(0.095 ** 2) / 0.035,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.012,
        keel_length_m=0.060,
        keel_layers=4,
        step_count=20,
        planform_area_m2=0.015,
        planform_aspect_ratio=0.60,
    )
    mid_unstable = AircraftCandidate(
        candidate_id="mid-unstable",
        config=ParametricOrigamiConfig(),
        step_count=20,
        geometry=geom,
        benchmark=FlightBenchmarkResult(
            low_speed=_make_metric(stability=0.90),
            mid_speed=_make_metric(speed_mps=10.0, stability=0.40),
            composite_score=3.0,
            valid=True,
        ),
    )
    assert grid.add_candidate(mid_unstable) is False
    assert mid_unstable.descriptor is not None
    assert mid_unstable.descriptor.static_stability == pytest.approx(0.40)


def test_stage2_tiebreak_epsilon_and_ground_skim_anti_hack() -> None:
    """Verify epsilon-guarded replacement, anti-hack penalties, and archive purge."""
    clean = _make_metric(bounce_count=0, max_abs_roll_deg=5.0, ground_skim_fraction=0.05)
    bouncy = _make_metric(bounce_count=3, max_abs_roll_deg=5.0, ground_skim_fraction=0.05)
    tumbling = _make_metric(bounce_count=0, max_abs_roll_deg=110.0, ground_skim_fraction=0.05)
    skimming = _make_metric(bounce_count=0, max_abs_roll_deg=5.0, ground_skim_fraction=0.90)

    assert clean.deterministic_score > bouncy.deterministic_score * 3.0
    assert clean.deterministic_score > tumbling.deterministic_score * 3.0
    assert clean.deterministic_score > skimming.deterministic_score * 3.0

    grid = MAPElitesGridArchive(
        bins=(4, 4, 4),
        ranges=((0.10, 0.20), (0.60, 0.95), (0.20, 0.30)),
        min_improvement_eps=1.0e-4,
    )
    base_cand = AircraftCandidate(
        candidate_id="elite-base",
        config=ParametricOrigamiConfig(),
        step_count=20,
        descriptor=BehaviorDescriptor(0.15, 0.85, 0.25),
        benchmark=FlightBenchmarkResult(
            low_speed=_make_metric(stability=0.90),
            mid_speed=_make_metric(speed_mps=10.0, stability=0.90),
            composite_score=5.000000000,
            valid=True,
        ),
    )
    noisy_cand = AircraftCandidate(
        candidate_id="elite-noisy",
        config=ParametricOrigamiConfig(),
        step_count=20,
        descriptor=BehaviorDescriptor(0.15, 0.85, 0.25),
        benchmark=FlightBenchmarkResult(
            low_speed=_make_metric(stability=0.75),
            mid_speed=_make_metric(speed_mps=10.0, stability=0.75),
            composite_score=5.000000001,
            valid=True,
        ),
    )
    assert grid.add_candidate(base_cand) is True
    assert grid.add_candidate(noisy_cand) is False
    assert grid.get_global_best() == base_cand

    base_cand.benchmark = FlightBenchmarkResult(
        low_speed=_make_metric(crashed=True),
        mid_speed=_make_metric(crashed=True),
        composite_score=0.0,
        valid=False,
    )
    assert grid.purge_invalid_elites() == 1
    assert len(grid.cells) == 0


def test_stage3_nelder_mead_escapes_stall_plateau_and_respects_bounds() -> None:
    """Optimizer must NOT false-converge in a zero-score stall cliff and must keep bounds."""
    evaluated_points: List[Tuple[float, float]] = []

    class _StallCliffBenchmark:
        def evaluate_candidate(self, cand: AircraftCandidate) -> FlightBenchmarkResult:
            e = cand.config.wing.elevon_left_deg
            d = cand.config.wing.dihedral_deg
            evaluated_points.append((e, d))
            if e >= 5.0:
                score = 0.0
                valid = False
            else:
                score = max(0.0, 6.0 - 0.5 * ((e - 1.5) ** 2 + (d - 3.0) ** 2))
                valid = score > 0.0
            m = _make_metric(crashed=not valid)
            res = FlightBenchmarkResult(low_speed=m, mid_speed=m, composite_score=score, valid=valid)
            cand.benchmark = res
            return res

    adapter = OrigamiAdapter()
    stalled_cfg = ParametricOrigamiConfig(
        wing=ParametricWingSpec(elevon_left_deg=8.0, elevon_right_deg=8.0, dihedral_deg=0.0)
    )
    cand = adapter.compile_candidate(stalled_cfg, candidate_id="stall-start")
    opt = NelderMeadTrimOptimizer(
        adapter=adapter,
        benchmark=_StallCliffBenchmark(),
        max_iter=25,
        elevon_bounds=(-4.0, 8.0),
        dihedral_bounds=(0.0, 8.0),
        max_restarts=2,
    )
    res = opt.optimize_trim(cand)
    assert res.initial_score == pytest.approx(0.0)
    assert res.optimized_score > 4.5
    assert -4.0 <= res.optimal_elevon_deg <= 8.0
    assert 0.0 <= res.optimal_dihedral_deg <= 8.0
    for e, d in evaluated_points:
        assert -4.0 <= e <= 8.0
        assert 0.0 <= d <= 8.0


def test_stage4_robustness_multi_azimuth_multi_seed_and_zero_crash_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify multi-direction x multi-seed Dryden ensemble, symmetric jitter, and 0.0 crash floor."""
    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="rob-audit")
    evaluator = RobustnessEvaluator(
        duration_s=0.15,
        dryden_wind_directions_deg=(0.0, 90.0, 180.0, 270.0),
        dryden_seeds=(101, 202),
    )
    report = evaluator.evaluate_candidate(cand)
    assert len(report.details["dryden_runs"]) == 8
    assert len(report.details["jitter_runs"]) == 4
    assert report.details["keel_height_m"] == pytest.approx(cand.geometry.keel_height_m)

    import optimization.eval.robustness as rob_mod

    class _AlwaysCrashSim:
        def __init__(self, *args, **kwargs) -> None:
            self.aircraft = kwargs.get("aircraft")

        def simulate(self, duration_s: float = 6.0) -> None:
            return None

        def metrics(self) -> dict:
            return {"flight_time_s": 0.1, "crashed": True, "diverged": False, "stalled": True}

    monkeypatch.setattr(rob_mod, "FlightSimulator", _AlwaysCrashSim)
    crash_report = evaluator.evaluate_candidate(cand)
    assert crash_report.dryden_survival_rate == pytest.approx(0.0)
    assert crash_report.jitter_robustness_score == pytest.approx(0.0)
    assert crash_report.high_pitch_recovery_rate == pytest.approx(0.0)
    assert crash_report.overall_robustness_score == pytest.approx(0.0)


def test_stage4_keel_depth_coupled_to_ground_stations() -> None:
    """Deep origami keel must trigger ground contact when CG altitude is less than keel_height_m."""
    env = EnvironmentConfig(ground_level_m=0.0)
    level_quat = (1.0, 0.0, 0.0, 0.0)

    deep_cfg = AircraftConfig(keel_height_m=0.045)
    deep_derived = derived_properties(deep_cfg)
    assert deep_derived["keel_height_m"] == pytest.approx(0.045)

    deep_stations = compute_ground_stations((0.0, 0.0, -0.030), level_quat, env, deep_derived)
    assert deep_stations["alt_keel"] == pytest.approx(-0.015, abs=1e-6)
    assert deep_stations["min_alt"] <= -0.015

    shallow_cfg = AircraftConfig(keel_height_m=0.010)
    shallow_derived = derived_properties(shallow_cfg)
    shallow_stations = compute_ground_stations((0.0, 0.0, -0.030), level_quat, env, shallow_derived)
    assert shallow_stations["alt_keel"] == pytest.approx(0.020, abs=1e-6)
    assert shallow_stations["min_alt"] > 0.0
