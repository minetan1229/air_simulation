"""
Unit tests for Hierarchical Kriging / Co-Kriging with Expected Improvement (EI)
in physics/ga2/surrogate.py according to Han & Görtz (2012) and Forrester et al. (2007).
"""
import math
import numpy as np
import pytest
from physics.ga2.surrogate import (
    CorrectionModel,
    expected_improvement,
    hierarchical_kriging_predict,
)


def test_expected_improvement_zero_variance():
    """Verify EI is exactly zero when predictive variance is zero."""
    mu = 10.0
    sigma = 0.0
    current_best = 5.0
    # Zero uncertainty means zero chance of improvement beyond known mu
    ei = expected_improvement(mu, sigma, current_best, maximize=True)
    assert ei >= 0.0
    assert math.isclose(ei, max(0.0, mu - current_best), abs_tol=1e-9)


def test_expected_improvement_positive_spread():
    """Verify EI rewards higher uncertainty when exploring for maximum."""
    current_best = 10.0
    mu = 9.0  # Slightly below best
    sigma_low = 0.5
    sigma_high = 2.5

    ei_low = expected_improvement(mu, sigma_low, current_best, maximize=True)
    ei_high = expected_improvement(mu, sigma_high, current_best, maximize=True)

    # High uncertainty has a greater chance of exceeding current_best
    assert ei_high > ei_low > 0.0


def test_correction_model_predict_with_uncertainty():
    """Verify CorrectionModel calculates analytical prediction variance s_e^2(x)."""
    # Create synthetic linear dataset: y = 2*x1 + 3*x2 + noise
    np.random.seed(42)
    X = np.random.uniform(-1.0, 1.0, size=(30, 2))
    Y = (2.0 * X[:, 0] + 3.0 * X[:, 1] + 0.1 * np.random.normal(size=30)).reshape(-1, 1)

    model = CorrectionModel(feature_names=["x1", "x2"], target_names=["y"], ridge=1e-4)
    model.fit(X, Y)

    # Point close to training center (0, 0) should have lower uncertainty than an extrapolated point (3, 3)
    pred_center, var_center = model.predict_with_uncertainty(np.array([[0.0, 0.0]]))
    pred_far, var_far = model.predict_with_uncertainty(np.array([[3.0, 3.0]]))

    assert var_center.shape == (1, 1)
    assert var_far.shape == (1, 1)
    assert var_center[0, 0] > 0.0
    # Far point has higher prediction variance s_e^2
    assert var_far[0, 0] > var_center[0, 0]


def test_hierarchical_kriging_predict():
    """Verify hierarchical kriging combines low-fidelity base trend with high-fidelity correction."""
    low_fi_trend = 12.0
    correction_mu = -1.5
    correction_sigma = 0.8

    y_hk, s_hk = hierarchical_kriging_predict(low_fi_trend, correction_mu, correction_sigma)
    assert math.isclose(y_hk, 10.5, abs_tol=1e-9)
    assert math.isclose(s_hk, 0.8, abs_tol=1e-9)
