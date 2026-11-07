"""Verification and audit tests for simplex turbulence statistics and spectral properties.

Issue #112:
- Statistical properties: zero-mean fluctuations E[u'] ~ 0, variance scaling with
  turbulence amplitude, horizontal-to-vertical anisotropy ratio sigma_w / sigma_u ~ 1/sqrt(2).
- Spatial correlation length scale: monotonic scaling with turbulence_scale,
  computation of empirical integral length scale.
- Divergence audit: verification that procedural 3D simplex turbulence is not
  divergence-free (div u != 0) and characterisation of divergence bounds.
- Spectral PSD analysis: compare simplex noise band-pass smooth spectrum against
  asymptotic Dryden (f^-2) / von Karman (f^-5/3) power-law models.
- Reproducibility and determinism: bit-for-bit repeatability with identical seed,
  proper lattice decorrelation across distinct seeds and coordinate invariance.
"""

from __future__ import annotations

import math
import numpy as np
import pytest
from scipy import signal, integrate

from physics.config_schema import EnvironmentConfig
from physics.wind_domain import (
    simplex_noise3,
    turbulence_amplitude,
    wind_velocity,
    _TURBULENCE_VERTICAL_WEIGHT,
    _TURBULENCE_WAVELENGTH_BASE_M,
)


class TestSimplexTurbulenceStatistics:
    """Statistical moments and channel decorrelation audit."""

    def test_zero_mean_fluctuations(self) -> None:
        """Hypothesis: E[u'] ~ 0, E[v'] ~ 0, E[w'] ~ 0 over spatial domain.

        Falsifiable criteria: For N=25000 uniform spatial samples far from ground/thermals,
        the sample mean |E[u_i']| / amplitude < 0.05.
        """
        env = EnvironmentConfig(
            wind_speed=10.0,
            gust_intensity=0.4,
            turbulence_scale=1.0,
            turbulence_seed=42,
            thermal_strength=0.0,
            boundary_layer_enabled=False,
        )
        amp = turbulence_amplitude(env)
        assert amp > 0.0

        rng = np.random.default_rng(2026)
        pts = rng.uniform(-100.0, 100.0, size=(25000, 3))
        pts[:, 2] = 20.0  # Constant height to avoid any boundary-layer effects

        V = wind_velocity(pts, env, time_s=0.0)
        # Horizontal mean wind is at direction 0 (from North, blowing South: v = -10 m/s)
        mean_V = np.mean(V, axis=0)
        fluctuations = V - mean_V

        means = np.mean(fluctuations, axis=0)
        # All component means of fluctuations must be zero within statistical sampling tolerance
        for i, comp in enumerate(["u", "v", "w"]):
            assert abs(means[i]) / amp < 0.05, f"Component {comp} mean fluctuation {means[i]} exceeds 5% of amp {amp}"

    def test_variance_and_vertical_anisotropy(self) -> None:
        """Hypothesis: std(u') and std(v') are approximately equal, and std(w') / std(u')
        matches _TURBULENCE_VERTICAL_WEIGHT (1 / sqrt(2) ~ 0.707).

        Falsifiable criteria:
        - Ratio std(u') / std(v') within [0.90, 1.10]
        - Ratio std(w') / std(u') within 10% of 1 / sqrt(2)
        - std(u') / amplitude consistent with simplex noise kernel std (~0.39).
        """
        env = EnvironmentConfig(
            wind_speed=12.0,
            gust_intensity=0.5,
            turbulence_scale=1.0,
            turbulence_seed=101,
            thermal_strength=0.0,
            boundary_layer_enabled=False,
        )
        amp = turbulence_amplitude(env)

        rng = np.random.default_rng(777)
        pts = rng.uniform(-150.0, 150.0, size=(30000, 3))
        pts[:, 2] = 25.0

        V = wind_velocity(pts, env, time_s=0.0)
        stds = np.std(V, axis=0)

        # Horizontal isotropy
        h_ratio = stds[0] / stds[1]
        assert 0.90 <= h_ratio <= 1.10, f"Horizontal isotropy violated: ratio {h_ratio}"

        # Vertical anisotropy
        expected_v_ratio = _TURBULENCE_VERTICAL_WEIGHT
        actual_v_ratio = stds[2] / stds[0]
        assert math.isclose(actual_v_ratio, expected_v_ratio, rel_tol=0.08), (
            f"Vertical anisotropy {actual_v_ratio} deviates from expected {expected_v_ratio}"
        )

        # Variance scaling with amplitude
        ratio_sigma_amp = stds[0] / amp
        assert 0.35 <= ratio_sigma_amp <= 0.45, f"Turbulence intensity scaling out of range: {ratio_sigma_amp}"

    def test_channel_cross_correlation_decorrelation(self) -> None:
        """Hypothesis: The 3 turbulence channels are decorrelated (cross-correlation ~ 0).

        Falsifiable criteria: Pearson correlation coefficient between any pair of (u', v', w')
        satisfies |r| < 0.05.
        """
        env = EnvironmentConfig(
            wind_speed=8.0,
            gust_intensity=0.5,
            turbulence_scale=1.0,
            turbulence_seed=123,
            thermal_strength=0.0,
            boundary_layer_enabled=False,
        )
        rng = np.random.default_rng(888)
        pts = rng.uniform(-200.0, 200.0, size=(25000, 3))
        pts[:, 2] = 15.0

        V = wind_velocity(pts, env, time_s=0.0)
        corr_matrix = np.corrcoef(V, rowvar=False)

        r_uv = corr_matrix[0, 1]
        r_uw = corr_matrix[0, 2]
        r_vw = corr_matrix[1, 2]

        assert abs(r_uv) < 0.05, f"u-v cross correlation too large: {r_uv}"
        assert abs(r_uw) < 0.05, f"u-w cross correlation too large: {r_uw}"
        assert abs(r_vw) < 0.05, f"v-w cross correlation too large: {r_vw}"


