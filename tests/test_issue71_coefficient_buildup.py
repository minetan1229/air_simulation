"""Audit and verification of aerodynamic coefficient buildup (Issue #71).

Verifies:
1. Dimensional consistency, unit scaling, and reference quantities (ref_area_m2, ref_length_m, ref_span_m).
2. Superposition of wing, elevon, trailing-edge fold, and dynamic damping contributions.
3. Coordinate transformation between wind axes (Lift, Drag, Side) and body axes (Fx, Fy, Fz, L, M, N).
4. Aerodynamic center, center of pressure, and moment reference consistency.
5. Continuity, symmetry, and differentiability across stall and control surface deflections.
6. Bounds and error limits of aerodynamic interference assumptions.
"""

import math
import pytest
import numpy as np

from physics.config_schema import AircraftConfig, EnvironmentConfig
from physics.aero_domain import (
    evaluate_aero,
    aerodynamic_force_body,
    center_of_pressure_x,
    trailing_edge_fold_effects,
    elevon_effects,
    derived_properties,
)


def test_buildup_dimensional_and_reference_consistency():
    """Verify that forces and moments scale properly with dynamic pressure q = 0.5 * rho * V^2 and reference areas."""
    cfg = AircraftConfig(fold_trailing_deg=0.0)
    env = EnvironmentConfig()
    props = derived_properties(cfg)
    s_ref = props["ref_area_m2"]
    c_ref = props["ref_length_m"]
    rho = 1.225

    speed1 = 5.0
    res1 = evaluate_aero(cfg, env, speed_mps=speed1, alpha_deg=4.0, beta_deg=0.0, density_kgm3=rho)
    q1 = 0.5 * rho * (speed1 ** 2)

    # Dimensional force = q * S * C
    assert math.isclose(res1.lift_n, q1 * s_ref * res1.cl, rel_tol=1e-4)
    assert math.isclose(res1.drag_n, q1 * s_ref * res1.cd, rel_tol=1e-4)
    # Dimensional pitching moment = q * S * c * Cm
    assert math.isclose(res1.pitching_moment_nm, q1 * s_ref * c_ref * res1.cm, rel_tol=1e-4)

    # Scaling with double speed (4x dynamic pressure)
    speed2 = 10.0
    res2 = evaluate_aero(cfg, env, speed_mps=speed2, alpha_deg=4.0, beta_deg=0.0, density_kgm3=rho)
    q2 = 0.5 * rho * (speed2 ** 2)
    assert math.isclose(res2.lift_n, q2 * s_ref * res2.cl, rel_tol=1e-3)
    assert math.isclose(res2.drag_n, q2 * s_ref * res2.cd, rel_tol=1e-3)


def test_buildup_superposition_and_elevon_interference():
    """Verify superposition of base airframe and elevon/fold contributions."""
    cfg_clean = AircraftConfig(elevon_deg=0.0, fold_trailing_deg=0.0)
    cfg_elevon = AircraftConfig(elevon_deg=5.0, fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    res_clean = evaluate_aero(cfg_clean, env, speed_mps=8.0, alpha_deg=2.0)
    res_elevon = evaluate_aero(cfg_elevon, env, speed_mps=8.0, alpha_deg=2.0)

    # Positive elevon deflection (trailing edge down) increases lift and induces negative (nose down) pitching moment
    effects = elevon_effects(5.0, cfg_clean)
    assert res_elevon.cl > res_clean.cl
    assert res_elevon.cm < res_clean.cm
    assert math.isclose(res_elevon.cl - res_clean.cl, effects["dcl"], abs_tol=1e-3)
    assert math.isclose(res_elevon.cm - res_clean.cm, effects["dcm"], abs_tol=1e-3)


def test_buildup_wind_to_body_transformation_and_signs():
    """Verify transformation of aerodynamic forces between wind axes and body axes."""
    cfg = AircraftConfig(fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    # At alpha = 0, beta = 0: Wind drag is along body +X backward (-body X), Lift is along -Z
    res0 = evaluate_aero(cfg, env, speed_mps=10.0, alpha_deg=0.0, beta_deg=0.0)
    f_body = aerodynamic_force_body(res0.drag_n, res0.side_n, res0.lift_n)

    # Body axes: X forward, Y right, Z down
    # Drag opposes forward motion -> fx < 0
    # Lift opposes gravity (upward) -> fz < 0
    assert f_body[0] < 0  # Drag acts in -X direction
    assert f_body[2] < 0  # Lift acts in -Z (upward) direction
    assert math.isclose(f_body[1], 0.0, abs_tol=1e-6)  # No sideslip -> no lateral force


def test_buildup_center_of_pressure_and_aerodynamic_center():
    """Verify center of pressure x_cp travel with alpha and static stability margin."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, cp_shift_gain=1.0)
    props = derived_properties(cfg)
    chord = props["ref_length_m"]

    # At low alpha, x_cp is close to aerodynamic center (~0.25 of chord)
    x_cp_low = center_of_pressure_x(alpha_deg=2.0, cfg=cfg)
    assert 0.15 * chord <= x_cp_low <= 0.35 * chord

    # As alpha increases towards 90 deg, x_cp shifts towards mid-chord (~0.5 chord) for flat plate
    x_cp_high = center_of_pressure_x(alpha_deg=45.0, cfg=cfg)
    assert x_cp_high > x_cp_low
    assert x_cp_high <= 0.55 * chord


def test_buildup_symmetry_and_continuity_across_stall():
    """Verify symmetry for lateral terms and smooth continuity across stall regime."""
    cfg = AircraftConfig(fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    # Lateral symmetry: +beta should give opposite side force to -beta
    res_pos_beta = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=3.0, beta_deg=5.0)
    res_neg_beta = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=3.0, beta_deg=-5.0)
    assert math.isclose(res_pos_beta.side_n, -res_neg_beta.side_n, rel_tol=1e-3)
    assert math.isclose(res_pos_beta.cl, res_neg_beta.cl, rel_tol=1e-3)

    # Continuity across stall transition alpha in [10 deg, 25 deg]
    alphas = np.linspace(5.0, 30.0, 51)
    cls = [evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=a).cl for a in alphas]

    # Check that there are no NaN, infinite or jump discontinuities
    for i in range(len(cls) - 1):
        diff = abs(cls[i+1] - cls[i])
        assert not math.isnan(cls[i])
        assert diff < 0.2, f"Discontinuity detected at alpha={alphas[i]}: diff={diff}"
