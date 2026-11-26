"""Re-export all Milcy falsification tests for the audit suite."""

from tests.milcy_falsification.w1_physics import TestW1Physics
from tests.milcy_falsification.w2_benchmark import (
    test_f02_benchmark_ghost_trailing_fold_and_cm_alpha,
    test_f04_legacy_v3_package_divergence,
    test_f17_antichat_penalties_wired_to_benchmark,
    test_f25_benchmark_robustness_parity_and_wind_log,
    test_f26_robustness_survival_requires_valid_glide,
)
from tests.milcy_falsification.w3_origami_compiler import (
    test_f08_bilateral_crease_side_self_intersection,
    test_f09_max_fold_steps_truncation_and_duplicate_keel_lock,
    test_f11_kernel_25d_z_index_collision_and_kawasaki_endpoint_vertex,
)
from tests.milcy_falsification.w4_origami_adapter import (
    test_f10_strict_self_intersection_fid_vs_index_adjacency,
    test_f16_origami_genome_pitch_yaw_inertia_axes,
    test_f19_extract_airframe_geometry_no_fake_keel_fallback,
    test_f20_validate_tier0_fail_closed_on_invalid_fields,
    test_f23_unclamped_nose_fold_count_gallivan_bypass,
)
from tests.milcy_falsification.w5_tools_viewer import (
    test_f12_printable_a4_folding_instructions_uses_candidate_config,
    test_f13_elevon_mountain_valley_sign_preserved,
    test_f14_wingspan_ratio_sign_and_keel_depth_a4,
    test_f15_viewer_2d_mirror_and_zero_deg_falsy,
)
from tests.milcy_falsification.w6_optimization import (
    test_f18_nelder_mead_enforces_tier0_and_updates_metadata,
    test_f21_no_tier0_invalid_fallback_in_mcts_engine_cli,
    test_f22_grid_archive_validates_tier0_and_geometry,
    test_f24_map_elites_grid_ranges_match_real_origami,
    test_f27_grid_planform_default_and_mutation_trim_bounds_parity,
)

__all__ = [
    "TestW1Physics",
    "test_f02_benchmark_ghost_trailing_fold_and_cm_alpha",
    "test_f04_legacy_v3_package_divergence",
    "test_f17_antichat_penalties_wired_to_benchmark",
    "test_f25_benchmark_robustness_parity_and_wind_log",
    "test_f26_robustness_survival_requires_valid_glide",
    "test_f08_bilateral_crease_side_self_intersection",
    "test_f09_max_fold_steps_truncation_and_duplicate_keel_lock",
    "test_f11_kernel_25d_z_index_collision_and_kawasaki_endpoint_vertex",
    "test_f10_strict_self_intersection_fid_vs_index_adjacency",
    "test_f16_origami_genome_pitch_yaw_inertia_axes",
    "test_f19_extract_airframe_geometry_no_fake_keel_fallback",
    "test_f20_validate_tier0_fail_closed_on_invalid_fields",
    "test_f23_unclamped_nose_fold_count_gallivan_bypass",
    "test_f12_printable_a4_folding_instructions_uses_candidate_config",
    "test_f13_elevon_mountain_valley_sign_preserved",
    "test_f14_wingspan_ratio_sign_and_keel_depth_a4",
    "test_f15_viewer_2d_mirror_and_zero_deg_falsy",
    "test_f18_nelder_mead_enforces_tier0_and_updates_metadata",
    "test_f21_no_tier0_invalid_fallback_in_mcts_engine_cli",
    "test_f22_grid_archive_validates_tier0_and_geometry",
    "test_f24_map_elites_grid_ranges_match_real_origami",
    "test_f27_grid_planform_default_and_mutation_trim_bounds_parity",
]
