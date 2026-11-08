"""Issue #110: ground-effect scope guard.

Current model applies Wieselsberger sigma to induced drag only; lift/pitch
changes are not represented. This documents that limitation and the h/b
envelope without adding an ungrounded lift correction.
"""
from __future__ import annotations

from physics import aero_kernel
from physics.aero_kernel import ScalarMath


def test_sigma_monotone_and_limits() -> None:
    span = 0.22
    hi = float(aero_kernel.ground_effect_factor(ScalarMath, 2.2, span))  # h/b=10
    lo = float(aero_kernel.ground_effect_factor(ScalarMath, 0.022, span))  # h/b=0.1
    assert hi > lo
    assert 0.0 <= lo < hi <= 1.0


def test_disabled_returns_unity() -> None:
    assert float(aero_kernel.ground_effect_factor(ScalarMath, 0.022, 0.22, enabled=False)) == 1.0
