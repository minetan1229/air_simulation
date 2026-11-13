"""Tests for physics correctness of low-speed pushover torque and ground effect isolation.

1. Test that at zero airspeed (speed = 0.0), aerodynamic pitching moment is strictly zero
   regardless of angle of attack (pushover zero-velocity extinction).
2. Test that ground effect reduction applies only to potential induced drag,
   leaving Polhamus vortex drag unaffected by ground proximity factor.
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
from physics.config_schema import AircraftConfig


def test_pushover_extinction_at_zero_airspeed() -> None:
    """At zero airspeed (speed = 0.0), pitching moment must be identically 0.0."""
    cfg = AircraftConfig(
        aspect_ratio=2.5,
        polhamus_kv=1.8,
        cm_alpha=-0.5,
        cm0=0.0,
    )
    p = params_from_aircraft(cfg)

    for alpha in [5.0, 10.0, 20.0, -15.0]:
        res = evaluate_coefficients(
            ScalarMath,
            p,
            alpha_deg=alpha,
            beta_deg=0.0,
            speed=0.0,
            density=1.225,
        )
        assert res["pitching_nm"] == pytest.approx(0.0, abs=1e-12), (
            f"At speed=0.0 and alpha={alpha}, pitching moment should be 0.0, got {res['pitching_nm']}"
        )


def test_pushover_active_in_low_speed_stall_region() -> None:
    """At low speeds (e.g. 1.5 m/s) with high alpha, pushover moment should still be active and restoring (nose-down)."""
    cfg = AircraftConfig(
        aspect_ratio=2.5,
        polhamus_kv=1.8,
        cm_alpha=-0.5,
        cm0=0.0,
    )
    p = params_from_aircraft(cfg)

    # At 1.5 m/s and alpha = +15 deg (stalled/high alpha), pitching moment should be negative (nose-down)
    res = evaluate_coefficients(
        ScalarMath,
        p,
        alpha_deg=15.0,
        beta_deg=0.0,
        speed=1.5,
        density=1.225,
    )
    assert res["pitching_nm"] < 0.0, f"Expected restoring pushover moment, got {res['pitching_nm']}"


def test_ground_effect_applies_only_to_potential_induced_drag() -> None:
    """Polhamus vortex drag should not be scaled by ground effect ratio."""
    cfg = AircraftConfig(
        aspect_ratio=2.0,
        polhamus_kv=2.5,
        cL0=0.0,
    )
    p = params_from_aircraft(cfg)
    p.ground_effect_enabled = True
    alpha = 8.0  # Well within attached flow regime (stall_alpha is typically ~14 deg)

    # High altitude (ground_ratio = 1.0, out of ground effect)
    res_free = evaluate_coefficients(
        ScalarMath,
        p,
        alpha_deg=alpha,
        beta_deg=0.0,
        speed=10.0,
        ground_height_m=100.0,
    )
    # Low altitude (in ground effect, height = 0.01 m, span = 0.3 m)
    res_ground = evaluate_coefficients(
        ScalarMath,
        p,
        alpha_deg=alpha,
        beta_deg=0.0,
        speed=10.0,
        ground_height_m=0.01,
    )

    cd_free = float(res_free["cd"])
    cd_ground = float(res_ground["cd"])
    vortex_cd = float(polhamus_vortex_drag(ScalarMath, alpha, p.polhamus_kv))

    # In ground effect, overall drag is reduced because induced drag decreases
    assert cd_ground < cd_free

    from physics.aero_kernel import ground_effect_factor
    ground_ratio = float(ground_effect_factor(ScalarMath, 0.01, float(p.span), True))
    assert ground_ratio < 1.0

    delta_cd_actual = cd_free - cd_ground
    # Expected reduction only from potential lift
    cl_pot = float(res_free["cl"]) - float(p.polhamus_kv) * (math.sin(math.radians(alpha)) ** 2) * math.cos(math.radians(alpha))
    polar = float(p.aspect) * math.pi * float(p.oswald)
    expected_pot_reduction = (cl_pot ** 2 / polar) * (1.0 - ground_ratio)

    assert delta_cd_actual == pytest.approx(expected_pot_reduction, rel=1e-5), (
        f"Drag reduction {delta_cd_actual} should match potential induced drag reduction {expected_pot_reduction}, "
        f"but got excess reduction indicating vortex drag was improperly scaled by ground effect."
    )


def test_domain_and_kernel_drag_coefficient_parity_with_ground_effect() -> None:
    """Verify aero_domain.drag_coefficient and aero_kernel.drag_coefficient agree and do not scale vortex drag by ground effect."""
    from physics.aero_domain import drag_coefficient as domain_cd
    from physics.aero_kernel import drag_coefficient as kernel_cd

    cfg = AircraftConfig(aspect_ratio=2.5, polhamus_kv=2.0, cL0=0.0, fold_trailing_deg=0.0)
    p = params_from_aircraft(cfg)
    alpha = 10.0

    # Test out of ground effect
    from physics.aero_domain import DRAG_POLAR_DEFAULT_REYNOLDS
    cd_free_kernel = float(
        kernel_cd(
            ScalarMath,
            alpha,
            p.cL0,
            p.cl_alpha,
            p.stall_alpha_deg,
            p.stall_sharpness,
            p.cl_max,
            cd0=p.cd0,
            reynolds=DRAG_POLAR_DEFAULT_REYNOLDS,
            re_exponent=p.re_cd0_exponent,
            polar_constant=p.aspect * math.pi * p.oswald,
            ground_ratio=1.0,
            polhamus_kv=p.polhamus_kv,
        )
    )
    cd_free_domain = float(domain_cd(alpha, cfg, ground_height_m=100.0))
    assert cd_free_kernel == pytest.approx(cd_free_domain, rel=1e-5)

    # Test in strong ground effect
    ground_ratio = 0.65
    cd_ge_kernel = float(
        kernel_cd(
            ScalarMath,
            alpha,
            p.cL0,
            p.cl_alpha,
            p.stall_alpha_deg,
            p.stall_sharpness,
            p.cl_max,
            cd0=p.cd0,
            reynolds=DRAG_POLAR_DEFAULT_REYNOLDS,
            re_exponent=p.re_cd0_exponent,
            polar_constant=p.aspect * math.pi * p.oswald,
            ground_ratio=ground_ratio,
            polhamus_kv=p.polhamus_kv,
        )
    )
    # Vortex drag
    vortex_cd = float(polhamus_vortex_drag(ScalarMath, alpha, p.polhamus_kv))
    pot_reduction = cd_free_kernel - cd_ge_kernel

    # The reduction must be strictly equal to potential induced drag reduction:
    cl_pot = float(p.cl_alpha * math.radians(alpha))
    polar = float(p.aspect * math.pi * p.oswald)
    expected_reduction = (cl_pot ** 2 / polar) * (1.0 - ground_ratio)
    assert pot_reduction == pytest.approx(expected_reduction, rel=1e-5)

    # And verify vortex drag is NOT reduced
    assert (cd_free_kernel - cd_ge_kernel) < (cd_free_kernel - p.cd0) * (1.0 - ground_ratio)

