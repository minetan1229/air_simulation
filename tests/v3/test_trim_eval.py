"""Unit tests for Stage 3 Nelder-Mead trim and Stage 4 Robustness evaluation."""

import json
from pathlib import Path
import pytest
from v3.interfaces import AircraftCandidate, ParametricOrigamiConfig
from v3.origami.adapter import OrigamiAdapter
from v3.physics.benchmark import DeterministicFlightBenchmark
from v3.trim.nelder_mead import NelderMeadTrimOptimizer
from v3.eval.robustness import RobustnessEvaluator


def test_nelder_mead_trim_optimization():
    """Verify Nelder-Mead trim optimizer executes and returns a valid TrimResult."""
    adapter = OrigamiAdapter()
    candidate = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="trim-test")

    bench = DeterministicFlightBenchmark(duration_s=0.2)
    optimizer = NelderMeadTrimOptimizer(benchmark=bench, max_iter=2)
    res = optimizer.optimize_trim(candidate)

    assert res.candidate_id == "trim-test"
    assert res.iterations > 0
    assert -5.0 <= res.optimal_elevon_deg <= 10.0
    assert 0.0 <= res.optimal_dihedral_deg <= 10.0


def test_robustness_evaluator_and_export(tmp_path: Path):
    """Verify robustness evaluation produces scores and exports valid OBJ and JSON."""
    adapter = OrigamiAdapter()
    candidate = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="eval-test")

    evaluator = RobustnessEvaluator(duration_s=0.2)
    report = evaluator.evaluate_candidate(candidate)

    assert report.candidate_id == "eval-test"
    assert 0.0 <= report.overall_robustness_score <= 1.0

    # Test file exports
    json_path = tmp_path / "candidate.json"
    obj_path = tmp_path / "aircraft.obj"

    evaluator.export_candidate_json(candidate, str(json_path))
    evaluator.export_candidate_obj(candidate, str(obj_path))

    assert json_path.exists()
    assert obj_path.exists()

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["candidate_id"] == "eval-test"
        assert data["step_count"] >= 16

    with open(obj_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        assert any(l.startswith("v ") for l in lines)
        assert any(l.startswith("f ") for l in lines)
