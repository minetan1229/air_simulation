"""Procedural origami genome: random fold programmes, grip filter, recipe.

A paper aeroplane is not a mesh that is scaled and rotated; it is the
*result of a programme* - an ordered sequence of folds applied to one flat
sheet.  This module makes that programme the unit of design:

``FoldStep``
    one fold operation: which panels it cuts, where the crease line runs,
    how far the paper is turned (mountain or valley, ``-180`` .. ``+180``
    degrees) and how the resulting layers are ordered.
``OrigamiGenome``
    the whole programme: a sheet outline plus the ordered fold steps.
``fold_genome``
    executes the programme: the sheet is cut along every crease in turn and
    the moving half is rotated about the crease line *in three space*, so the
    result is a real folded solid with real layer stacks - never a stretched
    or scaled copy of a template.
``evaluate_grip``
    the geometric filter: a folded sheet that has no grip (no keel under the
    centre, centre of gravity outside the grip, paper passing through
    itself) cannot be thrown by hand, so it is rejected *before* the
    expensive 6-DoF evaluation runs.
``recipe_steps`` / ``crease_pattern_svg`` / ``step_meshes``
    the human facing side: the ordered table of "which line, which way, how
    many degrees", the flat crease pattern with the mountains in red and the
    valleys in blue, and the intermediate solid of every single step.

The folding engine itself lives in :mod:`physics.origami_domain`; this module
adds the genome, the filter and the recipe on top of it.  Both are pure
geometry: the flight dynamics is not touched.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .origami_domain import (
    LAYER_PITCH_M,
    SHEET_LENGTH_M,
    SHEET_WIDTH_M,
    Crease,
    CreasePattern,
    FoldedFace,
    LayerAssignment,
    OrigamiMesh,
    Vec2,
    Vec3,
    assign_layers,
    build_pattern_mesh,
    compile_creases,
    mesh_payload,
)

__all__ = [
    "FoldStep",
    "OrigamiGenome",
    "GenomeGeometry",
    "GripThresholds",
    "GripReport",
    "SHEET_OUTLINE",
    "random_genome",
    "genome_from_creases",
    "genome_from_archetype",
    "genome_from_dict",
    "genome_to_creases",
    "fold_genome",
    "evaluate_grip",
    "detect_penetrations",
    "adjacent_panel_pairs",
    "genome_to_aircraft_config",
    "recipe_steps",
    "recipe_markdown",
    "crease_pattern_svg",
    "step_meshes",
    "genome_payload",
]

#: The flat sheet in sheet space: ``u`` across the short edge, ``v`` along the
#: long edge with the nose at ``v = 1``.
SHEET_OUTLINE: List[Vec2] = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

#: Symmetry modes of a fold step.
SYMMETRY_SYMMETRIC = "symmetric"
SYMMETRY_LEFT = "left"
SYMMETRY_RIGHT = "right"
SYMMETRY_MODES: Tuple[str, ...] = (SYMMETRY_SYMMETRIC, SYMMETRY_LEFT, SYMMETRY_RIGHT)

#: Layer order policies of a fold step.
LAYER_ORDER_AUTO = "auto"
LAYER_ORDER_ABOVE = "above"
LAYER_ORDER_BELOW = "below"
LAYER_ORDER_MODES: Tuple[str, ...] = (LAYER_ORDER_AUTO, LAYER_ORDER_ABOVE, LAYER_ORDER_BELOW)

#: The fold senses a client may name in the ``fold_type`` field of a step.
#: The keys are matched case-insensitively after the surrounding blanks have
#: been stripped; the Japanese words are the ones the recipe uses.
_FOLD_TYPE_ALIASES: Dict[str, bool] = {
    "mountain": True,
    "山": True,
    "true": True,
    "valley": False,
    "谷": False,
    "false": False,
}

#: Fold angle limits (degrees).  ``+180`` is a fully flattened fold (the two
#: halves lie on top of each other), ``0`` is a flat (unfolded) crease and
#: small angles are the trim folds of an elevon or a dihedral.
MIN_FOLD_ANGLE_DEG = -180.0
MAX_FOLD_ANGLE_DEG = 180.0


# --------------------------------------------------------------------------
# Genome data model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FoldStep:
    """One fold operation of the programme.

    ``crease_line``
        the crease in normalised sheet coordinates ``((u0, v0), (u1, v1))``.
    ``fold_angle_deg``
        how far the paper is turned, ``-180.0`` .. ``+180.0``.  The sign
        carries the mountain / valley sense: positive folds the moving half
        *away* from the reader (mountain), negative folds it *towards* the
        reader (valley).  ``+/-180`` is the fully flattened fold, small
        values are the trim folds.
    ``symmetry``
        ``"symmetric"`` turns both halves by half the angle (the centre
        keel), ``"left"`` / ``"right"`` turn a single half.
    ``layer_order``
        how the moving half is stacked against the static half:
        ``"auto"`` lets the layer solver decide, ``"above"`` / ``"below"``
        force the sense and thereby keep the paper from passing through
        itself.
    ``target_face_ids``
        the panels the crease actually cuts.  They are resolved when the
        programme is executed; the generator fills them in so that the
        recipe can name the panels a human has to move.
    """

    step_index: int
    crease_line: Tuple[Vec2, Vec2]
    fold_angle_deg: float
    symmetry: str = SYMMETRY_SYMMETRIC
    layer_order: str = LAYER_ORDER_AUTO
    target_face_ids: Tuple[int, ...] = ()
    group: Tuple[str, ...] = ()
    set_group: str = "base"
    set_group_pair: Optional[Tuple[str, str]] = None
    level: int = 0
    residual_deg: float = 0.0
    mountain: bool = True
    label: str = ""

    @property
    def angle_magnitude_deg(self) -> float:
        """The unsigned amount the paper is turned."""
        return abs(self.fold_angle_deg)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": int(self.step_index),
            "crease_line": [list(self.crease_line[0]), list(self.crease_line[1])],
            "crease_line_mm": [
                [self.crease_line[0][0] * SHEET_WIDTH_M, self.crease_line[0][1] * SHEET_LENGTH_M],
                [self.crease_line[1][0] * SHEET_WIDTH_M, self.crease_line[1][1] * SHEET_LENGTH_M],
            ],
            "fold_angle_deg": float(self.fold_angle_deg),
            "fold_angle_magnitude_deg": self.angle_magnitude_deg,
            "fold_type": "mountain" if self.mountain else "valley",
            "symmetry": self.symmetry,
            "layer_order": self.layer_order,
            "target_face_ids": [int(value) for value in self.target_face_ids],
            "group": list(self.group),
            "set_group": self.set_group,
            "set_group_pair": list(self.set_group_pair) if self.set_group_pair else None,
            "level": int(self.level),
            "residual_deg": float(self.residual_deg),
            "label": self.label,
        }


@dataclass
class OrigamiGenome:
    """A complete fold programme for one sheet of paper."""

    steps: List[FoldStep] = field(default_factory=list)
    sheet: List[Vec2] = field(default_factory=lambda: list(SHEET_OUTLINE))
    key: str = "random"
    label: str = "random fold programme"
    seed: int = 0
    strategy: str = "families"
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "seed": int(self.seed),
            "strategy": self.strategy,
            "sheet": [list(point) for point in self.sheet],
            "steps": [step.to_dict() for step in self.steps],
            "meta": dict(self.meta),
        }


@dataclass
class GenomeGeometry:
    """The solid a genome folds into, with its mass properties."""

    genome: OrigamiGenome
    pattern: CreasePattern
    faces: List[FoldedFace]
    layers: LayerAssignment
    mass_kg: float
    cg: Vec3
    inertia_tensor_kgm2: List[List[float]]
    roll_inertia_kgm2: float
    pitch_inertia_kgm2: float
    yaw_inertia_kgm2: float
    wing_area_m2: float
    span_m: float
    length_m: float
    chord_m: float
    total_area_m2: float
    max_layers: int

    def bounds(self) -> Tuple[Vec3, Vec3]:
        """Axis aligned bounds of the folded solid."""
        if not self.faces:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        lo = [math.inf, math.inf, math.inf]
        hi = [-math.inf, -math.inf, -math.inf]
        for face in self.faces:
            for point in face.poly3d:
                for axis in range(3):
                    lo[axis] = min(lo[axis], point[axis])
                    hi[axis] = max(hi[axis], point[axis])
        return (tuple(lo), tuple(hi))  # type: ignore[return-value]

    def to_dict(self) -> Dict[str, Any]:
        lo, hi = self.bounds()
        return {
            "mass_kg": float(self.mass_kg),
            "cg_m": list(self.cg),
            "inertia_tensor_kgm2": [list(row) for row in self.inertia_tensor_kgm2],
            "roll_inertia_kgm2": float(self.roll_inertia_kgm2),
            "pitch_inertia_kgm2": float(self.pitch_inertia_kgm2),
            "yaw_inertia_kgm2": float(self.yaw_inertia_kgm2),
            "wing_area_m2": float(self.wing_area_m2),
            "span_m": float(self.span_m),
            "length_m": float(self.length_m),
            "chord_m": float(self.chord_m),
            "total_area_m2": float(self.total_area_m2),
            "max_layers": int(self.max_layers),
            "face_count": len(self.faces),
            "bounds": {"min": list(lo), "max": list(hi)},
        }


@dataclass
class GripThresholds:
    """The minimum conditions a folded sheet has to meet to be throwable.

    The defaults describe an adult finger: a pinch needs roughly 8 mm of
    keel depth, 30 mm of fore/aft length and at least two layers of paper
    to be rigid enough to push against.
    """

    min_grip_height_m: float = 0.008
    min_grip_length_m: float = 0.030
    min_grip_layers: int = 2
    cg_tolerance_m: float = 0.015
    grip_half_width_m: float = 0.012
    max_penetrations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_grip_height_m": float(self.min_grip_height_m),
            "min_grip_length_m": float(self.min_grip_length_m),
            "min_grip_layers": int(self.min_grip_layers),
            "cg_tolerance_m": float(self.cg_tolerance_m),
            "grip_half_width_m": float(self.grip_half_width_m),
            "max_penetrations": int(self.max_penetrations),
        }


@dataclass
class GripReport:
    """The outcome of the geometric filter for one folded sheet."""

    valid: bool
    reasons: List[str]
    penalty: float
    fitness: float
    grip_height_m: float
    grip_length_m: float
    grip_layers: int
    grip_x_range_m: Tuple[float, float]
    cg: Vec3
    cg_offset_m: float
    penetrations: int
    wing_area_m2: float
    span_m: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": bool(self.valid),
            "reasons": list(self.reasons),
            "penalty": float(self.penalty),
            "fitness": float(self.fitness),
            "grip_height_m": float(self.grip_height_m),
            "grip_length_m": float(self.grip_length_m),
            "grip_layers": int(self.grip_layers),
            "grip_x_range_m": [float(self.grip_x_range_m[0]), float(self.grip_x_range_m[1])],
            "cg_m": list(self.cg),
            "cg_offset_m": float(self.cg_offset_m),
            "penetrations": int(self.penetrations),
            "wing_area_m2": float(self.wing_area_m2),
            "span_m": float(self.span_m),
        }


# --------------------------------------------------------------------------
# Genome generation
# --------------------------------------------------------------------------
def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def _random_crease_line(rng: random.Random) -> Tuple[Vec2, Vec2]:
    """A crease line drawn uniformly from the sheet."""
    edge = rng.randrange(4)
    t = rng.uniform(0.15, 0.85)
    if edge == 0:
        p0: Vec2 = (t, 0.0)
    elif edge == 1:
        p0 = (1.0, t)
    elif edge == 2:
        p0 = (1.0 - t, 1.0)
    else:
        p0 = (0.0, 1.0 - t)
    angle = rng.uniform(0.0, math.pi)
    length = rng.uniform(0.25, 1.2)
    p1 = (
        _clamp(p0[0] + length * math.cos(angle), 0.0, 1.0),
        _clamp(p0[1] + length * math.sin(angle), 0.0, 1.0),
    )
    if abs(p1[0] - p0[0]) < 1.0e-6 and abs(p1[1] - p0[1]) < 1.0e-6:
        p1 = (1.0 - p0[0], 1.0 - p0[1])
    return p0, p1


def _family_crease_line(rng: random.Random, family: str) -> Tuple[Vec2, Vec2]:
    """A crease line drawn from one of the classic fold families.

    The *programme* stays random (which family, which angle, which side,
    which order), but the lines themselves stay inside the family of lines a
    human would actually crease: the centre keel, the nose diagonals, the
    transverse wing folds and the trailing edge trims.
    """
    if family == "keel":
        offset = rng.uniform(-0.18, 0.18)
        u = _clamp(0.5 + offset, 0.02, 0.98)
        return (u, 0.0), (u, 1.0)
    if family == "diagonal":
        corner = rng.randrange(4)
        v_end = rng.uniform(0.35, 0.95)
        if corner == 0:
            return (0.0, 0.0), (1.0, v_end)
        if corner == 1:
            return (1.0, 0.0), (0.0, v_end)
        if corner == 2:
            return (1.0, 1.0), (0.0, 1.0 - v_end)
        return (0.0, 1.0), (1.0, 1.0 - v_end)
    if family == "cross":
        v = rng.uniform(0.10, 0.92)
        skew = rng.uniform(-0.15, 0.15)
        return (0.0, _clamp(v - skew, 0.0, 1.0)), (1.0, _clamp(v + skew, 0.0, 1.0))
    if family == "spar":
        v = rng.uniform(0.15, 0.95)
        u0 = rng.uniform(0.05, 0.45)
        u1 = rng.uniform(0.55, 0.95)
        return (u0, v), (u1, v)
    # "free"
    return _random_crease_line(rng)


#: The crease families the generator can draw from.
CREASE_FAMILIES: Tuple[str, ...] = ("keel", "diagonal", "cross", "spar", "free")


def random_genome(
    rng: Optional[random.Random] = None,
    steps: int = 8,
    strategy: str = "families",
    sheet: Optional[Sequence[Vec2]] = None,
    seed: int = 0,
    key: str = "random",
) -> OrigamiGenome:
    """Generate a random fold programme.

    ``strategy="free"`` is generation 0: every crease line, every angle and
    every symmetry is drawn uniformly at random, so the search starts from a
    completely unconstrained population.

    ``strategy="families"`` keeps the *lines* inside the classic fold
    families (keel, nose diagonals, transverse folds, trailing edge trims)
    while the programme itself - which family, which angle, which side, in
    which order, on which panel - stays random.  That population is still
    random but a large part of it can actually be folded by hand, so the
    grip filter has something to select.

    The folds form a tree: the first step cuts the whole sheet, its moving
    half joins a new group, and every later step either continues on the
    base sheet or works on one of the groups a previous step created.  That
    is what makes the programme a *sequence* instead of a bag of creases.
    """
    rng = rng or random.Random(seed)
    outline = list(sheet) if sheet else list(SHEET_OUTLINE)
    total = max(1, int(steps))
    families = CREASE_FAMILIES if strategy != "free" else ("free",)

    # The group pool: the name of the panels a crease may cut.  "base" is
    # the part of the sheet that has not been moved yet.
    groups: List[str] = ["base"]
    programme: List[FoldStep] = []

    for index in range(1, total + 1):
        family = families[rng.randrange(len(families))]
        line = _family_crease_line(rng, family)

        # A fold that keeps the paper together is either symmetric or turns
        # one half; a fold that peels a flap off is always asymmetric.
        if family == "keel" and rng.random() < 0.7:
            symmetry = SYMMETRY_SYMMETRIC
        else:
            symmetry = rng.choice(SYMMETRY_MODES)

        angle_magnitude = rng.uniform(15.0, 180.0)
        mountain = rng.random() < 0.5
        fold_angle = angle_magnitude if mountain else -angle_magnitude

        # The panel the crease cuts: mostly the untouched sheet, sometimes a
        # piece a previous step created.
        source: Tuple[str, ...] = ()
        if len(groups) > 1 and rng.random() < 0.45:
            source = (groups[rng.randrange(1, len(groups))],)
        moving = f"g{index}"

        layer_order = LAYER_ORDER_AUTO
        if rng.random() < 0.35:
            layer_order = LAYER_ORDER_ABOVE if rng.random() < 0.5 else LAYER_ORDER_BELOW

        programme.append(
            FoldStep(
                step_index=index,
                crease_line=line,
                fold_angle_deg=_clamp(fold_angle, MIN_FOLD_ANGLE_DEG, MAX_FOLD_ANGLE_DEG),
                symmetry=symmetry,
                layer_order=layer_order,
                target_face_ids=(),
                group=source,
                set_group=moving,
                mountain=mountain,
                label=f"{family} fold",
            )
        )
        groups.append(moving)

    return OrigamiGenome(
        steps=programme,
        sheet=outline,
        key=key,
        label=f"random fold programme ({strategy}, {total} steps)",
        seed=int(seed),
        strategy=strategy,
        meta={"families": list(families), "step_count": total},
    )


def genome_from_creases(
    creases: Sequence[Crease],
    key: str = "program",
    label: str = "fold programme",
    sheet: Optional[Sequence[Vec2]] = None,
) -> OrigamiGenome:
    """Wrap an existing crease programme into a genome.

    This is how the known-good archetypes become individuals of the search
    population: the genetic algorithm can then start from a design that is
    already throwable and mutate it, instead of having to rediscover the
    keel from scratch.
    """
    steps: List[FoldStep] = []
    for index, crease in enumerate(creases):
        if crease.side > 0:
            symmetry = SYMMETRY_LEFT
        elif crease.side < 0:
            symmetry = SYMMETRY_RIGHT
        else:
            symmetry = SYMMETRY_SYMMETRIC
        magnitude = abs(float(crease.angle_deg))
        mountain = bool(crease.up)
        group: Tuple[str, ...] = tuple(crease.groups) if crease.groups else ()
        set_group = crease.set_group or (group[0] if group else "base")
        pair = crease.set_group_pair
        steps.append(
            FoldStep(
                step_index=index + 1,
                crease_line=((float(crease.p0[0]), float(crease.p0[1])),
                             (float(crease.p1[0]), float(crease.p1[1]))),
                fold_angle_deg=magnitude if mountain else -magnitude,
                symmetry=symmetry,
                layer_order=LAYER_ORDER_AUTO,
                target_face_ids=(),
                group=group,
                set_group=set_group,
                set_group_pair=(pair[0], pair[1]) if pair else None,
                level=int(crease.level),
                residual_deg=float(crease.residual_deg),
                mountain=mountain,
                label=str(crease.key),
            )
        )
    return OrigamiGenome(
        steps=steps,
        sheet=list(sheet) if sheet else list(SHEET_OUTLINE),
        key=key,
        label=label,
        seed=0,
        strategy="archetype",
        meta={"step_count": len(steps)},
    )


def genome_from_archetype(
    archetype: str,
    params: Optional[Dict[str, float]] = None,
) -> OrigamiGenome:
    """The fold programme of one of the classic archetypes, as a genome."""
    from .origami_domain import ARCHETYPE_LABELS, _archetype_creases

    values = params or {}
    creases = _archetype_creases(
        archetype,
        float(values.get("keel_beta_deg", 3.0)),
        float(values.get("fold_leading_deg", 0.0)),
        float(values.get("fold_tip_deg", 0.0)),
        float(values.get("elevon_deg", 0.0)),
        float(values.get("fold_trailing_deg", 15.0)),
        float(values.get("dihedral_deg", 5.0)),
    )
    return genome_from_creases(
        creases,
        key=archetype,
        label=ARCHETYPE_LABELS.get(archetype, archetype),
    )


def _fold_sense(value: Any, field: str, index: int) -> bool:
    """Resolve the fold sense of one step from a ``fold_type`` field.

    ``value`` is either the boolean sense itself or the name of a sense.
    A name that is not known is an error: guessing "mountain" would turn
    every valley fold of a genome into a mountain fold.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _FOLD_TYPE_ALIASES:
            return _FOLD_TYPE_ALIASES[key]
        raise ValueError(
            f"step {index}: unknown {field} {value!r}; expected 'mountain' or 'valley'"
        )
    raise ValueError(
        f"step {index}: {field} must be a string or a boolean, got {type(value).__name__}"
    )


