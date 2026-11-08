"""OpenAPI 3.0 specification builder for the 3D paper-airplane simulator.

Generates a machine-readable specification from the Flask route map plus
hand-written operation metadata (examples, security, error shape) so the
Swagger UI at ``GET /docs`` and ``GET /api/openapi.json`` stay in sync with
the handlers without requiring a new dependency.

``docs/api.md`` remains the human-readable reference; this module provides the
structured machine-readable twin.
"""

from __future__ import annotations

import json
import os
from typing import Any

from flask import Flask, jsonify, render_template_string

from physics.config_schema import utc_now_iso

# ---------------------------------------------------------------------------
# Operation metadata (summary + description + examples) per path+method.
# Keep this exhaustive: every route in ``api/routes/*`` appears here exactly once.
# ---------------------------------------------------------------------------

def _ref(name: str) -> str:
    return f"#/components/schemas/{name}"


COMMON_SCHEMAS: dict[str, Any] = {
    "AircraftConfig": {
        "type": "object",
        "description": "Airframe geometry, material, trim and stability derivatives (physics.config_schema.AircraftConfig).",
        "properties": {
            "name": {"type": "string", "example": "Paper Glider"},
            "mass_kg": {"type": "number", "format": "float", "example": 0.005},
            "paper_gsm": {"type": "number", "example": 100.0},
            "wing_area_m2": {"type": "number", "example": 0.015},
            "wingspan_m": {"type": "number", "example": 0.2},
            "taper_ratio": {"type": "number", "example": 0.7},
            "dihedral_deg": {"type": "number", "example": 5.0},
            "elevon_deg": {"type": "number", "example": 2.0},
            "cd0": {"type": "number", "example": 0.02},
            "oswald_e": {"type": "number", "example": 0.75},
            "cl_max": {"type": "number", "example": 0.9},
            "stall_alpha_deg": {"type": "number", "example": 14.0},
            "low_re_enabled": {"type": "boolean", "example": False},
        },
    },
    "EnvironmentConfig": {
        "type": "object",
        "description": "Atmosphere / wind environment (physics.config_schema.EnvironmentConfig).",
        "properties": {
            "temperature_c": {"type": "number", "example": 15.0},
            "pressure_hpa": {"type": "number", "example": 1013.25},
            "relative_humidity_pct": {"type": "number", "example": 50.0},
            "mean_wind_mps": {"type": "number", "example": 0.0},
            "wind_direction_deg": {"type": "number", "example": 0.0},
            "turbulence_intensity": {"type": "number", "example": 0.0},
        },
    },
    "LaunchConfig": {
        "type": "object",
        "description": "Release condition (physics.config_schema.LaunchConfig).",
        "properties": {
            "launch_speed": {"type": "number", "example": 12.0},
            "launch_angle_deg": {"type": "number", "example": 12.0},
            "launch_roll_deg": {"type": "number", "example": 0.0},
            "launch_yaw_deg": {"type": "number", "example": 0.0},
            "launch_height_m": {"type": "number", "example": 1.2},
            "throw_style": {"type": "string", "example": "direct"},
        },
    },
    "Error": {
        "type": "object",
        "properties": {
            "error": {"type": "string"},
            "status": {"type": "integer"},
            "timestamp": {"type": "string", "format": "date-time"},
            "backend": {"type": "string", "example": "numpy"},
        },
    },
    "BackendEnvelope": {
        "type": "object",
        "description": "Every success envelope carries the executing backend identifier.",
        "properties": {
            "backend": {"type": "string", "enum": ["cupy", "torch", "numpy"]},
        },
    },
}

