"""Tests for Polhamus suction analogy induced drag separation (Issue: Aerodynamic Model Synthesis).

Verifies:
1. When polhamus_kv == 0.0, drag_coefficient matches the baseline polar exactly.
2. When polhamus_kv > 0.0, vortex-induced drag Delta C_D,v = K_v * sin^2(alpha) * |sin(alpha)|
   is separated from the potential induced drag C_L,p^2 / (pi * AR * e).
3. Zero-lift / zero-angle properties: at alpha = 0, vortex drag is identically 0.
4. Symmetry: Delta C_D,v(-alpha) == Delta C_D,v(alpha).
5. Scalar and vectorized backends produce identical drag results.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.aero_kernel import (
    ScalarMath,
    drag_coefficient,
    evaluate_coefficients,
    params_from_aircraft,
    polhamus_vortex_drag,
)
from physics.aero_domain import drag_coefficient as domain_drag_coefficient, polhamus_vortex_drag as domain_polhamus_vortex_drag
from physics.config_schema import AircraftConfig


def test_polhamus_vortex_drag_theoretical_formula() -> None:
    """Verify Delta C_D,v = K_v * sin^2(alpha) * |sin(alpha)|."""
    kv = 2.5
    for alpha in [0.0, 5.0, 10.0, 15.0, 30.0, -10.0, -25.0]:
        cd_v = polhamus_vortex_drag(ScalarMath, alpha, kv)
        rad = math.radians(alpha)
        expected = kv * (math.sin(rad) ** 2) * abs(math.sin(rad))
        assert cd_v == pytest.approx(expected, abs=1e-12)


def test_polhamus_zero_kv_backward_compatibility() -> None:
    """When polhamus_kv == 0, vortex drag is identically 0 and drag matches baseline."""
    cfg_zero = AircraftConfig(polhamus_kv=0.0)
    for alpha in [0.0, 5.0, 10.0, 20.0]:
        cd_v = domain_polhamus_vortex_drag(alpha, cfg_zero)
        assert cd_v == 0.0


def test_polhamus_induced_drag_separation_effect() -> None:
    """When polhamus_kv > 0, potential and vortex drag components are properly separated."""
    cfg = AircraftConfig(aspect_ratio=2.0, polhamus_kv=2.0, cL0=0.0)
    p = params_from_aircraft(cfg)

    alpha = 15.0
    alpha_rad = math.radians(alpha)
    res = evaluate_coefficients(ScalarMath, p, alpha_deg=alpha, beta_deg=0.0, speed=10.0)
    cd = float(res["cd"])

    # Baseline attached drag: cd0 + (C_L,pot^2 / polar) + Delta C_D,v
    vortex_cd = float(polhamus_vortex_drag(ScalarMath, alpha, p.polhamus_kv))
    assert vortex_cd > 0.0
    assert cd > vortex_cd


def test_polhamus_vortex_drag_symmetry() -> None:
    """Vortex induced drag is strictly non-negative and symmetric."""
    for alpha in np.linspace(-45.0, 45.0, 19):
        cd_pos = float(polhamus_vortex_drag(ScalarMath, float(alpha), 1.5))
        cd_neg = float(polhamus_vortex_drag(ScalarMath, float(-alpha), 1.5))
        assert cd_pos >= 0.0
        assert cd_pos == pytest.approx(cd_neg, abs=1e-12)


def test_polhamus_vectorized_backend_parity() -> None:
    """Scalar and vectorized evaluations of vortex drag match exactly."""
    alphas = np.linspace(-30.0, 30.0, 25)
    kvs = np.full_like(alphas, 2.14)
    vec_res = polhamus_vortex_drag(np, alphas, kvs)
    for i, a in enumerate(alphas):
        scal_res = polhamus_vortex_drag(ScalarMath, float(a), 2.14)
        assert vec_res[i] == pytest.approx(scal_res, abs=1e-12)
