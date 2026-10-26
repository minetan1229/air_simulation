"""Static invariants of the console stylesheet.

The browser is the only place where the real layout lives, but two classes of
regression are cheap to catch statically and expensive to notice by eye:

* **legibility floor** -- the console is a dense research tool, so the type
  scale is allowed to be small.  Below 9 px it stops being small and starts
  being unreadable, and the labels are a mix of Japanese and Latin text that
  needs the extra pixels.
* **implicit grid columns** -- a grid whose column is left to ``auto`` is
  sized by the *min-content* width of its widest row.  The top rail is a
  single flex row that is wider than a 1280 px viewport, so the implicit
  column pushed the whole console past the viewport and the right-hand
  action buttons (record, load, CSV, help) became unreachable.

The tool behind the assertions is ``tools/check_ui_css.py``; the same
invariants are run as an explicit step in both CI workflows.
"""

from __future__ import annotations

from tools.check_ui_css import (
    MIN_FONT_SIZE_PX,
    check_explicit_console_column,
    check_font_floor,
    check_inline_font_floor,
    check_top_rail_contracts,
)


def test_the_type_scale_stays_above_the_legibility_floor():
    """No stylesheet may declare a font size below 9 px."""
    findings = check_font_floor()
    assert not findings, "the console type scale drops below the floor:\n" + "\n".join(findings)


def test_the_template_does_not_inline_a_tiny_font_size():
    """The template may not inline a font size below 9 px either."""
    findings = check_inline_font_floor()
    assert not findings, "the template inlines a tiny font size:\n" + "\n".join(findings)


def test_the_console_grid_declares_an_explicit_column():
    """The console grid must shrink below the top rail's min-content width."""
    findings = check_explicit_console_column()
    assert not findings, "the console grid column is implicit:\n" + "\n".join(findings)


def test_the_top_rail_does_not_clip_its_readouts_or_hide_its_actions():
    """The top rail has to keep its read-outs and its actions reachable."""
    findings = check_top_rail_contracts()
    assert not findings, "the top rail contracts are violated:\n" + "\n".join(findings)


def test_the_floor_is_the_documented_one():
    """The floor is part of the contract; a silent change would hide it."""
    assert MIN_FONT_SIZE_PX == 9.0
