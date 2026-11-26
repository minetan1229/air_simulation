"""Iteration 2 falsification tests (Part 3): compiler symmetry, A4 elevon/lock consistency, viewer y1 & zero fallbacks."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import List, Tuple

from core.interfaces import (
    MAX_FOLD_STEPS,
    ParametricKeelSpec,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from geometry.origami_genome import OrigamiGenome, fold_genome
from geometry.v3_origami.adapter import extract_airframe_geometry
from geometry.v3_origami.compiler import ParametricOrigamiCompiler
from geometry.v3_origami.kernel_25d import compile_macro_origami
from tools.generate_printable_a4 import (
    build_folding_instructions,
    render_a4_max_fullsize_sheet,
    resolve_canonical_crease_style,
)
from tools.view_milcy_elite import build_viewer_html


def test_i2_04_compiler_symmetric_camber_padding_no_unpaired_left() -> None:
    """All 16 (fold_count, has_lock_fold, has_winglet) combos must keep camber_l == camber_r and |cg_z| < 1e-6."""
    compiler = ParametricOrigamiCompiler()
    for fold_count, has_lock_fold, has_winglet in itertools.product(
        (3, 4, 5, 6), (False, True), (False, True)
    ):
        cfg = ParametricOrigamiConfig(
            nose=ParametricNoseSpec(fold_count=fold_count),
            keel=ParametricKeelSpec(has_lock_fold=has_lock_fold),
            wing=ParametricWingSpec(has_winglet=has_winglet),
        )
        steps = compiler.compile_steps(cfg)
        assert len(steps) <= MAX_FOLD_STEPS

        camber_l_count = sum(1 for s in steps if s.label.startswith("camber_l"))
        camber_r_count = sum(1 for s in steps if s.label.startswith("camber_r"))
        assert camber_l_count == camber_r_count, (
            f"Unpaired camber padding for fold_count={fold_count}, "
            f"has_lock_fold={has_lock_fold}, has_winglet={has_winglet}: "
            f"camber_l={camber_l_count}, camber_r={camber_r_count}"
        )

        genome = OrigamiGenome(steps=steps, key="i2_04_sym")
        geom = extract_airframe_geometry(
            fold_genome(genome, paper_gsm=cfg.paper_gsm),
            step_count=len(steps),
        )
        assert abs(geom.cg_m[2]) < 1.0e-6, (
            f"Lateral CG offset {geom.cg_m[2]} for combo "
            f"({fold_count}, {has_lock_fold}, {has_winglet})"
        )


def test_i2_05_a4_elevon_page1_page2_consistency_and_symmetric_lock_styles(
    tmp_path: Path, monkeypatch
) -> None:
    """Page 1 SVG creases, Page 2 fallback instructions, and lock/cap crease styles must be consistent."""
    # 1. Symmetric styling for keel_lock and nose_cap regardless of raw_mountain
    assert (
        resolve_canonical_crease_style("keel_lock_l", True)[0]
        == resolve_canonical_crease_style("keel_lock_r", False)[0]
    )
    assert (
        resolve_canonical_crease_style("nose_cap_l", True)[0]
        == resolve_canonical_crease_style("nose_cap_r", False)[0]
    )

    # 2. Page 2 fallback instructions & Page 1 render_a4_max_fullsize_sheet agree with compile_macro_origami
    for elevon_deg, expected_type, expected_mtn in (
        (5.0, "谷折り (Valley)", False),
        (-5.0, "山折り (Mountain)", True),
    ):
        cfg = ParametricOrigamiConfig(
            wing=ParametricWingSpec(elevon_left_deg=elevon_deg, elevon_right_deg=elevon_deg)
        )
        macro_steps, _ = compile_macro_origami(cfg)
        macro_elevon = next(m for m in macro_steps if m.name == "elevon_reflex_trim")
        assert macro_elevon.mountain is expected_mtn

        cand_data = {
            "candidate_id": f"test-elevon-{elevon_deg}",
            "config": cfg.to_dict(),
            "benchmark": {"mid_speed": {"flight_time_s": 2.4, "distance_m": 4.5}, "composite_score": 0.188},
        }
        instructions = build_folding_instructions(cand_data)
        elev_inst = next(inst for inst in instructions if "⑧" in inst["step"])
        assert elev_inst["type"] == expected_type

        recorded: List[Tuple[str, bool, bool]] = []
        orig_resolve = resolve_canonical_crease_style

        def spy_resolve(label: str, raw_mountain: bool = True, _orig=orig_resolve):
            res = _orig(label, raw_mountain)
            if "elevon" in str(label).lower():
                recorded.append((label, raw_mountain, res[0]))
            return res

        monkeypatch.setattr("tools.generate_printable_a4.resolve_canonical_crease_style", spy_resolve)
        render_a4_max_fullsize_sheet(cand_data, tmp_path / f"full_{elevon_deg}.png", dpi=72)
        assert len(recorded) >= 2
        assert all(is_mtn is expected_mtn for _, _, is_mtn in recorded), (
            f"Page 1 elevon style mismatch for elevon={elevon_deg}: {recorded}"
        )


def test_i2_06_viewer_y1_inversion_and_zero_fallbacks() -> None:
    """Viewer HTML must invert cl.p1[1] and use != null / ?? instead of truthy ? / || fallbacks."""
    mock_elites = [
        {
            "rank": 1,
            "file_stem": "test_rank1_zero",
            "data": {
                "candidate_id": "test-zero",
                "config": {
                    "wing": {
                        "elevon_left_deg": 0.0,
                        "elevon_right_deg": 0.0,
                        "dihedral_deg": 0.0,
                        "winglet_angle_deg": 0.0,
                    }
                },
                "geometry": {
                    "wingspan_m": 0.0,
                    "mass_kg": 0.0,
                    "planform_aspect_ratio": 0.0,
                    "planform_area_m2": 0.0,
                    "keel_height_m": 0.0,
                    "chord_m": 0.0,
                },
                "benchmark": {"composite_score": 0.0},
            },
            "obj_content": "",
            "folding_steps_3d": [],
            "crease_lines": [],
        }
    ]
    html = build_viewer_html(mock_elites)

    # y1 inversion in previous crease lines loop
    assert "margin + (1.0 - cl.p1[1]) * drawH" in html
    assert "margin + cl.p1[1] * drawH" not in html

    # Truthy fallbacks replaced with != null / ?? so 0 values are preserved
    assert "bench.composite_score ? bench.composite_score.toFixed(4)" not in html
    assert "bench.composite_score != null ? bench.composite_score.toFixed(4)" in html

    assert "geom.wingspan_m ? (geom.wingspan_m * 100)" not in html
    assert "geom.wingspan_m != null ? (geom.wingspan_m * 100)" in html

    assert "geom.mass_kg ? (geom.mass_kg * 1000)" not in html
    assert "geom.mass_kg != null ? (geom.mass_kg * 1000)" in html

    assert "geom.planform_aspect_ratio ? geom.planform_aspect_ratio.toFixed(2)" not in html
    assert "geom.planform_aspect_ratio != null ? geom.planform_aspect_ratio.toFixed(2)" in html

    assert "geom.planform_area_m2 ? (geom.planform_area_m2 * 10000)" not in html
    assert "geom.planform_area_m2 != null ? (geom.planform_area_m2 * 10000)" in html

    assert "geom.chord_m || 0.28" not in html

    assert "geom.keel_height_m ? (geom.keel_height_m * 1000)" not in html
    assert "geom.keel_height_m != null ? (geom.keel_height_m * 1000)" in html

    assert "b.composite_score ? b.composite_score.toFixed(4)" not in html
    assert "b.composite_score != null ? b.composite_score.toFixed(4)" in html
