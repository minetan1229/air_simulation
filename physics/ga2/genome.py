"""The procedural origami genome of the second generation search.

The genome of the first generation was a list of creases; this one is a list
of *hybrid fold genes*.  A gene carries everything the folding engine needs
and nothing else:

``enabled``
    whether the fold is part of the programme at all.  A disabled gene stays
    in the genome, so a mutation can switch a fold on or off without
    changing the length of the programme.
``origin`` / ``crease_angle_deg`` / ``length_ratio``
    where the crease starts, which way it runs and how long it is.  The
    crease is the chord of the sheet through the origin at the given
    direction, cut to ``length_ratio`` of that chord.  The three numbers
    describe every straight crease of a sheet.
``fold_angle_deg``
    how far the paper is turned, ``-180..+180`` degrees.  The sign carries
    the mountain / valley sense.
``target_layers``
    the layers the crease acts on: an empty tuple cuts the whole sheet, a
    non empty tuple names the groups the earlier folds created.  This is
    what makes the nose pleats, the fuselage ribs and the winglets
    expressible: a fold can act on a layer that is already folded over.
``creates_pair``
    whether the fold splits its moving half into a left and a right group
    (the centre keel of a dart does, a single flap fold does not).
``symmetry``
    the left / right symmetry constraint of the fold.

A programme holds 3 to 8 genes, so the length of the genome itself is part
of the search space.  The conversion to and from the fold programme of
:mod:`physics.origami_genome` is lossless: an archetype that is read into a
hybrid genome and written back folds into the very same solid.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import origami_genome

__all__ = [
    "HybridFoldGene",
    "HybridGenome",
    "SYMMETRY_MODES",
    "MIN_FOLD_STEPS",
    "MAX_FOLD_STEPS",
    "crease_segment",
    "random_hybrid_genome",
    "mutate_hybrid",
    "crossover_hybrid",
    "hybrid_to_origami",
    "hybrid_from_origami",
    "archetype_hybrid_genomes",
    "seed_population",
    "hybrid_signature",
]

#: The symmetry modes a fold may carry.
SYMMETRY_MODES: Tuple[str, ...] = ("symmetric", "left", "right")

#: The shortest programme the search allows.
MIN_FOLD_STEPS = 3

#: The longest programme the search allows.
MAX_FOLD_STEPS = 8

#: The archetypes that seed the initial population.
ARCHETYPE_KEYS: Tuple[str, ...] = ("classic_dart", "nakamura_lock", "sky_king")

#: The shortest crease a gene may describe, as a fraction of the distance
#: from its start point to the edge of the sheet.
MIN_LENGTH_RATIO = 1.0e-4

#: The shortest crease the serialised gene format accepts.
GENE_LENGTH_RATIO_FLOOR = 0.05

#: The resolution of the crease coordinates.
#:
#: The direction of a crease is stored as an angle, and the conversion from
#: an angle back into a direction loses the last bit of the precision: an
#: axis aligned crease (the keel of every design) comes back with an error
#: of about 1e-16 of the sheet.  The error is far below the resolution of
#: the paper, but it decides whether two creases that are meant to meet at
#: one point really meet there, and the folding engine cuts the paper
#: differently when they do not.  The coordinates of a crease are therefore
#: canonicalised on a grid of this size.  The grid is a power of two, so the
#: canonicalisation is exact and idempotent, and a programme that is
#: written and read back folds into the very same paper.
CREASE_QUANTUM = 2.0 ** -40


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def _canonical(value: float) -> float:
    """Snap a crease coordinate onto the canonical grid."""
    return round(float(value) / CREASE_QUANTUM) * CREASE_QUANTUM


def _wrap_angle(value: float) -> float:
    """Fold an angle into ``(-180, 180]`` degrees."""
    wrapped = math.fmod(float(value) + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0


@dataclass(frozen=True)
class HybridFoldGene:
    """One fold operation of a hybrid programme."""

    enabled: bool
    origin: Tuple[float, float]
    crease_angle_deg: float
    fold_angle_deg: float
    target_layers: Tuple[int, ...] = ()
    creates_pair: bool = False
    creates_group: bool = True
    join_group: Optional[int] = None
    symmetry: str = "symmetric"
    length_ratio: float = 1.0
    level: int = 0
    residual_deg: float = 0.0
    mountain: bool = True
    label: str = ""

    @property
    def target_layer(self) -> int:
        """The primary layer of the fold (``-1`` for the whole sheet)."""
        return int(self.target_layers[0]) if self.target_layers else -1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "origin": [float(self.origin[0]), float(self.origin[1])],
            "crease_angle_deg": float(self.crease_angle_deg),
            "fold_angle_deg": float(self.fold_angle_deg),
            "target_layers": [int(value) for value in self.target_layers],
            "target_layer": self.target_layer,
            "creates_pair": bool(self.creates_pair),
            "creates_group": bool(self.creates_group),
            "join_group": None if self.join_group is None else int(self.join_group),
            "symmetry": str(self.symmetry),
            "length_ratio": float(self.length_ratio),
            "level": int(self.level),
            "residual_deg": float(self.residual_deg),
            "mountain": bool(self.mountain),
            "label": str(self.label),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "HybridFoldGene":
        data = dict(payload or {})
        origin = data.get("origin") or (0.5, 0.5)
        symmetry = str(data.get("symmetry", "symmetric"))
        if symmetry not in SYMMETRY_MODES:
            symmetry = "symmetric"
        layers = data.get("target_layers")
        if layers is None:
            layer = data.get("target_layer", -1)
            layers = [] if int(layer) < 0 else [int(layer)]
        join = data.get("join_group")
        return cls(
            enabled=bool(data.get("enabled", True)),
            origin=(
                _clamp(float(origin[0]), 0.0, 1.0),
                _clamp(float(origin[1]), 0.0, 1.0),
            ),
            crease_angle_deg=_wrap_angle(float(data.get("crease_angle_deg", 0.0))),
            fold_angle_deg=_clamp(
                float(data.get("fold_angle_deg", 0.0)),
                origami_genome.MIN_FOLD_ANGLE_DEG,
                origami_genome.MAX_FOLD_ANGLE_DEG,
            ),
            target_layers=tuple(sorted({int(value) for value in layers})),
            creates_pair=bool(data.get("creates_pair", False)),
            creates_group=bool(data.get("creates_group", True)),
            join_group=None if join is None else int(join),
            symmetry=symmetry,
            length_ratio=_clamp(
                float(data.get("length_ratio", 1.0)), GENE_LENGTH_RATIO_FLOOR, 1.0
            ),
            level=int(data.get("level", 0)),
            residual_deg=float(data.get("residual_deg", 0.0)),
            mountain=bool(data.get("mountain", True)),
            label=str(data.get("label", "")),
        )


@dataclass
class HybridGenome:
    """A complete hybrid fold programme (3 to 8 genes)."""

    genes: List[HybridFoldGene] = field(default_factory=list)
    key: str = "random"
    label: str = "random hybrid programme"
    seed: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        """The number of genes (enabled or not) of the programme."""
        return len(self.genes)

    @property
    def active_step_count(self) -> int:
        """The number of folds that are actually performed."""
        return sum(1 for gene in self.genes if gene.enabled)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": str(self.key),
            "label": str(self.label),
            "seed": int(self.seed),
            "meta": dict(self.meta),
            "genes": [gene.to_dict() for gene in self.genes],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "HybridGenome":
        data = dict(payload or {})
        genes = [HybridFoldGene.from_dict(item) for item in data.get("genes") or []]
        return cls(
            genes=genes,
            key=str(data.get("key", "random")),
            label=str(data.get("label", "random hybrid programme")),
            seed=int(data.get("seed", 0)),
            meta=dict(data.get("meta") or {}),
        )


def _sheet_bounds(
    sheet: Optional[Sequence[Tuple[float, float]]],
) -> Tuple[float, float, float, float]:
    """The bounding box of the sheet in normalised coordinates."""
    if not sheet:
        return (0.0, 1.0, 0.0, 1.0)
    us = [float(point[0]) for point in sheet]
    vs = [float(point[1]) for point in sheet]
    return (min(us), max(us), min(vs), max(vs))


def _forward_extent(
    origin: Tuple[float, float],
    dx: float,
    dy: float,
    bounds: Tuple[float, float, float, float],
) -> float:
    """The distance from the origin to the edge of the sheet along a ray."""
    u_low, u_high, v_low, v_high = bounds
    extent = math.inf
    for value, direction, low, high in (
        (origin[0], dx, u_low, u_high),
        (origin[1], dy, v_low, v_high),
    ):
        if abs(direction) < 1.0e-12:
            continue
        limit = (
            (high - value) / direction
            if direction > 0.0
            else (low - value) / direction
        )
        extent = min(extent, limit)
    if not math.isfinite(extent) or extent <= 0.0:
        return 0.0
    return extent


def crease_segment(
    origin: Tuple[float, float],
    angle_deg: float,
    length_ratio: float = 1.0,
    sheet: Optional[Sequence[Tuple[float, float]]] = None,
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """The crease of a gene in normalised sheet coordinates.

    The crease *starts* at ``origin`` and runs along ``angle_deg``; it is
    ``length_ratio`` times as long as the distance from the origin to the
    edge of the sheet in that direction.  A ratio of one therefore reaches
    the edge of the paper, a ratio below one describes a crease that stops
    in the middle of the sheet.

    The encoding is invertible: the start point, the direction and the
    length of a crease determine the three numbers of a gene, and the three
    numbers of a gene determine the crease again.  That is what makes the
    round trip through the hybrid genome lossless.  The two end points are
    canonicalised, so a programme that is written and read back folds into
    the very same paper.
    """
    bounds = _sheet_bounds(sheet)
    u_low, u_high, v_low, v_high = bounds
    ox = _canonical(_clamp(float(origin[0]), u_low, u_high))
    oy = _canonical(_clamp(float(origin[1]), v_low, v_high))
    radians = math.radians(float(angle_deg))
    dx = math.cos(radians)
    dy = math.sin(radians)
    extent = _forward_extent((ox, oy), dx, dy, bounds)
    if extent <= 1.0e-9:
        # The direction points away from the paper: the crease is turned
        # towards the centre of the sheet so that it stays a real crease.
        toward = (
            0.5 * (u_low + u_high) - ox,
            0.5 * (v_low + v_high) - oy,
        )
        norm = math.hypot(toward[0], toward[1])
        if norm < 1.0e-12:
            return (ox, oy), (ox, oy)
        dx = toward[0] / norm
        dy = toward[1] / norm
        extent = _forward_extent((ox, oy), dx, dy, bounds)
    ratio = _clamp(float(length_ratio), MIN_LENGTH_RATIO, 1.0)
    length = ratio * extent
    p0 = (ox, oy)
    p1 = (
        _canonical(_clamp(ox + length * dx, u_low, u_high)),
        _canonical(_clamp(oy + length * dy, v_low, v_high)),
    )
    return p0, p1


def _random_origin(rng: random.Random) -> Tuple[float, float]:
    return (rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95))


def _repair_genes(genes: Sequence[HybridFoldGene]) -> List[HybridFoldGene]:
    """Make the layer references of a programme consistent.

    A gene may only address the groups that the earlier folds created, so
    the references are clamped to the groups that exist when the fold is
    performed.  A programme that was crossed over or mutated can carry a
    dangling reference; the repair turns it into a valid one instead of
    failing the fold.
    """
    available = 0
    repaired: List[HybridFoldGene] = []
    for gene in genes:
        layers = tuple(
            value
            for value in sorted({int(item) for item in gene.target_layers})
            if 0 <= value < available
        )
        join = gene.join_group
        if join is not None and not (0 <= int(join) < available):
            join = None
        repaired.append(
            HybridFoldGene(
                enabled=gene.enabled,
                origin=gene.origin,
                crease_angle_deg=gene.crease_angle_deg,
                fold_angle_deg=gene.fold_angle_deg,
                target_layers=layers,
                creates_pair=gene.creates_pair,
                creates_group=gene.creates_group and join is None,
                join_group=join,
                symmetry=gene.symmetry,
                length_ratio=gene.length_ratio,
                level=gene.level,
                residual_deg=gene.residual_deg,
                mountain=gene.mountain,
                label=gene.label,
            )
        )
        if gene.creates_pair and gene.enabled:
            available += 2
        elif gene.creates_group and join is None and gene.enabled:
            available += 1
    return repaired


def random_hybrid_genome(
    rng: Optional[random.Random] = None,
    min_steps: int = MIN_FOLD_STEPS,
    max_steps: int = MAX_FOLD_STEPS,
    strategy: str = "free",
    key: str = "random",
) -> HybridGenome:
    """Draw a random hybrid programme.

    ``strategy="free"`` draws the crease position, the crease direction and
    the fold angle from uniform distributions.  ``strategy="families"`` keeps
    the crease in the classic families (keel, nose diagonals, transverse
    folds, trailing edge trims) so that a large part of the population can
    actually be folded by a human.
    """
    rng = rng or random.Random()
    low = max(1, int(min_steps))
    high = max(low, int(max_steps))
    total = rng.randint(low, high)
    genes: List[HybridFoldGene] = []
    available = 0
    for index in range(total):
        if strategy == "families":
            family = rng.choice(("keel", "nose", "transverse", "trailing", "winglet"))
            if family == "keel":
                origin = (0.5, rng.uniform(0.15, 0.85))
                angle = rng.uniform(-4.0, 4.0)
            elif family == "nose":
                origin = (rng.uniform(0.2, 0.8), rng.uniform(0.6, 0.95))
                angle = rng.choice((45.0, -45.0)) + rng.uniform(-6.0, 6.0)
            elif family == "transverse":
                origin = (rng.uniform(0.1, 0.9), rng.uniform(0.2, 0.8))
                angle = rng.uniform(-8.0, 8.0)
            elif family == "trailing":
                origin = (rng.uniform(0.1, 0.9), rng.uniform(0.05, 0.35))
                angle = rng.uniform(-12.0, 12.0)
            else:  # winglet
                origin = (rng.uniform(0.05, 0.35), rng.uniform(0.35, 0.75))
                angle = rng.uniform(70.0, 110.0)
            label = f"{family} fold"
        else:
            origin = _random_origin(rng)
            angle = rng.uniform(-180.0, 180.0)
            label = "free fold"
        magnitude = rng.uniform(8.0, 180.0)
        mountain = rng.random() < 0.5
        fold_angle = magnitude if mountain else -magnitude
        symmetry = "symmetric" if rng.random() < 0.55 else rng.choice(("left", "right"))
        creates_pair = symmetry == "symmetric" and rng.random() < 0.5
        if available > 0 and rng.random() < 0.4:
            start = rng.randrange(0, available)
            layers: Tuple[int, ...] = (start,)
            if creates_pair and start + 1 < available and rng.random() < 0.4:
                layers = (start, start + 1)
        else:
            layers = ()
        genes.append(
            HybridFoldGene(
                enabled=True,
                origin=origin,
                crease_angle_deg=_wrap_angle(angle),
                fold_angle_deg=fold_angle,
                target_layers=layers,
                creates_pair=creates_pair,
                symmetry=symmetry,
                length_ratio=1.0 if rng.random() < 0.7 else rng.uniform(0.3, 1.0),
                level=index,
                mountain=mountain,
                label=label,
            )
        )
        available += 2 if creates_pair else 1
    return HybridGenome(
        genes=genes,
        key=key,
        label=f"random hybrid programme ({strategy}, {total} steps)",
        seed=rng.randrange(1 << 30),
        meta={"strategy": strategy, "step_count": total},
    )


def mutate_hybrid(
    genome: HybridGenome,
    rng: Optional[random.Random] = None,
    rate: float = 0.2,
    max_steps: int = MAX_FOLD_STEPS,
    min_steps: int = MIN_FOLD_STEPS,
) -> HybridGenome:
    """Mutate a programme.

    The mutation touches the continuous parameters (crease position,
    direction, fold angle) and the discrete ones (enabled flag, target
    layer, symmetry).  It may also add or remove a fold, so the length of
    the programme is part of what the search optimises.
    """
    rng = rng or random.Random()
    genes = list(genome.genes)
    for index in range(len(genes)):
        if rng.random() >= rate:
            continue
        gene = genes[index]
        choice = rng.random()
        if choice < 0.26:
            genes[index] = dataclasses.replace(
                gene,
                origin=(
                    _clamp(gene.origin[0] + rng.gauss(0.0, 0.08), 0.02, 0.98),
                    _clamp(gene.origin[1] + rng.gauss(0.0, 0.08), 0.02, 0.98),
                ),
                crease_angle_deg=_wrap_angle(
                    gene.crease_angle_deg + rng.gauss(0.0, 12.0)
                ),
            )
        elif choice < 0.50:
            magnitude = abs(gene.fold_angle_deg)
            magnitude = _clamp(magnitude + rng.gauss(0.0, 18.0), 5.0, 180.0)
            sign = 1.0 if gene.fold_angle_deg >= 0.0 else -1.0
            mountain = gene.mountain
            if rng.random() < 0.1:
                sign = -sign
                mountain = not mountain
            genes[index] = dataclasses.replace(
                gene,
                fold_angle_deg=sign * magnitude,
                mountain=mountain,
            )
        elif choice < 0.62:
            genes[index] = dataclasses.replace(gene, enabled=not gene.enabled)
        elif choice < 0.74:
            genes[index] = dataclasses.replace(gene, creates_pair=not gene.creates_pair)
        elif choice < 0.86:
            layers = gene.target_layers
            if layers:
                layers = ()
            else:
                layers = (rng.randrange(0, max(1, index * 2)),)
            genes[index] = dataclasses.replace(gene, target_layers=layers)
        else:
            genes[index] = dataclasses.replace(
                gene,
                symmetry=rng.choice(SYMMETRY_MODES),
                length_ratio=_clamp(
                    gene.length_ratio + rng.gauss(0.0, 0.1), GENE_LENGTH_RATIO_FLOOR, 1.0
                ),
            )
    # --- structural mutations ------------------------------------------
    if len(genes) < max_steps and rng.random() < 0.25:
        genes.insert(rng.randrange(len(genes) + 1), _random_gene(rng, len(genes)))
    elif len(genes) > min_steps and rng.random() < 0.20:
        genes.pop(rng.randrange(len(genes)))
    if len(genes) > 1 and rng.random() < 0.10:
        first = rng.randrange(len(genes))
        second = rng.randrange(len(genes))
        genes[first], genes[second] = genes[second], genes[first]
    return HybridGenome(
        genes=_repair_genes(genes),
        key=genome.key,
        label=genome.label,
        seed=rng.randrange(1 << 30),
        meta={**genome.meta, "parent": genome.key},
    )


def _random_gene(rng: random.Random, available: int) -> HybridFoldGene:
    magnitude = rng.uniform(10.0, 180.0)
    mountain = rng.random() < 0.5
    symmetry = rng.choice(SYMMETRY_MODES)
    creates_pair = symmetry == "symmetric" and rng.random() < 0.5
    layers: Tuple[int, ...] = ()
    if available > 0 and rng.random() < 0.4:
        layers = (rng.randrange(0, available),)
    return HybridFoldGene(
        enabled=True,
        origin=_random_origin(rng),
        crease_angle_deg=rng.uniform(-180.0, 180.0),
        fold_angle_deg=magnitude if mountain else -magnitude,
        target_layers=layers,
        creates_pair=creates_pair,
        symmetry=symmetry,
        length_ratio=1.0 if rng.random() < 0.7 else rng.uniform(0.3, 1.0),
        mountain=mountain,
        label="free fold",
    )


def crossover_hybrid(
    first: HybridGenome,
    second: HybridGenome,
    rng: Optional[random.Random] = None,
    min_steps: int = MIN_FOLD_STEPS,
    max_steps: int = MAX_FOLD_STEPS,
) -> HybridGenome:
    """Recombine two programmes.

    The genes are inherited one by one, and the length of the child is the
    length of the parents plus the structural mutations the crossover
    applies, so a child may be shorter or longer than its parents.
    """
    rng = rng or random.Random()
    genes: List[HybridFoldGene] = []
    count = max(len(first.genes), len(second.genes))
    for index in range(count):
        take_first = rng.random() < 0.5
        if index < len(first.genes) and take_first:
            genes.append(first.genes[index])
        elif index < len(second.genes):
            genes.append(second.genes[index])
        elif index < len(first.genes):
            genes.append(first.genes[index])
    if not genes:
        genes = list(first.genes) or list(second.genes)
    if len(genes) > max_steps:
        genes = genes[:max_steps]
    elif len(genes) < min_steps:
        while len(genes) < min_steps:
            genes.append(_random_gene(rng, len(genes)))
    return HybridGenome(
        genes=_repair_genes(genes),
        key="crossover",
        label="hybrid crossover",
        seed=rng.randrange(1 << 30),
        meta={
            "parents": [first.key, second.key],
            "step_count": len(genes),
        },
    )


def hybrid_to_origami(genome: HybridGenome) -> origami_genome.OrigamiGenome:
    """Translate a hybrid programme into a programme of the folding engine.

    The *layer* of a gene is resolved against the groups the earlier folds
    created: an empty scope cuts the whole sheet, a scope names the groups
    the earlier folds created.  A gene that points at a group that does not
    exist falls back to the untouched sheet, so the programme stays
    executable.
    """
    genes = _repair_genes(genome.genes)
    steps: List[origami_genome.FoldStep] = []
    created: List[str] = []
    for index, gene in enumerate(genes):
        if not gene.enabled:
            continue
        p0, p1 = crease_segment(gene.origin, gene.crease_angle_deg, gene.length_ratio)
        scope = tuple(
            created[value] for value in gene.target_layers if 0 <= value < len(created)
        )
        set_group_pair: Optional[Tuple[str, str]] = None
        if gene.creates_pair:
            set_group = ""
            set_group_pair = (f"g{index + 1}l", f"g{index + 1}r")
            created.extend(set_group_pair)
        elif gene.join_group is not None and 0 <= gene.join_group < len(created):
            set_group = created[gene.join_group]
        elif gene.creates_group:
            set_group = f"g{index + 1}"
            created.append(set_group)
        else:
            set_group = ""
        steps.append(
            origami_genome.FoldStep(
                step_index=len(steps) + 1,
                crease_line=(p0, p1),
                fold_angle_deg=float(gene.fold_angle_deg),
                symmetry=gene.symmetry,
                layer_order=origami_genome.LAYER_ORDER_AUTO,
                target_face_ids=(),
                group=scope,
                set_group=set_group,
                set_group_pair=set_group_pair,
                level=int(gene.level),
                residual_deg=float(gene.residual_deg),
                mountain=bool(gene.mountain),
                label=gene.label,
            )
        )
    return origami_genome.OrigamiGenome(
        steps=steps,
        sheet=list(origami_genome.SHEET_OUTLINE),
        key=genome.key,
        label=genome.label,
        seed=int(genome.seed),
        strategy="hybrid",
        meta={**genome.meta, "step_count": len(steps)},
    )


def hybrid_from_origami(genome: origami_genome.OrigamiGenome) -> HybridGenome:
    """Translate a programme of the folding engine into a hybrid programme.

    The inverse of :func:`hybrid_to_origami`; it is used to seed the search
    with the archetypes and to read back a programme that was stored in the
    old format.
    """
    known: Dict[str, int] = {"base": -1}
    created: List[str] = []
    genes: List[HybridFoldGene] = []
    for step in genome.steps:
        scope_names = tuple(step.group) if step.group else ()
        indices: List[int] = []
        for name in scope_names:
            index = known.get(name)
            if index is not None and index >= 0:
                indices.append(index)
        pair = step.set_group_pair
        p0, p1 = step.crease_line
        origin = (_canonical(float(p0[0])), _canonical(float(p0[1])))
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        angle = _wrap_angle(math.degrees(math.atan2(dy, dx)))
        chord = crease_segment(origin, angle)
        span = math.hypot(chord[1][0] - chord[0][0], chord[1][1] - chord[0][1])
        length = math.hypot(dx, dy)
        ratio = (
            1.0
            if span < 1.0e-9
            else _clamp(length / span, MIN_LENGTH_RATIO, 1.0)
        )
        if pair is not None:
            creates_group = False
            join_group: Optional[int] = None
        elif step.set_group and step.set_group not in known:
            creates_group = True
            join_group = None
        elif step.set_group and known.get(step.set_group, -1) >= 0:
            creates_group = False
            join_group = int(known[step.set_group])
        else:
            creates_group = False
            join_group = None
        genes.append(
            HybridFoldGene(
                enabled=True,
                origin=origin,
                crease_angle_deg=angle,
                fold_angle_deg=float(step.fold_angle_deg),
                target_layers=tuple(sorted(set(indices))),
                creates_pair=pair is not None,
                creates_group=creates_group,
                join_group=join_group,
                symmetry=step.symmetry,
                length_ratio=ratio,
                level=int(step.level),
                residual_deg=float(step.residual_deg),
                mountain=bool(step.mountain),
                label=step.label,
            )
        )
        if pair is not None:
            for name in pair:
                known[name] = len(created)
                created.append(name)
        elif step.set_group and step.set_group not in known:
            known[step.set_group] = len(created)
            created.append(step.set_group)
    return HybridGenome(
        genes=genes,
        key=genome.key,
        label=genome.label,
        seed=int(genome.seed),
        meta={**genome.meta, "step_count": len(genes)},
    )


def archetype_hybrid_genomes() -> List[HybridGenome]:
    """The known good indoor designs of the specification.

    The classic dart, the Nakamura lock (the "heso" glider) and the Sky King
    competition glider are the three designs a human would try first; they
    are the warm start of the search.
    """
    genomes: List[HybridGenome] = []
    for key in ARCHETYPE_KEYS:
        try:
            source = origami_genome.genome_from_archetype(key)
        except Exception:  # pragma: no cover - depends on the engine
            continue
        genome = hybrid_from_origami(source)
        genome.key = key
        genome.label = f"archetype {key}"
        genome.meta = {**genome.meta, "archetype": key, "seeded": True}
        genomes.append(genome)
    return genomes


def hybrid_signature(genome: HybridGenome) -> str:
    """A short, stable hash of a programme.

    Two programmes with the same signature are the same design; the search
    uses the signature to avoid evaluating the same geometry twice.
    """
    digest = hashlib.blake2b(digest_size=8)
    for gene in genome.genes:
        digest.update(
            repr(
                (
                    bool(gene.enabled),
                    round(gene.origin[0], 4),
                    round(gene.origin[1], 4),
                    round(gene.crease_angle_deg, 3),
                    round(gene.fold_angle_deg, 3),
                    tuple(gene.target_layers),
                    bool(gene.creates_pair),
                    gene.symmetry,
                    round(gene.length_ratio, 3),
                )
            ).encode("utf-8")
        )
    return digest.hexdigest()


def seed_population(
    size: int,
    rng: Optional[random.Random] = None,
    archetype_fraction: float = 0.02,
    mutant_fraction: float = 0.08,
    min_steps: int = MIN_FOLD_STEPS,
    max_steps: int = MAX_FOLD_STEPS,
    strategy: str = "free",
) -> List[HybridGenome]:
    """Build the warm start population of a generation.

    Two percent of the individuals are the known good designs, eight percent
    are mutations of them and the remaining ninety percent are random
    programmes.  The random programmes are *not* filtered here: the geometry
    filter of stage 0 does that, and it is the only place where the filter
    is allowed to spend time.
    """
    rng = rng or random.Random()
    total = max(1, int(size))
    archetypes = archetype_hybrid_genomes()
    archetype_count = int(round(total * float(archetype_fraction)))
    mutant_count = int(round(total * float(mutant_fraction)))
    random_count = max(0, total - archetype_count - mutant_count)

    population: List[HybridGenome] = []
    for index in range(archetype_count):
        base = archetypes[index % len(archetypes)] if archetypes else random_hybrid_genome(rng)
        genome = HybridGenome(
            genes=list(base.genes),
            key=f"archetype:{base.key}",
            label=f"archetype {base.key}",
            seed=rng.randrange(1 << 30),
            meta={**base.meta, "origin": "archetype"},
        )
        population.append(genome)
    for index in range(mutant_count):
        base = archetypes[index % len(archetypes)] if archetypes else random_hybrid_genome(rng)
        mutant = mutate_hybrid(base, rng, rate=0.3, max_steps=max_steps, min_steps=min_steps)
        mutant.key = f"mutant:{base.key}"
        mutant.meta = {**mutant.meta, "origin": "mutant", "parent": base.key}
        population.append(mutant)
    for _ in range(random_count):
        population.append(
            random_hybrid_genome(
                rng,
                min_steps=min_steps,
                max_steps=max_steps,
                strategy=strategy,
            )
        )
    return population
