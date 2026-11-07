"""Audit and sensitivity verification for structural empirical coupling coefficients.

Issue #115:
Inventory and verify structural empirical coupling coefficients beyond KAPPA_SIGMA
in `physics/stress_domain.py`:
- Coefficients:
    - TIP_MOMENT_GAIN (1.6)
    - SPAN_SHIFT_GAIN (0.6)
    - DIHEDRAL_LOAD_COUPLING (0.8)
    - DIHEDRAL_MOMENT_COUPLING (0.45)
    - SIDELOAD_COUPLING (0.35)
    - ELEVON_ARM_GAIN (0.6)
    - ELEVON_TE_GAIN (1.5)
    - TE_FOLD_CHORD_REDUCTION (0.35)
    - TIP_SPAN_REDUCTION (0.20)
    - Z_SCALE_OFFSET (0.55)
    - Z_SCALE_GAIN (0.9)
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict

import numpy as np
import pytest

from physics import stress_domain as sd
from physics.config_schema import AircraftConfig
from physics.optimizer_domain import aggregate_score


# Inventory list required by Issue #115
AUDITED_COEFFICIENTS = [
    "TIP_MOMENT_GAIN",
    "SPAN_SHIFT_GAIN",
    "DIHEDRAL_LOAD_COUPLING",
    "DIHEDRAL_MOMENT_COUPLING",
    "SIDELOAD_COUPLING",
    "ELEVON_ARM_GAIN",
    "ELEVON_TE_GAIN",
    "TE_FOLD_CHORD_REDUCTION",
    "TIP_SPAN_REDUCTION",
    "Z_SCALE_OFFSET",
    "Z_SCALE_GAIN",
]


class TestStructuralInventoryAndRegistry:
    """Task 1: Inventory all empirical coefficients, locations, units, physical meanings, and calibration statuses."""

    def test_all_audited_coefficients_present_in_module_and_all(self) -> None:
        """Every audited coefficient must exist in module namespace and in __all__."""
        for name in AUDITED_COEFFICIENTS:
            assert hasattr(sd, name), f"{name} missing from physics.stress_domain"
            assert name in sd.__all__, f"{name} missing from physics.stress_domain.__all__"
            val = getattr(sd, name)
            assert isinstance(val, (int, float)), f"{name} must be numeric, got {type(val)}"
            assert math.isfinite(float(val)), f"{name} must be finite"

    def test_registry_contains_audited_coefficients(self) -> None:
        """STRUCTURAL_PARAMETER_REGISTRY must contain all audited coefficients."""
        reg = sd.STRUCTURAL_PARAMETER_REGISTRY
        for name in AUDITED_COEFFICIENTS:
            assert name in reg, f"{name} missing from STRUCTURAL_PARAMETER_REGISTRY"
            entry = reg[name]
            assert "value" in entry
            assert "unit" in entry
            assert "basis" in entry
            assert "scope" in entry
            assert entry["value"] == getattr(sd, name)
            assert entry["basis"] == "unvalidated empirical"

    def test_structural_parameters_documented_contract(self) -> None:
        """structural_parameters_documented() must validate all registry entries as unvalidated empirical or documented geometric model."""
        assert sd.structural_parameters_documented() is True


class TestCantileverBaselineVsFoldCouplings:
    """Task 2 & Falsifiable Criteria: Verify baseline cantilever / beam theory cases with and without fold coupling."""

    def test_cantilever_baseline_without_fold_couplings(self) -> None:
        """Plain flat wing (zero fold, zero dihedral, zero elevon) matches Euler-Bernoulli beam theory exactly."""
        cfg_plain = AircraftConfig(
            dihedral_deg=0.0,
            fold_tip_deg=0.0,
            fold_trailing_deg=0.0,
            elevon_deg=0.0,
            taper_ratio=1.0,
            mass_kg=0.003,
            wingspan_m=0.20,
            wing_area_m2=0.02,
            paper_gsm=80.0,
            paper_stiffness=0.5,
        )
        nz = 2.0
        g = 9.80665
        field = sd.stress_field(cfg_plain, dynamic_pressure_pa=100.0, load_factor=nz, spanwise=25, chordwise=17, beta_deg=0.0)

        # Baseline theoretical root moment: M = F * b / 8 for rectangular wing under uniform load
        expected_force = cfg_plain.mass_kg * g * nz
        expected_moment = expected_force * cfg_plain.wingspan_m / 8.0
        assert field["wing_root_moment_nm"] == pytest.approx(expected_moment, rel=1e-12)

        # Baseline theoretical section modulus: Z = c * t^2 * z_gain / 6
        chord = cfg_plain.wing_area_m2 / cfg_plain.wingspan_m
        thickness = (cfg_plain.paper_gsm * 1e-3) / sd.PAPER_THICKNESS_DENOMINATOR
        stiffness = sd.effective_paper_stiffness(cfg_plain)
        z_gain = sd.Z_SCALE_OFFSET + sd.Z_SCALE_GAIN * stiffness
        z_mod = chord * (thickness ** 2) * z_gain / 6.0
        expected_stress = sd.KAPPA_SIGMA * expected_moment / z_mod

        assert field["max_stress_pa"] == pytest.approx(expected_stress, rel=1e-12)

    def test_winglet_fold_coupling_increases_moment_and_shortens_semispan(self) -> None:
        """Enabling fold_tip_deg activates TIP_MOMENT_GAIN and TIP_SPAN_REDUCTION."""
        cfg_base = AircraftConfig(fold_tip_deg=0.0, dihedral_deg=0.0, fold_trailing_deg=0.0, elevon_deg=0.0)
        cfg_folded = replace(cfg_base, fold_tip_deg=30.0)

        f_base = sd.stress_field(cfg_base, dynamic_pressure_pa=100.0, load_factor=2.0)
        f_folded = sd.stress_field(cfg_folded, dynamic_pressure_pa=100.0, load_factor=2.0)

        tip_factor = math.sin(math.radians(30.0))
        # TIP_MOMENT_GAIN increases root moment and max stress
        expected_moment_ratio = 1.0 + sd.TIP_MOMENT_GAIN * tip_factor
        assert f_folded["max_stress_pa"] / f_base["max_stress_pa"] == pytest.approx(expected_moment_ratio, rel=1e-12)

        # TIP_SPAN_REDUCTION shortens reported semispan
        expected_semispan_ratio = 1.0 - sd.TIP_SPAN_REDUCTION * tip_factor
        assert f_folded["semispan_m"] / f_base["semispan_m"] == pytest.approx(expected_semispan_ratio, rel=1e-12)

    def test_te_fold_coupling_reduces_chord_and_elevon_concentrates_te(self) -> None:
        """Trailing-edge fold reduces section modulus chord; elevon increases trailing-edge stress row."""
        cfg_base = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=0.0, dihedral_deg=0.0, fold_tip_deg=0.0)
        cfg_te = replace(cfg_base, fold_trailing_deg=30.0)

        f_base = sd.stress_field(cfg_base, dynamic_pressure_pa=100.0, load_factor=2.0)
        f_te = sd.stress_field(cfg_te, dynamic_pressure_pa=100.0, load_factor=2.0)

        te_factor = math.sin(math.radians(30.0))
        expected_chord_scale = 1.0 - sd.TE_FOLD_CHORD_REDUCTION * te_factor
        # Peak root stress (at leading edge x=0) scales as 1 / chord_scale
        assert f_te["max_stress_pa"] / f_base["max_stress_pa"] == pytest.approx(1.0 / expected_chord_scale, rel=1e-12)

        # Elevon deflection increases the trailing edge row via ELEVON_TE_GAIN and ELEVON_ARM_GAIN
        cfg_elevon = replace(cfg_base, elevon_deg=15.0)
        f_elevon = sd.stress_field(cfg_elevon, dynamic_pressure_pa=100.0, load_factor=2.0)
        te_stress_base = np.array(f_base["stress_pa"])[-1, :]
        te_stress_elevon = np.array(f_elevon["stress_pa"])[-1, :]
        assert np.max(te_stress_elevon) > np.max(te_stress_base)


class TestEmpiricalCoefficientsSensitivity:
    """Task 2 & Falsifiable Criteria: Quantify sensitivity of peak stress, stress index, failure status, and objective score."""

    def _eval_configuration(self, cfg: AircraftConfig, nz: float, beta: float = 0.0) -> Dict[str, Any]:
        field = sd.stress_field(cfg, dynamic_pressure_pa=100.0, load_factor=nz, beta_deg=beta)
        obj_dict = {"min_max_stress": field["max_stress_index"]}
        score = aggregate_score(obj_dict, {"min_max_stress": 1.0}, ["min_max_stress"])
        return {
            "max_stress_pa": field["max_stress_pa"],
            "stress_index": field["max_stress_index"],
            "failure_index": field["failure_index"],
            "status": field["status"],
            "score": score,
            "semispan_m": field["semispan_m"],
            "field": field,
        }

    def test_tip_moment_gain_sensitivity_and_failure_transition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TIP_MOMENT_GAIN sensitivity: increases stress, reduces objective score, and can induce failure transition."""
        cfg = AircraftConfig(paper_gsm=80.0, paper_stiffness=0.3, fold_tip_deg=45.0)
        nz = 10.0

        # Baseline
        base = self._eval_configuration(cfg, nz)
        assert base["status"] == sd.STATUS_HIGH_LOAD

        # Perturb TIP_MOMENT_GAIN upward
        monkeypatch.setattr(sd, "TIP_MOMENT_GAIN", sd.TIP_MOMENT_GAIN * 1.5)
        higher = self._eval_configuration(cfg, nz)
        assert higher["max_stress_pa"] > base["max_stress_pa"]
        assert higher["stress_index"] > base["stress_index"]
        assert higher["score"] < base["score"]
        # With high enough gain, transitions to failed
        monkeypatch.setattr(sd, "TIP_MOMENT_GAIN", 3.0)
        failed = self._eval_configuration(cfg, nz)
        assert failed["status"] == sd.STATUS_FAILED

        # With zero gain, transitions to ok
        monkeypatch.setattr(sd, "TIP_MOMENT_GAIN", 0.0)
        safe = self._eval_configuration(cfg, nz)
        assert safe["status"] == sd.STATUS_OK

    def test_z_scale_offset_and_gain_sensitivity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Z_SCALE_OFFSET and Z_SCALE_GAIN sensitivity: increase section modulus, lowering stress and raising score."""
        cfg = AircraftConfig(paper_gsm=80.0, paper_stiffness=0.4, fold_tip_deg=20.0)
        nz = 8.0

        base = self._eval_configuration(cfg, nz)

        # Higher Z_SCALE_OFFSET -> higher Z -> lower stress -> higher score
        monkeypatch.setattr(sd, "Z_SCALE_OFFSET", sd.Z_SCALE_OFFSET * 1.3)
        perturbed_offset = self._eval_configuration(cfg, nz)
        assert perturbed_offset["max_stress_pa"] < base["max_stress_pa"]
        assert perturbed_offset["stress_index"] < base["stress_index"]
        assert perturbed_offset["score"] > base["score"]

        # Restore and test Z_SCALE_GAIN
        monkeypatch.setattr(sd, "Z_SCALE_OFFSET", 0.55)
        monkeypatch.setattr(sd, "Z_SCALE_GAIN", sd.Z_SCALE_GAIN * 1.3)
        perturbed_gain = self._eval_configuration(cfg, nz)
        assert perturbed_gain["max_stress_pa"] < base["max_stress_pa"]
        assert perturbed_gain["stress_index"] < base["stress_index"]
        assert perturbed_gain["score"] > base["score"]

    def test_te_fold_chord_reduction_sensitivity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TE_FOLD_CHORD_REDUCTION sensitivity: higher reduction -> narrower effective chord -> higher stress -> lower score."""
        cfg = AircraftConfig(fold_trailing_deg=30.0)
        nz = 5.0
        base = self._eval_configuration(cfg, nz)

        monkeypatch.setattr(sd, "TE_FOLD_CHORD_REDUCTION", sd.TE_FOLD_CHORD_REDUCTION * 1.5)
        perturbed = self._eval_configuration(cfg, nz)
        assert perturbed["max_stress_pa"] > base["max_stress_pa"]
        assert perturbed["stress_index"] > base["stress_index"]
        assert perturbed["score"] < base["score"]

    def test_sideload_coupling_sensitivity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SIDELOAD_COUPLING sensitivity: increases root moment under sideslip beta > 0."""
        cfg = AircraftConfig()
        nz = 5.0
        beta = 15.0

        base = self._eval_configuration(cfg, nz, beta=beta)

        monkeypatch.setattr(sd, "SIDELOAD_COUPLING", sd.SIDELOAD_COUPLING * 1.5)
        perturbed = self._eval_configuration(cfg, nz, beta=beta)
        assert perturbed["max_stress_pa"] > base["max_stress_pa"]
        assert perturbed["stress_index"] > base["stress_index"]
        assert perturbed["score"] < base["score"]

    def test_elevon_arm_gain_and_te_gain_sensitivity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ELEVON_ARM_GAIN and ELEVON_TE_GAIN sensitivity: increase trailing-edge row stress under elevon deflection."""
        cfg = AircraftConfig(elevon_deg=20.0)
        nz = 5.0

        base = self._eval_configuration(cfg, nz)
        base_te_peak = float(np.max(np.array(base["field"]["stress_pa"])[-1, :]))

        monkeypatch.setattr(sd, "ELEVON_TE_GAIN", sd.ELEVON_TE_GAIN * 1.5)
        perturbed_te = self._eval_configuration(cfg, nz)
        perturbed_te_peak = float(np.max(np.array(perturbed_te["field"]["stress_pa"])[-1, :]))
        assert perturbed_te_peak > base_te_peak

        monkeypatch.setattr(sd, "ELEVON_TE_GAIN", 1.5)
        monkeypatch.setattr(sd, "ELEVON_ARM_GAIN", sd.ELEVON_ARM_GAIN * 1.5)
        perturbed_arm = self._eval_configuration(cfg, nz)
        perturbed_arm_peak = float(np.max(np.array(perturbed_arm["field"]["stress_pa"])[-1, :]))
        assert perturbed_arm_peak > base_te_peak

    def test_tip_span_reduction_sensitivity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TIP_SPAN_REDUCTION sensitivity: shortens loaded semispan in presence of fold_tip_deg."""
        cfg = AircraftConfig(fold_tip_deg=30.0)
        nz = 5.0

        base = self._eval_configuration(cfg, nz)

        monkeypatch.setattr(sd, "TIP_SPAN_REDUCTION", sd.TIP_SPAN_REDUCTION * 1.5)
        perturbed = self._eval_configuration(cfg, nz)
        assert perturbed["semispan_m"] < base["semispan_m"]

    def test_retained_uncalibrated_coupling_inventory_values(self) -> None:
        """Verify that established baseline constants are retained without arbitrary modification."""
        expected_values = {
            "TIP_MOMENT_GAIN": 1.6,
            "SPAN_SHIFT_GAIN": 0.6,
            "DIHEDRAL_LOAD_COUPLING": 0.8,
            "DIHEDRAL_MOMENT_COUPLING": 0.45,
            "SIDELOAD_COUPLING": 0.35,
            "ELEVON_ARM_GAIN": 0.6,
            "ELEVON_TE_GAIN": 1.5,
            "TE_FOLD_CHORD_REDUCTION": 0.35,
            "TIP_SPAN_REDUCTION": 0.20,
            "Z_SCALE_OFFSET": 0.55,
            "Z_SCALE_GAIN": 0.9,
        }
        for name, expected in expected_values.items():
            assert getattr(sd, name) == pytest.approx(expected, rel=1e-12), (
                f"{name} was modified from established value {expected}"
            )
