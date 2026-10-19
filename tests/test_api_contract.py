"""SPECIFICATION 3: the REST surface must match the documented contract.

The implementation had drifted from the specification: five endpoints were
only reachable under a different path, so a client written against the
specification received ``404 Not Found``.  The specification paths are now
served as aliases of the existing ones.

This module asserts the contract itself, so the drift cannot come back.
"""

from __future__ import annotations

import pytest

from app import create_app


#: Every endpoint mandated by SPECIFICATION 3, as a Flask route pattern.
SPEC_ROUTES = (
    # 3.1 meta
    ("GET", "/api/v1/meta/health"),
    ("GET", "/api/v1/meta/schema"),
    ("GET", "/api/v1/meta/physics-references"),
    ("GET", "/api/v1/meta/test-fault"),
    # 3.2 physics
    ("POST", "/api/v1/aerodynamics/calculate"),
    ("POST", "/api/v1/aerodynamics/polar"),
    ("POST", "/api/v1/environment/atmosphere"),
    ("GET", "/api/v1/environment/current"),
    ("POST", "/api/v1/wind/field"),
    ("POST", "/api/v1/stress/analysis"),
    # 3.3 simulation
    ("POST", "/api/v1/simulation/step"),
    ("POST", "/api/v1/simulation/reset"),
    ("POST", "/api/v1/simulation/batch"),
    # 3.4 mesh
    ("POST", "/api/v1/mesh/import"),
    ("GET", "/api/v1/mesh/models"),
    ("POST", "/api/v1/mesh/select"),
    ("GET", "/api/v1/mesh/metrics/<model_id>"),
    # 3.5 jobs
    ("POST", "/api/v1/optimizer/run"),
    ("GET", "/api/v1/optimizer/status/<job_id>"),
    ("POST", "/api/v1/optimizer/cancel/<job_id>"),
    ("GET", "/api/v1/optimizer/result/<job_id>"),
    ("POST", "/api/v1/jobs/investigate"),
    ("GET", "/api/v1/jobs"),
    # 3.6 records
    ("GET", "/api/v1/records/presets"),
    ("GET", "/api/v1/records/preset/<name>"),
    ("GET", "/api/v1/records/guinness"),
    ("POST", "/api/v1/data/export"),
    ("POST", "/api/v1/data/import"),
    ("GET", "/api/v1/records/errors"),
)


#: Endpoints that are safe to call in a test: they neither start a background
#: job nor run a parameter sweep.  The remaining documented endpoints are
#: covered by the registration check alone.
PROBE_ENDPOINTS = (
    ("GET", "/api/v1/meta/health"),
    ("GET", "/api/v1/meta/schema"),
    ("GET", "/api/v1/meta/physics-references"),
    ("POST", "/api/v1/aerodynamics/calculate"),
    ("POST", "/api/v1/aerodynamics/polar"),
    ("POST", "/api/v1/environment/atmosphere"),
    ("GET", "/api/v1/environment/current"),
    ("POST", "/api/v1/wind/field"),
    ("POST", "/api/v1/stress/analysis"),
    ("POST", "/api/v1/simulation/reset"),
    ("GET", "/api/v1/simulation/history"),
    ("GET", "/api/v1/mesh/models"),
    ("GET", "/api/v1/records/presets"),
    ("GET", "/api/v1/records/preset/unite_31s"),
    ("GET", "/api/v1/records/guinness"),
    ("POST", "/api/v1/data/export"),
    ("POST", "/api/v1/data/import"),
    ("GET", "/api/v1/records/errors"),
    ("GET", "/api/v1/jobs"),
)


@pytest.fixture(scope="module")
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def _normalise(pattern: str) -> str:
    """Replace the path parameter names so the URL *shape* is compared.

    The specification writes ``<name>`` where the implementation uses
    ``<key>``; the parameter name is an internal detail, the URL shape is the
    contract.
    """
    import re

    return re.sub(r"<[^<>]+>", "<param>", pattern)


def _registered(app, method: str, pattern: str) -> bool:
    wanted = _normalise(pattern)
    for rule in app.url_map.iter_rules():
        if rule.methods is None or method not in rule.methods:
            continue
        if _normalise(str(rule.rule)) == wanted:
            return True
    return False


@pytest.mark.parametrize("method,pattern", SPEC_ROUTES)
def test_specification_endpoint_is_registered(app, method, pattern):
    """Every endpoint in SPECIFICATION 3 must be routable at its documented path."""
    assert _registered(app, method, pattern), f"{method} {pattern} is not registered"


@pytest.mark.parametrize("method,path", PROBE_ENDPOINTS)
def test_specification_endpoint_does_not_return_404(client, method, path):
    """A documented endpoint must never answer with "not found"."""
    if method == "GET":
        response = client.get(path)
    else:
        response = client.post(path, json={})
    assert response.status_code != 404, f"{method} {path} answered 404"


def test_legacy_paths_are_still_served(client):
    """The aliases must not replace the paths the existing clients use."""
    legacy = (
        ("GET", "/api/v1/meta/schemas"),
        ("GET", "/api/v1/records/presets/unite_31s"),
        ("GET", "/api/v1/guinness/verification"),
        ("GET", "/api/v1/errors"),
    )
    for method, path in legacy:
        if method == "GET":
            response = client.get(path)
        else:
            response = client.post(path, json={})
        assert response.status_code != 404, f"{method} {path} answered 404"


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/optimizer/run",
        "/api/v1/jobs/investigate",
    ),
)
def test_invalid_configuration_is_rejected_with_400(client, path):
    """A malformed overlay is a client error, not an internal server error."""
    response = client.post(path, json={"aircraft": "not-a-mapping"})
    assert response.status_code == 400, response.get_data(as_text=True)
    assert "error" in response.get_json()
