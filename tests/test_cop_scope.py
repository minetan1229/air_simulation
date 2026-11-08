"""Issue #109: CoP simplified-model scope guard.

Current rule shifts 0.25c..0.50c via smoothstep in |alpha|/alpha_stall only,
without Re/shape/history. Guard ensures the range is bounded and documents
that separation-bubble causation is unproven (no invented correlation).
"""
from __future__ import annotations

from physics.aero_kernel import ScalarMath, center_of_pressure_fraction


def test_cop_bounded_and_monotone() -> None:
    vals = [float(center_of_pressure_fraction(ScalarMath, a, 14.0, 1.0)) for a in (0.0, 7.0, 14.0, 20.0)]
    assert vals == sorted(vals)
    assert 0.25 <= min(vals) and max(vals) <= 0.51


def test_cop_zero_gain_pins_quarter() -> None:
    assert float(center_of_pressure_fraction(ScalarMath, 14.0, 14.0, 0.0)) == 0.25
