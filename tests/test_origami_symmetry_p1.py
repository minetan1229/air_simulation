"""Regression tests for Phase 2 contracts: P1-16 (Parameter interpretation) and P1-17 (Crease symmetry bounds).

Fixates:
- P1-16: Dihedral and fold angle parameter handling in origami domain without silent sign drops or clipping.
- P1-17: Quantifies and bounds left-right symmetry in folded meshes to prevent unmonitored asymmetric creep.
"""

from __future__ import annotations

import pytest

from physics.origami_domain import ARCHETYPES, build_mesh


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_p1_16_dihedral_parameter_influences_wing_geometry(archetype: str) -> None:
    """Dihedral angle parameter directly alters the lateral span bounds of the folded mesh."""
    # Build with zero dihedral
    mesh_flat = build_mesh(archetype, alpha=1.0, params={"dihedral_deg": 0.0})
    lo_flat, hi_flat = mesh_flat.bounds()
    span_flat = hi_flat[2] - lo_flat[2]

    # Build with positive dihedral (wingtips inclined inward/upward)
    mesh_dihedral = build_mesh(archetype, alpha=1.0, params={"dihedral_deg": 15.0})
    lo_dih, hi_dih = mesh_dihedral.bounds()
    span_dihedral = hi_dih[2] - lo_dih[2]

    # Elevated wingtips must reduce the projected lateral span
    assert span_dihedral < span_flat


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_p1_17_left_right_span_symmetry_is_bounded(archetype: str) -> None:
    """The left-right span difference due to multi-layer tessellation must remain strictly bounded."""
    mesh = build_mesh(archetype, alpha=1.0)
    lo, hi = mesh.bounds()

    # z axis is spanwise, centered around 0 (left wing is -z, right wing is +z)
    left_span_m = abs(lo[2])
    right_span_m = abs(hi[2])
    span_diff_mm = abs(left_span_m - right_span_m) * 1000.0

    # Ensure asymmetry is bounded within known manufacturing limits (under 5.0 mm)
    assert span_diff_mm < 5.0, f"{archetype} left/right span asymmetry exceeds 5.0mm: {span_diff_mm:.2f}mm"
    # Total wingspan must be positive and realistic for a paper sheet
    total_span_m = hi[2] - lo[2]
    assert 0.08 < total_span_m < 0.30
