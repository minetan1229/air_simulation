"""
Unit and functional tests for Discord Remote Bridge
"""

import asyncio
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import discord
    from tools.discord_bridge import (
        AVAILABLE_MODELS,
        ChatSelectView,
        DiscordBridgeClient,
        ModelSelectView,
        PromptReplyModal,
        ResponseTarget,
        SessionReplyView,
        build_chats_embed,
        build_help_embed,
        build_models_embed,
        build_status_embed,
        execute_agy_prompt,
        fetch_agy_models,
        format_duration,
        impl_models,
        make_error_id,
        new_task_id,
        parse_agy_models,
        resolve_conversation_id,
        resolve_model_id,
        split_text_for_discord,
        terminate_process_tree,
        validate_browser_url,
        validate_prompt,
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


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeMessage:
    """Minimal stand-in for discord.Message."""

    def __init__(self, channel_id: int = 4242, author_id: int = 7):
        self.id = 1
        self.content = "hello"
        self.author = SimpleNamespace(id=author_id)
        self.channel = SimpleNamespace(id=channel_id)
        self.replies = []
        self.reactions = []
        self.deleted = False

    async def reply(self, content=None, **kwargs):
        self.replies.append((content, kwargs))
        return FakeMessage(self.channel.id, self.author.id)

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def edit(self, **kwargs):
        return self

    async def delete(self):
        self.deleted = True


class FakeChannel:
    """Minimal stand-in for discord.TextChannel."""

    def __init__(self, channel_id: int = 4242):
        self.id = channel_id
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))
        return FakeMessage(self.id)


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append((content, kwargs))
        return FakeMessage()


class FakeInteractionResponse:
    def __init__(self):
        self.deferred = False
        self.messages = []

    async def defer(self):
        self.deferred = True

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))
        return FakeMessage()


