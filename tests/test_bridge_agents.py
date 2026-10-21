"""Tests for the Discord agent bridge: quota tracking, backends, router and monitor."""

import asyncio
import sys
import unittest
from unittest.mock import patch

import pytest

from tools.bridge_backends import (
    ANTIGRAVITY_BACKEND,
    ANTIGRAVITY_MODELS,
    KILO_BACKEND,
    KILO_DEFAULT_MODEL,
    KILO_MODELS,
    AntigravityBackend,
    KiloCodeBackend,
    build_default_backends,
    detect_backend,
    model_catalog,
    normalize_model,
    resolve_kilo_executable,
)
from tools.bridge_monitor import MonitorAgent, MonitorAlert
from tools.bridge_quota import (
    EXHAUSTED_THRESHOLD_PERCENT,
    QuotaEntry,
    QuotaSnapshot,
    QuotaTracker,
    parse_credits_output,
    parse_quota_output,
)
from tools.bridge_router import (
    BridgeRequest,
    BridgeRouter,
    backoff_delay,
    is_rate_limited,
    strip_rate_limit_noise,
)

REAL_QUOTA_OUTPUT = (
    "Gemini Models\tWeekly Limit Remaining\t1%\t2026-10-24T00:41:32Z\n"
    "Gemini Models\tFive Hour Limit Remaining\t100%\t2026-10-21T17:12:46Z\n"
    "Claude and GPT models\tWeekly Limit Remaining\t1%\t2026-10-22T06:33:22Z\n"
    "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-10-21T17:12:46Z\n"
)


class TestQuotaParsing(unittest.TestCase):
    def test_parse_real_quota_output(self):
        entries = parse_quota_output(REAL_QUOTA_OUTPUT)
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0].group, "Gemini Models")
        self.assertEqual(entries[0].remaining_percent, 1.0)
        self.assertIsNotNone(entries[0].reset_at)
        self.assertEqual(entries[1].kind, "five_hour")
        self.assertEqual(entries[0].kind, "weekly")

    def test_parse_quota_output_ignores_garbage(self):
        self.assertEqual(parse_quota_output(""), [])
        self.assertEqual(parse_quota_output("no numbers here"), [])

    def test_parse_credits_output(self):
        self.assertEqual(parse_credits_output("Remaining credits\t0\n"), 0)
        self.assertEqual(parse_credits_output("Remaining credits\t42\n"), 42)
        self.assertIsNone(parse_credits_output("Upgrade\thttps://example.com"))

    def test_snapshot_reports_exhaustion(self):
        snapshot = QuotaSnapshot(backend="antigravity", entries=parse_quota_output(REAL_QUOTA_OUTPUT))
        self.assertTrue(snapshot.is_exhausted)
        self.assertEqual(snapshot.worst_weekly_percent, 1.0)
        self.assertEqual(snapshot.worst_five_hour_percent, 100.0)
        self.assertTrue(any("1%" in line for line in snapshot.summary_lines()))

    def test_snapshot_not_exhausted_with_ample_quota(self):
        text = REAL_QUOTA_OUTPUT.replace("1%", "87%")
        snapshot = QuotaSnapshot(backend="antigravity", entries=parse_quota_output(text))
        self.assertFalse(snapshot.is_exhausted)

    def test_snapshot_error_is_never_exhausted(self):
        snapshot = QuotaSnapshot(backend="antigravity", error="agy CLI が見つかりません")
        self.assertFalse(snapshot.is_exhausted)
        self.assertIn("❌", snapshot.summary_lines()[0])

    def test_exhausted_threshold_is_one_percent(self):
        self.assertEqual(EXHAUSTED_THRESHOLD_PERCENT, 1.0)


