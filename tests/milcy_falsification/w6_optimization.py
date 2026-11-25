import pytest
import yaml
import inspect
import random
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    BehaviorDescriptor,
    EvaluationMetric,
    FlightBenchmarkResult,
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from geometry.v3_origami.adapter import OrigamiAdapter, validate_tier0
from geometry.v3_origami.mcts import MCTSOrigamiGenerator
from optimization.map_elites.engine import MAPElitesEngine
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.map_elites.mutation import mutate_parametric_config
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
import optimization.cli as cli_mod
import tools.overnight_milcy_loop as overnight_mod


def _make_metric(stability: float = 0.70) -> EvaluationMetric:
    return EvaluationMetric(
        speed_mps=6.0,
        flight_time_s=2.0,
        max_ld_ratio=8.0,
        steady_glide_fraction=0.8,
        stability=stability,
        distance_m=12.0,
        max_altitude_m=1.5,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )


class _RecordingBenchmark:
    def __init__(self, result: FlightBenchmarkResult):
        self.calls: List[Any] = []
        self._result = result

    def evaluate_candidate(self, candidate: AircraftCandidate) -> FlightBenchmarkResult:
        self.calls.append(candidate)
        res = self._result
        candidate.benchmark = res
        return res


class _SteppedBenchmark:
    def __init__(self):
        self.calls: List[Any] = []
        self._call_count = 0

    def evaluate_candidate(self, candidate: AircraftCandidate) -> FlightBenchmarkResult:
        self.calls.append(candidate)
        self._call_count += 1
        if self._call_count == 1:
            res = FlightBenchmarkResult(
                low_speed=_make_metric(),
                mid_speed=_make_metric(),
                composite_score=1.0,
                valid=True,
            )
        else:
            res = FlightBenchmarkResult(
                low_speed=_make_metric(),
                mid_speed=_make_metric(),
                composite_score=3.0,
                valid=True,
            )
        candidate.benchmark = res
        return res


def test_f18_nelder_mead_enforces_tier0_and_updates_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    # Subtest A
    adapter = OrigamiAdapter()
    base_cand = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="f18-base")

    bench = _RecordingBenchmark(
        FlightBenchmarkResult(
            low_speed=_make_metric(),
            mid_speed=_make_metric(),
            composite_score=5.0,
            valid=True,
        )
    )

    import geometry.v3_origami.adapter as adapter_mod
    import optimization.trim.nelder_mead as nelder_mead_mod

    monkeypatch.setattr(adapter_mod, "validate_tier0", lambda *a, **k: (False, ["forced_invalid"]), raising=False)
    monkeypatch.setattr(nelder_mead_mod, "validate_tier0", lambda *a, **k: (False, ["forced_invalid"]), raising=False)
    monkeypatch.setattr(
        adapter,
        "compile_candidate",
        lambda *a, **k: AircraftCandidate(
            candidate_id="f18-base-trim",
            config=ParametricOrigamiConfig(),
            step_count=12,
            geometry=None,
            metadata={"tier0_valid": False, "tier0_reasons": ["forced_invalid"]},
        ),
        raising=False,
    )

    cost, _, out_cand = NelderMeadTrimOptimizer(adapter=adapter, benchmark=bench, max_iter=2)._evaluate_point(1.0, 2.0, base_cand)

    assert len(bench.calls) == 0
    assert out_cand.metadata.get("tier0_valid") is False
    assert cost >= 1.0

    # Subtest B
    monkeypatch.undo()

    cand = OrigamiAdapter().compile_candidate(ParametricOrigamiConfig(), candidate_id="f18-stale")
    cand.step_count = 99
    cand.metadata["tier0_valid"] = False
    cand.metadata["tier0_reasons"] = ["stale"]

    stepped_bench = _SteppedBenchmark()

    res = NelderMeadTrimOptimizer(adapter=OrigamiAdapter(), benchmark=stepped_bench, max_iter=2).optimize_trim(cand)

    assert res.optimized_score == pytest.approx(3.0)
    assert cand.step_count != 99
    assert MIN_FOLD_STEPS <= cand.step_count <= MAX_FOLD_STEPS
    assert cand.metadata.get("tier0_valid") is True
    assert cand.metadata.get("tier0_reasons") == []


