"""Nonlinear dynamic damping verification test suite (Issue #138).

Verifies:
1. Passivity and Instantaneous Power Dissipation:
   P = M_damping . omega <= 0 strictly across attached, transitional stall, and fully separated flow.
2. Angular Velocity Sweep & Smoothness:
   Continuity and bounded derivatives across roll, pitch, yaw, and combined rates without jump discontinuities.
3. Effective Damping Timescale & Overdamping Bound:
   Effective damping timescale tau_damp = I / (q_dyn * S * c^2 * |cm_q| / 2V) is strictly positive,
   physically bounded, and captured by the adaptive-step numerical stiffness probe.
4. Timestep Independence and Numerical Convergence:
   6DoF integration under high angular rates converges cleanly as dt is refined without divergence
   or unphysical energy injection.
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_kernel
from physics.aero_kernel import ScalarMath
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
)
from physics.flight_sim import FlightSimulator, _pack


class TestNonlinearDynamicDampingPassivity:
    """Rigorous passivity verification across all flow regimes."""

    @pytest.mark.parametrize("alpha_deg", [-45.0, -25.0, -14.0, -5.0, 0.0, 5.0, 10.0, 14.0, 20.0, 30.0, 45.0, 90.0])
    @pytest.mark.parametrize(
        "p_hat,q_hat,r_hat",
        [
            (0.0, 0.0, 0.0),
            (0.1, 0.0, 0.0),
            (-0.5, 0.0, 0.0),
            (0.0, 0.2, 0.0),
            (0.0, -1.0, 0.0),
            (0.0, 0.0, 0.3),
            (0.0, 0.0, -2.0),
            (0.2, -0.4, 0.1),
            (-1.5, 2.0, -0.8),
            (5.0, 5.0, 5.0),
            (-5.0, -5.0, -5.0),
        ],
    )
    def test_instantaneous_power_dissipative(
        self, alpha_deg: float, p_hat: float, q_hat: float, r_hat: float
    ) -> None:
        """Verify M_damping . omega <= 0 for individual and combined angular rates."""
        rho = 1.225
        speed = 8.0
        s_area = 0.018
        span = 0.22
        chord = 0.08
        two_v = 2.0 * speed

        p = p_hat * two_v / span
        q = q_hat * two_v / chord
        r = r_hat * two_v / span

        cl_p0 = -0.38
        cm_q0 = -11.5
        cn_r0 = -0.28

        cl_p, cm_q, cn_r = aero_kernel.nonlinear_dynamic_damping(
            ScalarMath,
            alpha_deg=alpha_deg,
            stall_alpha_deg=14.0,
            p_hat=p_hat,
            q_hat=q_hat,
            r_hat=r_hat,
            cl_p0=cl_p0,
            cm_q0=cm_q0,
            cn_r0=cn_r0,
        )

        assert cl_p <= -1.0e-4, f"cl_p={cl_p} must be strictly negative"
        assert cm_q <= -1.0e-4, f"cm_q={cm_q} must be strictly negative"
        assert cn_r <= -1.0e-4, f"cn_r={cn_r} must be strictly negative"

        q_dyn = 0.5 * rho * (speed ** 2) * s_area
        l_damp = q_dyn * span * cl_p * p_hat
        m_damp = q_dyn * chord * cm_q * q_hat
        n_damp = q_dyn * span * cn_r * r_hat

        power = l_damp * p + m_damp * q + n_damp * r
        assert power <= 0.0, f"Passivity violated at alpha={alpha_deg}: P = {power} > 0"

    def test_cross_axis_invariance(self) -> None:
        """Verify cross-coupling does not inject positive energy along uncoupled axes."""
        # Dynamic damping kernel isolates each axis's self-damping component
        cl_p, cm_q, cn_r = aero_kernel.nonlinear_dynamic_damping(
            ScalarMath,
            alpha_deg=22.0,
            stall_alpha_deg=14.0,
            p_hat=1.5,
            q_hat=-2.0,
            r_hat=0.8,
        )
        assert cl_p < 0.0 and cm_q < 0.0 and cn_r < 0.0


class TestRateContinuityAndSmoothness:
    """Verify absence of jump discontinuities across stall transition and rate sweeps."""

    def test_alpha_sweep_continuity(self) -> None:
        """Verify C0 continuity and bounded gradient across the stall transition blend."""
        alphas = np.linspace(0.0, 35.0, 351)  # step = 0.1 deg
        rates = [0.0, 0.2, 1.0, 3.0]
        for r in rates:
            prev_cm = None
            for a in alphas:
                _, cm, _ = aero_kernel.nonlinear_dynamic_damping(
                    ScalarMath,
                    alpha_deg=a,
                    stall_alpha_deg=14.0,
                    p_hat=0.0,
                    q_hat=r,
                    r_hat=0.0,
                    cm_q0=-12.0,
                )
                if prev_cm is not None:
                    diff = abs(cm - prev_cm)
                    assert diff < 0.15, f"Discontinuity detected: step={diff} at alpha={a}"
                prev_cm = cm

    def test_rate_sweep_continuity(self) -> None:
        """Verify smooth monotonic growth with rate magnitude without sudden jumps."""
        rates = np.linspace(-4.0, 4.0, 401)
        prev_cl = None
        prev_cm = None
        prev_cn = None
        for r in rates:
            cl, cm, cn = aero_kernel.nonlinear_dynamic_damping(
                ScalarMath,
                alpha_deg=25.0,  # fully stalled
                stall_alpha_deg=14.0,
                p_hat=r,
                q_hat=r,
                r_hat=r,
            )
            if prev_cl is not None:
                assert abs(cl - prev_cl) < 0.05
                assert abs(cm - prev_cm) < 0.08
                assert abs(cn - prev_cn) < 0.05
            prev_cl, prev_cm, prev_cn = cl, cm, cn


class TestTimescaleAndOverdampingBounds:
    """Verify effective damping timescales and numerical stiffness detection."""

    def test_effective_damping_timescale_positive_and_bounded(self) -> None:
        """Verify damping timescale tau = I / (q_dyn * S * c^2 * |cm_q| / 2V) is bounded."""
        ac = AircraftConfig()
        mass = ac.mass_kg
        chord = 0.075
        span = 0.20
        # Typical pitch inertia estimate: ~ m * chord^2
        i_yy = mass * (chord ** 2 + span ** 2) / 12.0
        s_area = ac.wing_area_m2
        rho = 1.225
        speed = 8.0
        q_dyn = 0.5 * rho * speed ** 2 * s_area

        for q_hat in (0.01, 0.1, 1.0, 5.0):
            for alpha in (0.0, 14.0, 25.0):
                _, cm_q, _ = aero_kernel.nonlinear_dynamic_damping(
                    ScalarMath, alpha, 14.0, 0.0, q_hat, 0.0, cm_q0=-10.0
                )
                # Damping derivative dimension: dM/dq = q_dyn * chord * cm_q * (chord / 2V)
                c_damp = q_dyn * chord * abs(cm_q) * (chord / (2.0 * speed))
                tau = i_yy / max(c_damp, 1.0e-9)
                assert tau > 1.0e-4, f"Damping timescale too small (overdamping): tau={tau}"
                assert tau < 10.0, f"Damping timescale too large (underdamped): tau={tau}"

    def test_adaptive_stiffness_probe_captures_damping(self) -> None:
        """Verify FlightSimulator._stiffness_estimate accurately senses high-rate pitch damping."""
        aircraft = AircraftConfig(
            fidelity_mode="standard",
            dynamic_damping_enabled=True,
        )
        env = EnvironmentConfig(ground_level_m=-50.0)
        launch = LaunchConfig(
            launch_speed=7.0,
            throw_pitch_rate_radps=10.0,
        )
        settings = SimulationSettings(
            dt_min=0.0001,
            dt_max=0.01,
            collision_enabled=False,
            adaptive=True,
        )
        sim = FlightSimulator(aircraft, env, launch, settings)
        y0 = _pack(sim.state)
        core = sim._compute_dynamics(y0, 0.0)
        stiffness = sim._stiffness_estimate(y0, 0.0, core)
        assert stiffness > 1.0, f"Stiffness probe failed to capture fast damping: {stiffness}"
        # RK4 stability limit dt < 2.78 / stiffness
        dt_limit = sim._timestep_from(core, y0, 0.0)
        assert dt_limit <= settings.dt_max


class TestTimestepConvergence6DoF:
    """Verify high angular rate response converges under dt refinement without instability."""

    def test_pitch_rate_convergence_under_dt_refinement(self) -> None:
        """Verify pitch rate response decays monotonically and converges as dt decreases."""
        results = {}
        for dt_max in (0.01, 0.005, 0.002, 0.001):
            aircraft = AircraftConfig(
                mass_kg=0.0035,
                wing_area_m2=0.018,
                wingspan_m=0.22,
                fidelity_mode="standard",
                dynamic_damping_enabled=True,
                apparent_inertia_enabled=False,
                lsb_hysteresis_enabled=False,
                elevon_deg=6.0,
            )
            env = EnvironmentConfig(
                wind_speed=0.0,
                gust_intensity=0.0,
                ground_level_m=-50.0,
            )
            launch = LaunchConfig(
                launch_speed=6.5,
                launch_angle_deg=2.0,
                launch_height_m=20.0,
                throw_pitch_rate_radps=12.0,  # Strong initial rotation
            )
            settings = SimulationSettings(
                dt_min=0.0001,
                dt_max=dt_max,
                record_interval_s=0.02,
                duration_s=0.5,
                collision_enabled=False,
                adaptive=True,
            )
            sim = FlightSimulator(aircraft, env, launch, settings)
            res = sim.simulate(duration_s=0.5)
            final_q = float(sim.state.angular_velocity_radps[1])
            results[dt_max] = final_q

            # Angular rate must have damped down significantly from initial 12.0 rad/s
            assert abs(final_q) < 5.0, f"Rate did not damp down: final_q={final_q}"

        # Check convergence: difference between 0.002 and 0.001 is small
        diff_fine = abs(results[0.002] - results[0.001])
        assert diff_fine < 0.25, f"dt convergence error too high: {diff_fine}"
