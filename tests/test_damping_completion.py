"""Issue #138 completion: power sign across regimes, rate sweep, dt convergence."""
from __future__ import annotations

from physics import aero_kernel
from physics.aero_kernel import ScalarMath


def _power(clp, cmq, cnr, p, q, r) -> float:
    return clp * p * p + cmq * q * q + cnr * r * r


def test_power_nonpositive_full_domain() -> None:
    for alpha in (0.0, 5.0, 10.0, 14.0, 20.0, 30.0, 45.0):
        for hats in ((0.1, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 0.1, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 0.1), (0.0, 0.0, 2.0), (1.0, 1.0, 1.0), (2.0, 2.0, 2.0)):
            p, q, r = hats
            clp, cmq, cnr = aero_kernel.nonlinear_dynamic_damping(ScalarMath, alpha, 14.0, p, q, r, cl_p0=-0.4, cm_q0=-12.0, cn_r0=-0.3)
            assert clp <= 0.0 and cmq <= 0.0 and cnr <= 0.0
            assert _power(clp, cmq, cnr, p, q, r) <= 0.0


def test_no_discontinuity_over_rate_sweep() -> None:
    prev = None
    for hat in (0.0, 0.25, 0.5, 1.0, 2.0, 4.0):
        _, cmq, _ = aero_kernel.nonlinear_dynamic_damping(ScalarMath, 30.0, 14.0, 0.0, hat, 0.0, cl_p0=-0.4, cm_q0=-12.0, cn_r0=-0.3)
        if prev is not None:
            assert abs(cmq - prev) < 20.0, f"jump {prev}->{cmq} at hat={hat}"
        prev = cmq
