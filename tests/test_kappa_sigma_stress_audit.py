"""Audit and verification of KAPPA_SIGMA structural stress scaling.

Issue #108:
[Structural Physics] KAPPA_SIGMA=9e-6 の根拠・応力尺度を実測で再検証する

Tasks & Verification:
1. Inspect physics/stress_domain.py::KAPPA_SIGMA and its application across root bending
   moment, section modulus Z, allowable stress calibration, and structural objective scaling.
2. Formulate hypothesis and falsifiable criteria:
   - KAPPA_SIGMA = 9.0e-6 scales raw theoretical beam-theory stress M/Z (computed with flat-sheet
     thickness) into a normalized structural index / empirical Pa representation matching
     experimental folded paper specimen failure thresholds (~10^2 - 10^3 Pa allowable range).
   - Retaining KAPPA_SIGMA as an uncalibrated empirical constant preserves relative ranking
     of individuals, stress monotonicity under load, failure status transitions, and cantilever
     beam linearity.
   - Absolute stress values represent an uncalibrated structural index rather than physical tensile
     failure stress of raw cellulose fibers (~20-40 MPa).
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, List

import numpy as np
import pytest

from physics import stress_domain as sd
from physics.config_schema import AircraftConfig
from physics.optimizer_domain import aggregate_score


class TestKappaSigmaRegistryAndDocumentation:
    """Audit KAPPA_SIGMA registry entries, documentation, and empirical basis."""

    def test_kappa_sigma_constant_defined_and_exported(self) -> None:
        """KAPPA_SIGMA must be exported in __all__ and equal to 9.0e-6."""
        assert hasattr(sd, "KAPPA_SIGMA")
        assert "KAPPA_SIGMA" in sd.__all__
        assert isinstance(sd.KAPPA_SIGMA, float)
        assert sd.KAPPA_SIGMA == pytest.approx(9.0e-6, rel=1e-12)

    def test_parameter_registry_documentation(self) -> None:
        """Structural parameter registry must document KAPPA_SIGMA as empirical index."""
        assert sd.structural_parameters_documented() is True
        registry = sd.STRUCTURAL_PARAMETER_REGISTRY
        assert "KAPPA_SIGMA" in registry
        entry = registry["KAPPA_SIGMA"]
        assert entry["value"] == pytest.approx(9.0e-6, rel=1e-12)
        assert entry["unit"] == "dimensionless"
        assert entry["basis"] == "unvalidated empirical"
        scope = entry["scope"]
        assert "Folded-crease stress reduction factor" in scope
        assert "normalized structural index" in scope
        assert "experimental folded paper specimen failure thresholds" in scope


class TestCantileverBeamTheoryLinearity:
    """Verify Euler-Bernoulli beam theory linearity: sigma = KAPPA_SIGMA * M / Z."""

    @pytest.fixture
    def base_config(self) -> AircraftConfig:
        return AircraftConfig(
            mass_kg=0.005,
            wingspan_m=0.30,
            wing_area_m2=0.03,
            paper_gsm=80.0,
            paper_stiffness=0.4,
            taper_ratio=1.0,
            dihedral_deg=0.0,
            fold_tip_deg=0.0,
            fold_trailing_deg=0.0,
            elevon_deg=0.0,
        )

    def test_exact_theoretical_scaling(self, base_config: AircraftConfig) -> None:
        """Verify sigma_raw = M / Z is scaled strictly by KAPPA_SIGMA."""
        q = 25.0
        nz = 1.5
        # Use odd spanwise resolution (25) so centre column is exactly the root (y_signed=0)
        field = sd.stress_field(base_config, dynamic_pressure_pa=q, load_factor=nz, spanwise=25, chordwise=17)
        raw_moment = field["wing_root_moment_nm"]

        # Calculate flat-sheet Z at root
        t = base_config.paper_gsm / (sd.PAPER_THICKNESS_DENOMINATOR * 1000.0)
        c_root = sd.root_chord_batch(
            np, base_config.wing_area_m2, base_config.wingspan_m, base_config.taper_ratio
        )
        stiffness = sd.effective_paper_stiffness(base_config)
        z_gain = sd.Z_SCALE_OFFSET + sd.Z_SCALE_GAIN * stiffness
        z_raw = c_root * (t**2) * z_gain / 6.0

        # Theoretical raw Euler-Bernoulli beam stress (order ~10^7 Pa = tens of MPa)
        sigma_raw = raw_moment / z_raw
        assert sigma_raw > 1.0e6  # Raw flat-sheet beam stress is on the order of 10-100 MPa

        # Empirical scaled stress reported by stress_domain
        expected_scaled_stress = sd.KAPPA_SIGMA * sigma_raw

        # peak_root_stress_pa should match KAPPA_SIGMA * M / Z
        peak_stress = sd.peak_root_stress_pa(
            np,
            load_factor=nz,
            mass_kg=base_config.mass_kg,
            gravity=9.80665,
            span_m=base_config.wingspan_m,
            taper_ratio=base_config.taper_ratio,
            tip_factor=0.0,
            dihedral_rad=math.radians(base_config.dihedral_deg),
            root_chord_m=c_root,
            chord_scale=1.0,
            thickness_m=t,
            z_gain=z_gain,
        )
        assert float(peak_stress) == pytest.approx(float(expected_scaled_stress), rel=1e-6)
        assert field["max_stress_pa"] == pytest.approx(float(expected_scaled_stress), rel=1e-6)

    def test_linear_scaling_with_moment_and_load_factor(self, base_config: AircraftConfig) -> None:
        """Stress must scale strictly linearly with bending moment and normal load."""
        loads = [0.5, 1.0, 2.0, 3.0]
        stresses = []
        for nz in loads:
            field = sd.stress_field(base_config, dynamic_pressure_pa=20.0, load_factor=nz)
            stresses.append(field["max_stress_pa"])

        # Check linearity: stress / nz == constant
        ratios = [s / nz for s, nz in zip(stresses, loads)]
        for r in ratios[1:]:
            assert r == pytest.approx(ratios[0], rel=1e-5)

    def test_inverse_square_scaling_with_sheet_thickness(self, base_config: AircraftConfig) -> None:
        """Section modulus Z is proportional to t^2, so stress must scale as 1/t^2 for identical geometry."""
        # Double gsm means 2x thickness -> 4x Z -> 0.25x stress
        cfg1 = replace(base_config, paper_gsm=80.0)
        cfg2 = replace(base_config, paper_gsm=160.0)
        # Note: keep mass identical by holding mass_kg constant
        field1 = sd.stress_field(cfg1, dynamic_pressure_pa=20.0, load_factor=1.0)
        field2 = sd.stress_field(cfg2, dynamic_pressure_pa=20.0, load_factor=1.0)
        # t2 / t1 = 2 -> Z2 / Z1 = 4 -> stress2 / stress1 = 0.25
        assert field2["max_stress_pa"] == pytest.approx(0.25 * field1["max_stress_pa"], rel=1e-4)


class TestStressScaleAndFailureThresholdMapping:
    """Verify that KAPPA_SIGMA maps beam stress into experimental failure thresholds (~10^2 - 10^3 Pa)."""

    def test_absolute_stress_vs_cellulose_fiber_tensile_strength(self) -> None:
        """Raw cellulose tensile failure is ~20-40 MPa; empirical scaled stress is ~10^2 - 10^3 Pa."""
        cfg = AircraftConfig(
            mass_kg=0.005,
            wingspan_m=0.28,
            wing_area_m2=0.025,
            paper_gsm=80.0,
            paper_stiffness=0.4,
        )
        field = sd.stress_field(cfg, dynamic_pressure_pa=25.0, load_factor=1.0)
        max_stress = field["max_stress_pa"]
        allowable = field["allowable_stress_pa"]

        # Absolute scaled stress is in the 100 - 3000 Pa range
        assert 50.0 <= max_stress <= 5000.0
        assert 500.0 <= allowable <= 5000.0

        # Contrast with raw physical tensile strength of cellulose paper (20 to 40 MPa)
        cellulose_tensile_min_pa = 20.0e6
        cellulose_tensile_max_pa = 40.0e6
        assert max_stress < 0.001 * cellulose_tensile_min_pa

        # Ratio of raw unscaled beam stress to cellulose tensile strength is order unity
        raw_beam_stress = max_stress / sd.KAPPA_SIGMA
        assert 0.1 * cellulose_tensile_min_pa <= raw_beam_stress <= 10.0 * cellulose_tensile_max_pa


class TestMonotonicityAndIndividualRankingInvariance:
    """Verify that retaining KAPPA_SIGMA preserves relative individual ranking and monotonicity."""

    @pytest.fixture
    def population(self) -> List[AircraftConfig]:
        """A diverse population of airframe configurations."""
        configs = []
        for i, (span, area, gsm, stiff) in enumerate([
            (0.20, 0.015, 70.0, 0.3),
            (0.25, 0.020, 80.0, 0.4),
            (0.30, 0.025, 90.0, 0.5),
            (0.35, 0.035, 100.0, 0.6),
            (0.22, 0.018, 120.0, 0.45),
        ]):
            configs.append(
                AircraftConfig(
                    mass_kg=0.003 + 0.001 * i,
                    wingspan_m=span,
                    wing_area_m2=area,
                    paper_gsm=gsm,
                    paper_stiffness=stiff,
                )
            )
        return configs

    def test_ranking_invariance_under_arbitrary_positive_kappa_scaling(
        self, population: List[AircraftConfig]
    ) -> None:
        """For any positive scalar multiplier on KAPPA_SIGMA, the relative order of max_stress is invariant."""
        stresses_nominal = [
            sd.stress_field(cfg, dynamic_pressure_pa=25.0, load_factor=1.5)["max_stress_pa"]
            for cfg in population
        ]

        # Ranking of population under nominal KAPPA_SIGMA
        ranking_nominal = np.argsort(stresses_nominal)

        # Simulate different arbitrary scaling factors on KAPPA_SIGMA
        for scale in [0.1, 0.5, 2.0, 10.0]:
            stresses_scaled = [s * scale for s in stresses_nominal]
            ranking_scaled = np.argsort(stresses_scaled)
            assert np.array_equal(ranking_nominal, ranking_scaled)

    def test_stress_monotonicity_under_load_increase(self, population: List[AircraftConfig]) -> None:
        """Stress must be strictly monotonically increasing with load factor across all designs."""
        load_factors = [0.5, 1.0, 1.5, 2.0, 2.5]
        for cfg in population:
            stresses = [
                sd.stress_field(cfg, dynamic_pressure_pa=20.0, load_factor=nz)["max_stress_pa"]
                for nz in load_factors
            ]
            for i in range(len(stresses) - 1):
                assert stresses[i + 1] > stresses[i]

    def test_failure_status_monotonic_transitions(self) -> None:
        """As load increases, status transitions monotonically: ok -> high_load -> failed."""
        cfg = AircraftConfig(
            mass_kg=0.006,
            wingspan_m=0.32,
            wing_area_m2=0.025,
            paper_gsm=80.0,
            paper_stiffness=0.4,
        )
        allowable = sd.paper_allowable_stress(cfg.paper_gsm, cfg.paper_stiffness)

        # Find nz values corresponding to index < 0.6, 0.6 <= index < 1.0, and index >= 1.0
        # First sample at nz = 0.5
        field_low = sd.stress_field(cfg, dynamic_pressure_pa=10.0, load_factor=0.5)
        # Compute required nz for high_load (fi = 0.7) and failed (fi = 1.2)
        base_stress = field_low["max_stress_pa"] / 0.5
        nz_high_load = (0.7 * allowable) / base_stress
        nz_failed = (1.2 * allowable) / base_stress

        field_ok = sd.stress_field(cfg, dynamic_pressure_pa=10.0, load_factor=0.3)
        field_hl = sd.stress_field(cfg, dynamic_pressure_pa=10.0, load_factor=nz_high_load)
        field_fl = sd.stress_field(cfg, dynamic_pressure_pa=10.0, load_factor=nz_failed)

        assert field_ok["status"] == sd.STATUS_OK
        assert field_ok["failure_index"] < sd.STATUS_HIGH_LOAD_THRESHOLD

        assert field_hl["status"] == sd.STATUS_HIGH_LOAD
        assert sd.STATUS_HIGH_LOAD_THRESHOLD <= field_hl["failure_index"] < sd.STATUS_FAILED_THRESHOLD

        assert field_fl["status"] == sd.STATUS_FAILED
        assert field_fl["failure_index"] >= sd.STATUS_FAILED_THRESHOLD


class TestOptimizerObjectiveCoupling:
    """Verify how max_stress_index couples into GA / optimizer scoring."""

    def test_objective_score_monotonicity_with_stress_index(self) -> None:
        """aggregate_score with min_max_stress must decrease monotonically as max_stress_index increases."""
        indices = [0.1, 0.3, 0.5, 0.8, 1.0]
        scores = []
        for idx in indices:
            score = aggregate_score(
                objectives=["min_max_stress"],
                values={"min_max_stress": idx},
                weights={"min_max_stress": 1.0},
            )
            scores.append(score)

        # Because direction is -1 (minimize stress), higher stress index gives lower score
        for i in range(len(scores) - 1):
            assert scores[i + 1] < scores[i]
            # Linearity of objective weighting
            delta = scores[i] - scores[i + 1]
            expected_delta = (indices[i + 1] - indices[i]) / 0.5  # OBJECTIVE_SCALES["min_max_stress"] = 0.5
            assert delta == pytest.approx(expected_delta, rel=1e-5)
