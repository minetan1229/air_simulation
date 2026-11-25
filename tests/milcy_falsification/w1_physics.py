"""Falsification tests for W1 physics findings (F-01, F-03, F-05, F-06, F-07).

These tests exercise the exact code paths identified in the physics audit:
- F-01: Added-mass / Coriolis / Munk coupling energy conservation & CPU/GPU parity
- F-03: Ground contact friction impulse, angular rate clamp, energy monotonicity
- F-05: GPU build_params low-Re fields parity
- F-06: GPU station altitudes with keel height
- F-07: Aero domain low-Re parity vs aero_kernel
"""

import math

import numpy as np
import pytest

from physics import flight_sim
from physics import gpu_dynamics
from physics import aero_kernel
from physics import aero_domain
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    StateVector,
)


class TestW1Physics:
    """W1 physics falsification test suite."""

    def test_f01_added_mass_coriolis_energy_and_parity(self):
        """Test added-mass Coriolis/Munk energy conservation and CPU/GPU parity.

        Validates:
        - Kirchhoff kinetic energy conservation (zero spurious internal work)
        - CPU/GPU acceleration & angular acceleration parity at pre/post-stall w
        - Munk coupling enabled/disabled variants
        """
        for munk in (False, True):
            for w_val in (0.8, 3.5):  # pre-stall, post-stall
                ac = AircraftConfig(
                    added_mass_enabled=True,
                    apparent_inertia_enabled=True,
                    munk_coupling_enabled=munk,
                    aeroelastic_compliance=0.0,
                    aeroelastic_flex=0.0,
                    aeroelastic_twist=0.0,
                    unsteady_aero_enabled=False,
                    dynamic_damping_enabled=False,
                    mass_kg=0.005,
                    wing_area_m2=0.015,
                    wingspan_m=0.20,
                )
                env = EnvironmentConfig(
                    wind_speed=0.0,
                    gust_intensity=0.0,
                    thermal_strength=0.0,
                    gravity=0.0,
                )
                launch = LaunchConfig(
                    launch_speed=7.0,
                    launch_angle_deg=8.0,
                    launch_height_m=1.5,
                )
                settings = SimulationSettings()

                sim = flight_sim.FlightSimulator(ac, env, launch, settings)

                y = np.array(
                    [
                        0.0,
                        0.0,
                        -2.0,
                        6.0,
                        1.5,
                        w_val,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        0.4,
                        0.3,
                        0.2,
                    ],
                    dtype=np.float64,
                )

                c_cpu = sim._compute_dynamics(y, 0.0)

                params = gpu_dynamics.build_params(np, [ac], env)
                w_gpu = gpu_dynamics.wind_batch(np, params, y[:3][np.newaxis, :], 0.0)
                c_gpu = gpu_dynamics._evaluate_state(np, params, y[np.newaxis, :], w_gpu)

                np.testing.assert_allclose(
                    c_cpu["acceleration"],
                    c_gpu["acceleration"][0],
                    rtol=1e-10,
                    atol=1e-10,
                )
                np.testing.assert_allclose(
                    c_cpu["angular_acceleration"],
                    c_gpu["angular_acceleration"][0],
                    rtol=1e-10,
                    atol=1e-10,
                )

                added = aero_kernel.compute_added_mass(
                    aero_kernel.ScalarMath,
                    c_cpu["air"]["density_kgm3"],
                    sim._derived["ref_area_m2"],
                    sim._chord,
                    wingspan_m=sim._derived["ref_span_m"],
                )
                m_z = float(added["added_mass_z_kg"])
                m_vec = np.array(
                    [sim._mass, sim._mass, sim._mass + m_z], dtype=np.float64
                )
                I_eff = sim._inertia + np.diag(
                    [
                        float(added["added_inertia_xx_kgm2"]),
                        float(added["added_inertia_yy_kgm2"]),
                        float(added["added_inertia_zz_kgm2"]),
                    ]
                )

                v_b = y[3:6]
                omega = y[10:13]
                ext_power = float(np.dot(v_b, c_cpu["force_body"]) + np.dot(omega, c_cpu["moment_body"]))

                for a_b, alpha_b in (
                    (c_cpu["acceleration"], c_cpu["angular_acceleration"]),
                    (c_gpu["acceleration"][0], c_gpu["angular_acceleration"][0]),
                ):
                    dv_b = a_b - np.cross(omega, v_b)
                    dT_trans = float(np.dot(v_b * m_vec, dv_b))
                    dT_rot = float(np.dot(omega, I_eff @ alpha_b + np.cross(omega, I_eff @ omega)))
                    assert abs((dT_trans + dT_rot) - ext_power) < 1e-11

    def test_f03_ground_contact_friction_impulse_angular_rate_clamp(self):
        """Test ground contact: angular rate clamp, energy monotonicity, no divergence."""
        ac = AircraftConfig(
            mass_kg=0.003,
            wingspan_m=0.22,
            wing_area_m2=0.018,
        )
        env = EnvironmentConfig(
            ground_level_m=0.0,
            ground_friction=1.0,
            wind_speed=0.0,
        )
        settings = SimulationSettings(
            restitution=0.5,
            dt_max=0.01,
            dt_min=1e-5,
            adaptive=True,
            ground_crush_stroke_m=0.10,
        )
        launch = LaunchConfig(
            launch_speed=15.0,
            launch_height_m=0.5,
        )

        sim = flight_sim.FlightSimulator(ac, env, launch, settings)

        st = StateVector(
            time_s=0.1,
            position_m=(0.0, 0.0, -0.010),
            velocity_mps=(14.0, 8.0, 4.0),
            quaternion=(1.0, 0.0, 0.0, 0.0),
            angular_velocity_radps=(0.0, 0.0, 0.0),
            acceleration_mps2=(0.0, 0.0, 0.0),
            angular_accel_radps2=(0.0, 0.0, 0.0),
            landed=False,
            crashed=False,
        )

        e_pre = 0.5 * sim._mass * float(np.dot(st.velocity_mps, st.velocity_mps)) + 0.5 * float(
            np.asarray(st.angular_velocity_radps) @ sim._inertia @ np.asarray(st.angular_velocity_radps)
        )

        post = sim._apply_ground_contact(st, dt=0.01)

        omega_norm = float(np.linalg.norm(post.angular_velocity_radps))
        assert omega_norm <= 0.9 * flight_sim.HARD_ANGULAR_RATE_LIMIT_RPS + 1e-9

        e_post = 0.5 * sim._mass * float(np.dot(post.velocity_mps, post.velocity_mps)) + 0.5 * float(
            np.asarray(post.angular_velocity_radps) @ sim._inertia @ np.asarray(post.angular_velocity_radps)
        )
        assert e_post <= e_pre + 1e-9

        sim._state = post
        sim.step(dt=0.01)
        assert sim._diverged is False
        assert sim._status() != "diverged"

    def test_f05_gpu_build_params_low_re_fields(self):
        """Test GPU build_params propagates low-Re config fields and CPU/GPU CL/CD parity."""
        configs = [
            AircraftConfig(
                low_re_enabled=True,
                low_re_cl_alpha_gain=0.08,
                low_re_stall_gain=0.15,
                low_re_cl_max_gain=0.18,
                low_re_lsb_gain=0.20,
                low_re_cd0_gain=0.06,
            ),
            AircraftConfig(low_re_enabled=False),
        ]
        env = EnvironmentConfig(
            wind_speed=0.0,
            gust_intensity=0.0,
            thermal_strength=0.0,
        )

        params = gpu_dynamics.build_params(np, configs, env)

        low_re_enabled = np.asarray(params.aero.low_re_enabled)
        assert bool(low_re_enabled[0] if low_re_enabled.ndim > 0 else low_re_enabled) is True
        assert bool(low_re_enabled[1] if low_re_enabled.ndim > 0 else False) is False
        cl_alpha_gain = np.asarray(params.aero.low_re_cl_alpha_gain)
        assert (cl_alpha_gain[0] if cl_alpha_gain.ndim > 0 else cl_alpha_gain) == pytest.approx(0.08)
        stall_gain = np.asarray(params.aero.low_re_stall_gain)
        assert (stall_gain[0] if stall_gain.ndim > 0 else stall_gain) == pytest.approx(0.15)
        cl_max_gain = np.asarray(params.aero.low_re_cl_max_gain)
        assert (cl_max_gain[0] if cl_max_gain.ndim > 0 else cl_max_gain) == pytest.approx(0.18)
        lsb_gain = np.asarray(params.aero.low_re_lsb_gain)
        assert (lsb_gain[0] if lsb_gain.ndim > 0 else lsb_gain) == pytest.approx(0.20)
        cd0_gain = np.asarray(params.aero.low_re_cd0_gain)
        assert (cd0_gain[0] if cd0_gain.ndim > 0 else cd0_gain) == pytest.approx(0.06)

        launch = LaunchConfig(
            launch_speed=2.0,
            launch_angle_deg=6.0,
            launch_height_m=1.5,
        )

        sim0 = flight_sim.FlightSimulator(configs[0], env, launch, SimulationSettings())
        y0 = gpu_dynamics.launch_state_vector(launch, env, configs[0])
        p0 = gpu_dynamics.build_params(np, [configs[0]], env)
        w0 = gpu_dynamics.wind_batch(np, p0, y0[:3][np.newaxis, :], 0.0)
        c_cpu = sim0._compute_dynamics(y0, 0.0)
        c_gpu = gpu_dynamics._evaluate_state(np, p0, y0[np.newaxis, :], w0)

        assert float(c_gpu["cl"][0]) == pytest.approx(float(c_cpu["aero"].cl), rel=1e-10, abs=1e-10)
        assert float(c_gpu["cd"][0]) == pytest.approx(float(c_cpu["aero"].cd), rel=1e-10, abs=1e-10)

    def test_f06_gpu_station_altitudes_keel_height_m(self):
        """Test GPU _ground_stations respects keel_height_m."""
        ac_deep = AircraftConfig(keel_height_m=0.045)
        ac_shallow = AircraftConfig(keel_height_m=0.010)
        env = EnvironmentConfig(ground_level_m=0.0)

        params = gpu_dynamics.build_params(np, [ac_deep, ac_shallow], env)

        y_batch = np.array(
            [
                [0.0, 0.0, -0.030, 5.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, -0.030, 5.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )

        gpu_st = gpu_dynamics._ground_stations(np, params, y_batch)

        assert float(gpu_st["min_alt"][0]) == pytest.approx(-0.015, abs=1e-6)
        assert float(gpu_st["min_alt"][1]) == pytest.approx(0.020, abs=1e-6)

    def test_f07_aero_domain_low_re_parity(self):
        """Test aero_domain low-Re parity against aero_kernel at multiple alphas and polar."""
        cfg = AircraftConfig(
            low_re_enabled=True,
            low_re_cl_alpha_gain=0.08,
            low_re_stall_gain=0.15,
            low_re_cl_max_gain=0.18,
            low_re_cd0_gain=0.06,
            fold_trailing_deg=0.0,
        )
        env = EnvironmentConfig(
            ground_effect_enabled=False,
            compressibility_enabled=False,
        )
        p = aero_kernel.params_from_config(cfg, env)
        re = 1.0e4

        cl_off = aero_domain.elevon_effects(cfg.elevon_deg, cfg)["dcl"] + aero_domain.trailing_edge_fold_effects(cfg)["dcl"]

        for alpha in (5.0, 12.5, 18.0):
            k = aero_kernel.evaluate_coefficients(
                aero_kernel.ScalarMath,
                p,
                alpha_deg=alpha,
                speed=2.0,
                reynolds=re,
                mach=0.0,
                ground_height_m=1.0,
                density=1.225,
            )
            d_cl = aero_domain.lift_coefficient(
                alpha, cfg, compressible=False, mach=0.0, cl_offset=cl_off, reynolds=re
            )
            d_cd = aero_domain.drag_coefficient(
                alpha, cfg, reynolds=re, ground_height_m=None, compressible=False, cl_offset=cl_off
            )
            assert d_cl == pytest.approx(float(k["cl"]), rel=1e-12, abs=1e-12)
            assert d_cd == pytest.approx(float(k["cd"]), rel=1e-12, abs=1e-12)

        polar = aero_domain.drag_polar(
            cfg, alpha_min_deg=5.0, alpha_max_deg=18.0, points=14, reynolds=re
        )

        k5 = aero_kernel.evaluate_coefficients(
            aero_kernel.ScalarMath,
            p,
            alpha_deg=5.0,
            speed=2.0,
            reynolds=re,
            mach=0.0,
            ground_height_m=1.0,
            density=1.225,
        )
        k18 = aero_kernel.evaluate_coefficients(
            aero_kernel.ScalarMath,
            p,
            alpha_deg=18.0,
            speed=2.0,
            reynolds=re,
            mach=0.0,
            ground_height_m=1.0,
            density=1.225,
        )
        assert polar["cl"][0] == pytest.approx(float(k5["cl"]), rel=1e-12, abs=1e-12)
        assert polar["cd"][0] == pytest.approx(float(k5["cd"]), rel=1e-12, abs=1e-12)
        assert polar["cl"][-1] == pytest.approx(float(k18["cl"]), rel=1e-12, abs=1e-12)
        assert polar["cd"][-1] == pytest.approx(float(k18["cd"]), rel=1e-12, abs=1e-12)

        minima = aero_domain.drag_polar_minima(cfg, reynolds=re)
        assert polar["analytic"]["cd_min"] == pytest.approx(minima["cd_min"], rel=1e-12)

        assert aero_domain.lift_coefficient(
            5.0, AircraftConfig(low_re_enabled=False), reynolds=1e4
        ) == pytest.approx(
            aero_domain.lift_coefficient(5.0, AircraftConfig(low_re_enabled=False), reynolds=1e5),
            rel=1e-12,
        )
