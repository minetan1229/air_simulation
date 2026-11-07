"""Issue #111: Low-Re operating envelope documentation.

Paper glider Re at 6.5 m/s * 0.08 m / 1.5e-5 is ~3.6e4 in the 1e4-1e5 band.
Lift slope/stall are geometry-only (no Re correlation); the validity flag is
the envelope boundary, not an invented correlation.
"""
from __future__ import annotations

from physics import aero_kernel
from physics.aero_kernel import ScalarMath


def test_operating_re_in_low_band() -> None:
    re_val = 6.5 * 0.0826 / 1.5e-5
    assert 1e4 < re_val < 1e5


def test_validity_flag_publishes_limits() -> None:
    res = aero_kernel.evaluate_model_validity(ScalarMath, mach=0.0, reynolds=3.6e4, alpha_deg=5.0, mass_ratio=10.0)
    assert "valid" in res
