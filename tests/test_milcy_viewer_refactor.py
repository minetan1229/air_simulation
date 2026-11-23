"""Tests for 3D Viewer refactoring and integrity verification."""

from pathlib import Path
from tools.view_milcy_elite import build_viewer_html


def test_build_viewer_html_structure_and_guards():
    """Verify that build_viewer_html includes all stability, memory, and responsiveness guards."""
    mock_elites = [
        {
            "rank": 1,
            "file_stem": "elite_rank1_test",
            "data": {
                "candidate_id": "test_cand_01",
                "config": {
                    "nose": {"fold_count": 4},
                    "keel": {"crease_offset_u": 0.5},
                    "wing": {
                        "winglet_angle_deg": 45.0,
                        "elevon_left_deg": -3.5,
                        "dihedral_deg": 5.0,
                    },
                },
                "geometry": {
                    "wingspan_m": 0.12,
                    "mass_kg": 0.005,
                    "keel_height_m": 0.015,
                },
                "benchmark": {
                    "composite_score": 0.25,
                    "mid_speed": {
                        "distance_m": 6.2,
                        "max_altitude_m": 1.9,
                        "flight_time_s": 2.8,
                        "max_ld_ratio": 4.5,
                    },
                },
            },
            "obj_content": "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
            "folding_steps_3d": [
                {"step": 0, "label": "Flat Sheet", "positions": [0, 0, 0], "indices": [0]},
                {"step": 1, "label": "Body Crease", "positions": [0, 0, 0], "indices": [0]},
            ],
            "crease_lines": [
                {
                    "step": 1,
                    "p0": [0.5, 0.0],
                    "p1": [0.5, 1.0],
                    "mountain": False,
                    "angle_deg": -175.0,
                    "label": "body",
                    "badge": "①",
                }
            ],
        }
    ]

    html = build_viewer_html(mock_elites)

    # 1. Memory leak disposal hierarchy
    assert "disposeHierarchy" in html
    assert "disposeHierarchy(child)" in html
    assert "disposeHierarchy(trajectoryLine)" in html

    # 2. Performance guard: disabled shadow maps
    assert "renderer.shadowMap.enabled = false" in html

    # 3. Physics integrity: fake lateral sine wave eliminated
    assert "Math.sin(t * 2.0)" not in html
    assert "glideDrop" in html

    # 4. Event delegation: no per-render click listener pollution
    assert "stepsContainer.addEventListener" in html

    # 5. Local file:// protocol security guard
    assert "window.location.protocol === 'file:'" in html

    # 6. Responsive design breakpoints
    assert "@media (max-width: 960px)" in html
    assert "@media (max-width: 1200px)" in html
