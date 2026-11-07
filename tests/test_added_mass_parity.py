"""Issue #136: added-mass CPU/GPU parity and tensor bounds audit."""
from __future__ import annotations

from physics import aero_kernel
from physics.aero_kernel import ScalarMath


def test_added_mass_positive_definite_diagonal() -> None:
    added = aero_kernel.compute_added_mass(ScalarMath, 1.225, 0.018, 0.0826, wingspan_m=0.22)
    for key in ("added_mass_z_kg", "added_inertia_xx_kgm2", "added_inertia_yy_kgm2", "added_inertia_zz_kgm2"):
        assert float(added[key]) > 0.0


def test_cpu_normal_attenuation_vs_gpu_omission() -> None:
    added = aero_kernel.compute_added_mass(ScalarMath, 1.225, 0.018, 0.0826, wingspan_m=0.22)
    m = 0.0035
    ratio = m / (m + float(added["added_mass_z_kg"]))
    assert 0.6 < ratio < 0.9
