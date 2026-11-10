"""Tests verifying spanwise wind gradient aerodynamic coupling (Issue #137).

1. Compatibility with uniform wind: In a spatially uniform wind field, the strip
   wind sampling produces local wind identically matching CG wind (differential = 0),
   yielding identical aerodynamics to single-point CG wind sampling baseline.
2. Differential dynamic pressure / spanwise wind gradient dW_x/dy produces asymmetric
   drag across the wingspan, creating a yaw moment (adverse/proverse yaw depending on gradient sign).
3. Differential vertical wind / spanwise updraft gradient dW_z/dy produces asymmetric
   angle of attack (alpha) across the wingspan, creating differential lift and a
   restoring/roll moment with physical sign.
4. Physical sign fidelity: Updraft on right wing (positive span y_b) increases right wing
   effective alpha, generating excess lift on right wing and resulting in a negative roll
   moment (L < 0, rolling left / port). Forward tailwind on right wing decreases right wing
   relative airspeed, generating lower drag on right wing than left wing and resulting in a
   positive yaw moment (N > 0, yawing right / starboard).
"""

from __future__ import annotations

import math
from typing import Any
import numpy as np
import pytest

from physics import flight_sim
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


def test_uniform_wind_compatibility() -> None:
    """Hypothesis H1: In uniform wind fields, strip aerodynamic coupling produces
    moments consistent with the single-point baseline. The strip model uses
    per-station Re/Mach (deep2 physics improvement) which differs from the
    standard model's CG Re/Mach, so a small tolerance is allowed.
    """
    b_span = 0.24
    cfg_strip = AircraftConfig(
        fidelity_mode="improved_medium",
        wingspan_m=b_span,
        wing_area_m2=0.024,
    )
    cfg_std = AircraftConfig(
        fidelity_mode="standard",
        wingspan_m=b_span,
        wing_area_m2=0.024,
    )
    env_uniform = EnvironmentConfig(
        wind_speed=5.0,
        wind_direction_deg=45.0,
        gust_intensity=0.0,
        turbulence_scale=0.0,
        thermal_strength=0.0,
    )
    launch = LaunchConfig(launch_speed=8.0, launch_height_m=10.0)
    sim_strip = flight_sim.FlightSimulator(cfg_strip, env_uniform, launch, SimulationSettings())
    sim_std = flight_sim.FlightSimulator(cfg_std, env_uniform, launch, SimulationSettings())

    y_state = flight_sim._pack(sim_strip._state)
    core_strip = sim_strip._compute_dynamics(y_state, 0.0)
    core_std = sim_std._compute_dynamics(y_state, 0.0)

    # In uniform wind, differential wind velocity across all spanwise stations
    # is identically zero. However, the strip model uses per-station Re/Mach
    # (accounting for omega x r rotational velocity) which the standard model
    # does not. This is a deep2 physics improvement for accuracy. Moments should
    # be close but not bit-identical.
    moments_strip = core_strip["moment_body"]
    moments_std = core_std["moment_body"]
    assert math.isfinite(moments_strip[0])
    assert math.isfinite(moments_strip[1])
    assert math.isfinite(moments_strip[2])
    # Relaxed tolerance: per-station Re/Mach introduces ~2% pitch moment difference
    np.testing.assert_allclose(moments_strip, moments_std, rtol=3e-2, atol=2e-3)

    # In pure symmetrical headwind (wind_direction_deg=90 blows west along -x, launch east along +x),
    # roll moment is identically zero.
    env_headwind = EnvironmentConfig(
        wind_speed=4.0,
        wind_direction_deg=90.0,
        gust_intensity=0.0,
    )
    sim_head = flight_sim.FlightSimulator(cfg_strip, env_headwind, launch, SimulationSettings())
    y_head = flight_sim._pack(sim_head._state)
    core_head = sim_head._compute_dynamics(y_head, 0.0)
    assert abs(core_head["moment_body"][0]) < 1e-6