class TestQuotaTracker(unittest.TestCase):
    def test_cache_ttl(self):
        tracker = QuotaTracker(ttl=100.0)
        snapshot = QuotaSnapshot(backend="antigravity")
        tracker.store(snapshot)
        self.assertIs(tracker.cached("antigravity"), snapshot)
        tracker.ttl = -1.0
        self.assertIsNone(tracker.cached("antigravity"))

    def test_estimated_remaining_requests_requires_measurements(self):
        tracker = QuotaTracker(ttl=100.0)
        tracker.store(QuotaSnapshot(backend="antigravity", entries=parse_quota_output(REAL_QUOTA_OUTPUT)))
        self.assertIsNone(tracker.estimated_remaining_requests("antigravity"))

    def test_estimated_remaining_requests_from_measured_slope(self):
        tracker = QuotaTracker(ttl=100.0)
        tracker.store(QuotaSnapshot(backend="antigravity", entries=parse_quota_output(REAL_QUOTA_OUTPUT)))
        # Two requests consumed 10 percent -> 1 request per percent.
        tracker.record_request("antigravity")
        tracker.observe_percent("antigravity", 90.0)
        tracker.record_request("antigravity")
        tracker.observe_percent("antigravity", 80.0)
        tracker.store(QuotaSnapshot(backend="antigravity", entries=parse_quota_output(REAL_QUOTA_OUTPUT)))
        estimated = tracker.estimated_remaining_requests("antigravity")
        self.assertIsNotNone(estimated)
        self.assertGreater(estimated, 0.0)
        self.assertLessEqual(estimated, 2.0)

    def test_request_counter(self):
        tracker = QuotaTracker()
        self.assertEqual(tracker.request_count("kilo"), 0)
        tracker.record_request("kilo")
        tracker.record_request("kilo")
        self.assertEqual(tracker.request_count("kilo"), 2)


class TestRateLimitDetection(unittest.TestCase):
    def test_strong_markers(self):
        for text in (
            "HTTP 429 Too Many Requests",
            "error: 429 rate limited",
            "RESOURCE_EXHAUSTED: quota exceeded",
            "You have exhausted your quota on this model.",
            "individual quota reached",
        ):
            self.assertTrue(is_rate_limited(text), text)

    def test_weak_markers_need_the_error_stream(self):
        self.assertFalse(is_rate_limited("the rate limit of a wing is discussed"))
        self.assertTrue(is_rate_limited("rate limit", include_weak=True))

    def test_negative_case(self):
        self.assertFalse(is_rate_limited("everything is fine"))
        self.assertFalse(is_rate_limited(""))

    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual(backoff_delay(1, base=2.0, cap=60.0), 2.0)
        self.assertEqual(backoff_delay(2, base=2.0, cap=60.0), 4.0)
        self.assertEqual(backoff_delay(3, base=2.0, cap=60.0), 8.0)
        self.assertEqual(backoff_delay(10, base=2.0, cap=60.0), 60.0)

    def test_strip_rate_limit_noise(self):
        text = "partial answer\nHTTP 429 Too Many Requests\nmore text"
        self.assertEqual(strip_rate_limit_noise(text), "partial answer\nmore text")


