"""Unit tests for Discord Webhook and environment variable loading."""

import json
import os
from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest

from tools.notify_discord import (
    AUTO_REPORT_TYPES,
    COLOR_MAP,
    get_webhook_url,
    load_env_file,
    send_notification,
)


def test_color_map_values():
    """Verify color mappings match the AGENTS.md conventions."""
    assert COLOR_MAP["plan"] == 0x9B59B6  # 紫 (計画)
    assert COLOR_MAP["start"] == 0x2ECC71  # 緑
    assert COLOR_MAP["subagent"] == 0xE67E22  # オレンジ
    assert COLOR_MAP["verify"] == 0x1ABC9C  # 青緑
    assert COLOR_MAP["github"] == 0x5865F2  # 藍
    assert COLOR_MAP["end"] == 0x3498DB  # 青
    assert COLOR_MAP["finish"] == 0x3498DB  # 青
    assert COLOR_MAP["fail"] == 0xE74C3C  # 赤
    assert COLOR_MAP["error"] == 0xE74C3C  # 赤
    assert COLOR_MAP["warning"] == 0xF1C40F  # 黄色
    assert COLOR_MAP["warn"] == 0xF1C40F  # 黄色
    assert COLOR_MAP["question"] == 0xE91E63  # ぴんく


def test_auto_report_types_cover_completion_statuses():
    """Completion oriented statuses must carry the work report."""
    assert {"end", "finish", "github", "commit", "push"} <= AUTO_REPORT_TYPES
    assert "start" not in AUTO_REPORT_TYPES
    assert "plan" not in AUTO_REPORT_TYPES


def test_load_env_file_parses_correctly(tmp_path):
    """Verify load_env_file loads variables into os.environ without overwriting existing."""
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "# Comment line\n"
        "TEST_WEBHOOK_KEY=https://example.com/webhook/test\n"
        "QUOTED_KEY=\"quoted_value\"\n"
        "EMPTY_KEY=\n",
        encoding="utf-8",
    )

    # Clean env before test
    os.environ.pop("TEST_WEBHOOK_KEY", None)
    os.environ.pop("QUOTED_KEY", None)

    load_env_file(env_file)

    assert os.environ.get("TEST_WEBHOOK_KEY") == "https://example.com/webhook/test"
    assert os.environ.get("QUOTED_KEY") == "quoted_value"

    # Cleanup
    os.environ.pop("TEST_WEBHOOK_KEY", None)
    os.environ.pop("QUOTED_KEY", None)


def test_get_webhook_url_from_env():
    """Verify get_webhook_url retrieves from environment."""
    with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/test_url"}):
        url = get_webhook_url()
        assert url == "https://discord.com/api/test_url"


def test_get_webhook_url_unconfigured():
    """Verify get_webhook_url returns None when no env or file exists."""
    with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": ""}, clear=True):
        # Override load_env_file to do nothing
        with patch("tools.notify_discord.load_env_file"):
            url = get_webhook_url()
            assert url is None


def test_send_notification_skipped_when_no_url(capsys):
    """Verify send_notification gracefully skips when URL is not configured."""
    with patch("tools.notify_discord.get_webhook_url", return_value=None):
        result = send_notification("start", "Test Title", "Test Summary")
        assert result is False
        captured = capsys.readouterr()
        assert "Notice: DISCORD_WEBHOOK_URL is not set" in captured.out


def test_send_notification_payload_structure():
    """Verify correct payload and HTTP request are dispatched."""
    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_resp.__enter__.return_value = mock_resp

    with patch("tools.notify_discord.get_webhook_url", return_value="https://discord.com/api/mock_webhook"), \
         patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = send_notification(
            status_type="start",
            title="Start Test",
            summary="Testing payload",
            extra_info="Extra details here",
        )
        assert result is True
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://discord.com/api/mock_webhook"
        assert req.headers["Content-type"] == "application/json"

        body = json.loads(req.data.decode("utf-8"))
        assert len(body["embeds"]) == 1
        embed = body["embeds"][0]
        assert embed["title"] == "Start Test"
        assert embed["color"] == 0x2ECC71
        assert "Testing payload" in embed["description"]
        assert "Extra details here" in embed["description"]


def test_send_notification_attaches_report_for_completion():
    """Completion notifications must embed operation locations, counts and GitHub status."""
    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_resp.__enter__.return_value = mock_resp

    fake_report = {
        "snapshot": {
            "is_repo": True,
            "branch": "feat/x",
            "files": [{"path": "tools/a.py", "category": "modified", "code": " M"}],
            "counts": {"modified": 1, "untracked": 0},
            "insertions": 5,
            "deletions": 2,
            "total_files": 1,
        },
        "github": {"repo": "owner/repo", "ci_status": "成功 (#12)", "open_prs": 1},
    }

    with patch("tools.notify_discord.get_webhook_url", return_value="https://discord.com/api/mock"), \
         patch("tools.notify_discord.collect_work_report", return_value=fake_report) as mock_collect, \
         patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = send_notification("end", "完了", "すべての変更が完了しました")

    assert result is True
    mock_collect.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    body = json.loads(req.data.decode("utf-8"))
    embed = body["embeds"][0]
    field_names = [field["name"] for field in embed["fields"]]
    assert "📍 操作場所一覧" in field_names
    assert "📊 変更数" in field_names
    assert "🔗 GitHub連携状況" in field_names


def test_send_notification_skips_report_when_disabled():
    """`--no-report` style calls must not collect any report."""
    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_resp.__enter__.return_value = mock_resp

    with patch("tools.notify_discord.get_webhook_url", return_value="https://discord.com/api/mock"), \
         patch("tools.notify_discord.collect_work_report") as mock_collect, \
         patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = send_notification("end", "完了", "要約", auto_report=False)

    assert result is True
    mock_collect.assert_not_called()
    req = mock_urlopen.call_args[0][0]
    body = json.loads(req.data.decode("utf-8"))
    assert "fields" not in body["embeds"][0]


def test_plan_status_uses_purple_color():
    """Plan notifications must be visually distinct from the green start notification."""
    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_resp.__enter__.return_value = mock_resp

    with patch("tools.notify_discord.get_webhook_url", return_value="https://discord.com/api/mock"), \
         patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = send_notification("plan", "計画", "実装計画を確定しました")

    assert result is True
    req = mock_urlopen.call_args[0][0]
    body = json.loads(req.data.decode("utf-8"))
    assert body["embeds"][0]["color"] == 0x9B59B6
    assert "🗺️" in body["embeds"][0]["description"]