def test_f21_no_tier0_invalid_fallback_in_mcts_engine_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import geometry.v3_origami.adapter as adapter_mod
    import geometry.v3_origami.mcts as mcts_mod
    import optimization.map_elites.engine as engine_mod

    monkeypatch.setattr(adapter_mod, "validate_tier0", lambda *a, **k: (False, ["tier0 rejected"]), raising=False)
    monkeypatch.setattr(mcts_mod, "validate_tier0", lambda *a, **k: (False, ["tier0 rejected"]), raising=False)
    monkeypatch.setattr(engine_mod, "validate_tier0", lambda *a, **k: (False, ["tier0 rejected"]), raising=False)

    # Subtest A
    assert MCTSOrigamiGenerator(seed=7).search_valid_candidates(iterations=3, top_k=2) == []

    # Subtest B
    bench = _RecordingBenchmark(
        FlightBenchmarkResult(
            low_speed=_make_metric(),
            mid_speed=_make_metric(),
            composite_score=4.0,
            valid=True,
        )
    )
    engine = MAPElitesEngine(archive=MAPElitesGridArchive(), benchmark=bench, seed=7)
    engine.bootstrap_population(count=2, mcts_iterations=2)
    assert len(engine.archive.cells) == 0
    assert len(bench.calls) == 0

    best = engine.run_iterations(iterations=1)
    assert best is None
    assert len(engine.archive.cells) == 0
    assert len(bench.calls) == 0

    # Subtest C
    def noop_bootstrap(self, count: int = 5, mcts_iterations: int = 30) -> None:
        pass

    def noop_run_iterations(self, iterations: int = 50, progress_callback=None, mutation_rate=None):
        return None

    def noop_get_elites(self):
        return []

    monkeypatch.setattr(cli_mod.MAPElitesEngine, "bootstrap_population", noop_bootstrap, raising=False)
    monkeypatch.setattr(cli_mod.MAPElitesEngine, "run_iterations", noop_run_iterations, raising=False)
    monkeypatch.setattr(cli_mod.MAPElitesGridArchive, "get_elites", noop_get_elites, raising=False)

    cli_mod.run_pipeline(
        iterations=1,
        output_dir=str(tmp_path),
        bootstrap_count=1,
        mcts_iterations=1,
        trim_iterations=1,
    )
    out = capsys.readouterr().out
    assert "ALL V3 STAGES COMPLETED SUCCESSFULLY" not in out
    assert "WARN" in out.upper()
    assert "0" in out


def _make_valid_candidate(candidate_id: str = "test") -> AircraftCandidate:
    geometry = AirframeGeometry(
        mass_kg=0.004,
        wing_area_m2=0.020,
        wingspan_m=0.14,
        chord_m=0.14,
        aspect_ratio=0.98,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.012,
        keel_length_m=0.050,
        keel_layers=4,
        step_count=12,
        planform_area_m2=0.020,
        planform_aspect_ratio=0.98,
        penetrations=0,
        fold_valid=True,
        thickness_limit_ok=True,
        flat_foldable_ok=True,
    )
    descriptor = BehaviorDescriptor(wing_loading_kg_m2=0.20, static_stability=0.70, aspect_ratio=0.98)
    benchmark = FlightBenchmarkResult(
        low_speed=_make_metric(),
        mid_speed=_make_metric(),
        composite_score=5.0,
        valid=True,
    )
    return AircraftCandidate(
        candidate_id=candidate_id,
        config=ParametricOrigamiConfig(),
        step_count=12,
        geometry=geometry,
        descriptor=descriptor,
        benchmark=benchmark,
        metadata={"tier0_valid": True},
    )


def test_f22_grid_archive_validates_tier0_and_geometry() -> None:
    grid = MAPElitesGridArchive(bins=(4, 4, 4), ranges=((0.12, 0.28), (0.25, 0.95), (0.45, 1.10)))

    assert grid._is_candidate_valid(_make_valid_candidate()) is True

    # geometry=None
    bad = _make_valid_candidate("bad1")
    bad.geometry = None
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # step_count = MIN_FOLD_STEPS - 1
    bad = _make_valid_candidate("bad2")
    bad.step_count = MIN_FOLD_STEPS - 1
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # step_count = MAX_FOLD_STEPS + 1
    bad = _make_valid_candidate("bad3")
    bad.step_count = MAX_FOLD_STEPS + 1
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # keel_height_m = 0.005
    bad = _make_valid_candidate("bad4")
    bad.geometry = replace(bad.geometry, keel_height_m=0.005)
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # keel_length_m = 0.020
    bad = _make_valid_candidate("bad5")
    bad.geometry = replace(bad.geometry, keel_length_m=0.020)
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # keel_layers = 1
    bad = _make_valid_candidate("bad6")
    bad.geometry = replace(bad.geometry, keel_layers=1)
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # thickness_limit_ok = False
    bad = _make_valid_candidate("bad7")
    bad.geometry = replace(bad.geometry, thickness_limit_ok=False)
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # flat_foldable_ok = False
    bad = _make_valid_candidate("bad8")
    bad.geometry = replace(bad.geometry, flat_foldable_ok=False)
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # metadata tier0_valid = False
    bad = _make_valid_candidate("bad9")
    bad.metadata = {"tier0_valid": False}
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False

    # metadata = {}
    bad = _make_valid_candidate("bad10")
    bad.metadata = {}
    assert grid._is_candidate_valid(bad) is False
    assert grid.add_candidate(bad) is False


