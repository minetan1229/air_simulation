"""Distill append-only `.agents/knowledge.d/*.md` fragments into `.agents/Knowledge.md`.

Designed to run ONLY at the serial integration gate (Phase 5/6) so concurrent
sessions never experience merge conflicts on `.agents/Knowledge.md` or
`.agents/Project.md`.

Usage:
    python tools/distill_agents_state.py --dry-run
    python tools/distill_agents_state.py --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE_DIR = Path(__file__).resolve().parent.parent


def _extract_bullets(text: str) -> List[str]:
    bullets: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- ") or line.startswith("* "):
            norm = "- " + line[2:].strip()
            if norm != "- ":
                bullets.append(norm)
    return bullets


def distill_knowledge(
    root: Path,
    apply: bool = False,
    archive_fragments: bool = True,
    line_budget: int = 80,
) -> Dict[str, Any]:
    kb_path = root / ".agents" / "Knowledge.md"
    frag_dir = root / ".agents" / "knowledge.d"
    archive_dir = root / ".agents" / "archive" / "knowledge_fragments"

    existing_text = kb_path.read_text(encoding="utf-8") if kb_path.exists() else "# Knowledge Base\n"
    existing_lines = set(line.strip() for line in existing_text.splitlines() if line.strip())

    fragments = sorted(frag_dir.glob("*.md")) if frag_dir.is_dir() else []
    new_bullets: List[str] = []
    seen_new: set[str] = set()
    for frag in fragments:
        for b in _extract_bullets(frag.read_text(encoding="utf-8")):
            if b not in existing_lines and b not in seen_new:
                new_bullets.append(b)
                seen_new.add(b)

    updated_text = existing_text.rstrip()
    if new_bullets:
        section_header = "## 5. 蒸留済み追加知見（knowledge.d より統合）"
        if section_header not in updated_text:
            updated_text += f"\n\n{section_header}\n"
        else:
            updated_text += "\n"
        updated_text += "\n".join(new_bullets) + "\n"
    else:
        updated_text += "\n"

    resulting_lines = len(updated_text.splitlines())
    budget_warning = resulting_lines > line_budget

    if apply and fragments:
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        kb_path.write_text(updated_text, encoding="utf-8")
        if archive_fragments:
            archive_dir.mkdir(parents=True, exist_ok=True)
            for frag in fragments:
                shutil.move(str(frag), str(archive_dir / frag.name))

    return {
        "fragments_found": [f.name for f in fragments],
        "new_bullets_count": len(new_bullets),
        "new_bullets": new_bullets,
        "resulting_lines": resulting_lines,
        "budget_warning": budget_warning,
        "applied": bool(apply and fragments),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Distill .agents/knowledge.d/*.md into .agents/Knowledge.md.")
    parser.add_argument("--root", default=str(WORKSPACE_DIR))
    parser.add_argument("--apply", action="store_true", help="Write changes and archive processed fragments")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying files (default)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    res = distill_knowledge(Path(args.root), apply=bool(args.apply and not args.dry_run))
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        mode = "APPLIED" if res["applied"] else "DRY-RUN"
        print(
            f"[distill] {mode} | fragments={len(res['fragments_found'])} "
            f"new_bullets={res['new_bullets_count']} lines={res['resulting_lines']}"
        )
        if res["budget_warning"]:
            print("  WARN: .agents/Knowledge.md exceeds 80-line budget; consider archiving older items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
