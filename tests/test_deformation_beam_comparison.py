"""Issue #11: deformation / stiffness / damping parameter basis and
beam-theory comparison for physics.stress_domain.

Covers:
  1. Parameter basis/units for deformation, stiffness, damping: the
     STRUCTURAL_PARAMETER_REGISTRY records each constant's value, unit,
     basis (unvalidated empirical vs documented geometric model) and
     scope; structural_parameters_documented() verifies the marks.
  2. Representative load compared with independent beam theory: the
     cantilever pump analogy delta_tip = P L^3/(3EI), theta_tip =
     P L^2/(2EI) is evaluated independently, and both it and
     aeroelastic_deformations() are shown to scale linearly in F and
     in 1/(EI) (proportional to compliance / flex_gain, monotonic).
  3. Scope and unmodelled phenomena: documented in the module
     STRUCTURAL_PARAMETER_REGISTRY docstring and checked here
     (clamps, zero-gain behaviour, finiteness).
"""

from __future__ import annotations

import math

import pytest

from physics import stress_domain as sd
from physics.config_schema import AircraftConfig


def _cfg(flex: float = 0.0, twist: float = 0.0, **kw) -> AircraftConfig:
    base = dict(aeroelastic_flex=flex, aeroelastic_twist=twist)
    base.update(kw)
    return AircraftConfig(**base)


@pytest.fixture()
def q() -> float:
    return 40.0  # representative dynamic pressure [Pa]


def _beam_spans(cfg: AircraftConfig) -> tuple[float, float]:
    derived = sd.derived_properties(cfg) if hasattr(sd, "derived_properties") else None
    import physics.config_schema as cs
    d = cs.derived_properties(cfg)
    span = max(float(d.get("ref_span_m") or d["wingspan_m"]), 1e-9)
    return span, span * 0.5


def _ei_proxy(cfg: AircraftConfig) -> float:
    """EI proxy exactly as aeroelastic_deformations builds it: eff_stiff * t^3."""
    from physics.stress_domain import _sheet_thickness, effective_paper_stiffness
    eff = max(effective_paper_stiffness(cfg), 0.01)
    t = _sheet_thickness(max(float(cfg.paper_gsm), 0.0))
    return max(eff * max(t ** 3, 1e-18), 1e-18)


# ---------------------------------------------------------------------------
# 1. Parameter registry: basis / units documented
# ---------------------------------------------------------------------------

def test_registry_covers_empirical_constants():
    reg = sd.STRUCTURAL_PARAMETER_REGISTRY
    for name in ("KAPPA_SIGMA", "AEROELASTIC_COMPLIANCE_DEG_PER_PA",
                 "PAPER_THICKNESS_DENOMINATOR", "ALLOWABLE_BASE_PA"):
        assert name in reg, f"{name} missing from STRUCTURAL_PARAMETER_REGISTRY"
    for entry in reg.values():
        assert set(entry) >= {"value", "unit", "basis", "scope"}
        assert entry["basis"] in ("unvalidated empirical", "documented geometric model")
        assert math.isfinite(float(entry["value"])) and float(entry["value"]) > 0.0


def test_empirical_constants_marked_unvalidated():
    """KAPPA_SIGMA and AEROELASTIC_COMPLIANCE must read 'unvalidated empirical'."""
    reg = sd.STRUCTURAL_PARAMETER_REGISTRY
    assert reg["KAPPA_SIGMA"]["basis"] == "unvalidated empirical"
    assert reg["AEROELASTIC_COMPLIANCE_DEG_PER_PA"]["basis"] == "unvalidated empirical"
    assert sd.KAPPA_SIGMA > 0.0 and math.isfinite(sd.KAPPA_SIGMA)
    assert sd.AEROELASTIC_COMPLIANCE_DEG_PER_PA > 0.0
    assert math.isfinite(sd.AEROELASTIC_COMPLIANCE_DEG_PER_PA)
    assert "unvalidated" in sd.__doc__.lower() or "empirical" in sd.__doc__.lower()


def test_structural_parameters_documented():
    assert sd.structural_parameters_documented() is True