class TestBackends(unittest.TestCase):
    def test_detect_backend(self):
        self.assertEqual(detect_backend("gemini-3.8-flash-low"), ANTIGRAVITY_BACKEND)
        self.assertEqual(detect_backend("kilo/kilo-auto/free"), KILO_BACKEND)
        self.assertEqual(detect_backend("antigravity/gemini-3.8-flash-high"), ANTIGRAVITY_BACKEND)

    def test_normalize_model(self):
        self.assertEqual(normalize_model("antigravity/gemini-3.8-flash-low", ANTIGRAVITY_BACKEND), "gemini-3.8-flash-low")
        self.assertEqual(normalize_model("kilo/kilo-auto/free", KILO_BACKEND), "kilo/kilo-auto/free")

    def test_model_catalog_contains_both_backends(self):
        catalog = model_catalog()
        self.assertIn("gemini-3.8-flash-low", catalog)
        self.assertIn("kilo/kilo-auto/free", catalog)

    def test_kilo_default_model_is_free(self):
        self.assertIn("free", KILO_DEFAULT_MODEL)
        self.assertIn(KILO_DEFAULT_MODEL, KILO_MODELS)

    def test_antigravity_command(self):
        backend = AntigravityBackend("agy")
        cmd = backend.build_command("hello", "gemini-3.8-flash-low")
        self.assertEqual(cmd[0], "agy")
        self.assertIn("--output-format", cmd)
        self.assertIn("stream-json", cmd)
        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertNotIn("--conversation", cmd)

    def test_antigravity_command_resumes_and_continues(self):
        backend = AntigravityBackend("agy")
        cmd = backend.build_command("hello", "gemini-3.8-flash-low", session_id="conv-1")
        self.assertIn("--conversation", cmd)
        self.assertIn("conv-1", cmd)
        cmd = backend.build_command("hello", "gemini-3.8-flash-low", continue_session=True)
        self.assertIn("--continue", cmd)

    def test_kilo_command(self):
        backend = KiloCodeBackend("kilo")
        cmd = backend.build_command("hello", "kilo/kilo-auto/free")
        self.assertEqual(cmd[0], "kilo")
        self.assertEqual(cmd[1], "run")
        self.assertIn("--model", cmd)
        self.assertIn("kilo/kilo-auto/free", cmd)
        self.assertIn("json", cmd)
        self.assertIn("--auto", cmd)
        self.assertEqual(cmd[-1], "hello")
        self.assertEqual(cmd[-2], "--")

    def test_kilo_command_resumes_and_continues(self):
        backend = KiloCodeBackend("kilo")
        cmd = backend.build_command("hello", "kilo/kilo-auto/free", session_id="ses_1")
        self.assertIn("--session", cmd)
        self.assertIn("ses_1", cmd)
        cmd = backend.build_command("hello", "kilo/kilo-auto/free", continue_session=True)
        self.assertIn("--continue", cmd)

    def test_antigravity_parse_line(self):
        backend = AntigravityBackend("agy")
        init = backend.parse_line('{"event": "init", "conversation_id": "conv-9"}')
        self.assertIsNotNone(init)
        self.assertEqual(init.session_id, "conv-9")
        self.assertEqual(init.kind, "session")

        delta = backend.parse_line('{"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "hi"}}')
        self.assertEqual(delta.kind, "text")
        self.assertEqual(delta.text, "hi")

        result = backend.parse_line('{"event": "result", "result": {"response": "done"}}')
        self.assertEqual(result.kind, "final")
        self.assertEqual(result.text, "done")

        raw = backend.parse_line("plain text")
        self.assertEqual(raw.kind, "raw")

    def test_kilo_parse_line(self):
        backend = KiloCodeBackend("kilo")
        text = backend.parse_line('{"type": "text", "sessionID": "ses_1", "part": {"text": "hello"}}')
        self.assertEqual(text.kind, "text")
        self.assertEqual(text.text, "hello")
        self.assertEqual(text.session_id, "ses_1")

        finish = backend.parse_line(
            '{"type": "step_finish", "sessionID": "ses_1", "part": {"reason": "stop", '
            '"model": {"providerID": "kilo", "modelID": "minimax/minimax-m3.1-flash:free"}, '
            '"cost": 0, "tokens": {"total": 10}}}'
        )
        self.assertEqual(finish.kind, "final")
        self.assertEqual(finish.model, "kilo/minimax/minimax-m3.1-flash:free")
        self.assertEqual(finish.usage["cost"], 0)

    def test_default_backends_are_constructed(self):
        backends = build_default_backends()
        self.assertIn(ANTIGRAVITY_BACKEND, backends)
        self.assertIn(KILO_BACKEND, backends)

    def test_kilo_executable_resolution(self):
        resolved = resolve_kilo_executable()
        self.assertTrue(resolved is None or isinstance(resolved, str))


class ScriptedBackend:
    """Backend stub that replays scripted shell snippets through the router."""

    def __init__(self, name, scripts):
        self.name = name
        self.scripts = list(scripts)
        self.commands = []

    def is_available(self):
        return True

    def build_command(self, prompt, model, session_id=None, continue_session=False):
        self.commands.append(
            {
                "prompt": prompt,
                "model": model,
                "session_id": session_id,
                "continue_session": continue_session,
            }
        )
        script = self.scripts.pop(0) if self.scripts else "print('done')"
        return [sys.executable, "-c", script]

    def parse_line(self, line):
        from tools.bridge_backends import StreamEvent

        return StreamEvent(kind="raw", text=line)