def genome_from_dict(payload: Dict[str, Any]) -> OrigamiGenome:
    """Rebuild a genome from its JSON representation.

    The API accepts a genome from a client, so the dictionary form has to
    be parsed back into the data model without losing anything.

    The fold sense of a step is read from its ``fold_type`` field: the name
    of the sense (``"mountain"`` / ``"valley"``, the Japanese ``"山"`` /
    ``"谷"`` are accepted as well) or the boolean itself.  An unknown name
    is rejected with a :class:`ValueError` instead of being read as a
    mountain fold.  When the field is missing the boolean ``mountain`` key
    is used, and when that is missing too the sense follows the sign of the
    fold angle.  The angle that comes back carries the resolved sense in its
    sign (positive = mountain, negative = valley) and keeps its magnitude,
    which is the invariant the :class:`FoldStep` documents.

    The envelope of :func:`genome_payload` (``{"genome": {...}}``) is
    accepted as well, so a payload can be posted back as it was received.
    """
    if not isinstance(payload, dict):
        raise ValueError("a genome payload must be an object")
    if "steps" not in payload and isinstance(payload.get("genome"), dict):
        payload = payload["genome"]
    steps: List[FoldStep] = []
    for index, raw in enumerate(payload.get("steps") or [], start=1):
        line = raw.get("crease_line") or []
        if len(line) != 2:
            raise ValueError("every fold step needs a crease line with two points")
        p0 = (float(line[0][0]), float(line[0][1]))
        p1 = (float(line[1][0]), float(line[1][1]))
        angle = float(raw.get("fold_angle_deg", 0.0))
        magnitude = abs(_clamp(angle, MIN_FOLD_ANGLE_DEG, MAX_FOLD_ANGLE_DEG))
        fold_type = raw.get("fold_type")
        if fold_type is None:
            sense = raw.get("mountain")
            if sense is None:
                mountain = angle >= 0.0
            else:
                mountain = _fold_sense(sense, "mountain", index)
        else:
            mountain = _fold_sense(fold_type, "fold_type", index)
        # The sign of the angle carries the sense: the magnitude is kept
        # exactly, the sign follows the resolved sense.
        signed_angle = magnitude if mountain else -magnitude
        group = raw.get("group") or ()
        if isinstance(group, str):
            group = (group,)
        pair = raw.get("set_group_pair")
        steps.append(
            FoldStep(
                step_index=int(raw.get("step_index", index)),
                crease_line=(p0, p1),
                fold_angle_deg=signed_angle,
                symmetry=str(raw.get("symmetry", SYMMETRY_SYMMETRIC)),
                layer_order=str(raw.get("layer_order", LAYER_ORDER_AUTO)),
                target_face_ids=tuple(int(v) for v in (raw.get("target_face_ids") or ())),
                group=tuple(str(v) for v in group),
                set_group=str(raw.get("set_group", "base")),
                set_group_pair=(str(pair[0]), str(pair[1])) if pair else None,
                level=int(raw.get("level", 0)),
                residual_deg=float(raw.get("residual_deg", 0.0)),
                mountain=mountain,
                label=str(raw.get("label", "")),
            )
        )
    sheet = payload.get("sheet") or SHEET_OUTLINE
    return OrigamiGenome(
        steps=steps,
        sheet=[(float(point[0]), float(point[1])) for point in sheet],
        key=str(payload.get("key", "program")),
        label=str(payload.get("label", "fold programme")),
        seed=int(payload.get("seed", 0)),
        strategy=str(payload.get("strategy", "custom")),
        meta=dict(payload.get("meta") or {}),
    )


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def genome_to_creases(genome: OrigamiGenome) -> List[Crease]:
    """Translate a genome into the crease list of the folding engine."""
    creases: List[Crease] = []
    for step in genome.steps:
        (u0, v0), (u1, v1) = step.crease_line
        if symmetry_side(step.symmetry) == 0:
            side = 0
        else:
            side = symmetry_side(step.symmetry)
        creases.append(
            Crease(
                key=f"step{step.step_index:02d}",
                p0=(float(u0), float(v0)),
                p1=(float(u1), float(v1)),
                angle_deg=float(step.angle_magnitude_deg),
                side=int(side),
                up=bool(step.mountain),
                level=int(step.level),
                groups=None if not step.group else tuple(step.group),
                set_group=None
                if step.set_group_pair is not None
                else (None if step.set_group in step.group else step.set_group),
                set_group_pair=step.set_group_pair,
                residual_deg=float(step.residual_deg),
            )
        )
    return creases