class FakeInteraction:
    def __init__(self, channel_id: int = 4242, user_id: int = 7):
        self.response = FakeInteractionResponse()
        self.followup = FakeFollowup()
        self.user = SimpleNamespace(id=user_id)
        self.channel = SimpleNamespace(id=channel_id)
        self.channel_id = channel_id
        self.data = {}


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridgeValidation(unittest.TestCase):
    """Validation and resolution helpers guarding user supplied input."""

    def test_validate_prompt_rejects_empty(self):
        result = validate_prompt("   ")
        self.assertFalse(result.ok)
        self.assertIsNone(result.value)
        self.assertIn("空", result.message)

    def test_validate_prompt_rejects_oversized(self):
        with patch("tools.discord_bridge.MAX_PROMPT_CHARS", 10):
            result = validate_prompt("x" * 11)
        self.assertFalse(result.ok)
        self.assertIn("長すぎます", result.message)

    def test_validate_prompt_accepts_normal(self):
        result = validate_prompt("  do the thing  ")
        self.assertTrue(result.ok)
        self.assertEqual(result.value, "do the thing")

    def test_validate_browser_url_allows_http(self):
        for url in ("http://localhost:5000", "https://example.com/path"):
            result = validate_browser_url(url)
            self.assertTrue(result.ok, url)
            self.assertEqual(result.value, url)

    def test_validate_browser_url_rejects_dangerous_schemes(self):
        for url in (
            "file:///C:/Windows/System32",
            "javascript:alert(1)",
            "data:text/html,<h1>x</h1>",
            "ftp://example.com",
        ):
            result = validate_browser_url(url)
            self.assertFalse(result.ok, url)
            self.assertIn("スキーム", result.message)

    def test_validate_browser_url_rejects_credentials_and_empty_host(self):
        self.assertFalse(validate_browser_url("http://user:pass@example.com").ok)
        self.assertFalse(validate_browser_url("http://").ok)
        self.assertFalse(validate_browser_url("   ").ok)

    def test_resolve_model_id_exact(self):
        result = resolve_model_id("claude-sonnet-4-6")
        self.assertTrue(result.ok)
        self.assertEqual(result.value, "claude-sonnet-4-6")

    def test_resolve_model_id_unique_substring(self):
        result = resolve_model_id("gpt-oss")
        self.assertTrue(result.ok)
        self.assertEqual(result.value, "gpt-oss-120b-medium")

    def test_resolve_model_id_ambiguous_substring(self):
        result = resolve_model_id("gemini")
        self.assertFalse(result.ok)
        self.assertIn("複数", result.message)

    def test_resolve_model_id_unknown(self):
        result = resolve_model_id("does-not-exist")
        self.assertFalse(result.ok)
        self.assertIn("未知のモデル", result.message)

    def test_resolve_conversation_id_exact_and_prefix(self):
        sessions = [
            {"id": "aaaaaaaa-1111", "short_id": "aaaaaaaa"},
            {"id": "bbbbbbbb-2222", "short_id": "bbbbbbbb"},
        ]
        self.assertEqual(resolve_conversation_id("aaaaaaaa-1111", sessions).value, "aaaaaaaa-1111")
        self.assertEqual(resolve_conversation_id("bbbbbbbb", sessions).value, "bbbbbbbb-2222")

    def test_resolve_conversation_id_ambiguous_prefix(self):
        sessions = [
            {"id": "abcd1111-1111", "short_id": "abcd1111"},
            {"id": "abcd2222-2222", "short_id": "abcd2222"},
        ]
        result = resolve_conversation_id("abcd", sessions)
        self.assertFalse(result.ok)
        self.assertIn("複数", result.message)

    def test_resolve_conversation_id_unknown_warns_but_continues(self):
        result = resolve_conversation_id("zzz", [])
        self.assertTrue(result.ok)
        self.assertEqual(result.value, "zzz")
        self.assertIn("見つかりません", result.message)


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridgeHelpers(unittest.TestCase):
    """Pure helper functions of the bridge."""

    def test_split_text_for_discord_short_text(self):
        self.assertEqual(split_text_for_discord("short"), ["short"])

    def test_split_text_for_discord_respects_limit(self):
        text = "\n".join(f"line {i}" for i in range(500))
        chunks = split_text_for_discord(text, limit=200)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 200)
        self.assertEqual("".join(chunks).replace("\n", ""), text.replace("\n", ""))

    def test_split_text_for_discord_prefers_paragraph_boundary(self):
        text = "a" * 80 + "\n\n" + "b" * 80
        chunks = split_text_for_discord(text, limit=100)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].endswith("a" * 80))

    def test_parse_agy_models_tab_and_space_separated(self):
        output = "model-a\tModel A\nmodel-b Model B\n\nbroken-line-without-name\n"
        models = parse_agy_models(output)
        self.assertEqual(models["model-a"], "Model A")
        self.assertEqual(models["model-b"], "Model B")
        self.assertNotIn("broken-line-without-name", models)

    def test_new_task_id_is_unique(self):
        ids = {new_task_id(42) for _ in range(500)}
        self.assertEqual(len(ids), 500)
        self.assertTrue(all(task_id.startswith("42_") for task_id in ids))

    def test_make_error_id_shape(self):
        error_id = make_error_id()
        self.assertEqual(len(error_id), 8)
        self.assertTrue(all(char in "0123456789abcdef" for char in error_id))
        self.assertNotEqual(error_id, make_error_id())

    def test_format_duration(self):
        self.assertEqual(format_duration(5), "5s")
        self.assertEqual(format_duration(65), "1m 05s")
        self.assertEqual(format_duration(3725), "1h 02m 05s")
        self.assertEqual(format_duration(-10), "0s")

    def test_terminate_process_tree_kills_child(self):
        popen_kwargs = {"start_new_session": True} if os.name == "posix" else {}
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **popen_kwargs,
        )
        terminate_process_tree(proc)
        proc.wait(timeout=15)
        self.assertIsNotNone(proc.returncode)

    def test_fetch_agy_models_parses_cli_output(self):
        completed = subprocess.CompletedProcess(
            args=["agy", "models"], returncode=0, stdout="model-x\tModel X\n", stderr=""
        )
        with patch("tools.discord_bridge.subprocess.run", return_value=completed):
            models = fetch_agy_models(executable="agy")
        self.assertEqual(models, {"model-x": "Model X"})

    def test_fetch_agy_models_raises_on_failure(self):
        completed = subprocess.CompletedProcess(args=["agy", "models"], returncode=1, stdout="", stderr="")
        with patch("tools.discord_bridge.subprocess.run", return_value=completed):
            with self.assertRaises(RuntimeError):
                fetch_agy_models(executable="agy")


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridgeClientState(unittest.TestCase):
    """State handling, concurrency limits and task bookkeeping of the client."""

    def setUp(self):
        self.client = DiscordBridgeClient()

    def test_state_helpers_accept_missing_channel(self):
        self.assertEqual(self.client.get_model(None), "gemini-3.8-flash-low")
        self.client.set_model(None, "claude-sonnet-4-6")
        self.assertIsNone(self.client.get_conversation(None))
        self.client.set_conversation(None, "abc")
        self.assertEqual(self.client.channel_task_count(None), 0)

    def test_authorization_denies_unknown_channel_when_restricted(self):
        with patch("tools.discord_bridge.ALLOWED_USER_IDS", set()), \
             patch("tools.discord_bridge.ALLOWED_CHANNEL_IDS", {888}):
            self.assertFalse(self.client.is_authorized(111, None))
            self.assertTrue(self.client.is_authorized(111, 888))

    def test_channel_concurrency_limit(self):
        with patch("tools.discord_bridge.MAX_TASKS_PER_CHANNEL", 2):
            self.assertTrue(self.client.check_capacity(1).ok)
            self.client.register_task(1, "t1", MagicMock())
            self.assertTrue(self.client.check_capacity(1).ok)
            self.client.register_task(1, "t2", MagicMock())
            result = self.client.check_capacity(1)
            self.assertFalse(result.ok)
            self.assertIn("上限", result.message)

    def test_global_concurrency_limit(self):
        with patch("tools.discord_bridge.MAX_TASKS_GLOBAL", 1):
            self.client.register_task(1, "t1", MagicMock())
            result = self.client.check_capacity(2)
            self.assertFalse(result.ok)
            self.assertIn("上限", result.message)

    def test_abort_marks_task_as_aborted(self):
        proc = MagicMock()
        proc.returncode = None
        self.client.register_task(5, "task-x", proc)
        self.assertEqual(self.client.abort_tasks(task_id="task-x"), 1)
        self.assertIn("task-x", self.client.aborted_tasks)
        self.client.unregister_task(5, "task-x")
        self.assertNotIn("task-x", self.client.aborted_tasks)
        self.assertEqual(len(self.client.active_processes), 0)

    def test_spawn_background_tracks_and_reports_failures(self):
        async def scenario():
            with self.assertLogs("DiscordBridge", level="ERROR"):
                async def boom():
                    raise RuntimeError("boom")

                task = self.client.spawn_background(boom())
                await asyncio.sleep(0.05)
                self.assertTrue(task.done())
                self.assertEqual(len(self.client.background_tasks), 0)

        asyncio.run(scenario())

    def test_setup_hook_registers_persistent_views(self):
        async def scenario():
            local = DiscordBridgeClient()
            sessions = [
                {
                    "id": "abcdef01-2345-6789-abcd-ef0123456789",
                    "short_id": "abcdef01",
                    "mtime": 0.0,
                    "date_str": "01/01 00:00",
                    "prompt": "test",
                }
            ]
            with patch("tools.discord_bridge.get_recent_conversations", return_value=sessions), \
                 patch.object(local.tree, "sync", new=AsyncMock()):
                await local.setup_hook()
            registered = set()
            for view in local.persistent_views:
                for item in view.children:
                    registered.add(item.custom_id)
            self.assertIn("btn_reply_session:default", registered)
            self.assertIn("btn_reply_session:abcdef01-2345-6789-abcd-ef0123456789", registered)

        asyncio.run(scenario())


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridgeResponses(unittest.TestCase):
    """Response delivery for slash commands and prefix commands."""

    def test_message_target_does_not_send_none_content(self):
        async def scenario():
            message = FakeMessage()
            target = ResponseTarget(message=message)
            embed = build_help_embed()
            await target.send(embed=embed)
            content, kwargs = message.replies[0]
            self.assertIsNone(content)
            self.assertIn("embed", kwargs)
            self.assertNotIn("view", kwargs)

        asyncio.run(scenario())

    def test_deferred_followup_ignores_ephemeral(self):
        async def scenario():
            interaction = FakeInteraction()
            target = ResponseTarget(interaction=interaction)
            await target.defer()
            self.assertTrue(interaction.response.deferred)
            await target.send("hello", ephemeral=True)
            self.assertEqual(len(interaction.followup.messages), 1)
            content, kwargs = interaction.followup.messages[0]
            self.assertEqual(content, "hello")
            self.assertNotIn("ephemeral", kwargs)

        asyncio.run(scenario())

    def test_non_deferred_interaction_uses_ephemeral(self):
        async def scenario():
            interaction = FakeInteraction()
            target = ResponseTarget(interaction=interaction)
            await target.send("nope", ephemeral=True)
            content, kwargs = interaction.response.messages[0]
            self.assertEqual(content, "nope")
            self.assertTrue(kwargs.get("ephemeral"))

        asyncio.run(scenario())

    def test_model_refresh_updates_catalogue(self):
        async def scenario():
            target = ResponseTarget(message=FakeMessage())
            original = dict(AVAILABLE_MODELS)
            try:
                with patch(
                    "tools.discord_bridge.fetch_agy_models",
                    return_value={"only-model": "Only Model"},
                ):
                    await impl_models(target, refresh=True)
                self.assertIn("only-model", AVAILABLE_MODELS)
            finally:
                AVAILABLE_MODELS.clear()
                AVAILABLE_MODELS.update(original)

        asyncio.run(scenario())

    def test_model_refresh_failure_is_reported(self):
        async def scenario():
            message = FakeMessage()
            target = ResponseTarget(message=message)
            original = dict(AVAILABLE_MODELS)
            try:
                with patch(
                    "tools.discord_bridge.fetch_agy_models",
                    side_effect=RuntimeError("cli down"),
                ):
                    await impl_models(target, refresh=True)
                content, _ = message.replies[0]
                self.assertIn("失敗", content)
                self.assertNotIn("only-model", AVAILABLE_MODELS)
            finally:
                AVAILABLE_MODELS.clear()
                AVAILABLE_MODELS.update(original)

        asyncio.run(scenario())


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridgeExecution(unittest.TestCase):
    """Guardrails applied before an agent process is launched."""

    def test_execute_rejects_empty_prompt(self):
        async def scenario():
            channel = FakeChannel()
            await execute_agy_prompt(channel, "   ")
            self.assertEqual(len(channel.sent), 1)
            content, _ = channel.sent[0]
            self.assertIn("空", content)

        asyncio.run(scenario())

    def test_execute_rejects_oversized_prompt(self):
        async def scenario():
            channel = FakeChannel()
            with patch("tools.discord_bridge.MAX_PROMPT_CHARS", 5):
                await execute_agy_prompt(channel, "x" * 50)
            self.assertEqual(len(channel.sent), 1)
            content, _ = channel.sent[0]
            self.assertIn("長すぎます", content)

        asyncio.run(scenario())

    def test_execute_reports_missing_cli(self):
        async def scenario():
            channel = FakeChannel()
            with patch("tools.discord_bridge.resolve_agy_executable", return_value=None):
                await execute_agy_prompt(channel, "do something")
            self.assertEqual(len(channel.sent), 1)
            content, _ = channel.sent[0]
            self.assertIn("agy", content)

        asyncio.run(scenario())

    def test_execute_respects_channel_concurrency_limit(self):
        async def scenario():
            channel = FakeChannel()
            with patch("tools.discord_bridge.MAX_TASKS_PER_CHANNEL", 1), \
                 patch("tools.discord_bridge.resolve_agy_executable", return_value="agy"), \
                 patch("tools.discord_bridge.client.register_task") as register, \
                 patch("tools.discord_bridge.client.check_capacity") as capacity:
                capacity.return_value = type("R", (), {"ok": False, "message": "上限"})()
                await execute_agy_prompt(channel, "do something")
            register.assert_not_called()
            content, _ = channel.sent[0]
            self.assertIn("上限", content)

        asyncio.run(scenario())


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridgeExecutionEngine(unittest.TestCase):
    """End to end run of the agent engine against a stubbed `agy` CLI."""

    @staticmethod
    def _write_stub_cli(directory: str) -> str:
        stub = os.path.join(directory, "fake_agy.py")
        with open(stub, "w", encoding="utf-8") as handle:
            handle.write(
                "import sys, time\n"
                "print('{\"event\":\"init\",\"conversation_id\":\"stub-conv-0001\"}', flush=True)\n"
                "time.sleep(0.2)\n"
                "print('{\"event\":\"step_update\",\"step_update\":{\"step_type\":\"agent_response\","
                "\"text_delta\":\"Hello from the agent\",\"state\":\"DONE\"}}', flush=True)\n"
                "print('{\"event\":\"result\",\"result\":{\"response\":\"Hello from the agent\","
                "\"status\":\"SUCCESS\"}}', flush=True)\n"
            )
        if sys.platform == "win32":
            launcher = os.path.join(directory, "fake_agy.bat")
            with open(launcher, "w", encoding="utf-8") as handle:
                handle.write('@echo off\r\n"' + sys.executable + '" "' + stub + '" %*\r\n')
            return launcher
        launcher = os.path.join(directory, "fake_agy.sh")
        with open(launcher, "w", encoding="utf-8") as handle:
            handle.write('#!/bin/sh\nexec "' + sys.executable + '" "' + stub + '" "$@"\n')
        os.chmod(launcher, 0o755)
        return launcher

    def test_engine_streams_result_to_channel(self):
        async def scenario():
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                launcher = self._write_stub_cli(directory)
                channel = FakeChannel()
                with patch("tools.discord_bridge.resolve_agy_executable", return_value=launcher):
                    await execute_agy_prompt(channel, "hello agent")

                self.assertGreaterEqual(len(channel.sent), 2)
                final_content, final_kwargs = channel.sent[-1]
                self.assertIn("Hello from the agent", final_content)
                self.assertIn("view", final_kwargs)

        asyncio.run(scenario())

    def test_engine_reports_cli_timeout(self):
        async def scenario():
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                stub = os.path.join(directory, "fake_timeout.py")
                with open(stub, "w", encoding="utf-8") as handle:
                    handle.write(
                        "import sys, time\n"
                        "print('{\"event\":\"init\",\"conversation_id\":\"stub-conv-0002\"}', flush=True)\n"
                        "time.sleep(30)\n"
                    )
                if sys.platform == "win32":
                    launcher = os.path.join(directory, "fake_timeout.bat")
                    with open(launcher, "w", encoding="utf-8") as handle:
                        handle.write('@echo off\r\n"' + sys.executable + '" "' + stub + '" %*\r\n')
                else:
                    launcher = os.path.join(directory, "fake_timeout.sh")
                    with open(launcher, "w", encoding="utf-8") as handle:
                        handle.write('#!/bin/sh\nexec "' + sys.executable + '" "' + stub + '" "$@"\n')
                    os.chmod(launcher, 0o755)

                channel = FakeChannel()
                with patch("tools.discord_bridge.resolve_agy_executable", return_value=launcher), \
                     patch("tools.discord_bridge.TASK_TIMEOUT_SECONDS", 10):
                    await execute_agy_prompt(channel, "hello agent")

                final_content, _ = channel.sent[-1]
                self.assertIn("timed out", final_content)

        asyncio.run(scenario())


