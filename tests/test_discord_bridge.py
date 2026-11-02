"""
Unit and functional tests for Discord Remote Bridge
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import discord
    from tools.discord_bridge import (
        AVAILABLE_MODELS,
        DiscordBridgeClient,
        ModelSelectView,
        PromptReplyModal,
        SessionReplyView,
    )
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridge(unittest.TestCase):
    def setUp(self):
        self.client = DiscordBridgeClient()

    def test_available_models(self):
        self.assertIn("gemini-3.8-flash-low", AVAILABLE_MODELS)
        self.assertIn("claude-sonnet-4-6", AVAILABLE_MODELS)
        self.assertIn("claude-opus-4-6-thinking", AVAILABLE_MODELS)

    def test_authorization_logic_empty(self):
        # When ALLOWED_USER_IDS is empty, NO user is authorized (safe default deny-all)
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", set()), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", set()):
            self.assertFalse(self.client.is_authorized(12345, 67890))

    def test_authorization_logic_empty_denies_channel_only(self):
        # Empty user allowlist denies even when channel allowlist is set
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", set()), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", {888}):
            self.assertFalse(self.client.is_authorized(111, 888))

    def test_rate_limit_allows_burst_then_denies(self):
        from tools.discord_bridge import rate_limit_check
        with patch("tools.discord_bridge.RATE_LIMIT_MAX_COMMANDS", 3), \
             patch("tools.discord_bridge._user_command_times", {}):
            self.assertTrue(rate_limit_check(1))
            self.assertTrue(rate_limit_check(1))
            self.assertTrue(rate_limit_check(1))
            self.assertFalse(rate_limit_check(1))
            self.assertFalse(rate_limit_check(1))

    def test_rate_limit_window_expires(self):
        from tools.discord_bridge import rate_limit_check
        with patch("tools.discord_bridge.RATE_LIMIT_MAX_COMMANDS", 1), \
             patch("tools.discord_bridge.RATE_LIMIT_WINDOW_SECONDS", 0.01), \
             patch("tools.discord_bridge._user_command_times", {}), \
             patch("tools.discord_bridge.time.time", side_effect=[0.0, 0.0, 0.02]):
            self.assertTrue(rate_limit_check(1))
            self.assertFalse(rate_limit_check(1))
            self.assertTrue(rate_limit_check(1))

    def test_chrome_url_validation(self):
        from tools.discord_bridge import is_chrome_url_allowed
        self.assertTrue(is_chrome_url_allowed("http://localhost:5000"))
        self.assertTrue(is_chrome_url_allowed("https://example.com/page"))
        self.assertFalse(is_chrome_url_allowed("ftp://example.com"))
        self.assertFalse(is_chrome_url_allowed("http://127.0.0.1/evil"))
        self.assertFalse(is_chrome_url_allowed("http://10.0.0.1/evil"))
        self.assertFalse(is_chrome_url_allowed("http://169.254.169.254/meta"))
        self.assertFalse(is_chrome_url_allowed("http://metadata.google.internal/x"))
        self.assertFalse(is_chrome_url_allowed("not-a-url"))

    def test_authorization_logic_restricted_user(self):
        # When ALLOWED_USER_IDS is set, only authorized user is allowed
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", {111, 222}), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", set()):
            self.assertTrue(self.client.is_authorized(111, 999))
            self.assertFalse(self.client.is_authorized(333, 999))

    def test_authorization_logic_restricted_channel(self):
        # When ALLOWED_USER_IDS is set and channel allowlist is set, only authorized user+channel allowed
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", {111}), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", {888}):
            self.assertTrue(self.client.is_authorized(111, 888))
            self.assertFalse(self.client.is_authorized(111, 999))  # wrong channel
            self.assertFalse(self.client.is_authorized(333, 888))  # wrong user

    def test_model_switching_per_channel(self):
        channel_a = 1001
        channel_b = 1002

        # Default model
        self.assertEqual(self.client.get_model(channel_a), "gemini-3.8-flash-low")

        # Change for channel A
        self.client.set_model(channel_a, "claude-sonnet-4-6")
        self.assertEqual(self.client.get_model(channel_a), "claude-sonnet-4-6")
        # Channel B remains default
        self.assertEqual(self.client.get_model(channel_b), "gemini-3.8-flash-low")

    def test_model_select_view(self):
        callback = AsyncMock()
        view = ModelSelectView("gemini-3.8-flash-low", callback)
        self.assertEqual(len(view.children), 1)
        select_item = view.children[0]
        self.assertEqual(len(select_item.options), len(AVAILABLE_MODELS))
        # Ensure default option is selected
        default_opts = [opt for opt in select_item.options if opt.default]
        self.assertEqual(len(default_opts), 1)
        self.assertEqual(default_opts[0].value, "gemini-3.8-flash-low")

    def test_parallel_task_registration_and_abort(self):
        channel_id = 999
        mock_proc1 = MagicMock()
        mock_proc1.returncode = None
        mock_proc2 = MagicMock()
        mock_proc2.returncode = None

        self.client.register_task(channel_id, "task_1", mock_proc1)
        self.client.register_task(channel_id, "task_2", mock_proc2)

        self.assertEqual(len(self.client.active_processes), 2)
        self.assertEqual(len(self.client.channel_tasks[channel_id]), 2)

        # Abort one task
        aborted = self.client.abort_tasks(task_id="task_1")
        self.assertEqual(aborted, 1)
        mock_proc1.kill.assert_called_once()

        # Abort channel tasks
        aborted_ch = self.client.abort_tasks(channel_id=channel_id)
        self.assertEqual(aborted_ch, 2)
        mock_proc2.kill.assert_called_once()

        # Unregister task
        self.client.unregister_task(channel_id, "task_1")
        self.client.unregister_task(channel_id, "task_2")
        self.assertEqual(len(self.client.active_processes), 0)
        self.assertNotIn(channel_id, self.client.channel_tasks)

    def test_get_system_metrics(self):
        from tools.discord_bridge import get_system_metrics
        metrics = get_system_metrics()
        self.assertIn("cpu", metrics)
        self.assertIn("memory", metrics)
        self.assertIn("gpu", metrics)
        self.assertIsInstance(metrics["cpu"], str)
        self.assertIsInstance(metrics["memory"], str)

    def test_session_reply_view_structure(self):
        view = SessionReplyView(conv_id="test_session_12345678")
        self.assertEqual(len(view.children), 1)
        btn = view.children[0]
        self.assertIsInstance(btn, discord.ui.Button)
        self.assertEqual(btn.custom_id, "btn_reply_session:test_session_12345678")
        self.assertIn("返信する", btn.label)

    def test_prompt_reply_modal_structure(self):
        modal = PromptReplyModal(target_conv_id="test_session_12345678")
        self.assertIn("test_ses", modal.title)
        self.assertEqual(modal.prompt_input.max_length, 2000)
        self.assertTrue(modal.prompt_input.required)

    def test_launch_chrome_browser_not_found(self):
        from tools.discord_bridge import launch_chrome_browser
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", return_value=None):
            success, msg = launch_chrome_browser("http://localhost:5000")
            self.assertFalse(success)
            self.assertIn("見つかりませんでした", msg)


if __name__ == "__main__":
    unittest.main()