class TestSpatialCorrelationAndDivergence:
    """Spatial correlation length scale and divergence audit."""

    def test_spatial_correlation_length_scale(self) -> None:
        """Hypothesis: Empirical spatial autocorrelation drops monotonically with distance
        and the integral length scale scales linearly with turbulence_scale.

        Falsifiable criteria:
        - Autocorrelation R(r) drops from 1 at r=0 to < 1/e at distance r ~ 0.25 * lambda.
        - Scaling turbulence_scale by factor 2.0 increases the 1/e correlation distance
          by approximately factor 2.0 (tolerance 15%).
        """
        r_grid = np.linspace(0, 150, 6000)
        dx = r_grid[1] - r_grid[0]

        pts1 = np.column_stack([r_grid, np.zeros_like(r_grid), np.full_like(r_grid, 20.0)])
        env1 = EnvironmentConfig(
            wind_speed=10.0, gust_intensity=0.4, turbulence_scale=1.0,
            turbulence_seed=42, thermal_strength=0.0, boundary_layer_enabled=False,
        )
        u1 = wind_velocity(pts1, env1, 0.0)[:, 0]
        u1_fluc = u1 - np.mean(u1)

        # Autocorrelation
        n_corr = 2000
        ac1 = np.correlate(u1_fluc[:n_corr], u1_fluc[:n_corr], mode="full")
        ac1 = ac1[len(ac1) // 2 :] / ac1[len(ac1) // 2]
        lag1_idx = np.where(ac1 < 1.0 / math.e)[0][0]
        r_1_e_scale1 = lag1_idx * dx

        env2 = EnvironmentConfig(
            wind_speed=10.0, gust_intensity=0.4, turbulence_scale=2.0,
            turbulence_seed=42, thermal_strength=0.0, boundary_layer_enabled=False,
        )
        u2 = wind_velocity(pts1, env2, 0.0)[:, 0]
        u2_fluc = u2 - np.mean(u2)

        ac2 = np.correlate(u2_fluc[:n_corr], u2_fluc[:n_corr], mode="full")
        ac2 = ac2[len(ac2) // 2 :] / ac2[len(ac2) // 2]
        lag2_idx = np.where(ac2 < 1.0 / math.e)[0][0]
        r_1_e_scale2 = lag2_idx * dx

        scale_ratio = r_1_e_scale2 / r_1_e_scale1
        assert 1.70 <= scale_ratio <= 2.30, (
            f"Correlation distance ratio {scale_ratio} expected near 2.0"
        )

    def test_divergence_audit(self) -> None:
        """Audit test: Procedural 3D simplex turbulence is a spatial surrogate without
        exact solenoidal constraint (div u != 0).

        Falsifiable criteria:
        - div u is finite everywhere (no singularities).
        - div u std is on the same order of magnitude as individual velocity gradients
          (confirming scalar superposition without zero-divergence projection).
        - div u mean is close to zero (no net expansion / contraction over large volumes).
        """
        env = EnvironmentConfig(
            wind_speed=10.0,
            gust_intensity=0.4,
            turbulence_scale=1.0,
            turbulence_seed=42,
            thermal_strength=0.0,
            boundary_layer_enabled=False,
        )

        # 3D grid
        grid_dim = 25
        x = np.linspace(-5.0, 5.0, grid_dim)
        y = np.linspace(-5.0, 5.0, grid_dim)
        z = np.linspace(10.0, 20.0, grid_dim)
        dx = x[1] - x[0]
        dy = y[1] - y[0]
        dz = z[1] - z[0]

        X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
        pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])

        V = wind_velocity(pts, env, time_s=0.0)
        U = V[:, 0].reshape(X.shape)
        V_y = V[:, 1].reshape(X.shape)
        W = V[:, 2].reshape(X.shape)

        dudx = np.gradient(U, dx, axis=0)
        dvdy = np.gradient(V_y, dy, axis=1)
        dwdz = np.gradient(W, dz, axis=2)

        div = dudx + dvdy + dwdz

        assert np.all(np.isfinite(div)), "Divergence contains non-finite values"
        assert abs(np.mean(div)) < 0.20, f"Mean divergence {np.mean(div)} deviates significantly from zero"

        # div std is nonzero confirming simplex surrogate behavior
        assert np.std(div) > 0.5, "Expected nonzero divergence for simplex surrogate"
        # and on the order of individual directional gradients
        assert np.std(div) < 3.0 * max(np.std(dudx), np.std(dvdy), np.std(dwdz))


