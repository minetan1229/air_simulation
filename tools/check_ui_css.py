"""Static invariants of the console stylesheet.

The browser is the only place where the real layout lives, but two classes of
regression are cheap to catch statically and expensive to notice by eye:

* **legibility floor** -- the console is a dense research tool, so the type
  scale is allowed to be small.  Below 9 px it stops being small and starts
  being unreadable, and the text is a mix of Japanese and Latin labels that
  need the extra pixels.  A rule that drops below the floor is a defect even
  when the layout still fits.
* **implicit grid columns** -- a grid whose column is left to ``auto`` is
  sized by the *min-content* width of its widest row.  The top rail is a
  single flex row that is wider than a 1280 px viewport, so the implicit
  column pushed the whole console past the viewport and the right-hand
  action buttons (record, load, CSV, help) became unreachable.  The column
  has to be declared explicitly with ``minmax(0, ...)``.

Usage::

    python tools/check_ui_css.py

Exit code ``0`` when the invariants hold, ``1`` otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
CSS_DIR = REPO_ROOT / "static" / "css"
TEMPLATE = REPO_ROOT / "templates" / "index.html"

#: Smallest font size the console is allowed to use, in CSS pixels.
MIN_FONT_SIZE_PX = 9.0

#: ``font-size: 8.5px`` / ``font-size: 8px`` / ``font-size: .78rem``
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([0-9]*\.?[0-9]+)(px|rem|em)\b")
_INLINE_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([0-9]*\.?[0-9]+)px\b")


def _font_size_px(value: str, unit: str) -> float:
    if unit == "px":
        return float(value)
    # ``rem`` and ``em`` are resolved against the 13 px root font of the
    # console, which is the smallest size these units are used with.
    return float(value) * 13.0


def css_files(css_dir: Path = CSS_DIR) -> List[Path]:
    if not css_dir.is_dir():
        return []
    return sorted(css_dir.glob("*.css"))


def check_font_floor(css_dir: Path = CSS_DIR) -> List[str]:
    """Every ``font-size`` declaration at or below the floor."""
    findings: List[str] = []
    for path in css_files(css_dir):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _FONT_SIZE_RE.finditer(line):
                size = _font_size_px(match.group(1), match.group(2))
                if size < MIN_FONT_SIZE_PX:
                    findings.append(
                        f"{path.relative_to(REPO_ROOT)}:{number}: "
                        f"font-size {match.group(1)}{match.group(2)} "
                        f"({size:.1f}px) is below the {MIN_FONT_SIZE_PX:.0f}px floor"
                    )
    return findings


def check_inline_font_floor(template: Path = TEMPLATE) -> List[str]:
    """Inline ``font-size`` declarations in the template below the floor."""
    findings: List[str] = []
    if not template.is_file():
        return findings
    for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
        for match in _INLINE_FONT_SIZE_RE.finditer(line):
            size = float(match.group(1))
            if size < MIN_FONT_SIZE_PX:
                findings.append(
                    f"{template.relative_to(REPO_ROOT)}:{number}: "
                    f"inline font-size {size}px is below the {MIN_FONT_SIZE_PX:.0f}px floor"
                )
    return findings


def _strip_comments(text: str) -> str:
    """Remove the CSS comments so a comment cannot satisfy a declaration."""
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def _rule_body(text: str, selector: str) -> str:
    """The declarations of the first rule whose selector list contains
    ``selector`` (as a whole selector, not a prefix)."""
    text = _strip_comments(text)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        parts = [part.strip() for part in match.group(1).split(",")]
        if selector in parts:
            return match.group(2)
    return ""


def check_top_rail_contracts(css_dir: Path = CSS_DIR) -> List[str]:
    """The top rail must not clip its own read-outs or hide its actions.

    Two defects came out of the 1280 px audit, both invisible to a
    stylesheet linter:

    * the read-outs were shrinkable (``flex: 0 1 auto``).  The flex
      distribution handed them one pixel less than their content and every
      value ended in an ellipsis -- "READY" was shown as "REA...".
    * the action group was aligned with a plain ``flex-end``.  When the
      actions did not fit, the *first* group was pushed out of the box on
      the left, where it was unreachable (the rail's scrollbar is hidden).
      ``safe flex-end`` keeps the right alignment while it fits and falls
      back to the start edge otherwise.
    """
    findings: List[str] = []
    for path in css_files(css_dir):
        text = path.read_text(encoding="utf-8")
        readouts = _rule_body(text, ".toprail-readouts")
        if readouts:
            if re.search(r"flex\s*:\s*0\s+1\s+auto", readouts):
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}: .toprail-readouts is shrinkable; "
                    "the flex distribution then clips every value into an ellipsis"
                )
        actions = _rule_body(text, ".toprail-actions")
        if actions:
            if "justify-content: safe flex-end" not in actions:
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}: .toprail-actions is not aligned with "
                    "'safe flex-end'; an overflowing rail pushes its first group out of "
                    "the box, where it cannot be reached"
                )
    return findings


def check_explicit_console_column(css_dir: Path = CSS_DIR) -> List[str]:
    """The console grid has to declare its column explicitly."""
    findings: List[str] = []
    for path in css_files(css_dir):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
            selectors = [part.strip() for part in match.group(1).split(",")]
            # Only the *base* rule defines the shell.  A mode-specific rule
            # such as ``body[data-mode="research"] .console`` may override the
            # rows without repeating the columns.
            if ".console" not in selectors:
                continue
            body = match.group(2)
            if "display" not in body or "grid" not in body:
                continue
            if "grid-template-columns" not in body:
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}: the .console grid declares no "
                    "grid-template-columns; the implicit column is sized by the "
                    "min-content width of the top rail and overflows the viewport"
                )
                continue
            columns = re.search(r"grid-template-columns\s*:\s*([^;]+);", body)
            if columns is None:
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}: the .console grid-template-columns "
                    "could not be read"
                )
                continue
            value = columns.group(1)
            if "minmax(0" not in value.replace(" ", ""):
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}: the .console grid-template-columns "
                    f"is '{value.strip()}'; it has to use minmax(0, ...) so the column "
                    "can shrink below the min-content width of the top rail"
                )
    return findings


def check() -> Tuple[List[str], List[str], List[str], List[str]]:
    """Return ``(font, inline_font, grid, top_rail)`` findings."""
    return (
        check_font_floor(),
        check_inline_font_floor(),
        check_explicit_console_column(),
        check_top_rail_contracts(),
    )


def main() -> int:
    font_findings, inline_findings, grid_findings, rail_findings = check()
    findings = font_findings + inline_findings + grid_findings + rail_findings
    for finding in findings:
        print(finding)
    if findings:
        print(f"FAILED: {len(findings)} console style invariant(s) violated")
        return 1
    print(
        f"OK: the console type scale stays at or above {MIN_FONT_SIZE_PX:.0f}px, the "
        "console grid declares an explicit column and the top rail keeps its "
        "read-outs and its actions reachable"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
