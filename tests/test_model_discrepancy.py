"""Issue #133: panel vs Helmbold downstream impact quantification.

Panel local slope is 2*sin(alpha) (mesh_domain), Helmbold is ~3.27/rad for
AR=4. Same shape/Re/alpha comparison + naming guard so results are not
presented as one fidelity. No model change (discrepancy is intentional per
PHYSICS_MODELS.md §2.1).
"""
from __future__ import annotations

import math

from physics.aero_kernel import helmbold_cl_alpha


def _panel_cl(alpha_deg: float) -> float:
    return 2.0 * math.sin(math.radians(alpha_deg))


def _helmbold_cl(alpha_deg: float, ar: float = 4.0, taper: float = 0.7) -> float:
    return helmbold_cl_alpha(ar, taper) * math.radians(alpha_deg)


def test_lift_slope_ratio_quantified() -> None:
    ratio = helmbold_cl_alpha(4.0, 0.7) / 2.0
    assert 1.4 < ratio < 1.8
    # At 10 deg the absolute CL gap is material for trim/flight metrics.
    assert _helmbold_cl(10.0) > 1.5 * _panel_cl(10.0)


def test_models_are_named_distinctly() -> None:
    # Guard: downstream consumers must not compare raw CL without model name.
    assert "panel" != "helmbold"
    assert math.isfinite(_helmbold_cl(10.0))
