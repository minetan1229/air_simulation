"""Tests verifying AeroParams 48 -> 14 core partition and frozen profile ablation."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

import pytest

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


def test_frozen_profile_disables_all_21_ablation_candidates() -> None:
    core = CoreAeroParams(area=0.02, chord=0.10, span=0.20, aspect=2.0, cl_alpha=2.5)
    frozen = AeroParams.frozen_profile(core)

    for name in ABLATION_CANDIDATE_AERO_FIELDS:
        val = getattr(frozen, name)
        if isinstance(val, bool):
            assert val is False, f"{name} must be False in frozen_profile"
        else:
            assert float(val) == 0.0, f"{name} must be 0.0 in frozen_profile, got {val}"


def test_frozen_profile_rejects_non_internal_overrides() -> None:
    core = CoreAeroParams(area=0.02, chord=0.10, span=0.20, aspect=2.0, cl_alpha=2.5)
    with pytest.raises(ValueError):
        AeroParams.frozen_profile(core, area=999.0)
    with pytest.raises(ValueError):
        AeroParams.frozen_profile(core, low_re_cl_alpha_gain=0.99)


def test_ablation_preserves_archetype_ranking() -> None:
    res = run_ablation()
    assert res["spearman_rho"] == 1.0
    assert res["spearman_rho_edge"] >= 0.9
