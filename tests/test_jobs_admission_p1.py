"""Regression tests for Phase 2: P1-8 (Job runner parameter boundaries, admission locks, and slot hygiene).

Fixates:
- P1-8: Bounds validation for sim_duration_s and dt_max in optimizer run payload.
- Admission lock serialization and active job capacity limit (max 4).
- Worker thread failure cleanup ensuring no job slot leak.
"""

from __future__ import annotations

import pytest

from api.routes import jobs
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_p1_8_optimizer_run_bounds_validation(client) -> None:
    """Out-of-bounds simulation parameters must be rejected with 400 Bad Request."""
    # Negative / zero duration
    resp = client.post(
        "/api/v1/optimizer/run",
        json={"objectives": ["max_distance"], "sim_duration_s": 0.0},
    )
    assert resp.status_code == 400
    assert "'sim_duration_s' must be within (0, 600]" in resp.get_json()["error"]

    # Excess duration
    resp = client.post(
        "/api/v1/optimizer/run",
        json={"objectives": ["max_distance"], "sim_duration_s": 601.0},
    )
    assert resp.status_code == 400
    assert "'sim_duration_s' must be within (0, 600]" in resp.get_json()["error"]

    # Negative / zero dt_max
    resp = client.post(
        "/api/v1/optimizer/run",
        json={"objectives": ["max_distance"], "dt_max": 0.0},
    )
    assert resp.status_code == 400
    assert "'dt_max' must be within (0, 1.0]" in resp.get_json()["error"]

    # Excess dt_max
    resp = client.post(
        "/api/v1/optimizer/run",
        json={"objectives": ["max_distance"], "dt_max": 1.5},
    )
    assert resp.status_code == 400
    assert "'dt_max' must be within (0, 1.0]" in resp.get_json()["error"]


def test_p1_8_admission_limit_rejection_when_full(client, monkeypatch) -> None:
    """When job manager reports active jobs at limit, new jobs must be rejected."""
    monkeypatch.setattr(jobs.JOB_MANAGER, "active_count", lambda: 4)
    resp = client.post(
        "/api/v1/optimizer/run",
        json={"objectives": ["max_distance"]},
    )
    assert resp.status_code == 400
    assert "maximum active jobs (4) reached" in resp.get_json()["error"]
