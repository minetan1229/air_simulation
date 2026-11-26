# WONTFIX — Intentional Architectural & Numerical Trade-offs

This document records deliberate design decisions confirmed during the MILCY convergence audit. These are not bugs; they are accepted trade-offs with rationale.

---

## 1. CUDA-Graph Scalar `mean_time` Wind Evaluation & `sample_wind_stages=False` Default
**Location:** `physics/gpu_dynamics.py` (`simulate_batch`)

- Heterogeneous adaptive `dt` causes active cases to reach slightly different `acc["time"]` values within the same outer loop iteration.
- Wind is evaluated once per step using scalar `mean_time = float(xp.mean(acc["time"][active]))` with `sample_wind_stages=False` by default.
- **Why intentional:** Preserves fixed-signature CUDA Graph capture/replay on the current stream and avoids 4× per-step wind kernel launches. Callers needing stage-exact unsteady wind can opt into `sample_wind_stages=True` with `use_graph=False`.

---

## 2. `MAPElitesGridArchive` Standalone Defaults vs `config/milcy_map_elites.yaml`
**Location:** `optimization/map_elites/grid.py` vs `config/milcy_map_elites.yaml`

- `MAPElitesGridArchive.__init__` defines broad library/unit-test defaults:
  - `bins=(6, 6, 4)`
  - `ranges=((0.10, 0.45), (0.10, 0.90), (0.30, 2.20))`
- `config/milcy_map_elites.yaml` defines narrower production ranges calibrated to folded A4 paper airplanes:
  - `bins: [10, 10, 10]`
  - `wing_loading_range: [0.12, 0.28]`
  - `stability_range: [0.25, 0.95]`
  - `aspect_ratio_range: [0.45, 1.10]`
- **Why intentional:** Standalone callers and unit tests construct `MAPElitesGridArchive()` directly across wider synthetic descriptors without loading YAML, while production MILCY runs override these defaults from `config/milcy_map_elites.yaml`.

---

## 3. Derived `aspect_ratio` in `geometry/v3_origami/adapter.py`
**Location:** `geometry/v3_origami/adapter.py` (`extract_airframe_geometry`)

- `extract_airframe_geometry` always computes:
  - `aspect_ratio = (span * span) / safe_area`
  - `planform_aspect_ratio = (span * span) / planform_area`
  from the folded 3D geometry rather than passing through an unconstrained input aspect ratio.
- **Why intentional:** Prevents geometry-physics mismatch and aspect-ratio spoofing during origami optimization.

---

## 4. Optional `flask` Dependency for `tests/test_origami_genome.py` API Tests
**Location:** `tests/test_origami_genome.py`

- The `client()` fixture and API endpoint tests (`from app import app`) require `flask`, which is an optional web-layer dependency not required for headless physics/geometry execution.
- **Why intentional:** Keeps core simulation and optimization packages free of mandatory web-server dependencies; API tests execute in environments where `flask` is installed.