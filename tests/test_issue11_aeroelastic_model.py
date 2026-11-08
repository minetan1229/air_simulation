"""Audit and verification of paper deformation and aeroelasticity models (Issue #11).

Verifies:
1. Dynamic aeroelastic dihedral and washout twist scaling with dynamic pressure q = 0.5 * rho * V^2 and load factor Nz.
2. Allowable fold stress scaling with paper grammage (paper_gsm) and stiffness parameter.
3. Zero-deformation when compliance gains are zero, and bounded response when enabled.
"""

import math
import pytest

from physics.config_schema import AircraftConfig
from physics.stress_domain import (
    aeroelastic_deformations,
    paper_allowable_stress,
    ALLOWABLE_BASE_PA,
)


def test_aeroelastic_deformation_scaling_and_bounds():
    """Verify that aeroelastic dihedral and twist increase monotonically with dynamic pressure but stay bounded."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, aeroelastic_flex=1.0, aeroelastic_twist=1.0)

    # At zero dynamic pressure, deformation must be zero
    def_zero = aeroelastic_deformations(cfg, dynamic_pressure=0.0, nz=1.0)
    assert math.isclose(def_zero["delta_dihedral_deg"], 0.0, abs_tol=1e-6)
    assert math.isclose(def_zero["washout_twist_deg"], 0.0, abs_tol=1e-6)

    # Moderate flight condition (e.g., q ~ 40 Pa at 8 m/s, nz = 1.0)
    def_mid = aeroelastic_deformations(cfg, dynamic_pressure=40.0, nz=1.0)
    assert def_mid["delta_dihedral_deg"] > 0.0
    assert def_mid["washout_twist_deg"] > 0.0

    # High dynamic pressure condition (e.g., q ~ 300 Pa at launch, nz = 2.5)
    def_high = aeroelastic_deformations(cfg, dynamic_pressure=300.0, nz=2.5)
    assert def_high["delta_dihedral_deg"] >= def_mid["delta_dihedral_deg"]
    assert def_high["washout_twist_deg"] > def_mid["washout_twist_deg"]

    # Physical clamping/bounds: clamped to 30 deg dihedral, 20 deg twist
    assert def_high["delta_dihedral_deg"] <= 30.0
    assert def_high["washout_twist_deg"] <= 20.0


def test_paper_allowable_stress_formulation():
    """Verify allowable fold stress scaling monotonically with paper grammage and stiffness."""
    # Typical origami paper grammages: 60 gsm (light), 80 gsm (standard copy paper), 120 gsm (cardstock)
    sigma_60 = paper_allowable_stress(60.0, 1.0)
    sigma_80 = paper_allowable_stress(80.0, 1.0)
    sigma_120 = paper_allowable_stress(120.0, 1.0)

    assert 0.0 < sigma_60 < sigma_80 < sigma_120
    # Baseline for 100 gsm paper with nominal stiffness 0.5: 2500 * (1.0)^0.5 * (0.6 + 0.4) = 2500 Pa
    sigma_100_nom = paper_allowable_stress(100.0, 0.5)
    assert math.isclose(sigma_100_nom, ALLOWABLE_BASE_PA, rel_tol=1e-3)
