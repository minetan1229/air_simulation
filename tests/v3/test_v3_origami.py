"""Unit tests for v3.origami compiler, adapter, and MCTS generator."""

import pytest
from v3.interfaces import (
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
)
from v3.origami.adapter import OrigamiAdapter, validate_tier0
from v3.origami.compiler import ParametricOrigamiCompiler
from v3.origami.mcts import MCTSOrigamiGenerator


def test_compiler_step_count_bounds():
    """Verify compiler always generates between 16 and 24 fold steps."""
    compiler = ParametricOrigamiCompiler()
    cfg = ParametricOrigamiConfig()
    steps = compiler.compile_steps(cfg)

    assert MIN_FOLD_STEPS <= len(steps) <= MAX_FOLD_STEPS
    assert len(steps) >= 16


def test_tier0_validation_enforces_step_count():
    """Verify validate_tier0 rejects candidates with fewer than 16 steps."""
    adapter = OrigamiAdapter()
    candidate = adapter.compile_candidate(ParametricOrigamiConfig())

    # Artificially modify step count to 12
    candidate.step_count = 12
    valid, reasons = validate_tier0(candidate)
    assert not valid
    assert any("below minimum requirement 16" in r for r in reasons)


def test_adapter_produces_compliant_candidate():
    """Verify OrigamiAdapter produces valid geometry and Tier 0 pass."""
    adapter = OrigamiAdapter()
    candidate = adapter.compile_candidate(ParametricOrigamiConfig())

    assert candidate.step_count >= 16
    assert candidate.geometry is not None
    assert candidate.geometry.keel_height_m >= 0.008
    assert candidate.geometry.keel_length_m >= 0.030
    assert candidate.geometry.keel_layers >= 2
    assert candidate.metadata.get("tier0_valid") is True


def test_mcts_generator_deterministic():
    """Verify MCTS search produces valid candidates with fixed seed."""
    gen1 = MCTSOrigamiGenerator(seed=123)
    cands1 = gen1.search_valid_candidates(iterations=20, top_k=2)

    gen2 = MCTSOrigamiGenerator(seed=123)
    cands2 = gen2.search_valid_candidates(iterations=20, top_k=2)

    assert len(cands1) > 0
    assert len(cands1) == len(cands2)
    assert cands1[0].step_count == cands2[0].step_count
    assert cands1[0].config.nose.fold_count == cands2[0].config.nose.fold_count