@unittest.skipUnless(HAS_DISCORD, "discord.py not installed")
class TestDiscordBridgeEmbeds(unittest.TestCase):
    """Structure of the shared embeds."""

    def test_status_embed_fields(self):
        metrics = {"cpu": "10%", "memory": "1GB", "gpu": "GPU"}
        embed = build_status_embed(metrics, 2, 1, "gemini-3.8-flash-low", "abc12345")
        names = [field.name for field in embed.fields]
        self.assertEqual(len(names), 4)
        self.assertIn("2", embed.fields[3].value)

    def test_chats_embed_marks_active_session(self):
        sessions = [
            {"id": "aaaaaaaa", "short_id": "aaaaaaaa", "date_str": "01/01 00:00", "prompt": "hello"},
            {"id": "bbbbbbbb", "short_id": "bbbbbbbb", "date_str": "01/02 00:00", "prompt": "world"},
        ]
        embed = build_chats_embed(sessions, "bbbbbbbb")
        self.assertIn("bbbbbbbb", embed.fields[1].name)
        self.assertIn("aaaaaaaa", embed.fields[0].name)

    def test_models_embed_lists_catalogue(self):
        embed = build_models_embed("gemini-3.8-flash-low")
        self.assertEqual(len(embed.fields), len(AVAILABLE_MODELS))
        self.assertIn("gemini-3.8-flash-low", embed.description)

    def test_help_embed_lists_core_commands(self):
        embed = build_help_embed()
        text = " ".join(field.value for field in embed.fields)
        for command in ("!chat", "!chats", "!resume", "!model", "!status", "!chrome", "!abort"):
            self.assertIn(command, text)

    def test_chat_select_view_without_sessions(self):
        view = ChatSelectView([], None, AsyncMock())
        self.assertEqual(len(view.children), 0)


if __name__ == "__main__":
    unittest.main()
