"""Tests verifying AeroParams 48 -> 14 core partition and frozen profile ablation."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

from physics.aero_kernel import (
    ABLATION_CANDIDATE_AERO_FIELDS,
    CORE_AERO_FIELDS,
    INTERNAL_AERO_FIELDS,
    AeroParams,
    CoreAeroParams,
)
from tools.ablation_aero_params import run_ablation


def test_aero_params_partition_is_exact_and_disjoint() -> None:
    all_fields = {f.name for f in dataclass_fields(AeroParams)}
    core = set(CORE_AERO_FIELDS)
    internal = set(INTERNAL_AERO_FIELDS)
    ablation = set(ABLATION_CANDIDATE_AERO_FIELDS)

    assert len(core) == 14
    assert len(internal) == 13
    assert len(ablation) == 21
    assert core.isdisjoint(internal)
    assert core.isdisjoint(ablation)
    assert internal.isdisjoint(ablation)
    assert (core | internal | ablation) == all_fields


def test_frozen_profile_disables_all_ablation_candidates() -> None:
    core = CoreAeroParams(area=0.02, chord=0.10, span=0.20, aspect=2.0, cl_alpha=2.5)
    frozen = AeroParams.frozen_profile(core)

    assert frozen.compressible is False
    assert frozen.low_re_enabled is False
    for name in (
        "cm_roll_rate", "cm_yaw_rate", "cl_pitch_rate", "cl_yaw_rate",
        "cn_roll_rate", "cn_pitch_rate", "polhamus_kp", "polhamus_kv",
        "fold_trailing_deg", "re_cd0_exponent", "cg_offset_x", "cp_shift_gain",
        "ground_cushion_lift", "ground_pitch_coupling",
    ):
        assert float(getattr(frozen, name)) == 0.0


def test_ablation_preserves_archetype_ranking() -> None:
    res = run_ablation()
    assert res["spearman_rho"] == 1.0
    assert res["max_ld_rel_diff"] < 1e-3