# path -> method -> operation
OPERATIONS: dict[str, dict[str, Any]] = {
    "/api/v1/aerodynamics/calculate": {
        "post": {
            "summary": "Lift / drag / stall evaluation",
            "description": "Evaluate aerodynamic coefficients at a single flight condition (Helmbold / Polhamus / Hoerner chain).",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {
                "aircraft": {"$ref": _ref("AircraftConfig")},
                "environment": {"$ref": _ref("EnvironmentConfig")},
                "speed_mps": {"type": "number", "example": 10.0},
                "alpha_deg": {"type": "number", "example": 4.0},
                "beta_deg": {"type": "number", "example": 0.0},
                "mach": {"type": "number", "example": 0.0},
                "ground_height_m": {"type": "number", "example": 1.0},
            }}}}},
            "x-request-examples": {
                "single-point": {"summary": "Single condition", "value": {"speed_mps": 10.0, "alpha_deg": 4.0}},
            },
        }
    },
    "/api/v1/aerodynamics/polar": {
        "post": {
            "summary": "Drag polar sweep",
            "description": "Alpha sweep returning CD/CL/LD data for polar plotting.",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {
                "aircraft": {"$ref": _ref("AircraftConfig")},
                "alpha_min_deg": {"type": "number", "example": -8.0},
                "alpha_max_deg": {"type": "number", "example": 20.0},
                "points": {"type": "integer", "example": 57},
                "reynolds": {"type": "number", "example": 10000.0},
            }}}}},
        }
    },
    "/api/v1/environment/atmosphere": {
        "post": {
            "summary": "Set atmosphere and return properties",
            "description": "Update the session atmosphere (ISA + Sutherland) and return derived density / viscosity.",
        }
    },
    "/api/v1/environment/current": {
        "get": {"summary": "Current atmosphere", "description": "Read the session environment plus derived properties."}
    },
    "/api/v1/environment/config": {
        "post": {"summary": "Update environment config", "description": "Persist environment overrides on the session."}
    },
    "/api/v1/wind/field": {
        "post": {
            "summary": "3D wind vector field",
            "description": "Evaluate the 3D wind / turbulence field on a regular grid (GPU or NumPy fallback).",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {
                "bounds": {"type": "object", "example": {"x_min": -10, "x_max": 10, "y_min": -10, "y_max": 10, "z_min": 0, "z_max": 15}},
                "resolution": {"type": "array", "items": {"type": "integer"}, "example": [20, 20, 15]},
                "mean_wind": {"type": "array", "items": {"type": "number"}, "example": [2.0, 0.0, 0.0]},
                "turbulence_intensity": {"type": "number", "example": 0.15},
            }}}}},
        }
    },
    "/api/v1/stress/analysis": {
        "post": {
            "summary": "Stress heatmap analysis",
            "description": "Compute wing-root bending stress and per-vertex stress indices (0 = safe .. 1 = failure).",
        }
    },
    "/api/v1/simulation/step": {
        "post": {
            "summary": "Single 6DoF step",
            "description": "Advance the shared simulator by one backend step (variable dt with sub-stepping).",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {
                "dt": {"type": "number", "example": 0.01},
                "substeps": {"type": "integer", "example": 1},
                "n_steps": {"type": "integer", "example": 1},
            }}}}},
        }
    },
    "/api/v1/simulation/reset": {
        "post": {"summary": "Reset simulator", "description": "Reinitialise the airframe with the given payload (aircraft / environment / launch)."}
    },
    "/api/v1/simulation/launch": {
        "post": {
            "summary": "Full-flight headless launch",
            "description": "Run a complete trajectory from release until landing in one call. Returns state history and metrics.",
        }
    },
    "/api/v1/simulation/history": {
        "get": {"summary": "Recorded trajectory", "description": "Ring-buffer history of the shared simulator."}
    },
    "/api/v1/simulation/batch": {
        "post": {"summary": "Parallel parameter sweep", "description": "Run many evaluations in parallel (GPU or multiprocessing fallback)."}
    },
    "/api/v1/simulation/ensemble": {
        "post": {"summary": "Ensemble of identical launches", "description": "Monte-Carlo / ensemble statistics over launch perturbations."}
    },
    "/api/v1/mesh/import": {
        "post": {"summary": "Import 3D model", "description": "Upload STL / OBJ / GLB and compute derived planform, CG and inertia."}
    },
    "/api/v1/mesh/models": {
        "get": {"summary": "List imported models", "description": "Enumerate uploaded mesh records."}
    },
    "/api/v1/mesh/select": {
        "post": {"summary": "Select active mesh", "description": "Choose the mesh that drives the simulator geometry."}
    },
    "/api/v1/optimizer/run": {
        "post": {
            "summary": "Start async optimisation job",
            "description": "Queue an optimiser job (NSGA-II / DE / Nelder-Mead / Surrogate) and return job_id immediately.",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {
                "algorithm": {"type": "string", "example": "nsga2"},
                "objectives": {"type": "array", "items": {"type": "string"}, "example": ["max_distance"]},
                "population_size": {"type": "integer", "example": 24},
                "generations": {"type": "integer", "example": 10},
            }}}}},
        }
    },
    "/api/v1/optimizer/status/{job_id}": {
        "get": {"summary": "Job progress / ETA", "description": "Poll progress, evaluations per second and estimated completion."}
    },
    "/api/v1/optimizer/cancel/{job_id}": {
        "post": {"summary": "Cancel optimisation job", "description": "Abort a running optimiser job."}
    },
    "/api/v1/optimizer/result/{job_id}": {
        "get": {"summary": "Job result", "description": "Pareto front and best design variables."}
    },
    "/api/v1/optimizer/cancel/<job_id>": {
        "post": {"summary": "Cancel optimisation job (alt)", "description": "Alias path for Flask <job_id> routing; see /api/v1/optimizer/cancel/{job_id}."}
    },
    "/api/v1/optimizer/status/<job_id>": {
        "get": {"summary": "Job progress (alt)", "description": "Alias path for Flask <job_id> routing."}
    },
    "/api/v1/optimizer/result/<job_id>": {
        "get": {"summary": "Job result (alt)", "description": "Alias path for Flask <job_id> routing."}
    },
    "/api/v1/jobs": {
        "get": {"summary": "All async jobs", "description": "Enumerate every registered job and its status."}
    },
    "/api/v1/data/export": {
        "post": {"summary": "Export flight log", "description": "Serialise the flight log as CSV or JSON."}
    },
    "/api/v1/data/import": {
        "post": {"summary": "Import preset", "description": "Restore a saved aircraft / environment preset."}
    },
    "/api/v1/meta/schemas": {
        "get": {"summary": "UI single source of truth", "description": "Aircraft / environment / launch schemas, objectives, algorithms, design variables."}
    },
    "/api/v1/meta/schema": {
        "get": {"summary": "UI schema (alias)", "description": "Alias of /api/v1/meta/schemas."}
    },
    "/api/v1/meta/physics-references": {
        "get": {"summary": "Academic reference registry", "description": "Structured citations, equations and AeroSandbox mapping per physics model."}
    },
    "/api/v1/meta/health": {
        "get": {"summary": "Domain health", "description": "Per-domain availability plus GPU device info."}
    },
    "/api/v1/meta/test-fault": {
        "get": {"summary": "Diagnostic fault (test only)", "description": "Disabled outside development / TESTING=1 environments."}
    },
    "/api/v1/system/gpu": {
        "get": {"summary": "GPU device info", "description": "Backend, device name and availability of the parallel engine."}
    },
    "/api/v1/system/force-gpu-render": {
        "post": {"summary": "Force discrete GPU for browser", "description": "Loopback-only. Registers DirectX UserGpuPreferences and optionally launches the browser."}
    },
    "/api/v1/origami/archetypes": {
        "get": {"summary": "Origami archetypes", "description": "Foldable paper-plane families and their genome defaults."}
    },
    "/api/v1/origami/mesh": {
        "post": {"summary": "Generate origami mesh", "description": "Procedural solid from a crease pattern (A4 sheet simulation)."}
    },
    "/api/v1/origami/genome": {
        "get": {"summary": "Origami genome read", "description": "Current genome vector for the active origami design."},
        "post": {"summary": "Origami genome update", "description": "Set design variables and regenerate the folding sequence."},
    },
    "/api/v1/origami/genome/recipe": {
        "get": {"summary": "Folding recipe", "description": "Step-by-step folding instructions derived from the genome."}
    },
    "/api/v1/origami/genome/steps": {
        "get": {"summary": "Folding steps", "description": "Hinge sequence (body, corners, collar, wings) for the current genome."}
    },
    "/api/v1/records/presets": {
        "get": {"summary": "Saved presets", "description": "List stored records and presets."}
    },
    "/api/v1/records/presets/<key>": {
        "get": {"summary": "Preset by key (alt)", "description": "Flask <key> route variant."}
    },
    "/api/v1/records/preset/<key>": {
        "get": {"summary": "Preset by key", "description": "Fetch one preset record."}
    },
    "/api/v1/records/apply": {
        "post": {"summary": "Apply preset", "description": "Load a preset into the session."}
    },
    "/api/v1/records/throw_techniques": {
        "get": {"summary": "Throw technique catalogue", "description": "Human throw styles and their resolved launch distributions."}
    },
    "/api/v1/guinness/simulate": {
        "post": {"summary": "Guinness distance simulation", "description": "Long-range paper-plane attempt evaluator."}
    },
    "/api/v1/guinness/verification": {
        "get": {"summary": "Guinness verification", "description": "Verification rules and record criteria."}
    },
    "/api/v1/records/guinness": {
        "get": {"summary": "Guinness records (alt)", "description": "Alias of /api/v1/guinness/verification."}
    },
    "/api/v1/errors": {
        "get": {"summary": "Error log", "description": "Recent application errors."}
    },
    "/api/v1/records/errors": {
        "get": {"summary": "Error log (alt)", "description": "Alias of /api/v1/errors."}
    },
    "/api/v1/errors/report": {
        "post": {"summary": "Report client error", "description": "Ingest a frontend error sample into the server log."}
    },
    "/api/v1/errors/stats": {
        "get": {"summary": "Error statistics", "description": "Aggregate counts by category."}
    },
    "/api/v1/errors/clear": {
        "post": {"summary": "Clear error log", "description": "Purge stored errors (development use)."}
    },
    "/api/v1/investigate/run": {
        "post": {"summary": "Start investigate job", "description": "Asynchronous auto-investigation of a flight anomaly."}
    },
    "/api/v1/jobs/investigate": {
        "post": {"summary": "Start investigate job (alt)", "description": "Alias of /api/v1/investigate/run."}
    },
    "/api/v1/investigate/status/<job_id>": {
        "get": {"summary": "Investigate status", "description": "Progress of an investigate job."}
    },
    "/api/v1/investigate/cancel/<job_id>": {
        "post": {"summary": "Cancel investigate job", "description": "Abort an investigate job."}
    },
    "/api/v1/investigate/result/<job_id>": {
        "get": {"summary": "Investigate result", "description": "Diagnosis and recommended corrections."}
    },
    "/api/v1/jobs/join/<job_id>": {
        "post": {"summary": "Join a job", "description": "Wait for a running job to finish and return its result."}
    },
    # GA2 research API (ga_research_bp) — prefix resolved at runtime.
    "/api/v1/ga2/run": {
        "post": {"summary": "Start GA2 research run", "description": "Large-scale evolutionary search (physics/ga2)."}
    },
    "/api/v1/ga2/status/{run_id}": {
        "get": {"summary": "GA2 run status", "description": "Stage, generation and best fitness."}
    },
    "/api/v1/ga2/result/{run_id}": {
        "get": {"summary": "GA2 run result", "description": "Archive, Pareto and checkpoint metadata."}
    },
    "/api/v1/ga2/checkpoints/{run_id}": {
        "get": {"summary": "GA2 checkpoints", "description": "List resumable checkpoints."}
    },
    "/api/v1/ga2/resume": {
        "post": {"summary": "Resume GA2 run", "description": "Restore a checkpoint and continue evolution."}
    },
    "/api/v1/ga2/journal/{run_id}": {
        "get": {"summary": "GA2 journal", "description": "Event log for a research run."}
    },
}

