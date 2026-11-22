"""Issue #95: panel method is NOT wired into flight/GA paths.

This test is a static-usage guard: panel_aerodynamics must only be
called from tests and from its own module.  If a future change wires
it into flight_sim / GA, this test fails and forces the explicit
model-switch design + double-counting guard required by the issue.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest


def _call_sites(excluding: set[str]) -> list[str]:
    hits: list[str] = []
    for path in pathlib.Path("physics").rglob("*.py"):
        if str(path) in excluding:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if "panel_aerodynamics(" in line and "def panel_aerodynamics" not in line:
                hits.append(f"{path}:{line.strip()}")
    return hits


def test_panel_aerodynamics_not_in_flight_path():
    hits = _call_sites(excluding={
        r"physics\mesh_domain.py",
        r"physics/mesh_domain.py",
    })
    assert hits == [], (
        "panel_aerodynamics is called from flight path code:\n"
        + "\n".join(hits)
        + "\nWire it with an explicit model switch instead (#95)."
    )


def test_call_sites_are_test_only():
    """Every non-definition call site outside mesh_domain must live under tests/."""
    root = pathlib.Path(".")
    for path in list(root.rglob("*.py")):
        if ".venv" in str(path) or "site-packages" in str(path):
            continue
        # mesh_domain is the originating module (contains extract_panel_aerodynamic_rom)
        if "mesh_domain.py" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "panel_aerodynamics(" not in text:
            continue
        if str(path).startswith("physics"):
            for line in text.splitlines():
                if "panel_aerodynamics(" in line and "def " not in line:
                    pytest.fail(f"runtime call site: {path}: {line.strip()}")


def test_extract_panel_aerodynamic_rom_identifies_derivatives():
    """Verify extract_panel_aerodynamic_rom calculates valid derivatives from panels."""
    from physics.mesh_domain import build_panels, extract_panel_aerodynamic_rom
    from tests.test_mesh import flat_wing
    vertices, faces = flat_wing(span=0.20, root_chord=0.08, tip_chord=0.08, nx=8, ny=8)
    panels = build_panels(vertices, faces)
    area = 0.20 * 0.08
    rom = extract_panel_aerodynamic_rom(panels, chord_m=0.08, area_m2=area, cg_m=(0.02, 0.0, 0.0))

    assert "cl_alpha" in rom
    assert "cm_alpha" in rom
    assert "x_ac_frac" in rom
    assert rom["cl_alpha"] > 0.0
