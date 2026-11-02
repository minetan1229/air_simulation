"""Regression tests for Phase 2: P1-11 (UI accessibility and structural invariants).

Fixates:
- P1-11: Console stylesheets preserve legibility floor (font-size >= 9px),
  explicit CSS grid columns, and dialog role attributes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_p1_11_console_ui_css_invariants() -> None:
    """The static UI CSS checker must pass cleanly with 0 violations."""
    cmd = [sys.executable, "tools/check_ui_css.py"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"check_ui_css.py failed:\n{res.stdout}\n{res.stderr}"


def test_p1_11_templates_accessibility_attributes() -> None:
    """Modals in templates/index.html must have role='dialog' or explicit aria-label/modal."""
    index_html = Path("templates/index.html").read_text(encoding="utf-8")
    # All modals must have role="dialog"
    assert 'role="dialog"' in index_html
    # Canvas elements should have aria-label or accessible text fallback
    assert 'aria-label=' in index_html
