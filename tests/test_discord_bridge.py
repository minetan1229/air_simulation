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
        # When ALLOWED_USER_IDS is empty, any user is allowed
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", set()), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", set()):
            self.assertTrue(self.client.is_authorized(12345, 67890))

    def test_authorization_logic_restricted_user(self):
        # When ALLOWED_USER_IDS is set, only authorized user is allowed
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", {111, 222}), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", set()):
            self.assertTrue(self.client.is_authorized(111, 999))
            self.assertFalse(self.client.is_authorized(333, 999))

    def test_authorization_logic_restricted_channel(self):
        # When ALLOWED_CHANNEL_IDS is set, only authorized channel is allowed
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", set()), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", {888}):
            self.assertTrue(self.client.is_authorized(111, 888))
            self.assertFalse(self.client.is_authorized(111, 999))

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


if __name__ == "__main__":
    unittest.main()