class TestRouter(unittest.TestCase):
    def _router(self, antigravity_scripts, kilo_scripts, **kwargs):
        options = {
            "backoff_base": 0.01,
            "backoff_cap": 0.02,
            "max_rate_limit_retries": 2,
            "task_timeout": 30.0,
        }
        options.update(kwargs)
        agy = ScriptedBackend(ANTIGRAVITY_BACKEND, antigravity_scripts)
        kilo = ScriptedBackend(KILO_BACKEND, kilo_scripts)
        router = BridgeRouter(
            backends={ANTIGRAVITY_BACKEND: agy, KILO_BACKEND: kilo},
            **options,
        )
        return router, agy, kilo

    def test_rate_limit_is_retried_with_continue(self):
        router, agy, kilo = self._router(
            antigravity_scripts=[
                "print('RESOURCE_EXHAUSTED: quota exceeded')",
                "print('RESOURCE_EXHAUSTED: quota exceeded')",
                "print('finished successfully')",
            ],
            kilo_scripts=["print('kilo answer')"],
        )
        outcome = asyncio.run(
            router.execute(BridgeRequest(prompt="work", backend=ANTIGRAVITY_BACKEND, model="gemini-3.8-flash-low"))
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.rate_limit_retries, 2)
        self.assertEqual(outcome.attempts, 3)
        self.assertFalse(outcome.fallback_used)
        self.assertIn("finished successfully", outcome.text)
        # The retries continue the previous conversation.
        self.assertFalse(agy.commands[0]["continue_session"])
        self.assertTrue(agy.commands[1]["continue_session"])
        self.assertTrue(agy.commands[2]["continue_session"])
        self.assertEqual(len(kilo.commands), 0)

    def test_fallback_to_kilo_code_on_primary_error(self):
        router, agy, kilo = self._router(
            antigravity_scripts=["import sys; sys.stderr.write('fatal: backend exploded'); sys.exit(3)"],
            kilo_scripts=["print('kilo answer')"],
        )
        monitor = MonitorAgent()
        router.monitor = monitor
        outcome = asyncio.run(
            router.execute(BridgeRequest(prompt="work", backend=ANTIGRAVITY_BACKEND, model="gemini-3.8-flash-low"))
        )
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.fallback_used)
        self.assertEqual(outcome.backend, KILO_BACKEND)
        self.assertEqual(outcome.model, KILO_DEFAULT_MODEL)
        self.assertIn("kilo answer", outcome.text)
        self.assertIn("backend exploded", outcome.fallback_reason)
        # The monitor agent was informed about the fallback.
        self.assertEqual(monitor.fallback_count, 1)
        self.assertTrue(monitor.alerts)
        self.assertEqual(monitor.alerts[0].kind, "fallback")

    def test_fallback_can_be_disabled(self):
        router, agy, kilo = self._router(
            antigravity_scripts=["import sys; sys.stderr.write('fatal'); sys.exit(3)"],
            kilo_scripts=["print('kilo answer')"],
            fallback_enabled=False,
        )
        outcome = asyncio.run(
            router.execute(BridgeRequest(prompt="work", backend=ANTIGRAVITY_BACKEND, model="gemini-3.8-flash-low"))
        )
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.fallback_used)
        self.assertEqual(len(kilo.commands), 0)

    def test_exhausted_rate_limit_falls_back(self):
        router, agy, kilo = self._router(
            antigravity_scripts=[
                "print('RESOURCE_EXHAUSTED: quota exceeded')",
                "print('RESOURCE_EXHAUSTED: quota exceeded')",
                "print('RESOURCE_EXHAUSTED: quota exceeded')",
            ],
            kilo_scripts=["print('kilo answer')"],
        )
        outcome = asyncio.run(
            router.execute(BridgeRequest(prompt="work", backend=ANTIGRAVITY_BACKEND, model="gemini-3.8-flash-low"))
        )
        self.assertTrue(outcome.fallback_used)
        self.assertEqual(outcome.backend, KILO_BACKEND)
        self.assertEqual(len(kilo.commands), 1)

    def test_timeout_is_reported(self):
        router, agy, kilo = self._router(
            antigravity_scripts=["import time; time.sleep(5)"],
            kilo_scripts=["print('kilo answer')"],
            task_timeout=0.2,
        )
        outcome = asyncio.run(
            router.execute(BridgeRequest(prompt="work", backend=ANTIGRAVITY_BACKEND, model="gemini-3.8-flash-low"))
        )
        self.assertTrue(outcome.fallback_used)
        self.assertIn("timeout", outcome.fallback_reason.lower())


