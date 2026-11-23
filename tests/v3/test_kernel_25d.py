"""Unit tests for the 2.5D sequential origami folding kernel and MacroStep pipeline."""

from __future__ import annotations

import pytest
from core.interfaces import ParametricOrigamiConfig, ParametricNoseSpec, ParametricKeelSpec, ParametricWingSpec
from geometry.v3_origami.kernel_25d import (
    Sequential2DOrigamiKernel,
    MacroFoldStep,
    compile_macro_origami,
)
from geometry.v3_origami.adapter import OrigamiAdapter, validate_tier0
from geometry.v3_origami.compiler import ParametricOrigamiCompiler


def test_kernel_initialization():
    """Verify kernel initializes with flat A4 sheet polygon."""
    kernel = Sequential2DOrigamiKernel()
    assert len(kernel.polygons) == 1
    poly = kernel.polygons[0]
    assert len(poly.vertices) == 4
    assert poly.area == pytest.approx(1.0, rel=1e-5)
    assert poly.z_index == 0
    assert poly.face_up is True


def test_kernel_half_space_clipping_and_reflection():
    """Verify half-space polygon clipping splits polygon and tracks cut segment."""
    kernel = Sequential2DOrigamiKernel()
    # Fold along vertical line x = 0.5
    uv_creases = kernel.execute_fold(
        p0=(0.5, 0.0),
        direction=(0.0, 1.0),
        mountain=False,
        target_group="left_half",
    )
    # The sheet should be divided into fixed right half and reflected left half (now stacked on right)
    assert len(kernel.polygons) == 2
    assert len(uv_creases) == 1
    c0, c1 = uv_creases[0]
    # In UV space, cut line should be vertical along u = 0.5
    assert c0[0] == pytest.approx(0.5, abs=1e-4)
    assert c1[0] == pytest.approx(0.5, abs=1e-4)


def test_compile_macro_origami_step_counts():
    """Verify macro origami generates 6 to 10 MacroSteps and 10 to 16 FoldSteps."""
    cfg = ParametricOrigamiConfig()
    macros, steps = compile_macro_origami(cfg)

    # Human cognitive MacroSteps: 6 to 10
    assert 6 <= len(macros) <= 10
    # Atomic FoldSteps: 10 to 16
    assert 10 <= len(steps) <= 16

    # Verify no dummy camber padding exists
    camber_steps = [s for s in steps if "camber" in s.label]
    assert len(camber_steps) == 0


def test_compile_macro_origami_geometric_integrity():
    """Verify crease lines satisfy all non-breach and non-slicing invariants."""
    cfg = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=5, taper_ratio=0.75, nose_length_ratio=0.30),
        wing=ParametricWingSpec(wingspan_ratio=0.8, dihedral_deg=4.0, elevon_left_deg=3.0, elevon_right_deg=3.0),
        keel=ParametricKeelSpec(keel_depth_ratio=0.10),
    )
    macros, steps = compile_macro_origami(cfg)

    for s in steps:
        p0, p1 = s.crease_line
        # All coordinates within unit square bounds
        assert 0.0 <= p0[0] <= 1.0 and 0.0 <= p0[1] <= 1.0
        assert 0.0 <= p1[0] <= 1.0 and 0.0 <= p1[1] <= 1.0

        lbl = s.label.lower()
        # Symmetric left-side folds must not cross centerline into right half
        if lbl.endswith("_l") and not lbl.startswith("elevon") and not lbl.startswith("trailing"):
            assert p0[0] <= 0.5001, f"{s.label} p0 breached centerline: {p0}"
            assert p1[0] <= 0.5001, f"{s.label} p1 breached centerline: {p1}"

        # Control surfaces must not slice entire sheet width
        if "elevon" in lbl or "trailing" in lbl:
            span_width = abs(p1[0] - p0[0])
            assert span_width < 0.80, f"{s.label} sliced full width: {span_width}"


def test_origami_adapter_macro_steps_metadata():
    """Verify OrigamiAdapter populates macro_steps in candidate metadata."""
    adapter = OrigamiAdapter()
    cfg = ParametricOrigamiConfig()
    cand = adapter.compile_candidate(cfg)

    assert "macro_steps" in cand.metadata
    macros = cand.metadata["macro_steps"]
    assert isinstance(macros, list)
    assert len(macros) >= 6
    for m in macros:
        assert "step_index" in m
        assert "name" in m
        assert "fold_type" in m
        assert "desc" in m
