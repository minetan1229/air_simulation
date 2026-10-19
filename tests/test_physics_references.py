"""Issue #32: the academic reference registry must be published and verified.

SPECIFICATION 6 claims the ``/api/v1/meta/physics-references`` endpoint is
covered by a unit test.  No test referenced the endpoint at all, so the claim
was unsupported.
"""

from __future__ import annotations

import pytest

from app import create_app


#: Every citation the specification lists in section 5.
REQUIRED_REFERENCE_IDS = (
    "helmbold_1942",
    "polhamus_1966",
    "viterna_corrigan_1982",
    "hoerner_1965",
    "wieselsberger_1922",
    "sutherland_1893",
)

#: The AeroSandbox entry point has no dedicated id; it is referenced from the
#: individual entries instead.
AEROSANDBOX_MARKER = "aerosandbox"


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_physics_references_endpoint_is_public(client):
    response = client.get("/api/v1/meta/physics-references")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["count"] == len(body["references"])
    assert body["count"] >= len(REQUIRED_REFERENCE_IDS)


def test_physics_references_cover_every_cited_source(client):
    body = client.get("/api/v1/meta/physics-references").get_json()
    ids = [entry["id"] for entry in body["references"]]
    for required in REQUIRED_REFERENCE_IDS:
        assert required in ids, f"missing reference '{required}'"


def test_physics_references_entries_are_complete(client):
    """Every entry must carry the fields a citation needs."""
    body = client.get("/api/v1/meta/physics-references").get_json()
    for entry in body["references"]:
        assert entry["id"]
        assert entry["domain"]
        assert entry["model"]
        assert entry["equation"]
        assert isinstance(entry["year"], int)
        assert entry["authors"]
        assert entry["title"]
        assert entry["publication"]
        assert AEROSANDBOX_MARKER in entry["aerosandbox_reference"]


def test_physics_references_are_unique(client):
    body = client.get("/api/v1/meta/physics-references").get_json()
    ids = [entry["id"] for entry in body["references"]]
    assert len(ids) == len(set(ids))


def test_physics_references_cite_the_governing_models(client):
    """The registry must describe the models the simulator actually uses."""
    body = client.get("/api/v1/meta/physics-references").get_json()
    by_id = {entry["id"]: entry for entry in body["references"]}

    assert "AR" in by_id["helmbold_1942"]["equation"]
    assert "K_p" in by_id["polhamus_1966"]["equation"]
    assert "K_v" in by_id["polhamus_1966"]["equation"]
    assert by_id["wieselsberger_1922"]["year"] >= 1920
    assert by_id["sutherland_1893"]["year"] == 1893
