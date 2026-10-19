"""Splice the kernel-backed body into ``physics/aero_domain.evaluate_aero``.

One-shot maintenance helper: it replaces the hand-written coefficient block
of :func:`physics.aero_domain.evaluate_aero` with the delegation to
:mod:`physics.aero_kernel`.  Kept as a tool so the transformation is
reviewable and repeatable instead of a giant hand edit.
"""

from __future__ import annotations

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "physics" / "aero_domain.py"
BLOCK = ROOT / ".tmp_evaluate_aero_block.txt"

START = "    compressible = bool(env.compressibility_enabled)\n"
RETURN = "    return AeroResult(\n"


def main() -> int:
    source = io.open(TARGET, encoding="utf-8").read()
    lines = source.splitlines(keepends=True)
    try:
        start = lines.index(START)
    except ValueError:
        print("start marker not found -- already applied?")
        return 0
    try:
        ret = lines.index(RETURN, start)
    except ValueError:
        print("return marker not found")
        return 1
    end = ret
    while end < len(lines) and lines[end].rstrip("\n") != "    )":
        end += 1
    if end >= len(lines):
        print("closing parenthesis not found")
        return 1
    block = io.open(BLOCK, encoding="utf-8").read()
    new_lines = lines[:start] + [block] + lines[end + 1 :]
    io.open(TARGET, "w", encoding="utf-8", newline="\n").write("".join(new_lines))
    print(f"replaced lines {start + 1}..{end + 1} with the kernel delegation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