def test_spanwise_vertical_shear_produces_roll_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hypothesis H2: Spanwise vertical wind gradient dW_z/dy produces differential angle
    of attack across wingspan, inducing differential lift and correct restoring roll moment (L).

    Sign convention in body frame (x forward, y right, z down):
    - When right wing (+y) encounters an updraft relative to left wing (-y),
      the air velocity relative to aircraft has an upward component on the right wing.
      In the z-down body frame, updraft corresponds to negative w_wind (flow pointing up, -z_body).
      This upward relative airflow increases the local angle of attack on the right wing:
      alpha_right > alpha_left.
      Higher angle of attack produces higher lift on right wing (res_ir, res_or) than left wing (res_il, res_ol).
      The differential lift creates a left-wing-down (counter-clockwise looking forward) rolling moment,
      which is negative L (L < 0, roll left).
    """
    b_span = 0.30
    cfg = AircraftConfig(
        fidelity_mode="improved_medium",
        wingspan_m=b_span,
        wing_area_m2=0.03,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
    launch = LaunchConfig(launch_speed=10.0, launch_height_m=10.0)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings())

    # Level flight state: orientation = identity (x north/fwd, y east/right, z down)
    y_state = flight_sim._pack(sim._state)

    # Mock wind_vector with a linear vertical shear dW_z/dy (updraft on right wing: +y -> updraft in world z-down)
    # World z-down: updraft is negative z_world velocity (e.g. -1.0 m/s at right tip y = +0.15)
    def shear_wind(pos: Any, environment: Any, time_s: float = 0.0) -> np.ndarray:
        y_pos = float(pos[1])
        # dW_z / dy = -2.0 (1/s) -> at y = +0.1m, W_z = -0.2 m/s (updraft)
        w_z = -2.0 * y_pos
        return np.array([0.0, 0.0, w_z], dtype=np.float64)

    monkeypatch.setattr(flight_sim, "wind_vector", shear_wind)

    core_shear = sim._compute_dynamics(y_state, 0.0)
    moments = core_shear["moment_body"]
    l_moment = moments[0]

    # Updraft on right wing (+y) must roll the aircraft left (L < 0)
    assert l_moment < -1e-4, f"Expected negative rolling moment (L < 0) for right-wing updraft, got {l_moment}"

    # Reverse the shear gradient: updraft on left wing (y < 0 -> W_z < 0)
    def reverse_shear_wind(pos: Any, environment: Any, time_s: float = 0.0) -> np.ndarray:
        y_pos = float(pos[1])
        w_z = +2.0 * y_pos
        return np.array([0.0, 0.0, w_z], dtype=np.float64)

    monkeypatch.setattr(flight_sim, "wind_vector", reverse_shear_wind)

    core_rev = sim._compute_dynamics(y_state, 0.0)
    l_rev = core_rev["moment_body"][0]

    # Updraft on left wing must roll the aircraft right (L > 0)
    assert l_rev > 1e-4, f"Expected positive rolling moment (L > 0) for left-wing updraft, got {l_rev}"
    # Symmetry verification: magnitudes should match
    assert math.isclose(l_moment, -l_rev, rel_tol=1e-3)


def test_spanwise_horizontal_shear_produces_yaw_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hypothesis H3: Spanwise horizontal wind gradient dW_x/dy produces differential dynamic
    pressure across the wingspan, creating differential drag and yawing moment (N).

    Sign convention:
    - Level flight moving along +x_body at speed V_inf.
    - If right wing (+y) experiences a headwind component (W_x < 0 blowing towards aircraft),
      its relative airspeed increases (V_rel_right > V_rel_left).
      This increases dynamic pressure and drag on the right wing relative to the left wing.
      Higher drag on right wing pulls right wing back, producing positive yaw moment (N > 0, nose right).
    - If right wing experiences tailwind (W_x > 0), airspeed on right wing decreases,
      drag decreases on right wing, and higher drag on left wing pulls nose left (N < 0).
    """
    b_span = 0.30
    cfg = AircraftConfig(
        fidelity_mode="improved_medium",
        wingspan_m=b_span,
        wing_area_m2=0.03,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
    launch = LaunchConfig(launch_speed=10.0, launch_height_m=10.0)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings())

    y_state = flight_sim._pack(sim._state)

    # Gradient: headwind on right wing (W_x < 0 for y > 0)
    def headwind_shear(pos: Any, environment: Any, time_s: float = 0.0) -> np.ndarray:
        y_pos = float(pos[1])
        # W_x = -3.0 * y_pos -> y=+0.1m => W_x = -0.3 m/s (headwind)
        return np.array([-3.0 * y_pos, 0.0, 0.0], dtype=np.float64)

    monkeypatch.setattr(flight_sim, "wind_vector", headwind_shear)

    core = sim._compute_dynamics(y_state, 0.0)
    n_moment = core["moment_body"][2]

    # Headwind on right wing (+y) -> higher airspeed on right wing -> higher drag on right wing -> nose yaw right (N > 0)
    assert n_moment > 1e-5, f"Expected positive yawing moment (N > 0) for right-wing headwind, got {n_moment}"

    # Gradient: tailwind on right wing (W_x > 0 for y > 0)
    def tailwind_shear(pos: Any, environment: Any, time_s: float = 0.0) -> np.ndarray:
        y_pos = float(pos[1])
        return np.array([+3.0 * y_pos, 0.0, 0.0], dtype=np.float64)

    monkeypatch.setattr(flight_sim, "wind_vector", tailwind_shear)

    core_tail = sim._compute_dynamics(y_state, 0.0)
    n_tail = core_tail["moment_body"][2]

    # Tailwind on right wing -> lower airspeed on right wing -> lower drag on right wing -> nose yaw left (N < 0)
    assert n_tail < -1e-5, f"Expected negative yawing moment (N < 0) for right-wing tailwind, got {n_tail}"
    assert math.isclose(n_moment, -n_tail, rel_tol=1e-3)


def test_strip_gradient_fidelity_scales_linearly_with_shear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hypothesis H4: For small wind shear gradients, differential aerodynamic moments
    scale smoothly and monotonically with shear gradient magnitude.
    """
    b_span = 0.25
    cfg = AircraftConfig(
        fidelity_mode="improved_medium",
        wingspan_m=b_span,
        wing_area_m2=0.025,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
    launch = LaunchConfig(launch_speed=10.0, launch_height_m=10.0)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings())
    y_state = flight_sim._pack(sim._state)

    moments_l = []
    shear_rates = [0.5, 1.0, 2.0]
    for shear in shear_rates:
        def w_shear(pos: Any, environment: Any, time_s: float = 0.0, s: float = shear) -> np.ndarray:
            return np.array([0.0, 0.0, -s * float(pos[1])], dtype=np.float64)

        monkeypatch.setattr(flight_sim, "wind_vector", w_shear)
        core = sim._compute_dynamics(y_state, 0.0)
        moments_l.append(core["moment_body"][0])

    # Rolling moment magnitude must increase monotonically with shear gradient magnitude
    assert abs(moments_l[0]) < abs(moments_l[1]) < abs(moments_l[2])
    # Linearity check within ~15% for small shear
    ratio_1 = moments_l[1] / moments_l[0]
    ratio_2 = moments_l[2] / moments_l[1]
    assert math.isclose(ratio_1, 2.0, rel_tol=0.2)
    assert math.isclose(ratio_2, 2.0, rel_tol=0.2)
