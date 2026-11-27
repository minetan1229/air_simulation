"""Tests for tools/check_agents_hygiene.py."""

from __future__ import annotations

import json
from pathlib import Path

from tools.check_agents_hygiene import run_hygiene_check

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_repo_passes_agents_hygiene() -> None:
    res = run_hygiene_check(REPO_ROOT)
    assert res["ok"], f"hygiene errors: {res['errors']}"
    assert res["metrics"]["AGENTS.md"] <= 80
    assert res["metrics"][".agents/Knowledge.md"] <= 80
    for nested in ("physics/AGENTS.md", "geometry/AGENTS.md", "optimization/AGENTS.md"):
        assert res["metrics"][nested] <= 30


def test_detects_line_bloat_and_duplicate_flat_skills(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("\n".join(f"line {i}" for i in range(105)), encoding="utf-8")
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "Knowledge.md").write_text("ok\n", encoding="utf-8")
    skills = tmp_path / ".agents" / "skills"
    skills.mkdir(parents=True)
    (skills / "subagent-delegation.md").write_text("dup\n", encoding="utf-8")

    res = run_hygiene_check(tmp_path)
    assert not res["ok"]
    assert any("AGENTS.md" in e and "hard limit" in e for e in res["errors"])
    assert any("duplicate legacy flat skill files" in e for e in res["errors"])


def test_detects_broken_agent_frontmatter_and_adversarial_schema(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# ok\n", encoding="utf-8")
    agents = tmp_path / ".agents" / "agents"
    agents.mkdir(parents=True)
    (tmp_path / ".agents" / "Knowledge.md").write_text("# ok\n", encoding="utf-8")
    (agents / "bad.md").write_text("---\nname: bad\nmodel: invalid_tier\n---\nbody\n", encoding="utf-8")

    adv = tmp_path / "tests" / "adversarial_cases"
    adv.mkdir(parents=True)
    (adv / "broken.json").write_text(json.dumps({"case_id": "x"}), encoding="utf-8")

    res = run_hygiene_check(tmp_path)
    assert not res["ok"]
    assert any("missing required frontmatter key 'description'" in e for e in res["errors"])
    assert any("invalid model 'invalid_tier'" in e for e in res["errors"])
    assert any("missing required keys" in e for e in res["errors"])
