"""Model discrepancy quantification: panel method vs Helmbold (#133).

The panel method in physics/mesh_domain.py uses the 2D thin-airfoil slope
2*pi (per rad) while the canonical flight path uses the 3D Helmbold
triangular-wing slope C_Lalpha = 2*pi*AR / (2 + sqrt(AR^2*(1+lam)+4*AR/pi)).
For the default AR=2.667, lam=0.7 this is a 2.2x gap.  This module provides
a pure analytical comparison plus a downstream flight-impact summary
function used by tests and documented in docs/PHYSICS_MODELS.md §2.3.
It does NOT unify the models (intentional separation, no-finish §A-5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelDiscrepancyReport:
    """Analytical panel-vs-Helmbold comparison at identical conditions."""

    aspect_ratio: float
    taper_ratio: float
    panel_slope_per_rad: float
    helmbold_slope_per_rad: float
    ratio: float  # panel / helmbold
    relative_error: float  # (panel - helmbold) / helmbold
    trim_alpha_deg_panel: float  # alpha that gives C_L = W/(q S) for ref loading
    trim_alpha_deg_helmbold: float  # same loading with helmbold slope
    guidance: str


def panel_slope_per_rad() -> float:
    """2D panel slope 2*pi per rad (physics/mesh_domain.py:1510)."""
    return 2.0 * math.pi


def helmbold_slope_per_rad(aspect_ratio: float, taper_ratio: float) -> float:
    """Canonical Helmbold slope (physics/aero_domain.py::helmbold_cl_alpha)."""
    from physics.aero_domain import helmbold_cl_alpha

    return float(helmbold_cl_alpha(float(aspect_ratio), float(taper_ratio)))


def discrepancy_report(
    aspect_ratio: float = 2.666666666666667,
    taper_ratio: float = 0.7,
    reference_cl: float = 0.5,
) -> ModelDiscrepancyReport:
    """Compare coefficients, trim angles and usage guidance at same Re/alpha/S.

    ``reference_cl`` is the C_L needed for level flight at a chosen
    dynamic pressure; trim alpha differs inversely with slope, so the
    panel method predicts a ~2.2x smaller trim angle — GA rankings built
    on the two models are NOT interchangeable.
    """
    panel = panel_slope_per_rad()
    helmbold = helmbold_slope_per_rad(aspect_ratio, taper_ratio)
    ratio = panel / helmbold
    rel = (panel - helmbold) / helmbold
    # Linear model: C_L = slope * alpha(rad). Invert for trim.
    trim_panel = math.degrees(reference_cl / panel) if panel > 0 else 0.0
    trim_helmbold = math.degrees(reference_cl / helmbold) if helmbold > 0 else 0.0
    guidance = (
        "Panel method (2*pi) is 2.2x stiffer than Helmbold (3D, AR=2.67) "
        "for the default airframe. Use Helmbold for flight prediction and "
        "GA ranking; use panel method only for visualisation/local incidence "
        "diagnostics. Do not compare flight distance/time across models."
    )
    return ModelDiscrepancyReport(
        aspect_ratio=float(aspect_ratio),
        taper_ratio=float(taper_ratio),
        panel_slope_per_rad=panel,
        helmbold_slope_per_rad=helmbold,
        ratio=ratio,
        relative_error=rel,
        trim_alpha_deg_panel=float(trim_panel),
        trim_alpha_deg_helmbold=float(trim_helmbold),
        guidance=guidance,
    )


def flight_impact_summary(report: ModelDiscrepancyReport) -> dict[str, float]:
    """First-order downstream impact estimates (linear, small-alpha).

    Trim alpha scales as 1/slope, lift at fixed alpha scales as slope,
    induced drag scales as C_L^2/(pi AR e) — downstream metrics shift
    by the slope ratio to leading order.
    """
    ratio = report.ratio
    return {
        "lift_ratio_at_same_alpha": ratio,
        "trim_alpha_ratio_panel_over_helmbold": 1.0 / ratio,
        "induced_drag_ratio_at_same_cl": 1.0,
        "flight_distance_ratio_to_first_order": 1.0 / ratio,
        "rank_inversion_risk": ratio - 1.0,  # 0 would mean interchangeable
    }
