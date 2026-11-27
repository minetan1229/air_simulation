"""Lightweight hygiene linter for AGENTS.md, skills, custom subagents, and adversarial cases.

Checks:
  1. Line-count budgets (arXiv:2602.11988 bloat prevention):
     - Root AGENTS.md: warn > 80 lines, error > 100 lines
     - Nested {physics,geometry,optimization}/AGENTS.md: warn > 30 lines, error > 40 lines
     - .agents/Knowledge.md: warn > 80 lines, error > 100 lines
  2. Duplicate legacy flat skill files under .agents/skills/*.md (excluding _template.md).
  3. YAML frontmatter sanity on .agents/agents/*.md (required name, description, model).
  4. Schema validation on tests/adversarial_cases/*.json (if present).

Usage:
    python tools/check_agents_hygiene.py [--root <path>] [--strict] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_DIR = Path(__file__).resolve().parent.parent

NESTED_AGENTS = (
    "physics/AGENTS.md",
    "geometry/AGENTS.md",
    "optimization/AGENTS.md",
)
VALID_AGENT_MODELS = {"inherit", "flash", "pro"}
REQUIRED_ADVERSARIAL_KEYS = {
    "case_id",
    "discovered_by",
    "sanity_violation_type",
    "root_cause",
    "linked_test",
    "genome_or_payload",
}


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "missing opening '---' YAML frontmatter delimiter"
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None, "missing closing '---' YAML frontmatter delimiter"

    fields: Dict[str, str] = {}
    for raw in lines[1:end_idx]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if ":" in raw and not raw.startswith(" "):
            key, val = raw.split(":", 1)
            fields[key.strip()] = val.strip().strip('"').strip("'")
    return fields, None


def run_hygiene_check(root: Path) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {}

    # 1. Line budgets
    budgets = [
        ("AGENTS.md", 80, 100, True),
        (".agents/Knowledge.md", 80, 100, True),
        *[(rel, 30, 40, False) for rel in NESTED_AGENTS],
    ]
    for rel, warn_lim, err_lim, required in budgets:
        p = root / rel
        if not p.exists():
            if required:
                errors.append(f"missing required file: {rel}")
            else:
                warnings.append(f"missing nested domain AGENTS.md: {rel}")
            continue
        count = _line_count(p)
        metrics[rel] = count
        if count > err_lim:
            errors.append(f"{rel}: {count} lines exceeds hard limit ({err_lim})")
        elif count > warn_lim:
            warnings.append(f"{rel}: {count} lines exceeds target budget ({warn_lim})")

    # 2. Duplicate flat skill files
    skills_dir = root / ".agents" / "skills"
    if skills_dir.is_dir():
        flat_files = sorted(
            p.name
            for p in skills_dir.glob("*.md")
            if p.name != "_template.md"
        )
        if flat_files:
            errors.append(
                "duplicate legacy flat skill files in .agents/skills/ (use <name>/SKILL.md): "
                + ", ".join(flat_files)
            )

    # 3. Custom subagent YAML frontmatter
    agents_dir = root / ".agents" / "agents"
    if agents_dir.is_dir():
        agent_files = sorted(agents_dir.glob("*.md"))
        metrics["custom_agents"] = [p.name for p in agent_files]
        for af in agent_files:
            fields, err = _parse_frontmatter(af.read_text(encoding="utf-8"))
            if err or fields is None:
                errors.append(f".agents/agents/{af.name}: {err}")
                continue
            for req_key in ("name", "description"):
                if not fields.get(req_key):
                    errors.append(f".agents/agents/{af.name}: missing required frontmatter key '{req_key}'")
            model_val = fields.get("model", "inherit")
            if model_val not in VALID_AGENT_MODELS:
                errors.append(
                    f".agents/agents/{af.name}: invalid model '{model_val}' "
                    f"(expected one of {sorted(VALID_AGENT_MODELS)})"
                )
    else:
        warnings.append("missing .agents/agents/ directory")

    # 4. Adversarial benchmark JSON schema check
    adv_dir = root / "tests" / "adversarial_cases"
    if adv_dir.is_dir():
        adv_files = sorted(adv_dir.glob("*.json"))
        metrics["adversarial_cases"] = len(adv_files)
        for jf in adv_files:
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"tests/adversarial_cases/{jf.name}: invalid JSON ({exc})")
                continue
            if not isinstance(data, dict):
                errors.append(f"tests/adversarial_cases/{jf.name}: root must be a JSON object")
                continue
            missing = sorted(REQUIRED_ADVERSARIAL_KEYS - set(data.keys()))
            if missing:
                errors.append(
                    f"tests/adversarial_cases/{jf.name}: missing required keys {missing}"
                )

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AGENTS.md and .agents/ hygiene.")
    parser.add_argument("--root", default=str(WORKSPACE_DIR), help="Repository root path")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    result = run_hygiene_check(Path(args.root))
    failed = (not result["ok"]) or (args.strict and len(result["warnings"]) > 0)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if not failed else "FAIL"
        print(f"[agents-hygiene] {status} | metrics={result['metrics']}")
        for w in result["warnings"]:
            print(f"  WARN: {w}")
        for e in result["errors"]:
            print(f"  ERROR: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