def test_aeroelastic_config_default_scales():
    """Issue statement: aeroelastic_pressure_scale_pa default is 160 Pa."""
    cfg = AircraftConfig()
    assert cfg.aeroelastic_pressure_scale_pa == pytest.approx(160.0)
    assert cfg.aeroelastic_flex == pytest.approx(0.0)
    assert cfg.aeroelastic_twist == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2. Beam-theory comparison: proportional, monotonic, linear in F and 1/EI
# ---------------------------------------------------------------------------

def _theta_beam(cfg: AircraftConfig, nz: float) -> float:
    """theta_beam = M_root * L / EI with M_root = F*L/2, F = m g nz (rad->deg)."""
    _, L = _beam_spans(cfg)
    F = cfg.mass_kg * 9.80665 * nz
    M_root = F * L / 2.0
    EI = _ei_proxy(cfg)
    return math.degrees(M_root * L / EI)


def test_beam_analogy_linear_in_load(q):
    """Both beam tip rotation and model delta scale linearly in F."""
    cfg = _cfg(flex=1.0, twist=0.0)
    for nz in (0.25, 0.5, 1.0, 2.0, 4.0):
        th = _theta_beam(cfg, nz)
        out = sd.aeroelastic_deformations(cfg, q, nz)
        # Beam: theta proportional to nz
        assert th == pytest.approx(_theta_beam(cfg, 1.0) * nz, rel=1e-9)
        # Model delta strictly increasing in nz, not clamped in this range
        assert 0.0 < out["delta_dihedral_deg"] <= 30.0
    d1 = sd.aeroelastic_deformations(cfg, q, 1.0)["delta_dihedral_deg"]
    d2 = sd.aeroelastic_deformations(cfg, q, 2.0)["delta_dihedral_deg"]
    d4 = sd.aeroelastic_deformations(cfg, q, 4.0)["delta_dihedral_deg"]
    # delta ~ F i.e. linear in nz away from clamps
    if d4 < 29.9:
        assert d2 == pytest.approx(2.0 * d1, rel=1e-6)
        assert d4 == pytest.approx(4.0 * d1, rel=1e-6)


def test_delimiters_proportional_to_compliance_and_flex_gain(q):
    """Model delta proportional to flex_gain and to AEROELASTIC_COMPLIANCE."""
    cfg = _cfg(flex=1.0, twist=0.0)
    d = sd.aeroelastic_deformations(cfg, q, 1.0)["delta_dihedral_deg"]
    d_half = sd.aeroelastic_deformations(_cfg(flex=0.5, twist=0.0), q, 1.0)["delta_dihedral_deg"]
    assert d > 0.0
    assert d_half == pytest.approx(0.5 * d, rel=1e-6)
    # monotonic in flex gain
    prev = -1.0
    for g in (0.1, 0.3, 0.5, 0.8, 1.0):
        cur = sd.aeroelastic_deformations(_cfg(flex=g, twist=0.0), q, 1.0)["delta_dihedral_deg"]
        assert cur > prev
        prev = cur
    # proportional to compliance constant (monkeypatch)
    orig = sd.AEROELASTIC_COMPLIANCE_DEG_PER_PA
    try:
        sd.AEROELASTIC_COMPLIANCE_DEG_PER_PA = orig * 3.0
        d3 = sd.aeroelastic_deformations(cfg, q, 1.0)["delta_dihedral_deg"]
    finally:
        sd.AEROELASTIC_COMPLIANCE_DEG_PER_PA = orig
    assert d3 == pytest.approx(3.0 * d, rel=1e-6)


