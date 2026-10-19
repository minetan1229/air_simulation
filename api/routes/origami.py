"""REST endpoints for the procedural origami geometry.

The endpoints expose the folded crease pattern of the three paper aeroplane
archetypes.  The very same generator is used by the unit tests, so the
viewer and the test suite always agree on the geometry.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify

from api.core import BadRequest, as_float, body, domain

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


def _params_from(payload: Dict[str, Any]) -> Dict[str, float]:
    params: Dict[str, float] = {}
    for key in _PATTERN_PARAMS:
        if key in payload and payload[key] is not None:
            params[key] = as_float(payload[key], key)
    return params


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
