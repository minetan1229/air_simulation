"""Flask REST API entry point for the 3D paper-airplane simulator.

Integrates every physics domain behind the endpoints declared in AGENTS.md
section 10 / prompt.md section 7.
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
import threading
import traceback
from typing import Any, Dict, Optional, Tuple

import dotenv

dotenv.load_dotenv()

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from physics.config_schema import utc_now_iso
from api.core import (
    BadRequest,
    NotFound,
    SESSION,
    ServiceUnavailable,
    api_error,
    domain,
)
from api.routes import (
    ga_research_bp,
    jobs_bp,
    mesh_bp,
    meta_bp,
    origami_bp,
    physics_bp,
    records_bp,
    sim_bp,
)
from tools.branch_guard import BranchViolation, assert_branch_allowed, describe_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
)
log = logging.getLogger("paper_airplane")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024


# Pre-warm GPU kernels in background on app load
def _kick_gpu_warmup() -> None:
    try:
        from physics import parallel_engine
        threading.Thread(target=parallel_engine.warmup_gpu, daemon=True).start()
    except Exception:
        pass


_kick_gpu_warmup()


# ---------------------------------------------------------------------------
# Backward-compatibility alias for internal testing / references
# ---------------------------------------------------------------------------
_domain = domain
_error = api_error


# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(BadRequest)
def _handle_bad_request(exc: BadRequest):
    return api_error(str(exc), 400, exc=exc, level="WARNING", category="client_input")


@app.errorhandler(NotFound)
def _handle_not_found(exc: NotFound):
    return api_error(str(exc), 404, exc=exc, level="INFO", category="routing")


@app.errorhandler(ServiceUnavailable)
def _handle_unavailable(exc: ServiceUnavailable):
    return api_error(str(exc), 503, exc=exc, level="ERROR", category="backend")


@app.errorhandler(404)
def _handle_404(_exc):
    return api_error("resource not found", 404, level="INFO", category="routing")


@app.errorhandler(405)
def _handle_405(_exc):
    return api_error("method not allowed for this endpoint", 405, level="INFO", category="routing")


@app.errorhandler(ValueError)
def _handle_value_error(exc: ValueError):
    log.warning("ValueError on %s: %s", request.path, exc)
    return api_error(f"invalid input: {exc}", 400, exc=exc, level="WARNING", category="validation")


@app.errorhandler(OverflowError)
def _handle_overflow(exc: OverflowError):
    log.warning("OverflowError on %s: %s", request.path, exc)
    return api_error(f"numeric overflow for the given input: {exc}", 400, exc=exc, level="WARNING", category="numeric")


@app.errorhandler(ZeroDivisionError)
def _handle_zero_division(exc: ZeroDivisionError):
    log.warning("ZeroDivisionError on %s: %s", request.path, exc)
    return api_error(f"degenerate input caused a division by zero: {exc}", 400, exc=exc, level="WARNING", category="numeric")


@app.errorhandler(HTTPException)
def _handle_http_exception(exc: HTTPException):
    code = exc.code or 500
    lvl = "ERROR" if code >= 500 else "WARNING"
    return api_error(exc.description or str(exc), code, exc=exc, level=lvl, category="http")


@app.errorhandler(Exception)
def _handle_unexpected(exc: Exception):
    tb = traceback.format_exc()
    log.error("unhandled error on %s: %s\n%s", request.path, exc, tb)
    return api_error("an unexpected internal server error occurred", 500, exc=exc, traceback_str=tb, level="CRITICAL", category="unhandled")


# ---------------------------------------------------------------------------
# Authentication Middleware
# ---------------------------------------------------------------------------
@app.before_request
def _check_authentication() -> Optional[Tuple[Response, int]]:
    """Validate Bearer token / API Key on protected API endpoints when API_KEY is configured."""
    api_key = os.environ.get("API_KEY", "").strip()
    if not api_key:
        return None  # Auth is optional when API_KEY is not configured

    # Exempt public, health check, UI and diagnostic routes
    # Note: /api/v1/system/gpu (read-only) is exempt, while /api/v1/system/force-gpu-render is protected.
    path = request.path or "/"
    if (
        request.method == "OPTIONS"
        or path in ("/", "/healthz", "/healthz/ready", "/healthz/live", "/api/v1/system/gpu")
        or path.startswith(("/static/", "/api/v1/meta/"))
    ):
        return None

    auth_header = request.headers.get("Authorization", "").strip()
    token = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    elif request.headers.get("X-API-Key"):
        token = request.headers.get("X-API-Key", "").strip()

    # Compare the UTF-8 bytes: hmac.compare_digest rejects non-ASCII str
    # arguments with a TypeError, which would turn a malformed client token
    # into a 500 instead of a 401.  The digest itself stays constant time.
    if not token or not hmac.compare_digest(token.encode("utf-8"), api_key.encode("utf-8")):
        return api_error("Unauthorized: invalid or missing API Key / Bearer token", 401, category="security")
    return None


# ---------------------------------------------------------------------------
# Security & CORS headers
# ---------------------------------------------------------------------------
def _is_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    # Standard local origins
    if (
        origin.startswith("http://127.0.0.1:")
        or origin.startswith("http://localhost:")
        or origin.startswith("https://127.0.0.1:")
        or origin.startswith("https://localhost:")
        or origin in ("http://127.0.0.1", "http://localhost")
    ):
        return True

    # User configured allowed origins from ALLOWED_ORIGINS env
    allowed_list = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return origin in allowed_list


@app.after_request
def _add_security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: https://api.iconify.design; "
        "connect-src 'self' ws: wss:;"
    )
    origin = request.headers.get("Origin", "")
    allowed = _is_origin_allowed(origin)
    if "Access-Control-Allow-Origin" not in response.headers:
        response.headers["Access-Control-Allow-Origin"] = origin if allowed else "http://127.0.0.1:5000"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, DELETE"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Simulation-Session, X-API-Key"
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def _handle_options(path: str = ""):
    resp = Response(status=204)
    origin = request.headers.get("Origin", "")
    allowed = _is_origin_allowed(origin)
    resp.headers["Access-Control-Allow-Origin"] = origin if allowed else "http://127.0.0.1:5000"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Simulation-Session, X-API-Key"
    return resp


# ---------------------------------------------------------------------------
# UI & Health Check Entry Points
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/healthz/live")
def healthz_live():
    """Liveness probe: verifies process is responding."""
    return jsonify({
        "status": "alive",
        "timestamp": utc_now_iso(),
    })


@app.get("/healthz/ready")
def healthz_ready():
    """Readiness probe: checks memory, active job capacity, and domain readiness."""
    from physics.job_manager import JOB_MANAGER
    gpu_info = domain("parallel_engine").get_gpu_info()
    active_jobs = JOB_MANAGER.active_count()
    ready = active_jobs < 4
    return jsonify({
        "status": "ready" if ready else "busy",
        "ready": ready,
        "active_jobs": active_jobs,
        "max_jobs": 4,
        "gpu": gpu_info,
        "timestamp": utc_now_iso(),
    }), (200 if ready else 503)


# ---------------------------------------------------------------------------
# Blueprint Registration
# ---------------------------------------------------------------------------
app.register_blueprint(meta_bp)
app.register_blueprint(origami_bp)
app.register_blueprint(physics_bp)
app.register_blueprint(sim_bp)
app.register_blueprint(mesh_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(records_bp)
app.register_blueprint(ga_research_bp)


# ---------------------------------------------------------------------------
# App Factory & CLI Runner
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper airplane CFD simulator API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--allow-protected-branch",
        action="store_true",
        help="run even when the checked-out branch is master/main",
    )
    args = parser.parse_args()

    # Branch guard: the genetic-project phase is isolated from master.  The
    # server must not be started from a protected branch unless the operator
    # explicitly overrides it.
    try:
        status = assert_branch_allowed(allow_protected=bool(args.allow_protected_branch))
    except BranchViolation as exc:
        log.error("branch guard: %s", exc)
        raise SystemExit(2) from exc
    log.info("branch guard: %s", describe_status(status))

    try:
        parallel = domain("parallel_engine")
        threading.Thread(target=parallel.warmup_gpu, daemon=True).start()
    except Exception as exc:
        log.warning("GPU warmup thread skipped: %s", exc)

    log.info("starting on http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