# Flask ``<...>`` tokens must be displayed as ``{...}`` in OpenAPI.
def _normalize_path(path: str) -> str:
    import re

    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)


def _ga2_prefix(app: Flask) -> str | None:
    for rule in app.url_map.iter_rules():
        if "ga_research" in rule.endpoint and rule.rule.startswith("/api/"):
            # e.g. /api/v1/ga2/run
            parts = rule.rule.rstrip("/").split("/")
            if len(parts) >= 3 and parts[1] == "api":
                # Keep concrete prefix used by ga_research_bp
                maybe = "/" + "/".join(parts[1:3])
                # Heuristic: the first two segments define the prefix.
                return maybe if parts[2] != "v1" else maybe
    return None


def build_openapi_spec(app: Flask) -> dict[str, Any]:
    """Compose a valid OpenAPI 3.0 document from the live Flask route map."""
    paths: dict[str, Any] = {}
    for rule in app.url_map.iter_rules():
        raw = rule.rule
        if raw in {"/static/<path:filename>", "/<path:path>"}:
            continue
        methods = sorted(m for m in (rule.methods or set()) if m in {"GET", "POST", "PUT", "PATCH", "DELETE"})
        if not methods:
            continue
        norm = _normalize_path(raw)
        # GA2 blueprint uses an f-string prefix; enumerate the concrete prefix
        # discovered from the route map so Swagger shows real URLs.
        _ga2_real_path = "/api/v1/ga2/"
        if norm.startswith(_ga2_real_path) or "/ga2/" in norm:
            pass
        op_meta = OPERATIONS.get(raw) or OPERATIONS.get(norm) or {}
        path_item = paths.setdefault(norm, {})
        for method in methods:
            lowered = method.lower()
            meta = op_meta.get(lowered, {})
            if not meta and raw not in OPERATIONS and norm not in OPERATIONS:
                meta = {"summary": f"{method} {norm}", "description": f"Handler: {rule.endpoint}."}
            response_schema = {"type": "object", "properties": {"backend": {"type": "string", "example": "numpy"}}}
            operation: dict[str, Any] = {
                "operationId": f"{rule.endpoint.replace('.', '_')}_{lowered}",
                "summary": meta.get("summary", f"{method} {norm}"),
                "description": meta.get("description", ""),
                "tags": [raw.split("/")[2] if len(raw.split("/")) > 2 else "core"],
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {"application/json": {"schema": response_schema}},
                    },
                    "400": {
                        "description": "Client input error",
                        "content": {"application/json": {"schema": {"$ref": _ref("Error")}}},
                    },
                    "401": {"description": "Unauthorized (API_KEY configured)"},
                    "404": {
                        "description": "Not found",
                        "content": {"application/json": {"schema": {"$ref": _ref("Error")}}},
                    },
                    "500": {
                        "description": "Internal server error",
                        "content": {"application/json": {"schema": {"$ref": _ref("Error")}}},
                    },
                },
            }
            if meta.get("requestBody"):
                operation["requestBody"] = meta["requestBody"]
            if meta.get("x-request-examples"):
                operation["x-request-examples"] = meta["x-request-examples"]
            # Path parameters from the normalised template.
            params = []
            for seg in norm.split("/"):
                if seg.startswith("{") and seg.endswith("}"):
                    params.append({"name": seg[1:-1], "in": "path", "required": True, "schema": {"type": "string"}})
            if params:
                operation["parameters"] = params
            path_item[lowered] = operation

    # Resolve GA2 concrete paths if the handwritten keys used a different prefix.
    # Detect the real prefix from the route map and rename accordingly.
    ga2_real: list[str] = [p for p in paths if "/ga2/" in p or p.endswith("/run")]
    # If no ga2 paths matched the handwritten keys, the rename below is a no-op
    # because paths were already keyed by their real rule.rule above.
    _ = _ga2_prefix(app), ga2_real

    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "3D Paper Airplane Simulator API",
            "description": (
                "6DoF RK4 flight dynamics, low-Reynolds aerodynamics and GPU ensemble optimisation. "
                "Every success envelope carries `backend` (cupy / torch / numpy). "
                "Authentication is optional: when `API_KEY` is set, send `Authorization: Bearer <key>` or `X-API-Key`."
            ),
            "version": "3.0.0",
            "contact": {"name": "Engineering Team", "url": "https://github.com/minetan1229/air_simulation"},
            "license": {"name": "Proprietary"},
        },
        "servers": [{"url": "http://localhost:5000", "description": "Local development"}],
        "tags": [
            {"name": "aerodynamics", "description": "Lift / drag / polar"},
            {"name": "environment", "description": "Atmosphere and wind"},
            {"name": "stress", "description": "Structural stress"},
            {"name": "simulation", "description": "6DoF flight"},
            {"name": "mesh", "description": "3D model import"},
            {"name": "optimizer", "description": "Async optimisation jobs"},
            {"name": "jobs", "description": "Job registry"},
            {"name": "data", "description": "Export / import"},
            {"name": "meta", "description": "Schemas, health, physics references"},
            {"name": "system", "description": "GPU and system"},
            {"name": "origami", "description": "Procedural folds"},
            {"name": "records", "description": "Presets and records"},
            {"name": "guinness", "description": "Long-distance records"},
            {"name": "errors", "description": "Error log"},
            {"name": "investigate", "description": "Auto investigation"},
            {"name": "ga2", "description": "GA2 research runs"},
            {"name": "core", "description": "Health and UI"},
        ],
        "paths": paths,
        "components": {
            "schemas": COMMON_SCHEMAS,
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            },
        },
        "security": [{"ApiKeyAuth": []}],
    }
    return spec