def symmetry_side(symmetry: str) -> int:
    """Map a symmetry mode onto the engine's ``side`` convention."""
    if symmetry == SYMMETRY_SYMMETRIC:
        return 0
    if symmetry == SYMMETRY_LEFT:
        return 1
    if symmetry == SYMMETRY_RIGHT:
        return -1
    return 0


def _group_order(genome: OrigamiGenome) -> Dict[str, int]:
    """Stack order hint per group derived from the layer order rules."""
    order: Dict[str, int] = {}
    for step in genome.steps:
        if step.layer_order == LAYER_ORDER_ABOVE:
            # The moving half has to end up on top of the static half.
            order[step.set_group] = order.get(step.set_group, 0) + 1
            for name in step.group:
                order[name] = order.get(name, 0) - 1
        elif step.layer_order == LAYER_ORDER_BELOW:
            order[step.set_group] = order.get(step.set_group, 0) - 1
            for name in step.group:
                order[name] = order.get(name, 0) + 1
    return order


def fold_genome(
    genome: OrigamiGenome,
    alpha: float = 1.0,
    paper_gsm: float = 100.0,
) -> GenomeGeometry:
    """Execute a fold programme and return the resulting solid.

    The programme is evaluated in the order in which it is written: every
    crease cuts the panels it crosses along the *current* position of its
    line and rotates the moving half about that line, so the result is the
    state a pair of hands would actually produce.
    """
    creases = genome_to_creases(genome)
    pattern = compile_creases(creases, genome.sheet, alpha, genome.key, genome.label)
    faces = _fold_faces(pattern)
    layers = assign_layers(faces, _group_order(genome))

    areal_density = max(float(paper_gsm), 1.0e-3) * 1.0e-3  # g/m^2 -> kg/m^2
    total_area = sum(face.area_m2 for face in faces)
    mass = max(areal_density * total_area, 1.0e-9)

    if faces:
        cx = sum(face.area_m2 * face.centroid3d[0] for face in faces) / total_area
        cy = sum(face.area_m2 * face.centroid3d[1] for face in faces) / total_area
        cz = sum(face.area_m2 * face.centroid3d[2] for face in faces) / total_area
    else:
        cx = cy = cz = 0.0
    cg: Vec3 = (cx, cy, cz)

    tensor = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    for face in faces:
        weight = areal_density * face.area_m2
        dx = face.centroid3d[0] - cx
        dy = face.centroid3d[1] - cy
        dz = face.centroid3d[2] - cz
        r2 = dx * dx + dy * dy + dz * dz
        tensor[0][0] += weight * (r2 - dx * dx)
        tensor[1][1] += weight * (r2 - dy * dy)
        tensor[2][2] += weight * (r2 - dz * dz)
        tensor[0][1] -= weight * dx * dy
        tensor[0][2] -= weight * dx * dz
        tensor[1][2] -= weight * dy * dz
    tensor[1][0] = tensor[0][1]
    tensor[2][0] = tensor[0][2]
    tensor[2][1] = tensor[1][2]

    lo, hi = _solid_bounds(faces)
    span = max(hi[2] - lo[2], 1.0e-6)
    length = max(hi[0] - lo[0], 1.0e-6)
    wing_area = _planform_area(faces)
    chord = max(length, 1.0e-6)

    return GenomeGeometry(
        genome=genome,
        pattern=pattern,
        faces=faces,
        layers=layers,
        mass_kg=mass,
        cg=cg,
        inertia_tensor_kgm2=tensor,
        roll_inertia_kgm2=max(tensor[0][0], 1.0e-12),
        pitch_inertia_kgm2=max(tensor[1][1], 1.0e-12),
        yaw_inertia_kgm2=max(tensor[2][2], 1.0e-12),
        wing_area_m2=wing_area,
        span_m=span,
        length_m=length,
        chord_m=chord,
        total_area_m2=total_area,
        max_layers=layers.max_layers,
    )


