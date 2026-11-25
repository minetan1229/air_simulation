"""Falsification tests for the ParametricOrigamiCompiler (Milcy Week 3)."""

from __future__ import annotations

import math

import pytest

from core.interfaces import (
    MAX_FOLD_STEPS,
    ParametricKeelSpec,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from geometry.origami_genome import (
    OrigamiGenome,
    _separated_polygons,
    adjacent_panel_pairs,
    detect_penetrations,
    fold_genome,
)
from geometry.v3_origami.compiler import (
    ParametricOrigamiCompiler,
    validate_crease_pattern_physics,
)
from geometry.v3_origami.adapter import detect_strict_self_intersections
from geometry.v3_origami.kernel_25d import (
    MacroFoldStep,
    Sequential2DOrigamiKernel,
    compile_macro_origami,
)


def test_f08_bilateral_crease_side_self_intersection() -> None:
    """Bilateral creases use correct side; folded geometry has no self-intersections."""
    compiler = ParametricOrigamiCompiler()

    cfg_default = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True),
        wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=5.0, has_winglet=True),
    )
    steps = compiler.compile_steps(cfg_default)

    elevon_l_step = next(s for s in steps if s.label == "elevon_l")
    assert elevon_l_step.symmetry == "right"

    right_paired = {"nose_lock_r", "nose_reinforce_r", "keel_lock_r", "nose_cap_r"}
    for label in right_paired:
        step = next(s for s in steps if s.label == label)
        assert step.symmetry == "right", f"step {label} should have symmetry=='right'"

    keel_lock_l = next(s for s in steps if s.label == "keel_lock_l")
    keel_lock_r = next(s for s in steps if s.label == "keel_lock_r")
    assert keel_lock_l.group == ("flap_l",)
    assert keel_lock_r.group == ("flap_r",)

    geom = fold_genome(OrigamiGenome(steps=steps))
    assert abs(geom.cg[2]) < 1e-6

    wing_l_area = sum(f.area_m2 for f in geom.faces if f.group == "wing_l")
    wing_r_area = sum(f.area_m2 for f in geom.faces if f.group == "wing_r")
    assert abs(wing_l_area - wing_r_area) < 1e-6

    adj = adjacent_panel_pairs(geom.pattern)
    sep_polys = _separated_polygons(geom)
    assert len(detect_penetrations(geom.faces, adjacency=adj, polygons=sep_polys)) == 0

    cfg_zero_elev = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True),
        wing=ParametricWingSpec(elevon_left_deg=0.0, elevon_right_deg=0.0, has_winglet=True),
    )
    steps0 = compiler.compile_steps(cfg_zero_elev)
    geom0 = fold_genome(OrigamiGenome(steps=steps0))
    adj0 = adjacent_panel_pairs(geom0.pattern)
    assert len(detect_strict_self_intersections(geom0.faces, adjacency=adj0)) == 0

    macro_steps, macro_fold_steps = compile_macro_origami(cfg_default)
    macro_geom = fold_genome(OrigamiGenome(steps=macro_fold_steps))
    assert abs(macro_geom.cg[2]) < 1e-6
    macro_adj = adjacent_panel_pairs(macro_geom.pattern)
    macro_sep = _separated_polygons(macro_geom)
    assert len(detect_penetrations(macro_geom.faces, adjacency=macro_adj, polygons=macro_sep)) == 0

    macro_steps0, macro_fold_steps0 = compile_macro_origami(cfg_zero_elev)
    macro_geom0 = fold_genome(OrigamiGenome(steps=macro_fold_steps0))
    macro_adj0 = adjacent_panel_pairs(macro_geom0.pattern)
    assert len(detect_strict_self_intersections(macro_geom0.faces, adjacency=macro_adj0)) == 0

    cfg_neg_elev = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True),
        wing=ParametricWingSpec(elevon_left_deg=-5.0, elevon_right_deg=-5.0, has_winglet=True),
    )
    macro_steps_neg, _ = compile_macro_origami(cfg_neg_elev)
    elevon_trim_neg = next(s for s in macro_steps_neg if s.name == "elevon_reflex_trim")
    assert elevon_trim_neg.mountain is True

    cfg_pos_elev = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True),
        wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=5.0, has_winglet=True),
    )
    macro_steps_pos, _ = compile_macro_origami(cfg_pos_elev)
    elevon_trim_pos = next(s for s in macro_steps_pos if s.name == "elevon_reflex_trim")
    assert elevon_trim_pos.mountain is False

    cfg_wide = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True),
        wing=ParametricWingSpec(wingspan_ratio=0.88, has_winglet=True),
    )
    macro_steps_wide, _ = compile_macro_origami(cfg_wide)
    wing_fold_wide = next(s for s in macro_steps_wide if s.name == "wing_folds")
    expected_wide = 0.30 + 0.18 * 0.88
    assert math.isclose(wing_fold_wide.line_p0[0], expected_wide, rel_tol=1e-3)

    cfg_narrow = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True),
        wing=ParametricWingSpec(wingspan_ratio=0.55, has_winglet=True),
    )
    macro_steps_narrow, _ = compile_macro_origami(cfg_narrow)
    wing_fold_narrow = next(s for s in macro_steps_narrow if s.name == "wing_folds")
    expected_narrow = 0.30 + 0.18 * 0.55
    assert math.isclose(wing_fold_narrow.line_p0[0], expected_narrow, rel_tol=1e-3)
    assert wing_fold_wide.line_p0[0] > wing_fold_narrow.line_p0[0]