class TestMonitorAgent(unittest.TestCase):
    def test_record_fallback_raises_alert(self):
        monitor = MonitorAgent(error_threshold=3)
        alert = monitor.record_fallback(ANTIGRAVITY_BACKEND, KILO_BACKEND, "429")
        self.assertEqual(alert.kind, "fallback")
        self.assertEqual(alert.level, "warning")
        self.assertEqual(monitor.fallback_count, 1)
        self.assertIn("Kilo Code", alert.message)

    def test_consecutive_errors_raise_alert(self):
        monitor = MonitorAgent(error_threshold=2)
        monitor.record_error(ANTIGRAVITY_BACKEND, "boom")
        monitor.record_error(ANTIGRAVITY_BACKEND, "boom")
        alerts = asyncio.run(monitor.check({}))
        kinds = [alert.kind for alert in alerts]
        self.assertIn("consecutive_errors", kinds)

    def test_backend_availability_is_checked(self):
        class MissingBackend:
            def is_available(self):
                return False

        monitor = MonitorAgent()
        alerts = asyncio.run(monitor.check({"antigravity": MissingBackend()}))
        self.assertTrue(any(alert.kind == "backend_unavailable" for alert in alerts))

    def test_quota_exhaustion_raises_alert(self):
        monitor = MonitorAgent()
        monitor.record_quota(
            QuotaSnapshot(backend="antigravity", entries=parse_quota_output(REAL_QUOTA_OUTPUT))
        )
        alerts = asyncio.run(monitor.check({}))
        self.assertTrue(any(alert.kind == "quota_exhausted" for alert in alerts))

    def test_status_lines_mention_counters(self):
        monitor = MonitorAgent()
        monitor.record_success(ANTIGRAVITY_BACKEND, 1.5)
        monitor.record_error(KILO_BACKEND, "boom", rate_limited=True)
        lines = monitor.status_lines()
        self.assertTrue(any("成功" in line for line in lines))
        self.assertTrue(any("レート制限" in line for line in lines))
        status = monitor.status()
        self.assertEqual(status["success_count"], 1)
        self.assertEqual(status["rate_limit_count"], 1)

    def test_alert_callback_is_awaited(self):
        received = []

        async def on_alert(alert: MonitorAlert):
            received.append(alert)

        monitor = MonitorAgent(on_alert=on_alert)
        monitor.record_fallback(ANTIGRAVITY_BACKEND, KILO_BACKEND, "429")
        asyncio.run(monitor.notify(monitor.alerts[-1]))
        self.assertEqual(len(received), 1)


class TestDiscordBridgeIntegration(unittest.TestCase):
    def setUp(self):
        try:
            import discord  # noqa: F401
        except ImportError:
            self.skipTest("discord.py not installed")

    def test_available_models_include_kilo_free_model(self):
        from tools.discord_bridge import AVAILABLE_MODELS

        self.assertIn("gemini-3.8-flash-low", AVAILABLE_MODELS)
        self.assertIn("claude-opus-4-6-thinking", AVAILABLE_MODELS)
        self.assertIn(KILO_DEFAULT_MODEL, AVAILABLE_MODELS)

    def test_backend_switching(self):
        from tools.discord_bridge import DiscordBridgeClient

        bridge = DiscordBridgeClient()
        bridge.set_model(1001, "gemini-3.8-flash-low")
        self.assertEqual(bridge.get_backend(1001), ANTIGRAVITY_BACKEND)
        bridge.set_model(1002, KILO_DEFAULT_MODEL)
        self.assertEqual(bridge.get_backend(1002), KILO_BACKEND)
        bridge.set_backend(1003, KILO_BACKEND)
        self.assertEqual(bridge.get_backend(1003), KILO_BACKEND)
        self.assertEqual(bridge.get_model(1003), KILO_DEFAULT_MODEL)
        bridge.set_backend(1003, ANTIGRAVITY_BACKEND)
        self.assertEqual(bridge.get_model(1003), "gemini-3.8-flash-low")
        with self.assertRaises(ValueError):
            bridge.set_backend(1003, "unknown")

    def test_session_encoding_round_trip(self):
        from tools.discord_bridge import decode_session, encode_session

        encoded = encode_session(KILO_BACKEND, "ses_abc")
        backend, session_id = decode_session(encoded)
        self.assertEqual(backend, KILO_BACKEND)
        self.assertEqual(session_id, "ses_abc")
        backend, session_id = decode_session(encode_session(ANTIGRAVITY_BACKEND, "conv-1"))
        self.assertEqual(backend, ANTIGRAVITY_BACKEND)
        self.assertEqual(session_id, "conv-1")

    def test_router_is_wired_into_the_client(self):
        from tools.discord_bridge import client

        self.assertIn(ANTIGRAVITY_BACKEND, client.router.backends)
        self.assertIn(KILO_BACKEND, client.router.backends)
        self.assertIsNotNone(client.monitor)


if __name__ == "__main__":
    unittest.main()
