"""Stage 0 of the evaluation pipeline: the geometry and topology filter.

The filter is the first gate of the search.  It has to answer one question
for every individual of a population of up to 100 000 fold programmes per
generation: *can this programme be folded into something that flies?*  The
answer is split in two stages, because the two questions cost very different
amounts of time.

``quick_screen``
    Pure two dimensional work on the crease programme.  No sheet is folded in
    three dimensions, nothing is allocated beyond a handful of small
    polygons, and the cost is linear in the number of folds.  The screen
    rejects the programmes that cannot work at all: a crease that misses the
    sheet, a programme that never touches the base sheet, a wing that is too
    small to lift anything, a fold tree that is nested too deeply.

``full_check``
    The real thing: the sheet is folded in three dimensions with
    :func:`physics.origami_genome.fold_genome` and the resulting solid is
    inspected.  The screen detects the paper passing through itself, the
    wing area and the span of the solid, the number of paper layers at the
    thickest spot, the risk of tearing the paper at a spot that is creased
    too often and the static margin of the solid.

The cheap screen is deliberately *lenient*: it may pass a programme that the
expensive check rejects, but it must never reject a programme that the
expensive check would accept, because every individual it throws away is a
design the search never sees.

The two dimensional model of the quick screen mirrors the conventions of the
folding engine (the same half plane convention, the same mountain and valley
signs, the same group bookkeeping), so the planform area it estimates is the
planform area the three dimensional check measures.  The one place where the
model has to approximate is the orientation of a panel that has already been
folded: the model rotates the panel about the *flat* crease axis instead of
the folded one, which is exact for the first fold and a good approximation
for the shallow programmes the screen sees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import origami_genome
from ..origami_domain import SHEET_LENGTH_M, SHEET_WIDTH_M
from .genome import (
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    HybridFoldGene,
    HybridGenome,
    crease_segment,
    hybrid_to_origami,
)

__all__ = [
    "Stage0Report",
    "GeometryFilter",
    "SHEET_AREA_M2",
    "MIN_CREASE_CHORD_M",
    "MIN_CREASE_LENGTH_M",
    "MAX_FOLDS_AT_ONE_SPOT",
    "HOT_SPOT_RADIUS_M",
    "CP_CHORD_FRACTION",
]


#: The area of the sheet the programme folds.
SHEET_AREA_M2: float = SHEET_WIDTH_M * SHEET_LENGTH_M

#: The shortest crease that still cuts the sheet into two pieces.
MIN_CREASE_CHORD_M: float = 0.020

#: The shortest crease segment that is still a real crease.
MIN_CREASE_LENGTH_M: float = 0.004

#: How often the paper may be creased at the same spot before it tears.
#: A nose that is creased five times is still foldable by hand and a spot
#: with seven creases is not, so six is the last count the paper survives;
#: the criterion itself is written out in the docstring of
#: ``GeometryFilter._hot_spot_count``.  The nose lock of the Nakamura
#: design gathers five creases, the most of the known designs.
MAX_FOLDS_AT_ONE_SPOT: int = 6

#: The radius of the neighbourhood that counts as "the same spot".
#: The folding engine weights a vertex by the creases of its own face
#: within a radius of 4 mm (``physics.origami_domain._crease_weight``);
#: two creases are folded into the same spot of paper once their crease
#: influences meet, which is twice that radius.
HOT_SPOT_RADIUS_M: float = 0.008

#: The quarter chord point of a thin wing, as a fraction of the chord.
CP_CHORD_FRACTION: float = 0.25


# --------------------------------------------------------------------------
# Small two dimensional helpers
# --------------------------------------------------------------------------
def _polygon_area(poly: Sequence[Tuple[float, float]]) -> float:
    """The area of a polygon given by its vertices."""
    count = len(poly)
    if count < 3:
        return 0.0
    total = 0.0
    for index in range(count):
        ax, ay = poly[index]
        bx, by = poly[(index + 1) % count]
        total += ax * by - bx * ay
    return abs(0.5 * total)


def _clip_halfplane(
    poly: Sequence[Tuple[float, float]],
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    keep_positive: bool,
) -> List[Tuple[float, float]]:
    """Clip a convex polygon against the infinite line ``p0 -> p1``.

    The half plane that is kept is the one the directed line has on its
    left (``cross(dir, p - p0) > 0``) when ``keep_positive`` is true.  The
    convention is the one of the folding engine, so the two dimensional model
    splits a panel exactly where the engine splits it.
    """
    if not poly:
        return []
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    out: List[Tuple[float, float]] = []
    count = len(poly)
    for index in range(count):
        ax, ay = poly[index]
        bx, by = poly[(index + 1) % count]
        da = dx * (ay - p0[1]) - dy * (ax - p0[0])
        db = dx * (by - p0[1]) - dy * (bx - p0[0])
        in_a = da >= 0.0 if keep_positive else da <= 0.0
        in_b = db >= 0.0 if keep_positive else db <= 0.0
        if in_a:
            out.append((ax, ay))
        if in_a != in_b:
            denom = da - db
            if abs(denom) > 1.0e-15:
                ratio = da / denom
                out.append((ax + (bx - ax) * ratio, ay + (by - ay) * ratio))
    return out


def _rotate(
    vector: Tuple[float, float, float],
    axis: Tuple[float, float, float],
    angle_rad: float,
) -> Tuple[float, float, float]:
    """Rotate a vector about a unit axis (Rodrigues' formula)."""
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    dot = axis[0] * vector[0] + axis[1] * vector[1] + axis[2] * vector[2]
    cross = (
        axis[1] * vector[2] - axis[2] * vector[1],
        axis[2] * vector[0] - axis[0] * vector[2],
        axis[0] * vector[1] - axis[1] * vector[0],
    )
    return (
        vector[0] * cos_a + cross[0] * sin_a + axis[0] * dot * (1.0 - cos_a),
        vector[1] * cos_a + cross[1] * sin_a + axis[1] * dot * (1.0 - cos_a),
        vector[2] * cos_a + cross[2] * sin_a + axis[2] * dot * (1.0 - cos_a),
    )


def _crease_chord_m(origin: Tuple[float, float], angle_deg: float) -> float:
    """The length of the crease line inside the sheet, in metres.

    The value is zero when the line misses the sheet, which is the cheapest
    way to detect a crease that does not cross the paper at all.
    """
    ox = min(max(float(origin[0]), 0.0), 1.0)
    oy = min(max(float(origin[1]), 0.0), 1.0)
    radians = math.radians(float(angle_deg))
    dx = math.cos(radians)
    dy = math.sin(radians)
    t_low = -math.inf
    t_high = math.inf
    for value, direction in ((ox, dx), (oy, dy)):
        if abs(direction) < 1.0e-12:
            continue
        lower = -value / direction
        upper = (1.0 - value) / direction
        if direction > 0.0:
            t_low = max(t_low, lower)
            t_high = min(t_high, upper)
        else:
            t_low = max(t_low, upper)
            t_high = min(t_high, lower)
    if not math.isfinite(t_low) or not math.isfinite(t_high):
        return 0.0
    span = t_high - t_low
    if span <= 0.0:
        return 0.0
    return span * math.hypot(dx * SHEET_WIDTH_M, dy * SHEET_LENGTH_M)


def _segment_length_m(
    p0: Tuple[float, float], p1: Tuple[float, float]
) -> float:
    """The length of a crease segment in metres."""
    du = (p1[0] - p0[0]) * SHEET_WIDTH_M
    dv = (p1[1] - p0[1]) * SHEET_LENGTH_M
    return math.hypot(du, dv)


def _line_intersection(
    a0: Tuple[float, float],
    a1: Tuple[float, float],
    b0: Tuple[float, float],
    b1: Tuple[float, float],
) -> Optional[Tuple[float, float]]:
    """The intersection of two segments, or ``None`` if they do not cross.

    Collinear segments have no single intersection point; the caller handles
    the overlapping case separately.
    """
    ax = a1[0] - a0[0]
    ay = a1[1] - a0[1]
    bx = b1[0] - b0[0]
    by = b1[1] - b0[1]
    denom = ax * by - ay * bx
    if abs(denom) < 1.0e-15:
        return None
    diff_x = b0[0] - a0[0]
    diff_y = b0[1] - a0[1]
    t = (diff_x * by - diff_y * bx) / denom
    u = (diff_x * ay - diff_y * ax) / denom
    if t < -1.0e-9 or t > 1.0 + 1.0e-9:
        return None
    if u < -1.0e-9 or u > 1.0 + 1.0e-9:
        return None
    return (a0[0] + ax * t, a0[1] + ay * t)


def _segments_overlap(
    a0: Tuple[float, float],
    a1: Tuple[float, float],
    b0: Tuple[float, float],
    b1: Tuple[float, float],
) -> Optional[Tuple[float, float]]:
    """The middle of the overlap of two segments that lie on the same line.

    Two segments overlap only when they lie on the *same* line: segments
    that run parallel to each other but are offset from each other do not
    touch, and reporting them as overlapping would invent a junction where
    the paper has none.  The lengths and the projection are computed in
    metres, because the sheet is 210 x 297 mm and a normalised length is
    not a length.
    """
    a0m = (a0[0] * SHEET_WIDTH_M, a0[1] * SHEET_LENGTH_M)
    a1m = (a1[0] * SHEET_WIDTH_M, a1[1] * SHEET_LENGTH_M)
    b0m = (b0[0] * SHEET_WIDTH_M, b0[1] * SHEET_LENGTH_M)
    b1m = (b1[0] * SHEET_WIDTH_M, b1[1] * SHEET_LENGTH_M)
    ax = a1m[0] - a0m[0]
    ay = a1m[1] - a0m[1]
    bx = b1m[0] - b0m[0]
    by = b1m[1] - b0m[1]
    la = math.hypot(ax, ay)
    lb = math.hypot(bx, by)
    if la < 1.0e-12 or lb < 1.0e-12:
        return None
    ux, uy = ax / la, ay / la
    if abs(bx * uy - by * ux) > 1.0e-9:
        return None
    du = b0m[0] - a0m[0]
    dv = b0m[1] - a0m[1]
    if abs(du * -uy + dv * ux) > 1.0e-9:
        return None
    along = bx * ux + by * uy
    t0 = du * ux + dv * uy
    t1 = t0 + along
    start = max(0.0, min(t0, t1))
    end = min(la, max(t0, t1))
    if end <= start:
        return None
    middle = 0.5 * (start + end)
    return (
        (a0m[0] + ux * middle) / SHEET_WIDTH_M,
        (a0m[1] + uy * middle) / SHEET_LENGTH_M,
    )


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------
@dataclass
class Stage0Report:
    """The outcome of the geometry filter for one fold programme."""

    valid: bool
    reasons: List[str]
    wing_area_m2: float
    span_m: float
    aspect_ratio: float
    mass_kg: float
    cg: Tuple[float, float, float]
    static_margin_m: float
    penetrations: int
    fold_count: int
    layer_count: int

    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the report."""
        return {
            "valid": bool(self.valid),
            "reasons": list(self.reasons),
            "wing_area_m2": float(self.wing_area_m2),
            "span_m": float(self.span_m),
            "aspect_ratio": float(self.aspect_ratio),
            "mass_kg": float(self.mass_kg),
            "cg": [float(value) for value in self.cg],
            "static_margin_m": float(self.static_margin_m),
            "penetrations": int(self.penetrations),
            "fold_count": int(self.fold_count),
            "layer_count": int(self.layer_count),
        }


# --------------------------------------------------------------------------
# The cheap two dimensional fold model
# --------------------------------------------------------------------------
@dataclass
class _Flap:
    """One panel of the sheet in the cheap two dimensional fold model."""

    poly: List[Tuple[float, float]]
    normal: Tuple[float, float, float]
    group: int
    branch: int
    depth: int
    side: int = 0


@dataclass
class _Estimate:
    """What the cheap model says about a programme."""

    planform_m2: float
    total_planform_m2: float
    span_m: float
    stack_depth: int
    base_folds: int


def _model_programme(genes: Sequence[HybridFoldGene]) -> _Estimate:
    """Run the cheap two dimensional fold model over a programme.

    The model cuts every panel along the creases the programme applies, turns
    the moving part about the crease and keeps the orientation of the result,
    so the horizontal projection of every panel is known.  The sum of these
    projections is the planform of the folded model, the same quantity the
    three dimensional check measures on the real solid.
    """
    flaps: List[_Flap] = [
        _Flap(
            poly=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
            normal=(0.0, 1.0, 0.0),
            group=0,
            branch=0,
            depth=0,
        )
    ]
    created: List[int] = []
    next_group = 1
    base_folds = 0
    branches_ready = False

    for gene in genes:
        p0, p1 = crease_segment(gene.origin, gene.crease_angle_deg, gene.length_ratio)
        du = p1[0] - p0[0]
        dv = p1[1] - p0[1]
        norm = math.hypot(du, dv)
        if norm < 1.0e-12:
            continue
        du /= norm
        dv /= norm
        axis = (dv * SHEET_LENGTH_M, 0.0, du * SHEET_WIDTH_M)
        axis_len = math.hypot(axis[0], axis[2])
        if axis_len < 1.0e-12:
            continue
        axis = (axis[0] / axis_len, 0.0, axis[2] / axis_len)

        scope = tuple(
            created[value] for value in gene.target_layers if 0 <= value < len(created)
        )
        if not scope:
            base_folds += 1

        magnitude = abs(float(gene.fold_angle_deg))
        up = float(gene.fold_angle_deg) >= 0.0
        if gene.symmetry == "symmetric":
            side = 0
        elif gene.symmetry == "left":
            side = 1
        else:
            side = -1
        half = 0.5 * magnitude
        if side == 0:
            phi_plus = half if up else -half
            phi_minus = -half if up else half
        else:
            phi_plus = magnitude if up else -magnitude
            phi_minus = -phi_plus

        next_flaps: List[_Flap] = []
        moved: List[_Flap] = []
        assign_branches = not branches_ready
        for flap in flaps:
            if scope and flap.group not in scope:
                next_flaps.append(flap)
                continue
            plus = _clip_halfplane(flap.poly, p0, p1, True)
            minus = _clip_halfplane(flap.poly, p0, p1, False)
            if len(plus) < 3 or len(minus) < 3:
                next_flaps.append(flap)
                continue
            if not branches_ready:
                branches_ready = True
            if side == 0:
                for part, is_plus, phi in (
                    (plus, True, phi_plus),
                    (minus, False, phi_minus),
                ):
                    child = _Flap(
                        poly=part,
                        normal=_rotate(flap.normal, axis, math.radians(phi)),
                        group=flap.group,
                        branch=(
                            (0 if is_plus else 1) if assign_branches else flap.branch
                        ),
                        depth=flap.depth + 1,
                        side=1 if is_plus else -1,
                    )
                    next_flaps.append(child)
                    moved.append(child)
            else:
                if side > 0:
                    moving_part, static_part, phi = plus, minus, phi_plus
                else:
                    moving_part, static_part, phi = minus, plus, phi_minus
                child = _Flap(
                    poly=moving_part,
                    normal=_rotate(flap.normal, axis, math.radians(phi)),
                    group=flap.group,
                    branch=(0 if side > 0 else 1) if assign_branches else flap.branch,
                    depth=flap.depth + 1,
                    side=1 if side > 0 else -1,
                )
                static = _Flap(
                    poly=static_part,
                    normal=flap.normal,
                    group=flap.group,
                    branch=(1 if side > 0 else 0) if assign_branches else flap.branch,
                    depth=flap.depth,
                    side=-1 if side > 0 else 1,
                )
                next_flaps.append(child)
                next_flaps.append(static)
                moved.append(child)
        flaps = next_flaps

        if gene.creates_pair:
            pair = (next_group, next_group + 1)
            next_group += 2
            created.extend(pair)
            if side == 0:
                for flap in moved:
                    flap.group = pair[0] if flap.side > 0 else pair[1]
            else:
                for flap in moved:
                    flap.group = pair[0]
        elif gene.join_group is not None and 0 <= gene.join_group < len(created):
            for flap in moved:
                flap.group = created[gene.join_group]
        elif gene.creates_group:
            created.append(next_group)
            for flap in moved:
                flap.group = next_group
            next_group += 1

    branch_area = [0.0, 0.0]
    total = 0.0
    low_u = 1.0
    high_u = 0.0
    depth = 0
    for flap in flaps:
        projection = _polygon_area(flap.poly) * abs(flap.normal[1]) * SHEET_AREA_M2
        total += projection
        if 0 <= flap.branch < len(branch_area):
            branch_area[flap.branch] += projection
        depth = max(depth, flap.depth)
        for u, _v in flap.poly:
            low_u = min(low_u, u)
            high_u = max(high_u, u)
    return _Estimate(
        planform_m2=max(branch_area),
        total_planform_m2=total,
        span_m=max(high_u - low_u, 0.0) * SHEET_WIDTH_M,
        stack_depth=depth + 1,
        base_folds=base_folds,
    )


# --------------------------------------------------------------------------
# The filter
# --------------------------------------------------------------------------
class GeometryFilter:
    """The geometry and topology filter of stage 0.

    The filter owns the thresholds of the specification and the two ways it
    looks at a programme: the cheap two dimensional screen and the expensive
    three dimensional check.
    """

    def __init__(
        self,
        min_wing_area_m2: float = 0.010,
        min_span_m: float = 0.08,
        max_penetrations: int = 0,
        min_static_margin_m: float = -0.002,
        max_layers: int = 12,
        paper_gsm: float = 100.0,
        min_fold_steps: int = MIN_FOLD_STEPS,
        max_fold_levels: int = MAX_FOLD_STEPS,
        min_crease_chord_m: float = MIN_CREASE_CHORD_M,
        min_crease_length_m: float = MIN_CREASE_LENGTH_M,
        max_folds_at_spot: int = MAX_FOLDS_AT_ONE_SPOT,
        cp_chord_fraction: float = CP_CHORD_FRACTION,
    ) -> None:
        """Build the filter.

        ``min_fold_steps`` and ``max_fold_levels`` bound the fold programme:
        a programme needs at least three creases to be an aeroplane, and the
        fold tree may not be nested deeper than the engine resolves.  The
        *number* of creases of a programme is deliberately not bounded from
        above, because the archetype seeds and their mutants carry eleven to
        fourteen creases and a filter that threw them away would empty the
        warm start of the search.
        """
        self.min_wing_area_m2 = max(float(min_wing_area_m2), 0.0)
        self.min_span_m = max(float(min_span_m), 0.0)
        self.max_penetrations = max(int(max_penetrations), 0)
        self.min_static_margin_m = float(min_static_margin_m)
        self.max_layers = max(int(max_layers), 1)
        self.paper_gsm = max(float(paper_gsm), 1.0e-3)
        self.min_fold_steps = max(int(min_fold_steps), 0)
        self.max_fold_levels = max(int(max_fold_levels), 1)
        self.min_crease_chord_m = max(float(min_crease_chord_m), 0.0)
        self.min_crease_length_m = max(float(min_crease_length_m), 0.0)
        self.max_folds_at_spot = max(int(max_folds_at_spot), 1)
        self.cp_chord_fraction = min(max(float(cp_chord_fraction), 0.0), 1.0)

    # ------------------------------------------------------------------
    # The cheap screen
    # ------------------------------------------------------------------
    def quick_screen(self, genome: HybridGenome) -> Stage0Report:
        """Screen a programme in two dimensions, without folding it.

        The screen looks at the crease programme only: it counts the folds,
        checks that every crease really crosses the sheet, runs the cheap
        fold model and compares the planform, the span and the stack depth
        the model predicts with the thresholds of the filter.

        The screen works on the *enabled* genes of the programme, because a
        disabled fold is a fold the search has switched off.  The planform it
        estimates is the one of a single side of the aeroplane, the biggest
        of the two branches the first crease cuts the sheet into, and it is
        an upper bound of the real planform: the screen must not throw away
        a design the three dimensional check would accept.
        """
        reasons: List[str] = []
        genes = [gene for gene in genome.genes if gene.enabled]
        fold_count = len(genes)

        if fold_count < self.min_fold_steps:
            reasons.append("too few folds")
        levels = [int(gene.level) for gene in genes]
        fold_levels = (max(levels) + 1) if levels else 0
        if fold_levels > self.max_fold_levels:
            reasons.append("fold programme nested too deeply")

        for gene in genes:
            origin = (float(gene.origin[0]), float(gene.origin[1]))
            angle = float(gene.crease_angle_deg)
            if not (math.isfinite(origin[0]) and math.isfinite(origin[1])):
                reasons.append("crease parameters are not finite")
                break
            if not math.isfinite(angle):
                reasons.append("crease parameters are not finite")
                break
            if _crease_chord_m(origin, angle) < self.min_crease_chord_m:
                reasons.append("crease does not cross the sheet")
                break
            p0, p1 = crease_segment(origin, angle, float(gene.length_ratio))
            if _segment_length_m(p0, p1) < self.min_crease_length_m:
                reasons.append("crease too short")
                break

        estimate = _model_programme(genes)
        if estimate.base_folds < 1:
            reasons.append("no fold acts on the base sheet")
        if estimate.planform_m2 < self.min_wing_area_m2:
            reasons.append("wing area too small")
        if estimate.span_m < self.min_span_m:
            reasons.append("span too small")
        if estimate.stack_depth > self.max_layers:
            reasons.append("stack too deep")

        mass = self.paper_gsm * 1.0e-3 * SHEET_AREA_M2
        aspect = 0.0
        if estimate.span_m > 1.0e-9 and estimate.planform_m2 > 1.0e-9:
            aspect = (estimate.span_m * estimate.span_m) / estimate.planform_m2
        return Stage0Report(
            valid=not reasons,
            reasons=reasons,
            wing_area_m2=estimate.planform_m2,
            span_m=estimate.span_m,
            aspect_ratio=aspect,
            mass_kg=mass,
            cg=(0.0, 0.0, 0.0),
            static_margin_m=0.0,
            penetrations=0,
            fold_count=fold_count,
            layer_count=estimate.stack_depth,
        )

    # ------------------------------------------------------------------
    # The three dimensional check
    # ------------------------------------------------------------------
    def full_check(self, genome: HybridGenome) -> Stage0Report:
        """Fold the sheet in three dimensions and inspect the solid.

        The check measures the planform area, the span and the layer count of
        the solid, counts the panels that pass through each other, looks for
        the spots where the paper is creased too often and places the centre
        of pressure of the thin wing against the centre of gravity.

        The static margin of the report is the distance the centre of
        gravity lies ahead of the quarter chord point of the planform, the
        quarter chord point being measured from the rear edge of the solid.
        A folded aeroplane has no recoverable leading edge -- the archetype
        programmes fold the nose onto the body, so the fore and aft extent
        of the solid is shorter than the chord of the aeroplane -- and the
        rear edge is the edge the tail of every one of them is at.  The
        check therefore rejects the layouts that pile their mass up in the
        rear of the solid and leaves the exact placement of the centre of
        pressure to the flight model, which knows the aeroplane.
        """
        reasons: List[str] = []
        try:
            programme = hybrid_to_origami(genome)
            geometry = origami_genome.fold_genome(programme, paper_gsm=self.paper_gsm)
        except Exception as error:  # pragma: no cover - defensive
            return Stage0Report(
                valid=False,
                reasons=[f"folding failed: {error}"],
                wing_area_m2=0.0,
                span_m=0.0,
                aspect_ratio=0.0,
                mass_kg=0.0,
                cg=(0.0, 0.0, 0.0),
                static_margin_m=0.0,
                penetrations=0,
                fold_count=0,
                layer_count=0,
            )

        hits = origami_genome.detect_penetrations(
            geometry.faces,
            origami_genome.adjacent_panel_pairs(geometry.pattern),
            polygons=origami_genome._separated_polygons(geometry),
        )
        penetrations = len(hits)
        if penetrations > self.max_penetrations:
            reasons.append("self penetration")
        if geometry.wing_area_m2 < self.min_wing_area_m2:
            reasons.append("wing area too small")
        if geometry.span_m < self.min_span_m:
            reasons.append("span too small")
        if geometry.max_layers > self.max_layers:
            reasons.append("too many paper layers")

        hot_spot = self._hot_spot_count(genome)
        if hot_spot > self.max_folds_at_spot:
            reasons.append("paper would tear")

        low, high = geometry.bounds()
        chord = max(high[0] - low[0], 0.0)
        cp_x = low[0] + self.cp_chord_fraction * chord
        static_margin = float(geometry.cg[0]) - cp_x
        if static_margin < self.min_static_margin_m:
            reasons.append("CG behind CP")

        aspect = 0.0
        if geometry.span_m > 1.0e-9 and geometry.wing_area_m2 > 1.0e-9:
            aspect = (geometry.span_m * geometry.span_m) / geometry.wing_area_m2
        return Stage0Report(
            valid=not reasons,
            reasons=reasons,
            wing_area_m2=float(geometry.wing_area_m2),
            span_m=float(geometry.span_m),
            aspect_ratio=aspect,
            mass_kg=float(geometry.mass_kg),
            cg=(float(geometry.cg[0]), float(geometry.cg[1]), float(geometry.cg[2])),
            static_margin_m=static_margin,
            penetrations=penetrations,
            fold_count=sum(1 for gene in genome.genes if gene.enabled),
            layer_count=int(geometry.max_layers),
        )

    def evaluate(self, genome: HybridGenome) -> Stage0Report:
        """Screen a programme cheaply and, if it survives, fold it.

        This is the entry point of the stage: the cheap screen runs on every
        individual of the population, the three dimensional check only on the
        ones that survive it.
        """
        quick = self.quick_screen(genome)
        if not quick.valid:
            return quick
        return self.full_check(genome)

    # ------------------------------------------------------------------
    # The tear risk
    # ------------------------------------------------------------------
    def _hot_spot_count(self, genome: HybridGenome) -> int:
        """The largest number of creases that meet at one spot.

        Paper tears when it is creased at the same spot too often; a crease
        that crosses another crease is the spot where the fibre is bent
        twice.  The creases that meet at one point are collected and the
        clusters are counted, so a nose that is creased five times is still
        foldable by hand while a spot with seven creases is not.
        """
        segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        for gene in genome.genes:
            if not gene.enabled:
                continue
            p0, p1 = crease_segment(
                gene.origin, gene.crease_angle_deg, gene.length_ratio
            )
            segments.append((p0, p1))
        points: List[Tuple[float, float, int, int]] = []
        count = len(segments)
        for first in range(count):
            a0, a1 = segments[first]
            for second in range(first + 1, count):
                b0, b1 = segments[second]
                hit = _line_intersection(a0, a1, b0, b1)
                if hit is None:
                    overlap = _segments_overlap(a0, a1, b0, b1)
                    if overlap is not None:
                        points.append((overlap[0], overlap[1], first, second))
                    continue
                points.append((hit[0], hit[1], first, second))
        if not points:
            return 0
        worst = 0
        for index, (x0, y0, i0, j0) in enumerate(points):
            cluster = {i0, j0}
            mx = x0 * SHEET_WIDTH_M
            my = y0 * SHEET_LENGTH_M
            for x1, y1, i1, j1 in points[index + 1 :]:
                other = math.hypot(
                    x1 * SHEET_WIDTH_M - mx, y1 * SHEET_LENGTH_M - my
                )
                if other <= HOT_SPOT_RADIUS_M:
                    cluster.add(i1)
                    cluster.add(j1)
            worst = max(worst, len(cluster))
        return worst