def test_f09_max_fold_steps_truncation_and_duplicate_keel_lock() -> None:
    """MAX_FOLD_STEPS is 24; keel_lock_l and keel_lock_r appear exactly once each."""
    assert MAX_FOLD_STEPS == 24

    compiler = ParametricOrigamiCompiler()
    config = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(has_lock_fold=True),
        wing=ParametricWingSpec(has_winglet=True),
    )
    steps = compiler.compile_steps(config)

    assert len(steps) == 21

    labels = {s.label for s in steps}
    required = {"wing_r", "winglet_r", "elevon_r", "trailing_r"}
    assert required.issubset(labels)

    keel_lock_l_count = sum(1 for s in steps if s.label == "keel_lock_l")
    keel_lock_r_count = sum(1 for s in steps if s.label == "keel_lock_r")
    assert keel_lock_l_count == 1
    assert keel_lock_r_count == 1


def test_f11_kernel_25d_z_index_collision_and_kawasaki_endpoint_vertex() -> None:
    """Sequential2DOrigamiKernel assigns unique z_index; Kawasaki/Maekawa validation catches endpoint violations."""
    for m1, m2 in [(True, True), (False, False), (True, False), (False, True)]:
        kernel = Sequential2DOrigamiKernel()
        kernel.execute_fold((0.5, 0.0), (0.0, 1.0), mountain=m1)
        kernel.execute_fold((0.0, 0.5), (1.0, 0.0), mountain=m2)
        assert len(kernel.polygons) == 4
        z_indices = {p.z_index for p in kernel.polygons}
        assert len(z_indices) == 4, f"z_index collision: {z_indices}"

    all_mountain_4ray = [
        {"p0": (0.5, 0.0), "p1": (0.5, 1.0), "mountain": True},
        {"p0": (0.0, 0.5), "p1": (1.0, 0.5), "mountain": True},
        {"p0": (0.3, 0.3), "p1": (0.5, 0.5), "mountain": True},
        {"p0": (0.7, 0.3), "p1": (0.5, 0.5), "mountain": True},
    ]
    from geometry.origami_genome import FoldStep, OrigamiGenome
    steps = [
        FoldStep(step_index=i + 1, crease_line=(s["p0"], s["p1"]), fold_angle_deg=180.0 if s["mountain"] else -180.0, mountain=s["mountain"])
        for i, s in enumerate(all_mountain_4ray)
    ]
    result = validate_crease_pattern_physics(steps)
    assert result.flat_foldable_ok is False
    assert result.maekawa_violations >= 1

    kawasaki_angles = [60.0, 120.0, 100.0, 80.0]
    kawasaki_steps = []
    for i, angle in enumerate(kawasaki_angles):
        ka = math.radians(angle)
        p0 = (0.5, 0.5)
        p1 = (0.5 + math.cos(ka) * 0.5, 0.5 + math.sin(ka) * 0.5)
        kawasaki_steps.append(FoldStep(step_index=i + 1, crease_line=(p0, p1), fold_angle_deg=180.0, mountain=True))
    result = validate_crease_pattern_physics(kawasaki_steps)
    assert result.flat_foldable_ok is False

    t_junction = [
        {"p0": (0.5, 0.0), "p1": (0.5, 1.0), "mountain": True},
        {"p0": (0.3, 0.3), "p1": (0.5, 0.5), "mountain": True},
        {"p0": (0.7, 0.3), "p1": (0.5, 0.5), "mountain": True},
        {"p0": (0.5, 0.5), "p1": (0.8, 0.5), "mountain": True},
    ]
    steps = [
        FoldStep(step_index=i + 1, crease_line=(s["p0"], s["p1"]), fold_angle_deg=180.0 if s["mountain"] else -180.0, mountain=s["mountain"])
        for i, s in enumerate(t_junction)
    ]
    result = validate_crease_pattern_physics(steps)
    assert result.flat_foldable_ok is False

    for fold_count in (3, 4, 5, 6):
        cfg = ParametricOrigamiConfig(
            nose=ParametricNoseSpec(fold_count=fold_count),
            keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True, crease_offset_u=0.5),
            wing=ParametricWingSpec(has_winglet=True),
        )
        compiler = ParametricOrigamiCompiler()
        steps = compiler.compile_steps(cfg)
        result = validate_crease_pattern_physics(
            steps,
            paper_gsm=100.0,
            nose_fold_count=fold_count,
            nose_length_ratio=cfg.nose.nose_length_ratio,
        )
        assert result.flat_foldable_ok is True
        assert result.maekawa_violations == 0

    for offset in (0.48, 0.52):
        cfg = ParametricOrigamiConfig(
            nose=ParametricNoseSpec(fold_count=4),
            keel=ParametricKeelSpec(keel_depth_ratio=0.12, has_lock_fold=True, crease_offset_u=offset),
            wing=ParametricWingSpec(has_winglet=True),
        )
        compiler = ParametricOrigamiCompiler()
        steps = compiler.compile_steps(cfg)
        result = validate_crease_pattern_physics(
            steps,
            paper_gsm=100.0,
            nose_fold_count=cfg.nose.fold_count,
            nose_length_ratio=cfg.nose.nose_length_ratio,
        )
        assert result.flat_foldable_ok is False
