# Verification & Validation

**Verification Definition**: Whether mathematically/numerically implemented correctly (is the equation solution correct?).
**Validation Definition**: Whether physically valid against the real world (wind tunnel test values, actual flight data).

This project uses academic literature, closed-form solutions, and independent numerical solutions as **canonical reference solutions**, and conducts V&V in the following 3 layers.

---

## 1. Verification — Numerical Correctness

### 1.1 Closed-Form Matching (Domains with Analytical Solutions)

| Physical Quantity | Reference Solution | Tolerance | Test |
|---|---|---|---|
| ISA Atmosphere $T(h), p(h), \rho$ | US Standard Atmosphere 1976 | Relative error $10^{-6}$ | `tests/test_atmosphere_wind_conformance.py` / `test_atmo_wind.py` |
| Helmbold Lift Slope $C_{L\alpha}$ | Closed-form | Relative error $10^{-12}$ | `tests/test_aero.py::test_helmbold_matches_closed_form` |
| Induced Drag $C_{Di} = C_L^2/(\pi e AR)$ | Closed-form | Relative error $10^{-3}$ | `tests/test_aero.py` |
| Sutherland Dynamic Viscosity $\mu(T)$ | Sutherland (1893) Formula | Relative error $10^{-3}$ | `tests/test_atmosphere_wind_conformance.py` |
| Mesh Geometric Properties (Area, CG, Inertia) | Exact Integration | Relative error $10^{-6}$ | `tests/test_mesh.py` |
| RK4 Convergence (Timestep Dependence) | Systematic Convergence on Halving | 1st order convergence (Issue #31 verified) | `tests/test_issue12_timestep_convergence.py` |

### 1.2 Cross-Path Parity (GPU / CPU / Scalar / Batch)

- Scalar path (`aero_domain.py`) and batch path (`gpu_dynamics.py`) call the **same canonical function** (`aero_kernel.py`), so formula-level mismatches cannot occur (Single Implementation Principle).
- Parity Check Scripts:
  - `tools/check_aero_kernel_parity.py` — `low_re_enabled=False` legacy behavior bit-identical (difference 0.0)
  - `tools/check_stress_peak_parity.py` — Stress peak reduction exactness
  - `tests/test_gpu_kernel_parity.py` — GPU / NumPy kernel equivalence

### 1.3 Conservation & Invariants

- Quaternion norm preservation: Normalize RK4 per-step drift within $5 \times 10^{-3}$.
- Energy conservation (gravity-only condition): `tests/test_energy_conservation_ground.py`
- Boundary grounding / CCD: `tests/test_tunneling_ccd.py` / `test_ground_penetration.py`

---

## 2. Validation — Physical Validity

### 2.1 Low Reynolds Number (LSB) Model Validity

- **Applicable Range**: $Re \in [10^3, 10^5]$, $Ma \le 0.3$, thin plate (thick airfoil aerodynamics out of scope)
- **Correction Formulas**: Pelletier & Mueller 2000, Sunada et al. 2002, Westphal et al. 2003, O'Meara & Mueller 1987 reproduced in single implementation
  - $f_{C_{L\alpha}} = 1 - 0.04x,\quad f_{\alpha_{stall}} = 1 - 0.10x,\quad f_{C_{Lmax}} = 1 - 0.12x,\quad f_{CD0} = 1 + 0.03x$
  - Coordinate $x = \log_{10}(10^5 / Re)$, $x=0$ at $Re \ge 10^5$, $x=2$ at $Re \le 10^3$
- **Validation**: `tests/test_low_re_aerodynamics.py` / `test_issue68_reynolds_audit.py` (26 passed), `test_lowre_envelope.py`, `test_low_re_correction.py`

### 2.2 Remaining Physics Deviations (Known Limitations, Phase 4)

`PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §0.0 and `no-finish.md` are canonical:

- **P0–P3 Complete** (paper-conformance audit ledger)
- **F-8 / A-5**: Panel method (`mesh_domain.py`) and Helmbold formula show 2.2x deviation — **Intentional Retention** (two aerodynamic models not merged into one formula. Panel method not connected to actual flight / GA evaluation paths)
- **KAPPA_SIGMA / fold coefficients**: Values retained (sources not obtained). Re-opening conditions in `docs/PHYSICS_MODELS.md` §5.2

### 2.3 GA Optimization Validation Conditions

- Objective functions & search logic defaults consolidated in `physics/ga_config.py` (hardcoding prohibited)
- **Failed evaluation contamination of Pareto frontier** (relabeling to `timeout` / `crashed`) prohibited as reward hacking path
- Validation Tests: `tests/test_ga_physics_readiness.py` / `test_multifidelity_pipeline.py` / `test_model_fidelity_metadata_p1.py`

---

## 3. Benchmarks (How to Create Comparison Graphs)

### 3.1 Drag Polar

- Endpoint: `POST /api/v1/aerodynamics/polar`
- Example: `curl -X POST http://127.0.0.1:5000/api/v1/aerodynamics/polar -H 'Content-Type: application/json' -d '{"aircraft":{"wing_area_m2":0.015,"wingspan_m":0.2},"alpha_min_deg":-8,"alpha_max_deg":20,"points":57,"reynolds":10000}'`
- Comparison graphs with theoretical solutions (flat plate theory, Helmbold closed-form) to be added to `docs/images/` and referenced in this file.

### 3.2 Wind Field Isotropy & Statistics

- Tests: `tests/test_turbulence_stats.py` / `test_wind_turbulence_statistics.py` / `test_wind_turbulence_p1.py`
- Comparison with measured wind fields (Dryden / MIL-F-8785C spectra) quantified in Issue #81 (`test_issue81_wind_quantify.py`).

### 3.3 Benchmark Execution Reports

```powershell
python -m pytest tests/test_paper_conformance_p0_p1.py -q
python -m pytest tests/test_issue32_aero_reference_data.py -q
python tools/check_aero_kernel_parity.py
```

---

## 4. V&V Completion Criteria (Issue Close Conditions)

1. All "completion conditions" and "investigation/actions" items in the Issue body are physically/engineeringly satisfied.
2. Comparison with **independent reference solutions** (closed-form, wind tunnel test values) or above tests is established.
3. Completion by test addition only or documentation only is not accepted (added tests must not merely assert current behavior).

---

## 5. Related Documents

- [docs/SPECIFICATION.md](./SPECIFICATION.md) Technical Specifications Canonical
- [docs/PHYSICS_MODELS.md](./PHYSICS_MODELS.md) Coefficients & Formulas Canonical
- [PHYSICS_PAPER_CONFORMANCE_AUDIT.md](../PHYSICS_PAPER_CONFORMANCE_AUDIT.md) Paper Conformance Audit Records
- [docs/development.md](./development.md) Test Execution Procedures