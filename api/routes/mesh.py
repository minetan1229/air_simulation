"""3D mesh model import and metrics inspection routes."""

from __future__ import annotations

import struct
from typing import Any, Dict
from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from physics import mesh_domain
from physics.config_schema import as_dict, clamp
from ..core import SESSION, BadRequest, as_float, body, require

mesh_bp = Blueprint("mesh", __name__)


@mesh_bp.post("/api/v1/mesh/import")
def mesh_import():
    try:
        return _mesh_import_inner()
    except BadRequest:
        raise
    except (ValueError, KeyError, UnicodeDecodeError, IndexError, struct.error) as exc:
        raise BadRequest(f"could not import model: {exc}") from exc


def _mesh_import_inner():
    if request.files:
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            raise BadRequest("multipart upload must contain a 'file' field")
        data = uploaded.read()
        filename = uploaded.filename
        raw_gsm = as_float(request.form.get("paper_gsm", 100.0), "paper_gsm")
        metrics = mesh_domain.MESH_LIBRARY.import_model_bytes(
            data=data,
            filename=filename,
            scale_hint=request.form.get("scale_hint", "m"),
            cg_mode=request.form.get("cg_mode", "volume"),
            paper_gsm=clamp(raw_gsm, 10.0, 1000.0),
        )
    else:
        payload = body()
        raw_gsm = as_float(payload.get("paper_gsm", 100.0), "paper_gsm")
        paper_gsm = clamp(raw_gsm, 10.0, 1000.0)
        if "url" in payload and payload["url"]:
            url = str(payload["url"])
            if not url.lower().startswith(("http://", "https://")):
                raise BadRequest("'url' must start with http:// or https://")
            metrics = mesh_domain.MESH_LIBRARY.import_model_url(
                url=url,
                scale_hint=str(payload.get("scale_hint", "m")),
                cg_mode=str(payload.get("cg_mode", "volume")),
                paper_gsm=paper_gsm,
                overrides=payload.get("overrides"),
            )
        else:
            data = payload.get("data_base64")
            if not data:
                raise BadRequest("provide a multipart 'file', a 'url', or 'data_base64'")
            import base64

            try:
                raw = base64.b64decode(data, validate=True)
            except Exception as exc:
                raise BadRequest(f"'data_base64' is not valid base64: {exc}") from exc
            raw_fn = str(payload.get("filename", "model.glb"))
            filename = secure_filename(raw_fn) or "model.glb"
            metrics = mesh_domain.MESH_LIBRARY.import_model_bytes(
                data=raw,
                filename=filename,
                scale_hint=str(payload.get("scale_hint", "m")),
                cg_mode=str(payload.get("cg_mode", "volume")),
                paper_gsm=paper_gsm,
                overrides=payload.get("overrides"),
            )
    with SESSION.lock:
        SESSION.mesh_model_id = metrics.model_id
        SESSION.selected_mesh = metrics
        SESSION.set_aircraft(mesh_domain.MESH_LIBRARY.to_aircraft_config(metrics.model_id, SESSION.aircraft))
    return jsonify(as_dict(metrics))


@mesh_bp.get("/api/v1/mesh/models")
def mesh_models():
    models = mesh_domain.MESH_LIBRARY.list_models()
    selected = mesh_domain.MESH_LIBRARY.selected()
    return jsonify(
        {
            "models": models,
            "count": len(models),
            "selected": selected.model_id if selected else None,
        }
    )


@mesh_bp.post("/api/v1/mesh/select")
def mesh_select():
    payload = body()
    model_id = str(require(payload, "model_id"))
    try:
        metrics = mesh_domain.MESH_LIBRARY.select(model_id)
    except KeyError as exc:
        raise BadRequest(f"unknown model_id '{model_id}'") from exc
    with SESSION.lock:
        SESSION.mesh_model_id = metrics.model_id
        SESSION.selected_mesh = metrics
        SESSION.set_aircraft(mesh_domain.MESH_LIBRARY.to_aircraft_config(model_id, SESSION.aircraft))
    return jsonify(as_dict(metrics))


@mesh_bp.get("/api/v1/mesh/metrics/<model_id>")
def mesh_metrics(model_id: str):
    try:
        metrics = mesh_domain.MESH_LIBRARY.get_metrics(model_id)
    except KeyError as exc:
        raise BadRequest(f"unknown model_id '{model_id}'") from exc
    return jsonify(as_dict(metrics))


@mesh_bp.post("/api/v1/mesh/overrides/<model_id>")
def mesh_overrides(model_id: str):
    payload = body()
    overrides = payload.get("overrides", payload)
    if not isinstance(overrides, dict):
        raise BadRequest("'overrides' must be a JSON object")
    try:
        metrics = mesh_domain.MESH_LIBRARY.apply_overrides(model_id, overrides)
    except KeyError as exc:
        raise BadRequest(f"unknown model_id '{model_id}'") from exc
    return jsonify(as_dict(metrics))
