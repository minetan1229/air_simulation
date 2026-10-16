"""Unit tests for Discord Webhook and environment variable loading."""

import json
import os
from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest

from tools.notify_discord import (
    COLOR_MAP,
    get_webhook_url,
    load_env_file,
    send_notification,
)


def test_color_map_values():
    """Verify color mappings match the AGENTS.md conventions."""
    assert COLOR_MAP["start"] == 0x2ECC71  # 緑
    assert COLOR_MAP["subagent"] == 0xE67E22  # オレンジ
    assert COLOR_MAP["end"] == 0x3498DB  # 青
    assert COLOR_MAP["finish"] == 0x3498DB  # 青
    assert COLOR_MAP["fail"] == 0xE74C3C  # 赤
    assert COLOR_MAP["error"] == 0xE74C3C  # 赤
    assert COLOR_MAP["warning"] == 0xF1C40F  # 黄色
    assert COLOR_MAP["warn"] == 0xF1C40F  # 黄色
    assert COLOR_MAP["question"] == 0xE91E63  # ぴんく


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
