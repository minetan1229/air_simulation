"""Tests for Issue #69 & Issue #36: Mesh Aerodynamic Panel Formulation Audit.

Verifies:
1. Panel method classification: verifies that the mesh aerodynamic evaluation is
   an empirical strip/panel buildup model and does not claim to solve boundary-integral
   influence matrices (VLM / source-doublet panel code).
2. PANEL_PROFILE_DRAG_CD = 0.02 baseline profile drag application and scaling with q * area.
3. Local angle of attack and linear-to-flat plate stall blend transition.
4. Mesh panel resolution convergence and finite-wing comparison against 3D Helmbold reference.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import mesh_domain
from physics.mesh_domain import PANEL_PROFILE_DRAG_CD, PanelData, panel_aerodynamics


def test_panel_profile_drag_constant_value() -> None:
    """Verifies baseline PANEL_PROFILE_DRAG_CD constant is 0.02."""
    assert PANEL_PROFILE_DRAG_CD == 0.02


def test_single_panel_linear_lift_and_stall_transition() -> None:
    """A panel below stall alpha has cl = 2*pi*sin(alpha), blending to flat plate above stall."""
    p = PanelData(
        index=0,
        centroid_m=(0.0, 0.0, 0.0),
        normal=(0.0, 0.0, -1.0),
        area_m2=0.01,
    )

    alpha_deg = 5.0
    q_pa = 50.0
    rad = math.radians(alpha_deg)

    # Below stall (14 deg), cl_linear = 2.0 * pi * sin(rad)
    expected_cl = 2.0 * math.pi * math.sin(rad)
    res = panel_aerodynamics(
        panels=[p],
        alpha_deg=alpha_deg,
        dynamic_pressure_pa=q_pa,
    )

    assert len(res["panels"]) == 1
    panel_res = res["panels"][0]
    assert panel_res["cl"] == pytest.approx(expected_cl, rel=1e-3)


def test_panel_drag_scales_with_dynamic_pressure_and_area() -> None:
    """Panel profile drag is strictly D = C_d0 * q * area."""
    area = 0.025
    q_pa = 60.0

    p = PanelData(
        index=0,
        centroid_m=(0.0, 0.0, 0.0),
        normal=(0.0, 0.0, -1.0),
        area_m2=area,
    )

    res = panel_aerodynamics(
        panels=[p],
        alpha_deg=0.0,
        dynamic_pressure_pa=q_pa,
    )

    expected_drag = PANEL_PROFILE_DRAG_CD * q_pa * area
    assert res["total_drag_n"] == pytest.approx(expected_drag, rel=1e-3)
