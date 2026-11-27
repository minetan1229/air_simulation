from __future__ import annotations

from pathlib import Path
import pytest


def test_f12_printable_a4_folding_instructions_uses_candidate_config(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    from tools.generate_printable_a4 import (
        render_a4_printable_sheet,
        build_folding_instructions,
    )
    from geometry.v3_origami.adapter import OrigamiAdapter
    from core.interfaces import ParametricOrigamiConfig, ParametricWingSpec

    cfg = ParametricOrigamiConfig(
        wing=ParametricWingSpec(
            elevon_left_deg=7.5,
            elevon_right_deg=7.5,
            dihedral_deg=9.0,
            winglet_angle_deg=65.0,
        )
    )
    cand_data = {
        "candidate_id": "test-cand",
        "config": cfg.to_dict(),
        "benchmark": {"mid_speed": {"flight_time_s": 2.4, "distance_m": 4.5}, "composite_score": 0.188},
    }

    captured = []

    def mock_render_max_fullsize(cand_data, out_path, dpi=300):
        return out_path

    def mock_render_step_manual(cand_data, instructions, out_path, dpi=300):
        captured.append(instructions)
        return out_path

    monkeypatch.setattr("tools.generate_printable_a4.render_a4_max_fullsize_sheet", mock_render_max_fullsize)
    monkeypatch.setattr("tools.generate_printable_a4.render_a4_step_manual", mock_render_step_manual)

    pytest.importorskip("matplotlib")

    render_a4_printable_sheet(cand_data, tmp_path / "pattern.png", include_manual_page=True, dpi=72)

    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(cfg)
    expected = build_folding_instructions(cand.to_dict())

    assert len(captured) == 1
    assert captured[0] == expected
    assert any("7.5" in inst.get("desc", "") or "7.5" in inst.get("title", "") for inst in captured[0])
    assert not any("3.5°" in inst.get("desc", "") or "3.5°" in inst.get("title", "") for inst in captured[0])


def test_f13_elevon_mountain_valley_sign_preserved():
    from tools.generate_printable_a4 import resolve_canonical_crease_style
    from geometry.v3_origami.kernel_25d import compile_macro_origami
    from core.interfaces import ParametricOrigamiConfig, ParametricWingSpec

    assert resolve_canonical_crease_style("elevon_l", True) == (True, "⑧", "エレボントリム反り")
    assert resolve_canonical_crease_style("elevon_l", False) == (False, "⑧", "エレボントリム反り")
    assert resolve_canonical_crease_style("elevon_r", True)[0] is True
    assert resolve_canonical_crease_style("elevon_r", False)[0] is False

    cfg_pos = ParametricOrigamiConfig(
        wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=5.0)
    )
    macro_pos, _ = compile_macro_origami(cfg_pos)
    elevon_step_pos = next(m for m in macro_pos if m.name == "elevon_reflex_trim")
    assert elevon_step_pos.mountain is False

    cfg_neg = ParametricOrigamiConfig(
        wing=ParametricWingSpec(elevon_left_deg=-5.0, elevon_right_deg=-5.0)
    )
    macro_neg, _ = compile_macro_origami(cfg_neg)
    elevon_step_neg = next(m for m in macro_neg if m.name == "elevon_reflex_trim")
    assert elevon_step_neg.mountain is True


