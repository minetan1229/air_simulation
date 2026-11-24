"""Unit tests for origami geometry validation fixes."""

import math
import pytest
import numpy as np
from geometry.validation_fixes import (
    validate_flat_foldable_vertex,
    panels_intersect_3d,
    cluster_signed_depth,
    area_weighted_centroid_3d,
    origami_to_frd_inertia,
    orthotropic_spanwise_modulus,
    elastica_cantilever_tip,
)
from geometry.origami_genome import genome_from_archetype, fold_genome
from geometry.origami_domain import ARCHETYPE_DART


def test_kawasaki_and_maekawa_reject_invalid_vertex():
    # Valid: 90, 90, 90, 90 deg, M=3, V=1 -> valid
    ok, errs = validate_flat_foldable_vertex([math.pi / 2] * 4, [1, 1, 1, -1])
    assert ok and not errs

    # Kawasaki violation: even sum != odd sum (e.g., 60, 90, 120, 90 -> even=180, odd=180 is valid!)
    # Use 50, 90, 120, 100 -> even=170, odd=190 (violation)
    ok_k, errs_k = validate_flat_foldable_vertex(
        [math.radians(a) for a in (50, 90, 120, 100)], [1, 1, 1, -1]
    )
    assert not ok_k and any("Kawasaki" in e for e in errs_k)

    # Maekawa violation: M=2, V=2 (|M-V|=0)
    ok_m, errs_m = validate_flat_foldable_vertex([math.pi / 2] * 4, [1, -1, 1, -1])
    assert not ok_m and any("Maekawa" in e for e in errs_m)


def test_panels_intersect_catches_3d_slice_and_coplanar_cross():
    # 3D cross-penetration: XZ rectangle vs YZ rectangle
    rect_a = [(-0.05, 0.0, -0.01), (0.05, 0.0, -0.01), (0.05, 0.0, 0.01), (-0.05, 0.0, 0.01)]
    rect_b_3d = [(-0.01, -0.05, 0.0), (0.01, -0.05, 0.0), (0.01, 0.05, 0.0), (-0.01, 0.05, 0.0)]
    # Coplanar + cross overlap
    rect_b_2d = [(-0.01, 0.0, -0.05), (0.01, 0.0, -0.05), (0.01, 0.0, 0.05), (-0.01, 0.0, 0.05)]

    assert panels_intersect_3d(rect_a, (0.0, 1.0, 0.0), rect_b_3d, (0.0, 0.0, 1.0)) is True
    assert panels_intersect_3d(rect_a, (0.0, 1.0, 0.0), rect_b_2d, (0.0, 1.0, 0.0)) is True


def test_flipped_layer_depth_is_positive_above_reference():
    # Face at y=0.002 above reference at y=0.0 with ref_normal=(0,1,0) should have positive depth
    assert cluster_signed_depth((0.04, 0.002, 0.04), (0.05, 0.0, 0.05), (0.0, 1.0, 0.0)) > 0.0


def test_frd_inertia_satisfies_perpendicular_axis_ordering():
    gg = fold_genome(genome_from_archetype(ARCHETYPE_DART))
    frd = origami_to_frd_inertia(gg.inertia_tensor_kgm2)
    # For a flat wing: I_yy (pitch) > I_zz (yaw) > I_xx (roll) in FRD
    # Actually for a dart: span < chord, so yaw (about Z) > pitch (about Y)
    assert frd["yaw_inertia_kgm2"] > frd["pitch_inertia_kgm2"] > frd["roll_inertia_kgm2"]


def test_area_weighted_centroid_matches_exact_value():
    # Asymmetric polygon: triangle + rectangle
    poly = [(0.0, 0.0, 0.0), (0.10, 0.0, 0.0), (0.10, 0.0, 0.10), (0.01, 0.0, 0.01), (0.0, 0.0, 0.02)]
    c, area = area_weighted_centroid_3d(poly)
    assert area > 0.0
    # Expected centroid of this shape
    assert math.isclose(c[0], 0.06542, abs_tol=1.0e-4)


def test_orthotropic_spanwise_stiffness_peaks_at_90deg_transverse_grain():
    # grain_angle_deg=0 means MD along fuselage (x), so spanwise (z) is 90 deg from MD
    e_long = orthotropic_spanwise_modulus(e_md_pa=4.0e9, anisotropy_ratio=2.0, grain_angle_deg=0.0)
    e_trans = orthotropic_spanwise_modulus(e_md_pa=4.0e9, anisotropy_ratio=2.0, grain_angle_deg=90.0)
    # Transverse grain (90 deg) should have higher spanwise stiffness (E_CD along span)
    assert e_trans > e_long
    # At grain_angle=90, MD is along span, so E_span = E_MD = 4e9
    assert math.isclose(e_trans, 4.0e9, rel_tol=1.0e-6)
    # At grain_angle=0, MD is along fuselage, span is CD, so E_span = E_CD = 2e9
    assert math.isclose(e_long, 2.0e9, rel_tol=1.0e-6)


def test_elastica_cantilever_preserves_inextensibility():
    L = 0.10
    z_tip, y_tip, theta_tip = elastica_cantilever_tip(semispan_m=L, uniform_line_load_npm=5.0, ei_nm2=1.0e-3)
    assert 0.0 < y_tip < L
    assert z_tip < L  # Foreshortening occurs
    # Inextensibility: arc length = L, so projected tip distance <= L
    assert math.hypot(z_tip, y_tip) <= L + 1.0e-9


def test_planform_area_silhouette_no_double_count():
    from geometry.validation_fixes import planform_area_silhouette
    from geometry.origami_domain import FoldedFace

    # Two identical overlapping panels - should count area once
    face1 = FoldedFace(
        fid=1, poly3d=[(0,0,0), (1,0,0), (1,0,1), (0,0,1)],
        normal=(0,1,0), front=(1,0,0), level=0, group="wing", role="panel",
        parent=None, centroid3d=(0.5, 0, 0.5), area_m2=1.0
    )
    face2 = FoldedFace(
        fid=2, poly3d=[(0,0,0), (1,0,0), (1,0,1), (0,0,1)],
        normal=(0,1,0), front=(1,0,0), level=1, group="wing", role="panel",
        parent=None, centroid3d=(0.5, 0, 0.5), area_m2=1.0
    )
    area = planform_area_silhouette([face1, face2])
    # Should be 1.0 (not 2.0)
    assert math.isclose(area, 1.0, rel_tol=1e-3)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
