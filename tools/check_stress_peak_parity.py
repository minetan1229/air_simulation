"""Check that the vectorised structural model reproduces the stress field.

``stress_field`` computes the full span/chord stress grid; the GPU batch
path needs only the peak.  This tool proves that the scalar reduction
``peak_root_stress_pa`` returns exactly the maximum of that grid, so the two
paths report the same number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from physics.config_schema import AircraftConfig, EnvironmentConfig, derived_properties
from physics import stress_domain


def main() -> int:
    env = EnvironmentConfig()
    cases = [
        (AircraftConfig(), 1.0),
        (AircraftConfig(), 3.0),
        (AircraftConfig(), 12.0),
        (AircraftConfig(fold_tip_deg=25.0), 4.0),
        (AircraftConfig(fold_trailing_deg=30.0), 4.0),
        (AircraftConfig(paper_gsm=160.0, paper_stiffness=0.7, taper_ratio=0.3), 2.0),
    ]
    worst = 0.0
    for cfg, nz in cases:
        derived = derived_properties(cfg)
        # ``spanwise=3`` is the grid the 6-DoF path asks for: with an odd
        # number of span stations the centre row sits exactly on the wing
        # root, so the grid peak *is* the root stress.
        field = stress_domain.stress_field(
            cfg=cfg,
            dynamic_pressure_pa=30.0,
            load_factor=nz,
            spanwise=3,
            chordwise=2,
            backend="numpy",
            gravity=env.gravity,
        )
        thickness = stress_domain._sheet_thickness(cfg.paper_gsm)
        z_gain = stress_domain.Z_SCALE_OFFSET + stress_domain.Z_SCALE_GAIN * stress_domain.effective_paper_stiffness(cfg)
        tip_factor = abs(np.sin(np.radians(cfg.fold_tip_deg)))
        peak = float(
            stress_domain.peak_root_stress_pa(
                np,
                load_factor=nz,
                mass_kg=derived["mass_kg"],
                gravity=env.gravity,
                span_m=derived["ref_span_m"],
                taper_ratio=derived["taper_ratio"],
                root_chord_m=stress_domain.root_chord_batch(
                    np, derived["ref_area_m2"], derived["ref_span_m"], derived["taper_ratio"]
                ),
                thickness_m=thickness,
                z_gain=z_gain,
                chord_scale=stress_domain.te_fold_chord_scale(np, cfg.fold_trailing_deg),
                tip_factor=tip_factor,
            )
        )
        grid_peak = float(field["max_stress_pa"])
        error = abs(peak - grid_peak) / max(abs(grid_peak), 1.0e-12)
        worst = max(worst, error)
        print(
            f"grid={grid_peak:.10g}  reduction={peak:.10g}  rel={error:.3e}  "
            f"allowable={field['allowable_stress_pa']:.6g}"
        )
    print(f"worst relative difference = {worst:.3e}")
    return 0 if worst < 1.0e-9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
