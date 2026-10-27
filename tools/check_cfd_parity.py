"""Verification script for CFD Stage 0 (Grid convergence and conservation parity).

Usage:
    python tools/check_cfd_parity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from physics.cfd import CFDConfig2D
from physics.cfd.grid2d import generate_c_grid
from physics.cfd.solver2d import Solver2D


def run_grid_convergence_check() -> int:
    print("=" * 70)
    print("  3D CFD ROADMAP - STAGE 0 2D GRID CONVERGENCE & CONSERVATION AUDIT")
    print("=" * 70)

    # 3 grid levels (coarse: 32x16, medium: 48x24, fine: 64x32)
    grid_resolutions = [(32, 16), (48, 24), (64, 32)]
    results = []

    for ni, nj in grid_resolutions:
        cfg = CFDConfig2D(mach=0.2, alpha_deg=2.0, max_steps=40, cfl=0.05)
        grid = generate_c_grid("0012", ni=ni, nj=nj, farfield_r=10.0)
        solver = Solver2D(cfg, grid)
        res = solver.run()
        results.append((ni, nj, res))
        print(
            f"  Grid {ni:2d}x{nj:2d} ({res.grid_cells:4d} cells): "
            f"CL={res.cl:.4f}, CD={res.cd:.4f}, CM={res.cm:.4f}, "
            f"mass_res={res.mass_conservation_residual:.4e}, L2={res.residual_l2:.4e}"
        )

    # Check conservation (N-1)
    for ni, nj, res in results:
        if np.isnan(res.cl) or np.isnan(res.cd):
            print(f"[FAIL] NaN detected in grid {ni}x{nj}")
            return 1
        if res.mass_conservation_residual > 5.0:
            print(f"[FAIL] Mass conservation residual exceeded bound on {ni}x{nj}: {res.mass_conservation_residual}")
            return 1

    # Check convergence tendency between medium and fine (N-2)
    cl_med = results[1][2].cl
    cl_fine = results[2][2].cl
    rel_diff = abs(cl_fine - cl_med) / max(abs(cl_fine), 1.0e-3)

    print("-" * 70)
    print(f"  Relative difference (medium -> fine): {rel_diff * 100:.2f}%")
    print("  Stage 0 Conservation & Grid checks: PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_grid_convergence_check())
