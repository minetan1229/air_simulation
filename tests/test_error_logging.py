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


def test_error_record_to_public_dict():
    rec_with_tb = ErrorRecord(
        id="err_pub_1",
        timestamp="2026-10-19T00:00:00Z",
        level="ERROR",
        category="api",
        message="Simulated crash",
        details={"route": "/step"},
        traceback_str="Traceback (most recent call last):\n  File 'secret.py'...",
        path="/step",
        method="POST",
        status_code=500,
        client_ip="192.168.1.50",
    )
    pub = rec_with_tb.to_public_dict()
    assert "traceback_str" not in pub
    assert "client_ip" not in pub
    assert pub["has_traceback"] is True
    assert pub["message"] == "Simulated crash"
    assert pub["id"] == "err_pub_1"

    rec_no_tb = ErrorRecord(
        id="err_pub_2",
        timestamp="2026-10-19T00:00:00Z",
        level="INFO",
        category="meta",
        message="Status check",
    )
    pub_no_tb = rec_no_tb.to_public_dict()
    assert "traceback_str" not in pub_no_tb
    assert "client_ip" not in pub_no_tb
    assert pub_no_tb["has_traceback"] is False


def test_public_statistics_removes_absolute_paths(temp_logger):
    stats = temp_logger.statistics()
    pub_stats = temp_logger.public_statistics()

    # Original statistics has full paths
    assert os.path.isabs(stats["log_files"]["text"]) or os.path.dirname(stats["log_files"]["text"])

    # Public statistics has only basenames
    assert pub_stats["log_files"]["text"] == "error.log"
    assert pub_stats["log_files"]["jsonl"] == "error.jsonl"
    assert "/" not in pub_stats["log_files"]["text"] and "\\" not in pub_stats["log_files"]["text"]


def test_api_records_errors_information_leakage():
    client = flask_app_module.app.test_client()

    # Ensure at least one error with traceback exists
    client.post("/api/v1/aerodynamics/calculate", json={"speed_mps": -99.0})

    resp = client.get("/api/v1/records/errors")
    assert resp.status_code == 200
    data = resp.get_json()

    for err in data.get("errors", []):
        assert "traceback_str" not in err, "Public error logs must not leak traceback_str"
        assert "client_ip" not in err, "Public error logs must not leak client_ip"
        assert "has_traceback" in err

    pub_files = data.get("statistics", {}).get("log_files", {})
    assert "/" not in pub_files.get("text", "") and "\\" not in pub_files.get("text", "")


def test_report_client_error_strict_validation():
    client = flask_app_module.app.test_client()

    # 1. Message exceeding 2000 characters
    long_msg = "X" * 2001
    resp_long = client.post("/api/v1/errors/report", json={"message": long_msg})
    assert resp_long.status_code == 400
    assert "must not exceed 2000 characters" in resp_long.get_json()["error"]

    # 2. Non-string message
    resp_type = client.post("/api/v1/errors/report", json={"message": 12345})
    assert resp_type.status_code == 400
    assert "'message' must be a string" in resp_type.get_json()["error"]

    # 3. Disallowed level
    resp_lvl = client.post("/api/v1/errors/report", json={"message": "ok", "level": "UNKNOWN_LEVEL"})
    assert resp_lvl.status_code == 400
    assert "'level' must be one of" in resp_lvl.get_json()["error"]

    # 4. Non-dict details
    resp_det = client.post("/api/v1/errors/report", json={"message": "ok", "details": "not_dict"})
    assert resp_det.status_code == 400
    assert "'details' must be a JSON object" in resp_det.get_json()["error"]

    # 5. Valid report
    resp_valid = client.post("/api/v1/errors/report", json={
        "message": "Valid error report",
        "level": "warning",
        "details": {"canvas": "threejs"},
    })
    assert resp_valid.status_code == 200
    assert resp_valid.get_json()["ok"] is True
    assert "error_id" in resp_valid.get_json()

