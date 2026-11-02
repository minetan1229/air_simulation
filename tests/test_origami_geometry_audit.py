"""Regression fixation of the origami / analysis geometry audit.

The audit compares the *folded* model with the analysis airframe.  The area
that is comparable with ``derived_properties()["wing_area_m2"]`` is the
**planform** -- the silhouette of the folded solid seen along the flight axis.
The developed paper area (``OrigamiMesh.surface_area()``) counts both faces
of the sheet and every folded layer, so it is about 3.8 times the planform of
a dart and must not be compared with a planform area.

The ledger item is ``no-finish.md`` P1-15 (the quantitative version of G-1 /
G-2): the folded model and the analysis airframe disagree in span, planform
area and geometric aspect ratio.
"""

from __future__ import annotations

import math

import pytest

from physics import origami_geometry_audit
from physics.origami_geometry_audit import _planform_area_m2, audit_geometry
from physics.origami_domain import ARCHETYPES


class _FlatMesh:
    """A minimal mesh stub: a rectangle in the chord-span plane."""

    def __init__(self, positions, indices):
        self.positions = positions
        self.indices = indices

    def bounds(self):
        lo = [min(p[axis] for p in self.positions) for axis in range(3)]
        hi = [max(p[axis] for p in self.positions) for axis in range(3)]
        return (tuple(lo), tuple(hi))

    def surface_area(self) -> float:
        return 0.0


def _rectangle_mesh(chord_m: float, span_m: float) -> _FlatMesh:
    positions = [
        (0.0, 0.0, 0.0),
        (chord_m, 0.0, 0.0),
        (chord_m, 0.0, span_m),
        (0.0, 0.0, span_m),
    ]
    indices = [(0, 1, 2), (0, 2, 3)]
    return _FlatMesh(positions, indices)


def test_planform_area_of_a_flat_rectangle_is_exact():
    """The silhouette raster reproduces a known area."""
    chord, span = 0.08, 0.04
    mesh = _rectangle_mesh(chord, span)
    area = _planform_area_m2(mesh, cell_m=1.0e-4)
    assert area == pytest.approx(chord * span, rel=5.0e-3)


def test_planform_area_counts_overlapping_layers_once():
    """Two stacked copies of the same rectangle cover the same planform.

    The developed area of the stack is twice as large, so a metric that used
    the surface area would report twice the planform.
    """
    single = _rectangle_mesh(0.08, 0.04)
    doubled_positions = list(single.positions) + [
        (x, 0.01, z) for (x, _y, z) in single.positions
    ]
    doubled = _FlatMesh(
        doubled_positions,
        list(single.indices) + [(a + 4, b + 4, c + 4) for (a, b, c) in single.indices],
    )
    one = _planform_area_m2(single, cell_m=1.0e-4)
    two = _planform_area_m2(doubled, cell_m=1.0e-4)
    assert two == pytest.approx(one, rel=5.0e-3)


def test_planform_area_is_zero_for_an_empty_mesh():
    assert _planform_area_m2(_FlatMesh([], []), cell_m=1.0e-3) == 0.0


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_audit_reports_a_physical_planform_and_aspect_ratio(archetype):
    """The published planform has to be a wing-sized area, not a paper area."""
    audit = audit_geometry(archetype=archetype, alpha=1.0)

    assert audit.origami_planform_area_m2 > 0.0
    # The developed sheet area counts both faces and every folded layer, so it
    # is strictly larger than the planform of the same model.
    assert audit.origami_sheet_area_m2 > audit.origami_planform_area_m2
    # The planform cannot exceed the planform area of the sheet it is cut from.
    assert audit.origami_planform_area_m2 < audit.origami_sheet_area_m2
    # The aspect ratio is the one derived from the planform, not from the
    # developed area: a dart is a low aspect ratio model.
    expected_ar = (audit.origami_span_m ** 2) / audit.origami_planform_area_m2
    assert audit.origami_aspect_ratio == pytest.approx(expected_ar, rel=1e-12)
    assert audit.origami_aspect_ratio < 2.0


def test_audit_differences_are_self_consistent():
    audit = audit_geometry(archetype="classic_dart", alpha=1.0)
    assert audit.span_diff_m == pytest.approx(
        audit.origami_span_m - audit.analysis_span_m, rel=1e-12
    )
    assert audit.area_diff_m2 == pytest.approx(
        audit.origami_planform_area_m2 - audit.analysis_area_m2, rel=1e-12
    )
    assert audit.aspect_diff == pytest.approx(
        audit.origami_aspect_ratio - audit.analysis_aspect_ratio, rel=1e-12
    )
    assert audit.sheet_area_diff_m2 == pytest.approx(
        audit.origami_sheet_area_m2 - audit.analysis_sheet_area_m2, rel=1e-12
    )
    assert audit.area_diff_pct == pytest.approx(
        100.0 * audit.area_diff_m2 / audit.analysis_area_m2, rel=1e-12
    )


def test_audit_span_fit_reproduces_the_requested_span():
    """The 2-stage fit (api/routes/origami.py) must be honoured."""
    audit = audit_geometry(archetype="classic_dart", alpha=1.0, span_m=0.2)
    assert audit.origami_span_m == pytest.approx(0.2, rel=5.0e-3)


def test_audit_rejects_an_unknown_archetype():
    with pytest.raises(ValueError):
        audit_geometry(archetype="not_an_archetype")


def test_audit_clamps_the_fold_parameter():
    audit = audit_geometry(archetype="classic_dart", alpha=4.0)
    assert audit.alpha == 1.0
    audit = audit_geometry(archetype="classic_dart", alpha=-3.0)
    assert audit.alpha == 0.0


def test_the_audit_module_does_not_touch_the_physics():
    """The audit is a measurement only: it must not change the airframe.

    This is the A-14 guardrail in code: the folded model is display-only, so
    the audit may read the airframe but never write to it.
    """
    from physics.config_schema import AircraftConfig

    config = AircraftConfig()
    before = repr(config)
    audit_geometry(archetype="classic_dart", alpha=1.0, config=config)
    assert repr(config) == before


def test_planform_area_matches_the_ledger_quantification():
    """The planform of the default dart is the order the ledger records.

    ``no-finish.md`` P1-15 records 0.036 m^2 and a geometric aspect ratio of
    0.60 for the folded airframe; the rasterised silhouette has to land in
    the same neighbourhood (the exact value depends on the projection
    convention and on the sheet revision).
    """
    audit = audit_geometry(archetype="classic_dart", alpha=1.0)
    assert 0.02 < audit.origami_planform_area_m2 < 0.045
    assert 0.45 < audit.origami_aspect_ratio < 1.0
    assert math.isclose(audit.analysis_aspect_ratio, 8.0 / 3.0, rel_tol=1e-9)
