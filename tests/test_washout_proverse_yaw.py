"""
Unit and physical verification tests for:
Wing-tip washout / twist producing Prandtl bell-shaped circulation
and proverse yaw (Cnp > 0) according to Bowers et al. (2016 NASA/TP-2016-219072).
"""
import math
import pytest
from physics import aero_kernel
from physics.config_schema import AircraftConfig, EnvironmentConfig
from physics.aero_kernel import ScalarMath


def test_prandtl_bell_proverse_yaw_zero_washout():
    """Verify flat wing (washout = 0) produces no proverse yaw increment."""
    delta_cnp = aero_kernel.prandtl_bell_proverse_yaw(washout_deg=0.0, cl=0.5, aspect_ratio=2.0)
    assert delta_cnp == pytest.approx(0.0, abs=1e-12)


def test_prandtl_bell_proverse_yaw_zero_lift():
    """Verify zero lift (cl = 0) produces no induced thrust differential (delta_cnp = 0)."""
    delta_cnp = aero_kernel.prandtl_bell_proverse_yaw(washout_deg=5.0, cl=0.0, aspect_ratio=2.0)
    assert delta_cnp == pytest.approx(0.0, abs=1e-12)


def test_prandtl_bell_proverse_yaw_scaling():
    """Verify washout produces positive (proverse) yaw with magnitude proportional to washout and CL."""
    # Base case: 4 degrees washout, CL = 0.4, AR = 2.0
    delta_cnp_base = aero_kernel.prandtl_bell_proverse_yaw(washout_deg=4.0, cl=0.4, aspect_ratio=2.0)
    # Proverse yaw must be positive (yaw in the direction of roll)
    assert delta_cnp_base > 0.0

    # Doubling washout doubles delta_cnp
    delta_cnp_2x_washout = aero_kernel.prandtl_bell_proverse_yaw(washout_deg=8.0, cl=0.4, aspect_ratio=2.0)
    assert math.isclose(delta_cnp_2x_washout, 2.0 * delta_cnp_base, rel_tol=1e-3)

    # Doubling CL doubles delta_cnp
    delta_cnp_2x_cl = aero_kernel.prandtl_bell_proverse_yaw(washout_deg=4.0, cl=0.8, aspect_ratio=2.0)
    assert math.isclose(delta_cnp_2x_cl, 2.0 * delta_cnp_base, rel_tol=1e-3)


def test_aircraft_config_washout_integration():
    """Verify AircraftConfig with washout_deg modifies cn_roll_rate in params_from_aircraft."""
    # Flat wing
    cfg_flat = AircraftConfig(washout_deg=0.0, cn_roll_rate=0.0)
    params_flat = aero_kernel.params_from_aircraft(cfg_flat)
    assert params_flat.cn_roll_rate == pytest.approx(0.0, abs=1e-12)

    # Wing with 5 deg tip reflex / washout
    cfg_washout = AircraftConfig(washout_deg=5.0, cn_roll_rate=0.0, cL0=0.3)
    params_washout = aero_kernel.params_from_aircraft(cfg_washout)
    assert params_washout.cn_roll_rate > 0.0
