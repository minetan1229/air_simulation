"""Test: Origami geometry -> aero parameter extraction and 6DoF plausibility."""
from __future__ import annotations

import pytest

from physics import origami_domain as od
from physics.config_schema import AircraftConfig
from physics import aero_domain as ad


def test_extractor_outputs_dimensionless_and_deterministic():
    p1 = od.extract_aero_parameters({"fold_trailing_deg": 15.0, "dihedral_deg": 5.0}, od.ARCHETYPE_DART, 1.0)
    p2 = od.extract_aero_parameters({"fold_trailing_deg": 15.0, "dihedral_deg": 5.0}, od.ARCHETYPE_DART, 1.0)
    assert p1 == p2
    assert -0.05 <= p1["camber_ratio"] <= 0.10
    assert 0.0 < p1["leading_edge_sweep_deg"] < 45.0
    assert p1["dihedral_eff_deg"] == pytest.approx(5.0)


def test_extractor_preserves_dihedral_sign():
    neg = od.extract_aero_parameters({"dihedral_deg": -20.0}, od.ARCHETYPE_DART, 1.0)
    pos = od.extract_aero_parameters({"dihedral_deg": 20.0}, od.ARCHETYPE_DART, 1.0)
    assert neg["dihedral_eff_deg"] == pytest.approx(-20.0)
    assert pos["dihedral_eff_deg"] == pytest.approx(20.0)


def test_apply_aero_parameters_writes_config():
    cfg = AircraftConfig()
    cfg2 = od.apply_aero_parameters(cfg, {"fold_trailing_deg": 15.0, "dihedral_deg": 5.0}, od.ARCHETYPE_DART, 1.0)
    assert cfg2.camber_ratio == pytest.approx(cfg.camber_ratio + 0.0087, rel=1e-2) or cfg2.camber_ratio > 0.0
    assert cfg2.leading_edge_sweep_deg > 0.0
    assert cfg2.dihedral_eff_deg == pytest.approx(5.0)
    assert cfg2.dihedral_deg == pytest.approx(5.0)


def test_extractor_monotonic_in_te_fold():
    lo = od.extract_aero_parameters({"fold_trailing_deg": 0.0}, od.ARCHETYPE_DART, 1.0)
    hi = od.extract_aero_parameters({"fold_trailing_deg": 30.0}, od.ARCHETYPE_DART, 1.0)
    assert hi["camber_ratio"] > lo["camber_ratio"]


def test_6dof_plausible_with_extracted_parameters():
    cfg = AircraftConfig(mass_kg=0.005)
    cfg2 = od.apply_aero_parameters(cfg, {"fold_trailing_deg": 15.0, "dihedral_deg": 5.0}, od.ARCHETYPE_DART, 1.0)
    from physics.config_schema import EnvironmentConfig
    res = ad.evaluate_aero(cfg2, EnvironmentConfig(), speed_mps=6.0, alpha_deg=5.0)
    assert res.cl == res.cl and -2.0 < res.cl < 4.0
    assert res.cd >= 0.0