def test_f14_wingspan_ratio_sign_and_keel_depth_a4():
    from tools.generate_printable_a4 import build_folding_instructions
    from geometry.v3_origami.compiler import ParametricOrigamiCompiler
    from geometry.v3_origami.kernel_25d import compile_macro_origami
    from core.interfaces import ParametricOrigamiConfig, ParametricWingSpec

    cfg_low = ParametricOrigamiConfig(wing=ParametricWingSpec(wingspan_ratio=0.55))
    cfg_high = ParametricOrigamiConfig(wing=ParametricWingSpec(wingspan_ratio=0.85))

    compiler = ParametricOrigamiCompiler()
    steps_low = compiler.compile_steps(cfg_low)
    steps_high = compiler.compile_steps(cfg_high)

    wing_l_low = next(s for s in steps_low if s.label == "wing_l")
    wing_l_high = next(s for s in steps_high if s.label == "wing_l")
    assert wing_l_high.crease_line[0][0] > wing_l_low.crease_line[0][0]

    macro_low, fold_low = compile_macro_origami(cfg_low)
    macro_high, fold_high = compile_macro_origami(cfg_high)

    wing_folds_low = next(m for m in macro_low if m.name == "wing_folds")
    wing_folds_high = next(m for m in macro_high if m.name == "wing_folds")
    assert wing_folds_high.line_p0[0] > wing_folds_low.line_p0[0]

    wing_step_low = next(s for s in fold_low if s.label == "wing_l")
    wing_step_high = next(s for s in fold_high if s.label == "wing_l")
    assert wing_step_high.crease_line[0][0] > wing_step_low.crease_line[0][0]

    cand_data_geom = {
        "config": {"keel": {"keel_depth_ratio": 0.08}},
        "geometry": {"keel_height_m": 0.015},
    }
    inst_geom = build_folding_instructions(cand_data_geom)
    step6_geom = next(i for i in inst_geom if i["step"] == "⑥")
    assert "15.0mm" in step6_geom["title"] or "15.0mm" in step6_geom["desc"]
    assert "11.9mm" not in step6_geom["title"] and "11.9mm" not in step6_geom["desc"]

    cand_data_fallback = {
        "config": {"keel": {"keel_depth_ratio": 0.10}},
    }
    inst_fallback = build_folding_instructions(cand_data_fallback)
    step6_fallback = next(i for i in inst_fallback if i["step"] == "⑥")
    expected_fallback = f"{round(0.10 * 210.0 * 0.5, 1)}mm"
    assert expected_fallback in step6_fallback["title"] or expected_fallback in step6_fallback["desc"]
    assert "14.9mm" not in step6_fallback["title"] and "14.9mm" not in step6_fallback["desc"]

    cand_data_macro = {
        "metadata": {
            "macro_steps": [
                {"step_index": 6, "name": "wing_folds", "mountain": True, "desc": "主翼の折り下げ（持ち手キールを残して外側へ水平折り）"}
            ]
        },
        "geometry": {"keel_height_m": 0.015},
    }
    inst_macro = build_folding_instructions(cand_data_macro)
    step6_macro = next(i for i in inst_macro if i["step"] == "⑥")
    assert "15.0mm" in step6_macro["title"] or "15.0mm" in step6_macro["desc"]


def test_f15_viewer_2d_mirror_and_zero_deg_falsy():
    from tools.view_milcy_elite import build_viewer_html

    mock_elites = [
        {
            "rank": 1,
            "file_stem": "test_rank1",
            "data": {
                "candidate_id": "test-cand",
                "config": {
                    "wing": {
                        "elevon_left_deg": 0.0,
                        "elevon_right_deg": 0.0,
                        "dihedral_deg": 0.0,
                        "winglet_angle_deg": 0.0,
                    }
                },
                "geometry": {},
                "benchmark": {},
            },
            "obj_content": "",
            "folding_steps_3d": [],
            "crease_lines": [],
        }
    ]

    html = build_viewer_html(mock_elites)

    assert "w/2 + (pos[i0*3 + 2] / 0.105) * (drawW / 2)" in html
    assert "w/2 + (pos[i1*3 + 2] / 0.105) * (drawW / 2)" in html
    assert "w/2 + (pos[i2*3 + 2] / 0.105) * (drawW / 2)" in html
    assert "w/2 - (pos[i0*3 + 2]" not in html

    assert "cfg.wing.winglet_angle_deg ?? 50.3" in html
    assert "cfg.wing.elevon_left_deg ?? 3.9" in html
    assert "cfg.wing.dihedral_deg ?? 4.4" in html
    assert "cfg.wing.elevon_left_deg ?? 0" in html
    assert "cfg.wing.dihedral_deg ?? 0" in html

    assert "winglet_angle_deg || 50.3" not in html
    assert "elevon_left_deg || 3.9" not in html
    assert "dihedral_deg || 4.4" not in html