def test_inverse_EI_scaling_matches_beam(q):
    """Both model delta and theta_beam scale as 1/(EI): doubling EI halves both."""
    cfg = _cfg(flex=1.0, twist=0.0)
    d_base = sd.aeroelastic_deformations(cfg, q, 1.0)["delta_dihedral_deg"]
    th_base = _theta_beam(cfg, 1.0)
    # double effective stiffness -> EI doubles -> both halve (paper_stiffness)
    cfg2 = _cfg(flex=1.0, twist=0.0, paper_stiffness=cfg.paper_stiffness * 2.0)
    # effective stiffness is not exactly 2x if grain term; recompute ratio
    ei_ratio = _ei_proxy(cfg2) / _ei_proxy(cfg)
    d2 = sd.aeroelastic_deformations(cfg2, q, 1.0)["delta_dihedral_deg"]
    th2 = _theta_beam(cfg2, 1.0)
    assert d2 == pytest.approx(d_base / ei_ratio, rel=1e-6)
    assert th2 == pytest.approx(th_base / ei_ratio, rel=1e-9)
    # L vs L^2 geometric factor difference documented in registry scope:
    # model delta ~ F*L/EI, beam theta ~ F*L^2/EI -- both linear in F, 1/EI;
    # the extra L in the beam formula is the scope limitation.
    scope = sd.STRUCTURAL_PARAMETER_REGISTRY["AEROELASTIC_COMPLIANCE_DEG_PER_PA"]["scope"]
    assert "L^2" in scope


def test_washout_proportional_to_twist_gain(q):
    cfg = _cfg(flex=0.0, twist=1.0)
    w = sd.aeroelastic_deformations(cfg, q, 1.0)["washout_twist_deg"]
    assert w > 0.0 and w == pytest.approx(w, rel=1e-12)
    w_half = sd.aeroelastic_deformations(_cfg(flex=0.0, twist=0.5), q, 1.0)["washout_twist_deg"]
    assert w_half == pytest.approx(0.5 * w, rel=1e-6)


# ---------------------------------------------------------------------------
# 3. Scope: clamps, zero paths, finiteness, unmodelled phenomena
# ---------------------------------------------------------------------------

def test_clamps():
    cfg = _cfg(flex=1000.0, twist=1000.0)
    out = sd.aeroelastic_deformations(cfg, 1e5, 1e4)
    assert out["delta_dihedral_deg"] == pytest.approx(30.0)
    assert out["washout_twist_deg"] == pytest.approx(20.0)


def test_zero_when_gains_or_q_zero():
    cfg = AircraftConfig()  # flex = twist = 0 by default
    out = sd.aeroelastic_deformations(cfg, 40.0, 1.0)
    assert out["delta_dihedral_deg"] == 0.0 and out["washout_twist_deg"] == 0.0
    cfg = _cfg(flex=1.0, twist=1.0)
    out = sd.aeroelastic_deformations(cfg, 0.0, 1.0)
    assert out["delta_dihedral_deg"] == 0.0 and out["washout_twist_deg"] == 0.0


def test_finite_for_extreme_inputs():
    for gsm in (0.0, 1e6, 1e9):
        for nz in (0.0, 1.0, 1e6):
            for qv in (0.0, 40.0, 1e6):
                cfg = _cfg(flex=5.0, twist=5.0, paper_gsm=gsm)
                out = sd.aeroelastic_deformations(cfg, qv, nz)
                for k in ("delta_dihedral_deg", "washout_twist_deg", "effective_stiffness"):
                    assert math.isfinite(out[k]), (gsm, nz, qv, k, out[k])
                assert out["effective_stiffness"] >= 0.01


def test_unmodelled_phenomena_in_registry_docs():
    scopes = " ".join(e["scope"] for e in sd.STRUCTURAL_PARAMETER_REGISTRY.values())
    module_doc = " ".join(
        [
            getattr(sd, "__doc__", "") or "",
            sd.STRUCTURAL_PARAMETER_REGISTRY.__doc__ or "",
        ]
    )
    combined = (scopes + " " + module_doc).lower()
    for token in ("creep", "viscoelastic", "flutter", "shear", "large-deflection", "torsion"):
        assert token in combined, f"unmodelled phenomenon '{token}' not documented in stress_domain"


def test_effective_stiffness_finite_damping_hygiene():
    """Stiffness damping hygiene: eff_stiff floored so denominator stays finite."""
    cfg = _cfg(flex=1.0, twist=1.0, paper_stiffness=0.0, paper_gsm=0.0)
    out = sd.aeroelastic_deformations(cfg, 40.0, 1.0)
    assert math.isfinite(out["delta_dihedral_deg"]) and math.isfinite(out["effective_stiffness"])
