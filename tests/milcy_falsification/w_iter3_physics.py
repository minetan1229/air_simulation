"""Falsification tests for W iter3 physics findings.

These tests exercise the exact code paths identified in the physics audit:
- P1: Aero domain trim cache includes low_re_enabled and geometry
- P2: Aero kernel fused preserves bool fields
- P3: GPU dynamics RK4 stiffness safety limit
"""

import math
import pytest
import numpy as np

from physics import aero_domain
from physics import aero_kernel
from physics import gpu_dynamics
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig


class TestWIter3Physics:
    """W iter3 physics falsification test suite."""

    def test_i3_p1_aero_domain_trim_cache_includes_low_re_and_geometry(self):
        """Test that _GLOBAL_TRIM_CACHE keys include low_re_enabled and elevon_chord_ratio."""
        # Test low_re_enabled=False vs True
        ac_base = AircraftConfig(
            elevon_deg=-2.0,
            fold_trailing_deg=0.0,
        )
        env = EnvironmentConfig(
            wind_speed=0.0,
            gust_intensity=0.0,
            thermal_strength=0.0,
        )

        # Clear cache and evaluate with low_re_enabled=False
        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_false = AircraftConfig(
            elevon_deg=-2.0,
            fold_trailing_deg=0.0,
            low_re_enabled=False,
        )
        r_false = aero_domain.evaluate_aero(ac_false, env, 8.0, 5.0, compute_trim=True)

        # WITHOUT clearing cache, evaluate with low_re_enabled=True (should use cached if buggy)
        ac_true_cached = AircraftConfig(
            elevon_deg=-2.0,
            fold_trailing_deg=0.0,
            low_re_enabled=True,
        )
        r_cached = aero_domain.evaluate_aero(ac_true_cached, env, 8.0, 5.0, compute_trim=True)

        # Clear cache and evaluate fresh with low_re_enabled=True
        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_true_fresh = AircraftConfig(
            elevon_deg=-2.0,
            fold_trailing_deg=0.0,
            low_re_enabled=True,
        )
        r_fresh = aero_domain.evaluate_aero(ac_true_fresh, env, 8.0, 5.0, compute_trim=True)

        # The trim_alpha_deg should be identical between cached and fresh when cache is correct
        # If cache key includes low_re_enabled, r_cached should equal r_fresh (both use low_re_enabled=True)
        assert r_cached.trim_alpha_deg == pytest.approx(r_fresh.trim_alpha_deg, abs=1e-12)

        # Test elevon_chord_ratio=0.10 vs 0.40
        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_small = AircraftConfig(
            elevon_deg=-2.0,
            fold_trailing_deg=0.0,
            elevon_chord_ratio=0.10,
        )
        r_small = aero_domain.evaluate_aero(ac_small, env, 8.0, 5.0, compute_trim=True)

        # WITHOUT clearing cache, evaluate with elevon_chord_ratio=0.40
        ac_large_cached = AircraftConfig(
            elevon_deg=-2.0,
            fold_trailing_deg=0.0,
            elevon_chord_ratio=0.40,
        )
        r_cached = aero_domain.evaluate_aero(ac_large_cached, env, 8.0, 5.0, compute_trim=True)

        # Clear cache and evaluate fresh with elevon_chord_ratio=0.40
        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_large_fresh = AircraftConfig(
            elevon_deg=-2.0,
            fold_trailing_deg=0.0,
            elevon_chord_ratio=0.40,
        )
        r_fresh = aero_domain.evaluate_aero(ac_large_fresh, env, 8.0, 5.0, compute_trim=True)

        # The trim_alpha_deg should be identical between cached and fresh
        assert r_cached.trim_alpha_deg == pytest.approx(r_fresh.trim_alpha_deg, abs=1e-12)
        # The small vs fresh should differ significantly (different geometry)
        assert abs(r_small.trim_alpha_deg - r_fresh.trim_alpha_deg) > 1e-3

    def test_i3_p2_aero_kernel_fused_preserves_bool_fields(self):
        """Test that fused GPU kernel preserves bool fields (low_re_enabled, etc.)."""
        try:
            import cupy as cp
            if not cp.cuda.is_available() or cp.cuda.runtime.getDeviceCount() == 0:
                pytest.skip("CuPy CUDA unavailable")
        except Exception:
            pytest.skip("CuPy CUDA unavailable")

        aero_kernel._FUSED_STATE = 'unavailable'
        aero_kernel._FUSED_KERNEL = None

        # First evaluate AircraftConfig(low_re_enabled=True)
        ac_low_re = AircraftConfig(low_re_enabled=True)
        env = EnvironmentConfig()
        p_low_re = gpu_dynamics.build_params(cp, [ac_low_re], env)

        alpha_deg = cp.array([5.0])
        speed = cp.array([8.0])
        aero_kernel.evaluate_coefficients(cp, p_low_re.aero, alpha_deg=alpha_deg, speed=speed)

        assert aero_kernel.fused_gpu_state() == 'verified'
        assert aero_kernel.fused_gpu_state() != 'rejected'

        # Then evaluate default AircraftConfig() (low_re_enabled=False)
        ac_default = AircraftConfig()
        p_default = gpu_dynamics.build_params(cp, [ac_default], env)
        aero_kernel.evaluate_coefficients(cp, p_default.aero, alpha_deg=alpha_deg, speed=speed)

        assert aero_kernel.fused_gpu_state() == 'verified'
        assert aero_kernel.fused_gpu_state() != 'rejected'

    def test_i3_p3_gpu_dynamics_rk4_stiffness_safety_limit(self):
        """Test that RK4 stiffness safety limit works correctly with heterogeneous configs."""
        ac = AircraftConfig(
            low_re_enabled=True,
            fold_trailing_deg=0.0,
        )
        ac_other = AircraftConfig(
            low_re_enabled=False,
            fold_trailing_deg=-5.0,
            added_mass_enabled=True,
            apparent_inertia_enabled=True,
            munk_coupling_enabled=True,
        )
        env = EnvironmentConfig(
            wind_speed=0.0,
            gust_intensity=0.0,
            thermal_strength=0.0,
        )
        launch = LaunchConfig(launch_speed=8.0)

        # Single config
        p_s = gpu_dynamics.build_params(np, [ac], env)
        y_s = gpu_dynamics.launch_state_vector(launch, env, ac)[np.newaxis, :]
        r_s = gpu_dynamics.simulate_batch(np, p_s, y_s, 1.0, dt_max=0.01)

        # Heterogeneous configs
        p_h = gpu_dynamics.build_params(np, [ac, ac_other], env)
        y_h = np.stack([
            gpu_dynamics.launch_state_vector(launch, env, ac),
            gpu_dynamics.launch_state_vector(launch, env, ac_other),
        ], axis=0)
        r_h = gpu_dynamics.simulate_batch(np, p_h, y_h, 1.0, dt_max=0.01)

        # State[0, 11] is the pitch rate (index 11 in 13-state vector: pos(3), vel(3), quat(4), omega(3))
        # The first case in both simulations should match
        assert abs(float(r_s['state'][0, 11] - r_h['state'][0, 11])) < 1e-6

    def test_i4_p1_trim_cache_all_aero_fields(self):
        """Test that _GLOBAL_TRIM_CACHE keys include cLalpha_per_rad, polhamus_kp, and lift_scale."""
        # Pair (a): cLalpha_per_rad=0.0 vs 6.0
        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_a1 = AircraftConfig(elevon_deg=-2.0, cLalpha_per_rad=0.0)
        env = EnvironmentConfig(lift_scale=1.0, wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
        r_first = aero_domain.trim_alpha_from_elevon(ac_a1, -2.0, speed_mps=8.0, env=env)

        ac_a2_cached = AircraftConfig(elevon_deg=-2.0, cLalpha_per_rad=6.0)
        r_cached = aero_domain.trim_alpha_from_elevon(ac_a2_cached, -2.0, speed_mps=8.0, env=env)

        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_a2_fresh = AircraftConfig(elevon_deg=-2.0, cLalpha_per_rad=6.0)
        r_fresh = aero_domain.trim_alpha_from_elevon(ac_a2_fresh, -2.0, speed_mps=8.0, env=env)

        assert r_cached == pytest.approx(r_fresh, abs=1e-12)
        assert abs(r_first - r_fresh) > 1e-3

        # Pair (b): polhamus_kp=0.0 vs 2.0
        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_b1 = AircraftConfig(elevon_deg=-2.0, polhamus_kp=0.0)
        r_first = aero_domain.trim_alpha_from_elevon(ac_b1, -2.0, speed_mps=8.0, env=env)

        ac_b2_cached = AircraftConfig(elevon_deg=-2.0, polhamus_kp=2.0)
        r_cached = aero_domain.trim_alpha_from_elevon(ac_b2_cached, -2.0, speed_mps=8.0, env=env)

        aero_domain._GLOBAL_TRIM_CACHE.clear()
        ac_b2_fresh = AircraftConfig(elevon_deg=-2.0, polhamus_kp=2.0)
        r_fresh = aero_domain.trim_alpha_from_elevon(ac_b2_fresh, -2.0, speed_mps=8.0, env=env)

        assert r_cached == pytest.approx(r_fresh, abs=1e-12)
        assert abs(r_first - r_fresh) > 1e-3

        # Pair (c): lift_scale=1.0 vs 3.0
        aero_domain._GLOBAL_TRIM_CACHE.clear()
        env_c1 = EnvironmentConfig(lift_scale=1.0, wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
        ac_c = AircraftConfig(elevon_deg=-2.0)
        r_first = aero_domain.trim_alpha_from_elevon(ac_c, -2.0, speed_mps=8.0, env=env_c1)

        env_c2_cached = EnvironmentConfig(lift_scale=3.0, wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
        r_cached = aero_domain.trim_alpha_from_elevon(ac_c, -2.0, speed_mps=8.0, env=env_c2_cached)

        aero_domain._GLOBAL_TRIM_CACHE.clear()
        env_c2_fresh = EnvironmentConfig(lift_scale=3.0, wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
        r_fresh = aero_domain.trim_alpha_from_elevon(ac_c, -2.0, speed_mps=8.0, env=env_c2_fresh)

        assert r_cached == pytest.approx(r_fresh, abs=1e-12)
        assert abs(r_first - r_fresh) > 1e-3

    def test_i4_p3_gpu_stiffness_matches_cpu_exact(self, monkeypatch):
        """Test that GPU _step_size stiffness matches CPU _stiffness_estimate exactly."""
        from physics.flight_sim import FlightSimulator, _pack, SimulationSettings

        ac = AircraftConfig(
            added_mass_enabled=True,
            munk_coupling_enabled=True,
            elevon_deg=-2.0,
            low_re_enabled=True,
            cp_shift_gain=0.5,
        )
        env = EnvironmentConfig(
            wind_speed=0.0,
            gust_intensity=0.0,
            thermal_strength=0.0,
        )
        launch = LaunchConfig(launch_speed=8.0)

        # CPU stiffness
        sim = FlightSimulator(ac, env, launch, SimulationSettings())
        y_cpu = _pack(sim._state)
        core_cpu = sim._compute_dynamics(y_cpu, 0.0)
        stiff_cpu = sim._stiffness_estimate(y_cpu, 0.0, core_cpu)

        # GPU stiffness via _step_size
        params = gpu_dynamics.build_params(np, [ac], env)
        y_gpu = gpu_dynamics.launch_state_vector(launch, env, ac)[np.newaxis, :]
        wind = np.zeros((1, 3), dtype=np.float64)
        core_gpu = gpu_dynamics._evaluate_state(np, params, y_gpu, wind)

        # Disable step limiters so stiffness dominates _step_size
        monkeypatch.setattr(gpu_dynamics, 'MAX_ATTITUDE_STEP_RAD', 1e9)
        monkeypatch.setattr(gpu_dynamics, 'TRAVEL_FRACTION_PER_STEP', 1e9)
        monkeypatch.setattr(gpu_dynamics, 'MAX_RATE_CHANGE_RADPS', 1e9)

        step = float(gpu_dynamics._step_size(np, params, y_gpu, core_gpu, 10.0, wind=wind)[0])
        stiff_gpu = gpu_dynamics.RK4_STIFFNESS_SAFETY / step

        assert math.isclose(stiff_gpu, stiff_cpu, rel_tol=1e-6)