SWAGGER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>3D Paper Airplane Simulator — API Docs</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
window.onload = function() {
  const ui = SwaggerUIBundle({
    url: '/api/openapi.json',
    dom_id: '#swagger-ui',
    deepLinking: true,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: 'StandaloneLayout'
  });
};
</script>
</body>
</html>
"""


def register_openapi_routes(app: Flask) -> None:
    """Attach ``GET /api/openapi.json`` and ``GET /docs`` to the Flask app."""

    @app.get("/api/openapi.json")
    def _openapi_json():  # type: ignore[no-untyped-def]
        return jsonify(build_openapi_spec(app))

    @app.get("/docs")
    def _swagger_ui():  # type: ignore[no-untyped-def]
        return render_template_string(SWAGGER_HTML)

    # Keep a versioned discoverable entry for tooling that expects v1.
    @app.get("/api/v1/openapi.json")
    def _openapi_json_v1():  # type: ignore[no-untyped-def]
        return jsonify(build_openapi_spec(app))


def write_openapi_json(path: str = "docs/api.openapi.json") -> str:
    """Write the spec to ``path`` (used by CI / docs pipelines)."""
    from app import app as flask_app  # lazy: avoids import cycle at module load

    spec = build_openapi_spec(flask_app)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def _main() -> None:
    out = write_openapi_json(os.environ.get("OPENAPI_OUT", "docs/api.openapi.json"))
    print(f"wrote {out} at {utc_now_iso()}")


if __name__ == "__main__":  # pragma: no cover - manual generation entry point
    _main()