def test_f24_map_elites_grid_ranges_match_real_origami(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    expected = ((0.12, 0.28), (0.25, 0.95), (0.45, 1.10))

    config_path = Path("config/milcy_map_elites.yaml")
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg["grid"]["wing_loading_range"] == [0.12, 0.28]
    assert cfg["grid"]["stability_range"] == [0.25, 0.95]
    assert cfg["grid"]["aspect_ratio_range"] == [0.45, 1.10]

    sig = inspect.signature(cli_mod.run_pipeline)
    assert sig.parameters["grid_ranges"].default == expected

    empty_cfg = tmp_path / "empty.yaml"
    empty_cfg.write_text("{}\n", encoding="utf-8")

    recorded_cli: List[Any] = []

    def record_run_pipeline(*args, **kwargs):
        recorded_cli.append(kwargs.get("grid_ranges"))
        return None

    monkeypatch.setattr(sys, "argv", ["cli.py", "--config", str(empty_cfg)], raising=False)
    monkeypatch.setattr(cli_mod, "run_pipeline", record_run_pipeline, raising=False)
    cli_mod.main()
    assert recorded_cli == [expected]

    recorded_rounds: List[Any] = []

    def record_overnight_pipeline(*args, **kwargs):
        recorded_rounds.append(kwargs.get("grid_ranges"))
        return None

    call_counter = {"count": 0}

    def mock_get_current_jst():
        call_counter["count"] += 1
        if call_counter["count"] <= 3:
            return datetime(2026, 11, 25, 8, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        return datetime(2026, 11, 25, 9, 5, 0, tzinfo=timezone(timedelta(hours=9)))

    monkeypatch.setattr(overnight_mod, "warmup_gpu", lambda *a, **kw: False, raising=False)
    monkeypatch.setattr(overnight_mod, "notify_discord", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(overnight_mod, "update_3d_viewer", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(overnight_mod, "apply_process_hygiene", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(overnight_mod, "load_previous_champion", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(overnight_mod, "get_current_jst", mock_get_current_jst, raising=False)
    monkeypatch.setattr(overnight_mod, "run_pipeline", record_overnight_pipeline, raising=False)

    overnight_mod.run_overnight_loop(
        target_hour=9,
        target_minute=0,
        base_output_dir=str(tmp_path / "overnight"),
    )
    assert recorded_rounds == [expected, expected]


def test_f27_grid_planform_default_and_mutation_trim_bounds_parity() -> None:
    grid = MAPElitesGridArchive(bins=(4, 4, 4), ranges=((0.10, 0.45), (0.10, 0.95), (0.30, 2.20)))

    assert grid.use_planform_bd is True

    geometry = AirframeGeometry(
        mass_kg=0.004,
        wing_area_m2=0.025,
        wingspan_m=0.14,
        chord_m=0.14,
        aspect_ratio=0.784,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.012,
        keel_length_m=0.050,
        keel_layers=4,
        step_count=12,
        planform_area_m2=0.020,
        planform_aspect_ratio=0.98,
        penetrations=0,
        fold_valid=True,
        thickness_limit_ok=True,
        flat_foldable_ok=True,
    )
    benchmark = FlightBenchmarkResult(
        low_speed=_make_metric(),
        mid_speed=_make_metric(),
        composite_score=5.0,
        valid=True,
    )
    cand = AircraftCandidate(
        candidate_id="test_planform",
        config=ParametricOrigamiConfig(),
        step_count=12,
        geometry=geometry,
        descriptor=None,
        benchmark=benchmark,
        metadata={"tier0_valid": True},
    )

    assert grid.add_candidate(cand) is True
    assert cand.descriptor is not None
    assert cand.descriptor.wing_loading_kg_m2 == pytest.approx(0.004 / 0.020)
    assert cand.descriptor.aspect_ratio == pytest.approx(0.98)

    low_edge = ParametricOrigamiConfig(
        wing=ParametricWingSpec(
            dihedral_deg=0.0,
            elevon_left_deg=-4.0,
            elevon_right_deg=-4.0,
        )
    )
    high_edge = ParametricOrigamiConfig(
        wing=ParametricWingSpec(
            dihedral_deg=8.0,
            elevon_left_deg=8.0,
            elevon_right_deg=8.0,
        )
    )

    rng = random.Random(123)
    for _ in range(200):
        m = mutate_parametric_config(low_edge, mutation_rate=1.0, rng=rng)
        assert 0.0 <= m.wing.dihedral_deg <= 8.0
        assert -4.0 <= m.wing.elevon_left_deg <= 8.0
        assert -4.0 <= m.wing.elevon_right_deg <= 8.0

    for _ in range(200):
        m = mutate_parametric_config(high_edge, mutation_rate=1.0, rng=rng)
        assert 0.0 <= m.wing.dihedral_deg <= 8.0
        assert -4.0 <= m.wing.elevon_left_deg <= 8.0
        assert -4.0 <= m.wing.elevon_right_deg <= 8.0