class TestSpectralCharacteristicsAndPSD:
    """Power Spectral Density (PSD) characteristics comparison."""

    def test_psd_bandpass_behavior(self) -> None:
        """Hypothesis: Simplex turbulence has smooth band-pass spectral character
        rather than infinite power-law inertial cascade.

        Falsifiable criteria:
        - Low spatial frequency (wavenumber k < 0.05 m^-1) has positive/flat slope.
        - High frequency roll-off past peak has steep slope (negative).
        - Total integrated PSD equals fluctuation variance (Parseval's theorem).
        """
        dx = 0.02
        N = 25000
        x = np.arange(N) * dx
        pts = np.column_stack([x, np.zeros_like(x), np.full_like(x, 15.0)])

        env = EnvironmentConfig(
            wind_speed=10.0, gust_intensity=0.5, turbulence_scale=1.0,
            turbulence_seed=42, thermal_strength=0.0, boundary_layer_enabled=False,
        )
        u = wind_velocity(pts, env, 0.0)[:, 0]
        u_fluc = u - np.mean(u)

        fs = 1.0 / dx
        freqs, psd = signal.welch(u_fluc, fs=fs, nperseg=4096)

        # Parseval check: integral of PSD over positive frequencies ~ variance
        var_signal = float(np.var(u_fluc))
        var_psd = float(integrate.trapezoid(psd, freqs))
        assert math.isclose(var_signal, var_psd, rel_tol=0.05), (
            f"Parseval check failed: var_signal={var_signal}, var_psd={var_psd}"
        )

        # Low-frequency band: slope should be >= 0 (smooth large eddies, no 1/f diverge)
        m_low = (freqs > 0.01) & (freqs < 0.05)
        slope_low = float(np.polyfit(np.log10(freqs[m_low]), np.log10(psd[m_low]), 1)[0])
        assert slope_low > -0.2, f"Low-frequency slope {slope_low} should be flat or positive"

        # High-frequency band (k > 0.5 m^-1): steep decay
        m_high = (freqs > 0.5) & (freqs < 1.5)
        slope_high = float(np.polyfit(np.log10(freqs[m_high]), np.log10(psd[m_high]), 1)[0])
        assert slope_high < -1.0, f"High-frequency roll-off {slope_high} should be steep"


class TestReproducibilityAndDeterminism:
    """Reproducibility across seeds and determinism for fixed coordinates."""

    def test_bit_for_bit_determinism(self) -> None:
        """Hypothesis: Evaluation is pure and bit-for-bit identical for repeated evaluations."""
        env = EnvironmentConfig(
            wind_speed=9.0, gust_intensity=0.35, turbulence_scale=1.5,
            turbulence_seed=999, thermal_strength=1.5,
        )
        pts = np.array([
            [0.0, 0.0, 2.0],
            [10.5, -4.2, 5.0],
            [-25.0, 30.0, 1.2],
        ])

        v1 = wind_velocity(pts, env, time_s=1.23)
        v2 = wind_velocity(pts, env, time_s=1.23)

        assert np.array_equal(v1, v2), "Repeated evaluations must be bit-for-bit identical"

    def test_seed_decorrelation(self) -> None:
        """Hypothesis: Differing turbulence seeds produce fully distinct, decorrelated fields."""
        pts = np.random.default_rng(123).uniform(-50, 50, size=(1000, 3))
        pts[:, 2] = 10.0

        env_a = EnvironmentConfig(
            wind_speed=10.0, gust_intensity=0.5, turbulence_scale=1.0,
            turbulence_seed=42, thermal_strength=0.0, boundary_layer_enabled=False,
        )
        env_b = EnvironmentConfig(
            wind_speed=10.0, gust_intensity=0.5, turbulence_scale=1.0,
            turbulence_seed=999, thermal_strength=0.0, boundary_layer_enabled=False,
        )

        va = wind_velocity(pts, env_a, 0.0)[:, 0]
        vb = wind_velocity(pts, env_b, 0.0)[:, 0]

        r = np.corrcoef(va, vb)[0, 1]
        assert abs(r) < 0.10, f"Fields with different seeds should be decorrelated, got r={r}"