def _fold_faces(pattern: CreasePattern) -> List[FoldedFace]:
    """Local import shim so the engine internals stay private."""
    from .origami_domain import _fold_faces as engine_fold_faces

    return engine_fold_faces(pattern)


def _solid_bounds(faces: Sequence[FoldedFace]) -> Tuple[Vec3, Vec3]:
    if not faces:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    lo = [math.inf, math.inf, math.inf]
    hi = [-math.inf, -math.inf, -math.inf]
    for face in faces:
        for point in face.poly3d:
            for axis in range(3):
                lo[axis] = min(lo[axis], point[axis])
                hi[axis] = max(hi[axis], point[axis])
    return (tuple(lo), tuple(hi))  # type: ignore[return-value]


def _planform_area(faces: Sequence[FoldedFace]) -> float:
    """Planform (shadow) area of the folded solid.

    The planform is what the flight dynamics sees as the wing area: the
    projection of every panel onto the horizontal plane, counted once per
    panel (overlapping layers are different parts of the paper and therefore
    both contribute).
    """
    total = 0.0
    for face in faces:
        if len(face.poly3d) < 3:
            continue
        area = 0.0
        for index in range(len(face.poly3d)):
            a = face.poly3d[index]
            b = face.poly3d[(index + 1) % len(face.poly3d)]
            area += a[0] * b[2] - b[0] * a[2]
        total += abs(0.5 * area)
    return max(total, 1.0e-9)


