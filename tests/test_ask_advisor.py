"""Tests for tools/ask_advisor.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools import ask_advisor
from tools.rate_limit_guard import remaining_cooldown


def test_ask_advisor_dry_run_exits_zero(capsys) -> None:
    rc = ask_advisor.main(["--dry-run", "--topic", "smoke", "--question", "疎通テスト"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude-opus-4-6-thinking" in out
    assert "疎通テスト" in out


def test_ask_advisor_falls_back_on_429_and_saves_log(tmp_path: Path) -> None:
    rl_path = tmp_path / "rate_limit.json"
    cdir = tmp_path / "consultations"

    def fake_invoke(_agy: str, model_id: str, _prompt: str, _timeout: int):
        if "opus" in model_id:
            return False, "", "HTTP 429 RESOURCE_EXHAUSTED"
        return True, "1. **結論**: 物理式を修正してください。", ""

    with patch.object(ask_advisor, "resolve_agy_cli", return_value="/usr/bin/agy"), patch.object(
        ask_advisor, "invoke_agy", side_effect=fake_invoke
    ):
        res = ask_advisor.consult_advisor(
            model="opus4.6",
            topic="rk4-energy",
            question="エネルギー微増の原因は？",
            rate_limit_path=rl_path,
            consultations_dir=cdir,
        )

    assert res["success"] is True
    assert res["model"] == "gemini-4-hydrogen-xhigh"
    assert remaining_cooldown("claude-opus-4-6-thinking", state_path=rl_path) > 0.0
    saved_logs = list(cdir.glob("*.md"))
    assert len(saved_logs) == 1
    assert "rk4-energy" in saved_logs[0].read_text(encoding="utf-8")
