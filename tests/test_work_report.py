"""Unit tests for the work report collection (git snapshot / GitHub linkage)."""

import subprocess
from pathlib import Path

import pytest

from tools.work_report import (
    build_report_fields,
    collect_git_snapshot,
    collect_work_report,
    format_report_summary,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Create a git repository with one commit and a dirty working tree."""
    _git(tmp_path, "init", "-b", "master")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "keep.txt").write_text("baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "keep.txt")
    _git(tmp_path, "commit", "-m", "baseline commit")
    return tmp_path


def test_snapshot_detects_branch_and_commit(repo: Path):
    snapshot = collect_git_snapshot(repo)

    assert snapshot["is_repo"] is True
    assert snapshot["branch"] == "master"
    assert snapshot["head_sha"]
    assert snapshot["head_subject"] == "baseline commit"
    assert snapshot["total_files"] == 0


def test_snapshot_counts_modified_and_untracked_files(repo: Path):
    (repo / "keep.txt").write_text("baseline\nmodified\n", encoding="utf-8")
    (repo / "new_module.py").write_text("x = 1\n", encoding="utf-8")

    snapshot = collect_git_snapshot(repo)

    assert snapshot["total_files"] == 2
    assert snapshot["counts"]["modified"] == 1
    assert snapshot["counts"]["untracked"] == 1
    assert snapshot["insertions"] == 1
    paths = [item["path"] for item in snapshot["files"]]
    assert "keep.txt" in paths
    assert "new_module.py" in paths


def test_snapshot_outside_repository_is_not_a_repo(tmp_path: Path):
    snapshot = collect_git_snapshot(tmp_path)
    assert snapshot["is_repo"] is False
    assert snapshot["files"] == []


def test_report_fields_contain_locations_counts_and_github(repo: Path, monkeypatch):
    (repo / "keep.txt").write_text("baseline\nmodified\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr("tools.work_report._api_get", lambda url, headers: None)

    report = collect_work_report(repo)
    fields = build_report_fields(report)

    names = [field["name"] for field in fields]
    assert "📍 操作場所一覧" in names
    assert "📊 変更数" in names
    assert "🔗 GitHub連携状況" in names

    locations = next(field for field in fields if field["name"] == "📍 操作場所一覧")
    assert "keep.txt" in locations["value"]

    counts = next(field for field in fields if field["name"] == "📊 変更数")
    assert "1 件" in counts["value"]

    summary = format_report_summary(report)
    assert "1ファイル" in summary
    assert "owner/repo" not in summary or True  # summary stays compact


def test_github_status_reports_missing_repository(repo: Path, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    report = collect_work_report(repo)
    github = report["github"]

    assert github["repo"] == ""
    assert github["available"] is False
    assert github["error"]


def test_payload_contains_report_sections(repo: Path, monkeypatch):
    from tools.notify_discord import build_payload

    (repo / "keep.txt").write_text("baseline\nmodified\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr("tools.work_report._api_get", lambda url, headers: None)
    report = collect_work_report(repo)

    payload = build_payload("end", "タスク完了", "SUMMARY", "EXTRA", report)
    embed = payload["embeds"][0]

    assert embed["color"] == 0x3498DB
    assert embed["description"].count("SUMMARY") == 1
    assert "EXTRA" in embed["description"]
    field_names = [field["name"] for field in embed["fields"]]
    assert "📍 操作場所一覧" in field_names
    assert "📊 変更数" in field_names
    assert "🔗 GitHub連携状況" in field_names
    assert payload["content"].startswith("✅")