# --------------------------------------------------------------------------
# Self intersection
# --------------------------------------------------------------------------
def _segment_distance(p: Vec3, q: Vec3) -> float:
    dx = q[0] - p[0]
    dy = q[1] - p[1]
    dz = q[2] - p[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _point_in_polygon3d(point: Vec3, polygon: Sequence[Vec3], normal: Vec3, eps: float) -> bool:
    """Is ``point`` *strictly* inside the planar polygon ``polygon``?

    A point that sits on the boundary does not count: two panels that share a
    crease touch along their common edge, and that is not a penetration.
    """
    if len(polygon) < 3:
        return False
    distance = abs(
        normal[0] * (point[0] - polygon[0][0])
        + normal[1] * (point[1] - polygon[0][1])
        + normal[2] * (point[2] - polygon[0][2])
    )
    if distance > eps:
        return False
    # Project onto the plane of the polygon and use the even-odd rule.
    axis = _dominant_axis(normal)
    if axis == 0:
        px, py = point[1], point[2]
        coords = [(vertex[1], vertex[2]) for vertex in polygon]
    elif axis == 1:
        px, py = point[0], point[2]
        coords = [(vertex[0], vertex[2]) for vertex in polygon]
    else:
        px, py = point[0], point[1]
        coords = [(vertex[0], vertex[1]) for vertex in polygon]
    for index in range(len(coords)):
        ax, ay = coords[index]
        bx, by = coords[(index + 1) % len(coords)]
        if _point_segment_distance_2d(px, py, ax, ay, bx, by) <= eps:
            return False
    inside = False
    count = len(coords)
    for index in range(count):
        ax, ay = coords[index]
        bx, by = coords[(index + 1) % count]
        if (ay > py) != (by > py):
            t = (py - ay) / (by - ay) if abs(by - ay) > 1.0e-18 else 0.0
            if px < ax + t * (bx - ax):
                inside = not inside
    return inside


def _point_segment_distance_2d(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Distance from a point to a segment in the plane."""
    dx = bx - ax
    dy = by - ay
    length2 = dx * dx + dy * dy
    if length2 < 1.0e-24:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _dominant_axis(normal: Vec3) -> int:
    ax, ay, az = abs(normal[0]), abs(normal[1]), abs(normal[2])
    if ax >= ay and ax >= az:
        return 0
    if ay >= az:
        return 1
    return 2


def _segments_cross(p1: Vec3, q1: Vec3, p2: Vec3, q2: Vec3, eps: float) -> bool:
    """Do the two segments cross *through* each other?

    Collinear (overlapping) segments do **not** count: two panels that share
    a crease meet exactly along their common edge, and that is a legitimate
    touch, not a penetration.  Only a proper crossing - the segments meet in
    the interior of both - is a penetration.
    """
    d1 = (q1[0] - p1[0], q1[1] - p1[1], q1[2] - p1[2])
    d2 = (q2[0] - p2[0], q2[1] - p2[1], q2[2] - p2[2])
    r = (p1[0] - p2[0], p1[1] - p2[1], p1[2] - p2[2])
    a = d1[0] * d1[0] + d1[1] * d1[1] + d1[2] * d1[2]
    b = d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]
    c = d2[0] * d2[0] + d2[1] * d2[1] + d2[2] * d2[2]
    d = d1[0] * r[0] + d1[1] * r[1] + d1[2] * r[2]
    e = d2[0] * r[0] + d2[1] * r[1] + d2[2] * r[2]
    denom = a * c - b * b
    if abs(denom) <= 1.0e-18:
        # Parallel or collinear: a shared crease edge, not a crossing.
        return False
    s = (b * e - c * d) / denom
    t = (a * e - b * d) / denom
    margin = 1.0e-9
    if not (margin <= s <= 1.0 - margin and margin <= t <= 1.0 - margin):
        return False
    # The closest approach has to be a *common* point.  Two skew segments
    # that merely pass near each other have closest-approach parameters in
    # the interior of both segments without ever meeting.
    cx = (p1[0] + s * d1[0]) - (p2[0] + t * d2[0])
    cy = (p1[1] + s * d1[1]) - (p2[1] + t * d2[1])
    cz = (p1[2] + s * d1[2]) - (p2[2] + t * d2[2])
    return math.sqrt(cx * cx + cy * cy + cz * cz) <= eps


def _separated_polygons(
    geometry: GenomeGeometry,
    alpha: float = 1.0,
) -> Dict[int, List[Vec3]]:
    """The panel polygons with the layer stack separated.

    In the raw folded geometry the layers of a stack are exactly coplanar -
    the paper is infinitely thin there.  The solid separates them by the
    layer pitch, so the self-intersection test has to run on the separated
    polygons, otherwise every legitimate stack would be reported as the
    paper passing through itself.
    """
    separation = LAYER_PITCH_M * max(alpha, 0.0)
    polygons: Dict[int, List[Vec3]] = {}
    for face in geometry.faces:
        reference = geometry.layers.reference.get(face.fid, (0.0, 0.0, 1.0))
        amount = geometry.layers.index.get(face.fid, 0) * separation
        dx = reference[0] * amount
        dy = reference[1] * amount
        dz = reference[2] * amount
        polygons[face.fid] = [
            (p[0] + dx, p[1] + dy, p[2] + dz) for p in face.poly3d
        ]
    return polygons


def detect_penetrations(
    faces: Sequence[FoldedFace],
    adjacency: Optional[Iterable[Tuple[int, int]]] = None,
    tolerance: float = 1.0e-6,
    polygons: Optional[Dict[int, List[Vec3]]] = None,
) -> List[Dict[str, Any]]:
    """Find the panels that pass through each other.

    Two panels that share a crease legitimately touch, so they are skipped.
    Everything else is tested for a real intersection: an edge crossing an
    edge, or one panel's corner inside the other.  A folded sheet that
    intersects itself cannot be produced by a pair of hands, so it is an
    invalid geometry.
    """
    adjacent: set = set()
    for pair in adjacency or ():
        adjacent.add((min(int(pair[0]), int(pair[1])), max(int(pair[0]), int(pair[1]))))

    def outline(face: FoldedFace) -> List[Vec3]:
        if polygons is not None:
            return polygons.get(face.fid, list(face.poly3d))
        return list(face.poly3d)

    count = len(faces)
    radius = [0.0] * count
    for index, face in enumerate(faces):
        points = outline(face)
        if not points:
            radius[index] = 0.0
            continue
        cx = sum(point[0] for point in points) / len(points)
        cy = sum(point[1] for point in points) / len(points)
        cz = sum(point[2] for point in points) / len(points)
        radius[index] = max(
            math.sqrt(
                (point[0] - cx) ** 2 + (point[1] - cy) ** 2 + (point[2] - cz) ** 2
            )
            for point in points
        )

    hits: List[Dict[str, Any]] = []
    for i in range(count):
        face_i = faces[i]
        poly_i = outline(face_i)
        if len(poly_i) < 3:
            continue
        for j in range(i + 1, count):
            face_j = faces[j]
            poly_j = outline(face_j)
            if len(poly_j) < 3:
                continue
            if (i, j) in adjacent:
                continue
            # Cheap rejection on the bounding spheres.
            distance = math.sqrt(
                (face_i.centroid3d[0] - face_j.centroid3d[0]) ** 2
                + (face_i.centroid3d[1] - face_j.centroid3d[1]) ** 2
                + (face_i.centroid3d[2] - face_j.centroid3d[2]) ** 2
            )
            if distance > radius[i] + radius[j] + tolerance:
                continue
            if _panels_intersect(face_i, face_j, poly_i, poly_j, tolerance):
                hits.append(
                    {
                        "face_a": int(face_i.fid),
                        "face_b": int(face_j.fid),
                        "group_a": face_i.group,
                        "group_b": face_j.group,
                    }
                )
    return hits


def _panels_intersect(
    face_a: FoldedFace,
    face_b: FoldedFace,
    poly_a: Sequence[Vec3],
    poly_b: Sequence[Vec3],
    tolerance: float,
) -> bool:
    """Does one panel pass through the other?

    The unambiguous test is **containment**: a corner of one panel that lies
    strictly inside the other panel means the paper of the first one is
    inside the second one, which no pair of hands can produce.

    An edge of one panel crossing an edge of the other is *not* a
    penetration: two crease lines of the same sheet cross each other all the
    time (the nose fold runs across the wing fold), and the panels that
    carry them can still lie on opposite sides of the crossing.  Whenever
    two panels really overlap, the overlap region has a corner inside the
    other panel, so the containment test also catches a panel that slices
    through another one.
    """
    for point in poly_a:
        if _point_in_polygon3d(point, poly_b, face_b.normal, tolerance):
            return True
    for point in poly_b:
        if _point_in_polygon3d(point, poly_a, face_a.normal, tolerance):
            return True
    return False


def _centroid(points: Sequence[Vec3]) -> Vec3:
    count = len(points)
    return (
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
        sum(point[2] for point in points) / count,
    )


# --------------------------------------------------------------------------
# The grip filter
# --------------------------------------------------------------------------
def adjacent_panel_pairs(pattern: CreasePattern) -> List[Tuple[int, int]]:
    """The panel pairs that share a crease and therefore touch legitimately."""
    pairs: set = set()
    for face in pattern.faces:
        for link in face.links:
            if link.neighbour_fid >= 0:
                pairs.add(
                    (min(face.fid, link.neighbour_fid), max(face.fid, link.neighbour_fid))
                )
    return sorted(pairs)


def evaluate_grip(
    geometry: GenomeGeometry,
    thresholds: Optional[GripThresholds] = None,
    penetrations: Optional[Sequence[Dict[str, Any]]] = None,
) -> GripReport:
    """Decide whether the folded sheet can actually be thrown by hand.

    The grip is not a single point but a **section** of the body: a finger
    pinches the paper where it is doubled, under the centre line.  The
    central slab of the folded sheet is therefore cut into longitudinal
    stations, and a station belongs to the grip when at least
    ``min_grip_layers`` layers of paper cover it.

    Three conditions have to hold:

    1. a **grip** exists: the grip stations are deep enough to pinch
       (the vertical extent of the layers), long enough to hold and thick
       enough to push against,
    2. the **centre of gravity** lies inside the fore/aft range of that
       grip, so the aeroplane balances on the hand instead of tipping over,
    3. the paper does **not** pass through itself.

    A failure is not fatal, it is a penalty: the report carries a numeric
    penalty and a bounded fitness so a genetic algorithm can still climb
    towards a valid geometry.
    """
    limits = thresholds or GripThresholds()
    faces = geometry.faces
    hits = (
        list(penetrations)
        if penetrations is not None
        else detect_penetrations(
            faces,
            adjacent_panel_pairs(geometry.pattern),
            polygons=_separated_polygons(geometry),
        )
    )

    reasons: List[str] = []
    penalty = 0.0

    half_width = max(limits.grip_half_width_m, 1.0e-6)
    central = [face for face in faces if abs(face.centroid3d[2]) <= half_width]
    outer = [face for face in faces if abs(face.centroid3d[2]) > half_width]

    # The stations of the central slab: the fore/aft extent of every panel.
    edges: set = set()
    spans: List[Tuple[float, float, FoldedFace]] = []
    for face in central:
        if not face.poly3d:
            continue
        xs = [point[0] for point in face.poly3d]
        low, high = min(xs), max(xs)
        if high - low <= 1.0e-9:
            continue
        spans.append((low, high, face))
        edges.add(low)
        edges.add(high)

    stations = sorted(edges)
    grip_intervals: List[Tuple[float, float]] = []
    grip_height = 0.0
    grip_layers = 0
    for index in range(len(stations) - 1):
        start, end = stations[index], stations[index + 1]
        middle = 0.5 * (start + end)
        covering = [
            face
            for low, high, face in spans
            if low - 1.0e-9 <= middle <= high + 1.0e-9
        ]
        if not covering:
            continue
        layers = len(covering)
        ys = [point[1] for face in covering for point in face.poly3d]
        height = max(ys) - min(ys)
        if layers >= limits.min_grip_layers:
            grip_intervals.append((start, end))
            grip_height = max(grip_height, height)
            grip_layers = max(grip_layers, layers)

    if grip_intervals:
        grip_x_min = min(interval[0] for interval in grip_intervals)
        grip_x_max = max(interval[1] for interval in grip_intervals)
        grip_length = sum(interval[1] - interval[0] for interval in grip_intervals)
    else:
        grip_x_min = 0.0
        grip_x_max = 0.0
        grip_length = 0.0

    if not central:
        reasons.append("no-central-section")
        penalty += 100.0
    elif not outer:
        reasons.append("no-wing")
        penalty += 50.0
    if not grip_intervals:
        reasons.append("no-grip")
        penalty += 100.0
    else:
        if grip_height < limits.min_grip_height_m:
            reasons.append("grip-too-shallow")
            penalty += 100.0 * (1.0 - grip_height / max(limits.min_grip_height_m, 1.0e-9))
        if grip_length < limits.min_grip_length_m:
            reasons.append("grip-too-short")
            penalty += 100.0 * (1.0 - grip_length / max(limits.min_grip_length_m, 1.0e-9))
        if grip_layers < limits.min_grip_layers:
            reasons.append("grip-too-thin")
            penalty += 100.0 * (limits.min_grip_layers - grip_layers)

    cg_x = geometry.cg[0]
    if grip_intervals:
        centre = 0.5 * (grip_x_min + grip_x_max)
        offset = abs(cg_x - centre)
        tolerance = max(limits.cg_tolerance_m, 0.0)
        if cg_x < grip_x_min - tolerance or cg_x > grip_x_max + tolerance:
            reasons.append("cg-outside-grip")
            penalty += 100.0 * (offset / max(tolerance + 1.0e-9, 1.0e-9) - 1.0)
    else:
        offset = 0.0
        centre = 0.0

    if len(hits) > limits.max_penetrations:
        reasons.append("self-intersection")
        penalty += 1000.0 * (len(hits) - limits.max_penetrations)

    valid = not reasons
    fitness = 1.0 / (1.0 + max(penalty, 0.0))
    return GripReport(
        valid=valid,
        reasons=reasons,
        penalty=float(penalty),
        fitness=float(fitness),
        grip_height_m=float(grip_height),
        grip_length_m=float(grip_length),
        grip_layers=int(grip_layers),
        grip_x_range_m=(float(grip_x_min), float(grip_x_max)),
        cg=geometry.cg,
        cg_offset_m=float(offset),
        penetrations=len(hits),
        wing_area_m2=float(geometry.wing_area_m2),
        span_m=float(geometry.span_m),
    )


# --------------------------------------------------------------------------
# Handover to the 6-DoF model
# --------------------------------------------------------------------------
def genome_to_aircraft_config(
    geometry: GenomeGeometry,
    paper_gsm: float = 100.0,
    base: Optional[Any] = None,
) -> Any:
    """Build the ``AircraftConfig`` that matches a folded genome.

    The geometry of the fold becomes the geometry of the flight model: the
    planform area and the span of the solid, the paper areal density for the
    mass and the inertia tensor of the folded sheet about its centre of
    gravity.
    """
    import dataclasses

    from .config_schema import AircraftConfig

    template = base if base is not None else AircraftConfig()
    config = dataclasses.replace(template)
    areal_density = max(float(paper_gsm), 1.0e-3) * 1.0e-3

    span = max(float(geometry.span_m), 1.0e-6)
    length = max(float(geometry.length_m), 1.0e-6)
    area = max(float(geometry.wing_area_m2), 1.0e-9)
    aspect = max((span * span) / area, 1.0e-3)
    taper = 0.5

    config.wingspan_m = span
    config.wing_area_m2 = area
    config.aspect_ratio = aspect
    config.taper_ratio = taper
    config.mass_kg = max(areal_density * max(float(geometry.total_area_m2), 1.0e-9), 1.0e-9)
    config.sheet_area_m2 = max(float(geometry.total_area_m2), 1.0e-9)
    config.roll_inertia_kgm2 = max(float(geometry.roll_inertia_kgm2), 1.0e-12)
    config.pitch_inertia_kgm2 = max(float(geometry.pitch_inertia_kgm2), 1.0e-12)
    config.yaw_inertia_kgm2 = max(float(geometry.yaw_inertia_kgm2), 1.0e-12)
    config.inertia_xy_kgm2 = float(geometry.inertia_tensor_kgm2[0][1])
    config.inertia_xz_kgm2 = float(geometry.inertia_tensor_kgm2[0][2])
    config.inertia_yz_kgm2 = float(geometry.inertia_tensor_kgm2[1][2])
    return config


# --------------------------------------------------------------------------
# Recipe and visualisation
# --------------------------------------------------------------------------
def recipe_steps(
    genome: OrigamiGenome,
    geometry: Optional[GenomeGeometry] = None,
) -> List[Dict[str, Any]]:
    """The ordered table of folds a human has to perform.

    Every entry names the line, the sense (mountain / valley), the exact
    angle in degrees, the symmetry, the panels that move and the stack
    order, so the sheet can be folded from the recipe alone.
    """
    steps: List[Dict[str, Any]] = []
    for step in genome.steps:
        entry = step.to_dict()
        entry["instruction"] = _instruction(step)
        steps.append(entry)
    if geometry is not None:
        # Name the panels the crease really cuts.
        for index, step in enumerate(genome.steps):
            if index >= len(steps):
                break
            steps[index]["target_face_ids"] = [
                int(value) for value in _cut_faces(geometry, index)
            ]
    return steps


def _cut_faces(geometry: GenomeGeometry, step_index: int) -> List[int]:
    """Face ids the crease of ``step_index`` cuts, resolved by replaying.

    The programme is replayed up to (but not including) the step, and the
    crease is then tested against every panel with the very same split the
    folding engine uses, so the recipe names exactly the panels a human has
    to move.
    """
    from .origami_domain import _crease_frame, _split_polygon_3d

    creases = genome_to_creases(geometry.genome)
    if step_index >= len(creases):
        return []
    prefix = compile_creases(creases[:step_index], geometry.genome.sheet, 1.0)
    crease = creases[step_index]
    cut: List[int] = []
    for face in prefix.faces:
        if crease.groups is not None and face.group not in crease.groups:
            continue
        origin, axis, side = _crease_frame(face, crease)
        plus, minus, shared = _split_polygon_3d(face.poly3d, origin, axis, side)
        if plus and minus and shared is not None:
            cut.append(int(face.fid))
    return cut


def _instruction(step: FoldStep) -> str:
    """The human readable instruction of one fold."""
    sense = "山折り" if step.mountain else "谷折り"
    if step.symmetry == SYMMETRY_SYMMETRIC:
        shape = "左右対称に"
    elif step.symmetry == SYMMETRY_LEFT:
        shape = "左側を"
    else:
        shape = "右側を"
    angle = abs(step.fold_angle_deg)
    text = f"{shape}{sense} {angle:.1f}°"
    if step.layer_order == LAYER_ORDER_ABOVE:
        text += "（-folded panel above-）"
    elif step.layer_order == LAYER_ORDER_BELOW:
        text += "（folded panel below）"
    return text


def recipe_markdown(
    genome: OrigamiGenome,
    geometry: Optional[GenomeGeometry] = None,
) -> str:
    """Render the fold programme as a Markdown table."""
    steps = recipe_steps(genome, geometry)
    lines: List[str] = []
    lines.append(f"# 折り手順 / Folding recipe - {genome.label}")
    lines.append("")
    lines.append(
        f"seed: `{genome.seed}`  |  strategy: `{genome.strategy}`  |  "
        f"steps: {len(steps)}  |  sheet: {SHEET_WIDTH_M * 1000:.0f} x "
        f"{SHEET_LENGTH_M * 1000:.0f} mm"
    )
    lines.append("")
    lines.append("| # | 折り線 (mm) | 種別 | 角度 | 対象パネル | 層順序 |")
    lines.append("|---|---|---|---|---|---|")
    for entry in steps:
        (p0, p1) = entry["crease_line_mm"]
        line = (
            f"({p0[0]:.1f}, {p0[1]:.1f}) -> ({p1[0]:.1f}, {p1[1]:.1f})"
        )
        sense = "山" if entry["fold_type"] == "mountain" else "谷"
        symmetry = {"symmetric": "左右対称", "left": "左", "right": "右"}[entry["symmetry"]]
        targets = ", ".join(str(value) for value in entry["target_face_ids"]) or "-"
        lines.append(
            f"| {entry['step_index']} | {line} | {symmetry}{sense} | "
            f"{entry['fold_angle_magnitude_deg']:.1f}° | {targets} | "
            f"{entry['layer_order']} |"
        )
    lines.append("")
    lines.append("## 展開図 / Crease pattern")
    lines.append("")
    lines.append("![crease pattern](data:image/svg+xml;base64,)")
    return "\n".join(lines)


def crease_pattern_svg(
    genome: OrigamiGenome,
    width_px: int = 620,
    margin_px: int = 40,
) -> str:
    """Render the flat crease pattern as an SVG.

    Mountain folds are red, valley folds blue, the step number and the
    angle are written next to every line.  The sheet is drawn in its real
    aspect ratio, so the drawing can be printed and used as a template.
    """
    margin = float(margin_px)
    sheet_w = SHEET_WIDTH_M * 1000.0
    sheet_h = SHEET_LENGTH_M * 1000.0
    scale = min((width_px - 2.0 * margin) / sheet_w, (width_px * 1.45 - 2.0 * margin) / sheet_h)
    draw_w = sheet_w * scale
    draw_h = sheet_h * scale
    total_w = draw_w + 2.0 * margin
    total_h = draw_h + 2.0 * margin

    def to_canvas(point: Vec2) -> Tuple[float, float]:
        # Sheet space (u across, v along, nose at v = 1) -> canvas, nose up.
        return (margin + point[0] * draw_w, margin + (1.0 - point[1]) * draw_h)

    parts: List[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w:.0f}" '
        f'height="{total_h:.0f}" viewBox="0 0 {total_w:.0f} {total_h:.0f}" '
        f'font-family="DejaVu Sans, sans-serif">'
    )
    parts.append(
        f'<rect x="{margin:.1f}" y="{margin:.1f}" width="{draw_w:.1f}" '
        f'height="{draw_h:.1f}" fill="#fdfcf7" stroke="#202020" stroke-width="1.5"/>'
    )
    parts.append(
        f'<text x="{margin:.1f}" y="{margin - 12:.1f}" font-size="13" fill="#202020">'
        f"crease pattern - {len(genome.steps)} folds (seed {genome.seed})</text>"
    )
    for step in genome.steps:
        (p0, p1) = step.crease_line
        (x0, y0) = to_canvas(p0)
        (x1, y1) = to_canvas(p1)
        colour = "#c02020" if step.mountain else "#2050c0"
        parts.append(
            f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
            f'stroke="{colour}" stroke-width="2.0" stroke-dasharray="'
            f'{"6 4" if step.mountain else "2 4"}"/>'
        )
        mx = 0.5 * (x0 + x1)
        my = 0.5 * (y0 + y1)
        label = f"{step.step_index}: {abs(step.fold_angle_deg):.1f}°"
        parts.append(
            f'<text x="{mx + 4:.1f}" y="{my - 4:.1f}" font-size="11" fill="{colour}">'
            f"{label}</text>"
        )
    parts.append(
        '<text x="{:.1f}" y="{:.1f}" font-size="11" fill="#606060">'
        "red dashed = mountain (山折り), blue dotted = valley (谷折り)</text>".format(
            margin, total_h - 10.0
        )
    )
    parts.append("</svg>")
    return "".join(parts)


def step_meshes(
    genome: OrigamiGenome,
    alpha: float = 1.0,
) -> List[Dict[str, Any]]:
    """The solid of every intermediate fold state.

    Index 0 is the flat sheet, index *n* the finished aeroplane.  The
    payload is the same vertex buffer format the 3D viewer already knows, so
    a slider can step through the folding one move at a time.
    """
    creases = genome_to_creases(genome)
    states: List[Dict[str, Any]] = []
    for count in range(len(creases) + 1):
        pattern = compile_creases(creases[:count], genome.sheet, alpha, genome.key)
        mesh = build_pattern_mesh(pattern, alpha, 1.0, genome.key)
        payload = mesh_payload(mesh)
        payload["step_index"] = count
        payload["label"] = (
            "flat sheet"
            if count == 0
            else f"after step {count}: {genome.steps[count - 1].label}"
        )
        states.append(payload)
    return states


def genome_payload(
    genome: OrigamiGenome,
    geometry: Optional[GenomeGeometry] = None,
    grip: Optional[GripReport] = None,
) -> Dict[str, Any]:
    """The JSON payload of one genome (geometry, filter result and recipe)."""
    payload: Dict[str, Any] = {"genome": genome.to_dict()}
    if geometry is not None:
        payload["geometry"] = geometry.to_dict()
    if grip is not None:
        payload["grip"] = grip.to_dict()
    payload["recipe"] = recipe_steps(genome, geometry)
    return payload
