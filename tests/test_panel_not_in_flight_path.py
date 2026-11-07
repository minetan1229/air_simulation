"""Issue #95: panel method is NOT wired into flight/GA paths.

This test is a static-usage guard: panel_aerodynamics must only be
called from tests and from its own module.  If a future change wires
it into flight_sim / GA, this test fails and forces the explicit
model-switch design + double-counting guard required by the issue.
"""

from __future__ import annotations

import pathlib

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
    """Every non-definition call site must live under tests/."""
    root = pathlib.Path(".")
    for path in list(root.rglob("*.py")):
        if ".venv" in str(path) or "site-packages" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "panel_aerodynamics(" not in text:
            continue
        if str(path).startswith("physics"):
            for line in text.splitlines():
                if "panel_aerodynamics(" in line and "def " not in line:
                    pytest.fail(f"runtime call site: {path}: {line.strip()}")
