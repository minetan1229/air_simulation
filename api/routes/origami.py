"""REST endpoints for the procedural origami geometry.

The endpoints expose the folded crease pattern of the three paper aeroplane
archetypes and the **procedural origami engine**: a fold programme (the
*genome*) that is generated at random, folded into a solid, checked for a
grip a human can actually hold and rendered back into a recipe: the ordered
table of folds with their exact angles, the flat crease pattern and the
intermediate solid of every single step.

The very same engine is used by the unit tests, so the viewer and the test
suite always agree on the geometry.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify

from api.core import BadRequest, as_float, as_int, body, domain

origami_bp = Blueprint("origami", __name__)

#: Query parameters that are forwarded to the crease pattern compiler.
_PATTERN_PARAMS = (
    "keel_beta_deg",
    "fold_leading_deg",
    "fold_tip_deg",
    "fold_trailing_deg",
    "elevon_deg",
    "dihedral_deg",
)

#: The strategies the genome generator can use.
_GENOME_STRATEGIES = ("free", "families")


def _params_from(payload: Dict[str, Any]) -> Dict[str, float]:
    params: Dict[str, float] = {}
    for key in _PATTERN_PARAMS:
        if key in payload and payload[key] is not None:
            params[key] = as_float(payload[key], key)
    return params


def _genome_from_request(payload: Dict[str, Any]) -> Any:
    """Build the genome a request asks for.

    Either an explicit genome is posted (``{"genome": {...}}`` or a body
    whose ``steps`` is a list of folds), or one is generated: either from an
    archetype of the catalogue or as a random fold programme.  The count of
    the generated programme is the query parameter ``step_count`` so it
    cannot be confused with the list of folds.
    """
    genome = domain("origami_genome")
    explicit = payload.get("genome")
    if not isinstance(explicit, dict):
        raw = payload.get("steps")
        explicit = payload if isinstance(raw, (list, tuple)) and raw else None
    if explicit is not None:
        try:
            return genome.genome_from_dict(explicit)
        except (TypeError, ValueError, IndexError) as exc:
            raise BadRequest(f"invalid genome: {exc}") from exc

    archetype = str(payload.get("archetype", "")).strip()
    if archetype:
        origami = domain("origami_domain")
        if archetype not in origami.ARCHETYPES:
            raise BadRequest(
                f"unknown archetype '{archetype}'; expected one of {', '.join(origami.ARCHETYPES)}"
            )
        return genome.genome_from_archetype(archetype, _params_from(payload))

    seed = as_int(payload.get("seed", 0), "seed")
    steps = as_int(payload.get("step_count", 8), "step_count")
    if steps < 1 or steps > 64:
        raise BadRequest("field 'step_count' must be within [1, 64]")
    strategy = str(payload.get("strategy", "families")).strip()
    if strategy not in _GENOME_STRATEGIES:
        raise BadRequest(
            f"field 'strategy' must be one of {', '.join(_GENOME_STRATEGIES)}"
        )
    return genome.random_genome(random.Random(seed), steps=steps, strategy=strategy, seed=seed)


def _thresholds_from(payload: Dict[str, Any]) -> Any:
    genome = domain("origami_genome")
    limits = genome.GripThresholds()
    for field in (
        "min_grip_height_m",
        "min_grip_length_m",
        "min_grip_layers",
        "cg_tolerance_m",
        "grip_half_width_m",
    ):
        if payload.get(field) is not None:
            value = as_float(payload[field], field)
            if field == "min_grip_layers":
                limits.min_grip_layers = int(value)
            else:
                setattr(limits, field, value)
    return limits


@origami_bp.get("/api/v1/origami/archetypes")
def origami_archetypes():
    """List the supported paper aeroplane archetypes."""
    origami = domain("origami_domain")
    return jsonify({"archetypes": origami.archetype_catalog()})


@origami_bp.post("/api/v1/origami/mesh")
def origami_mesh():
    """Return the folded origami mesh of one archetype.

    The payload carries the vertex buffers of the folded sheet together with
    the per-vertex shading attributes (layer count, crease weight, elastic
    weight and twist weight) that the viewer needs for the paper material.
    """
    origami = domain("origami_domain")
    payload = body()
    archetype = str(payload.get("archetype", origami.ARCHETYPE_DART)).strip()
    if archetype not in origami.ARCHETYPES:
        raise BadRequest(
            f"unknown archetype '{archetype}'; expected one of {', '.join(origami.ARCHETYPES)}"
        )
    alpha = as_float(payload.get("alpha", 1.0), "alpha")
    if not 0.0 <= alpha <= 1.0:
        raise BadRequest("field 'alpha' must be within [0, 1]")
    wingspan = payload.get("wingspan_m")
    scale = 1.0
    if wingspan is not None:
        span = as_float(wingspan, "wingspan_m")
        if span <= 0.0:
            raise BadRequest("field 'wingspan_m' must be > 0")
        scale = span / origami.SHEET_WIDTH_M
    mesh = origami.build_mesh(archetype, alpha, _params_from(payload), scale)
    return jsonify(origami.mesh_payload(mesh))


@origami_bp.get("/api/v1/origami/genome")
def origami_genome():
    """Generate a fold programme and evaluate it.

    The response carries the genome itself, the geometry of the folded
    solid, the result of the grip filter (can this be thrown by hand?) and
    the ordered recipe of the folds.
    """
    genome = domain("origami_genome")
    payload = _request_payload()
    program = _genome_from_request(payload)
    paper_gsm = as_float(payload.get("paper_gsm", 100.0), "paper_gsm")
    geometry = genome.fold_genome(program, paper_gsm=paper_gsm)
    report = genome.evaluate_grip(geometry, _thresholds_from(payload))
    return jsonify(genome.genome_payload(program, geometry, report))


@origami_bp.post("/api/v1/origami/genome")
def origami_genome_evaluate():
    """Evaluate a fold programme that the client sends."""
    genome = domain("origami_genome")
    payload = body()
    program = _genome_from_request(payload)
    paper_gsm = as_float(payload.get("paper_gsm", 100.0), "paper_gsm")
    geometry = genome.fold_genome(program, paper_gsm=paper_gsm)
    report = genome.evaluate_grip(geometry, _thresholds_from(payload))
    return jsonify(genome.genome_payload(program, geometry, report))


@origami_bp.get("/api/v1/origami/genome/recipe")
def origami_genome_recipe():
    """The recipe of a fold programme.

    The payload carries the ordered step table (which line, mountain or
    valley, how many degrees), the Markdown version for printing and the
    crease pattern as an SVG drawing.
    """
    genome = domain("origami_genome")
    payload = _request_payload()
    program = _genome_from_request(payload)
    geometry = genome.fold_genome(program)
    steps = genome.recipe_steps(program, geometry)
    return jsonify(
        {
            "genome": program.to_dict(),
            "steps": steps,
            "markdown": genome.recipe_markdown(program, geometry),
            "svg": genome.crease_pattern_svg(program),
            "valid": bool(genome.evaluate_grip(geometry).valid),
        }
    )


@origami_bp.get("/api/v1/origami/genome/steps")
def origami_genome_steps():
    """The solid of every intermediate fold state.

    Index 0 is the flat sheet, the last index the finished aeroplane, so a
    slider in the viewer can walk through the folding one move at a time.
    """
    genome = domain("origami_genome")
    payload = _request_payload()
    program = _genome_from_request(payload)
    alpha = as_float(payload.get("alpha", 1.0), "alpha")
    if not 0.0 <= alpha <= 1.0:
        raise BadRequest("field 'alpha' must be within [0, 1]")
    states = genome.step_meshes(program, alpha)
    return jsonify(
        {
            "genome": program.to_dict(),
            "step_count": len(states) - 1,
            "states": states,
        }
    )


def _request_payload() -> Dict[str, Any]:
    """The query parameters of a GET request, as a plain dictionary."""
    from flask import request

    payload: Dict[str, Any] = {}
    for key, value in request.args.items():
        payload[key] = value
    return payload

