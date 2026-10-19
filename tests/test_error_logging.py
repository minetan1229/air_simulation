"""Tests for physics.error_domain and API error logging."""

import json
import os
import pytest
from flask import Flask
from physics import error_domain
from physics.error_domain import ErrorLoggerDomain, ErrorRecord
import app as flask_app_module


@pytest.fixture
def temp_logger(tmp_path):
    log_dir = str(tmp_path / "test_logs")
    return ErrorLoggerDomain(log_dir=log_dir, max_buffer_size=10)


def test_error_record_creation_and_to_dict():
    rec = ErrorRecord(
        id="err_123",
        timestamp="2026-10-17T00:00:00Z",
        level="ERROR",
        category="physics",
        message="Stall divergence detected",
        details={"alpha_deg": 35.0},
        traceback_str="Traceback...",
        path="/api/v1/simulation/step",
        method="POST",
        status_code=400,
        client_ip="127.0.0.1",
    )
    d = rec.to_dict()
    assert d["id"] == "err_123"
    assert d["level"] == "ERROR"
    assert d["category"] == "physics"
    assert d["details"]["alpha_deg"] == 35.0
    assert d["status_code"] == 400


def test_logger_persists_to_file_and_buffer(temp_logger):
    try:
        raise ValueError("Invalid wing area")
    except ValueError as exc:
        rec = temp_logger.record_error(
            message="Validation error",
            level="WARNING",
            category="validation",
            exc=exc,
            details={"area": -0.5},
            path="/api/v1/aerodynamics/calculate",
            method="POST",
            status_code=400,
        )

    assert rec.message == "Validation error"
    assert "ValueError: Invalid wing area" in (rec.traceback_str or "")

    # Check memory buffer
    logs = temp_logger.get_logs()
    assert len(logs) == 1
    assert logs[0]["id"] == rec.id

    # Check files created
    assert os.path.exists(temp_logger.text_log_path)
    assert os.path.exists(temp_logger.jsonl_log_path)

    with open(temp_logger.jsonl_log_path, "r", encoding="utf-8") as f:
        line = f.readline()
        data = json.loads(line)
        assert data["id"] == rec.id
        assert data["level"] == "WARNING"


def test_logger_ring_buffer_limit(temp_logger):
    for i in range(15):
        temp_logger.record_error(f"Error {i}")

    logs = temp_logger.get_logs(limit=100)
    assert len(logs) == 10  # max_buffer_size was set to 10
    # Most recent first
    assert logs[0]["message"] == "Error 14"
    assert logs[-1]["message"] == "Error 5"


def test_logger_filters(temp_logger):
    temp_logger.record_error("Alpha error", level="WARNING", category="physics")
    temp_logger.record_error("Beta crash", level="CRITICAL", category="flight")
    temp_logger.record_error("Gamma not found", level="INFO", category="routing")

    # Filter by level
    warns = temp_logger.get_logs(level="WARNING")
    assert len(warns) == 1
    assert warns[0]["message"] == "Alpha error"

    # Filter by category
    flights = temp_logger.get_logs(category="flight")
    assert len(flights) == 1
    assert flights[0]["message"] == "Beta crash"

    # Search text
    results = temp_logger.get_logs(search="crash")
    assert len(results) == 1
    assert results[0]["message"] == "Beta crash"


def test_logger_clear_and_stats(temp_logger):
    temp_logger.record_error("Err 1", level="ERROR", category="physics")
    temp_logger.record_error("Err 2", level="ERROR", category="api")
    temp_logger.record_error("Warn 1", level="WARNING", category="api")

    stats = temp_logger.statistics()
    assert stats["total_in_buffer"] == 3
    assert stats["by_level"]["ERROR"] == 2
    assert stats["by_level"]["WARNING"] == 1
    assert stats["by_category"]["physics"] == 1
    assert stats["by_category"]["api"] == 2

    temp_logger.clear()
    assert len(temp_logger.get_logs()) == 0
    assert temp_logger.statistics()["total_in_buffer"] == 0


def test_api_error_logging_endpoints():
    client = flask_app_module.app.test_client()

    # Trigger a 400 bad request error (negative speed)
    res = client.post("/api/v1/aerodynamics/calculate", json={"speed_mps": -10.0})
    assert res.status_code == 400
    data = res.get_json()
    assert "error" in data
    assert "error_id" in data

    # Trigger a 405 / routing error
    res_err = client.get("/api/v1/aerodynamics/calculate")
    assert res_err.status_code == 405
    data_err = res_err.get_json()
    assert "error_id" in data_err

    # Post a client-side error
    res_client = client.post("/api/v1/errors/report", json={
        "message": "WebGL shader compile error",
        "level": "ERROR",
        "category": "webgl",
        "stack": "Error at initShader()",
        "details": {"gpu": "Test GPU"},
    })
    assert res_client.status_code == 200
    assert res_client.get_json()["ok"] is True

    # Retrieve error list
    res_list = client.get("/api/v1/errors?limit=50")
    assert res_list.status_code == 200
    errors_data = res_list.get_json()
    assert "errors" in errors_data
    assert any("WebGL shader" in e["message"] for e in errors_data["errors"])

    # Retrieve stats
    res_stats = client.get("/api/v1/errors/stats")
    assert res_stats.status_code == 200
    assert "by_level" in res_stats.get_json()


def test_error_endpoints_do_not_republish_stack_traces_or_server_paths():
    """SPECIFICATION 4.1: the REST layer must not leak what the 500 handler hides.

    The 500 handler deliberately returns only an ``error_id``.  The diagnostic
    endpoints used to re-publish the very same stack traces -- and the absolute
    path of the server log directory -- to any client.
    """
    client = flask_app_module.app.test_client()

    secret = "SENSITIVE_INTERNAL_DETAIL_DO_NOT_PUBLISH"
    res_report = client.post(
        "/api/v1/errors/report",
        json={
            "message": "shader failure",
            "level": "ERROR",
            "category": "webgl",
            "stack": f"Traceback (most recent call last):\n  {secret}",
            "details": {"gpu": "Test GPU"},
        },
    )
    assert res_report.status_code == 200

    res_list = client.get("/api/v1/errors?limit=50")
    assert res_list.status_code == 200
    raw = res_list.get_data(as_text=True)
    assert secret not in raw
    assert "Traceback (most recent call last)" not in raw
    for entry in res_list.get_json()["errors"]:
        assert "traceback_str" not in entry
        assert "client_ip" not in entry
        assert isinstance(entry["has_traceback"], bool)

    res_stats = client.get("/api/v1/errors/stats")
    assert res_stats.status_code == 200
    stats_raw = res_stats.get_data(as_text=True)
    files = res_stats.get_json()["log_files"]
    # Only the file names are published, never the server directory.
    assert os.sep not in files["text"]
    assert os.sep not in files["jsonl"]
    assert "logs" + os.sep not in stats_raw


def test_error_report_rejects_invalid_payloads():
    """A client must not be able to inject arbitrary content into the audit log."""
    client = flask_app_module.app.test_client()

    res_level = client.post("/api/v1/errors/report", json={"message": "x", "level": "NOT_A_LEVEL"})
    assert res_level.status_code == 400

    res_details = client.post("/api/v1/errors/report", json={"message": "x", "details": "not-a-mapping"})
    assert res_details.status_code == 400

    res_ok = client.post("/api/v1/errors/report", json={"message": "x", "level": "WARNING"})
    assert res_ok.status_code == 200
    assert res_ok.get_json()["ok"] is True
