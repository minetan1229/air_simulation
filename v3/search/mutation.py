"""Parametric mutation operators for 20-step origami genomes."""

from __future__ import annotations

import random
from typing import Optional

from v3.interfaces import (
    ParametricKeelSpec,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def mutate_parametric_config(
    parent: ParametricOrigamiConfig,
    mutation_rate: float = 0.35,
    rng: Optional[random.Random] = None,
) -> ParametricOrigamiConfig:
    """Apply stochastic mutation to high-level origami parameters."""
    r = rng or random.Random()

    # Phase 1: Nose mutation
    nose = parent.nose
    new_fold_count = nose.fold_count
    if r.random() < mutation_rate:
        # +/- 1 fold step within [3, 6]
        delta = r.choice([-1, 1])
        new_fold_count = int(_clamp(nose.fold_count + delta, 3.0, 6.0))

    new_taper = nose.taper_ratio
    if r.random() < mutation_rate:
        new_taper = _clamp(nose.taper_ratio + r.gauss(0.0, 0.05), 0.45, 0.85)

    new_nose_len = nose.nose_length_ratio
    if r.random() < mutation_rate:
        new_nose_len = _clamp(nose.nose_length_ratio + r.gauss(0.0, 0.03), 0.20, 0.35)

    mutated_angles = tuple(
        180.0 if i % 2 == 0 else -180.0 for i in range(new_fold_count)
    )

    mutated_nose = ParametricNoseSpec(
        fold_count=new_fold_count,
        taper_ratio=new_taper,
        nose_length_ratio=new_nose_len,
        fold_angles_deg=mutated_angles,
    )

    # Phase 2: Keel mutation
    keel = parent.keel
    new_keel_depth = keel.keel_depth_ratio
    if r.random() < mutation_rate:
        new_keel_depth = _clamp(keel.keel_depth_ratio + r.gauss(0.0, 0.005), 0.06, 0.14)

    new_keel_offset = keel.crease_offset_u
    if r.random() < mutation_rate:
        new_keel_offset = _clamp(keel.crease_offset_u + r.gauss(0.0, 0.002), 0.48, 0.52)

    mutated_keel = ParametricKeelSpec(
        keel_depth_ratio=new_keel_depth,
        has_lock_fold=True,
        lock_fold_angle_deg=keel.lock_fold_angle_deg,
        keel_layers=keel.keel_layers,
        crease_offset_u=new_keel_offset,
    )

    # Phase 3: Wing mutation
    wing = parent.wing
    new_span = wing.wingspan_ratio
    if r.random() < mutation_rate:
        new_span = _clamp(wing.wingspan_ratio + r.gauss(0.0, 0.04), 0.55, 0.88)

    new_dihedral = wing.dihedral_deg
    if r.random() < mutation_rate:
        new_dihedral = _clamp(wing.dihedral_deg + r.gauss(0.0, 1.0), -2.0, 10.0)

    new_elevon_l = wing.elevon_left_deg
    new_elevon_r = wing.elevon_right_deg
    if r.random() < mutation_rate:
        delta_e = r.gauss(0.0, 1.2)
        new_elevon_l = _clamp(wing.elevon_left_deg + delta_e, -5.0, 12.0)
        # Keep symmetric by default with occasional slight trim
        new_elevon_r = new_elevon_l if (r.random() > 0.15) else _clamp(
            wing.elevon_right_deg + delta_e + r.gauss(0.0, 0.3), -5.0, 12.0
        )

    new_winglet = wing.winglet_angle_deg
    if r.random() < mutation_rate:
        new_winglet = _clamp(wing.winglet_angle_deg + r.gauss(0.0, 5.0), 15.0, 80.0)

    mutated_wing = ParametricWingSpec(
        wingspan_ratio=new_span,
        dihedral_deg=new_dihedral,
        elevon_left_deg=new_elevon_l,
        elevon_right_deg=new_elevon_r,
        winglet_angle_deg=new_winglet,
        has_winglet=wing.has_winglet,
    )

    return ParametricOrigamiConfig(
        nose=mutated_nose,
        keel=mutated_keel,
        wing=mutated_wing,
        paper_gsm=parent.paper_gsm,
    )
