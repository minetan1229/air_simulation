# 3D CFD Roadmap (Issue #47)

This document answers **Issue #47 "[CFD Roadmap] 3D Navier–Stokes Solver Requirements & Phased Introduction Plan"** and is a **design document**. No code changes accompany it. It is a **separate layer** from the existing 6DoF panel method model, and defines how to add 3D CFD as a future option.

---

## 0. Current Positioning

Normal flight and optimization paths use the coefficient-based 6DoF aero model (`physics/aero_domain.py` + `physics/aero_kernel.py`). Separately, `physics/cfd/solver3d.py` and `grid3d.py` contain a prototype compressible 3D Euler finite-volume solver and wing grid generator on structured grids. This is prototype/verification code, not a Navier–Stokes CFD connected to the normal flight path.

- `physics/mesh_domain.py` panel method uses **wing panel *vortex / doublet* representations** and does not solve Navier–Stokes. Geometry is built from folded panel polygons, but the flow is not solved.
- As stated in `docs/SPECIFICATION.md` §6 Issues #29 / #35, this distinction is also explicit in the UI.
- Therefore this roadmap is **not** a plan to "replace the current model with CFD," but rather requirements to **enable a separate solution method (viscous 3D Navier–Stokes CFD) as an additional mode**. The existing Euler prototype is distinct from this goal.

**Principle**: Do **not break** the existing 6DoF path. 3D CFD will have a **separate `model` tag**, machine-readably distinguished like `reduced_surrogate` / `rigid_body_6dof`.

---

## 1. Requirements

### 1.1 Physics

| # | Requirement | Basis |
|---|---|---|
| R-1 | Solve **compressible Navier–Stokes** (incompressible OK for paper airplane flight Mach 0.1–0.3, but compressibility needed for transonic transition) | Issue #47 |
| R-2 | Turbulence: **RANS** (k-ε or k-ω SST) first; LES later | Compute budget (§4) |
| R-3 | Boundary conditions: Aircraft as **moving wall** (no-slip + aircraft velocity), far-field as **far-field** (wind speed, turbulence boundary layer) | Consistent with `physics/wind_domain.py` |
| R-4 | **Couple** heat and compressibility (paper is lightweight/low-stiffness; heating changes aircraft attitude) | Consistent with aeroelastic (`aeroelastic_flex` / `aeroelastic_twist`) |
| R-5 | Separation, trailing-edge vortices, wingtip vortices **emerge from solution** (not prescribed by model) | Terms Helmbold/Hoerner cannot capture (`docs/PHYSICS_MODELS.md` §3) |

### 1.2 Numerics

| # | Requirement |
|---|---|
| N-1 | **Conservation**: Mass, momentum, energy monitored as **integrated residual < 1e-6** (global balance, not local cell error) |
| N-2 | **Grid Convergence**: 3 grid levels (coarse/medium/fine); $C_L$/$C_D$ change **< 2%**. Report Richardson extrapolated value. |
| N-3 | **Time Convergence**: No forward Euler. **2nd-order implicit** (BDF2 or Crank–Nicolson) or explicit (RK2/3). Physical timestep with **CFL ≤ 1**. |
| N-4 | **Implicit Linearization**: Newton + LU decomposition (parallel: block-CG / ILU preconditioner). Nonlinear residual down to 1e-8. |
| N-5 | **Boundary Layer**: Specify y+ (target y+ ≤ 1 for wall-resolved turbulence, y+ ≈ 30 for wall functions). Coarse grids use **wall functions**, fine grids use **wall-resolved**. |
| N-6 | **Reproducibility**: Same input and turbulence seed yields **bit-identical** results. |

### 1.3 Performance

| # | Requirement |
|---|---|
| P-1 | 1 aircraft × 1 flight (10 s physical time) completes in **minutes to tens of minutes, for verification only** |
| P-2 | **Do NOT use for optimization or GA** (1 min/eval = 16 hrs for 1000 individuals). CFD is a **verification model**; search fitness remains 6DoF path. |
| P-3 | Single flight analysis outputs **lift, drag, moment, pressure distribution** comparable to 6DoF model under **same definitions** ($C_L$, $C_D$, $C_m$, wing root moment) |

### 1.4 Integration

| # | Requirement |
|---|---|
| I-1 | Do **not break** existing `physics/aero_domain.py` / `aero_kernel.py` calling convention (`evaluate_aero` returns `AeroResult` fields) |
| I-2 | 3D CFD path has **separate `model` tag** (e.g., `model = "cfd_v1"`, `model_detail = "rans_ns_finite_volume"`), UI distinguishes |
| I-3 | Verification results recorded in **ledger (`no-finish.md`)**; **do not hide discrepancies** |

---

## 2. Phased Introduction Plan

Each stage must satisfy **exit criteria** before proceeding. No skipping stages.

### Stage 0: Static 2D Verification (Grid & Conservation Foundation)

- **Content**: Implement compressible RANS on single 2D airfoil grid (NACA 0012 class). Achieve grid convergence for lift/drag.
- **Exit Criteria**:
  - N-1 (conservation residual < 1e-6), N-2 (3-grid < 2%)
  - Difference from experimental data (e.g., NACA 0012 $C_l$–$C_d$ curves) **< 5%**
  - **External Code Comparison**: $C_L$/$C_D$ difference **< 3%** vs published Navier–Stokes solutions (airfoil cases with separation profiles). This is the minimum unit of "independent verification."
- **Deliverables**: Initial `physics/cfd/` package, verification script `tools/check_cfd_parity.py`

### Stage 1: 2D Steady Airfoil → 3D Steady Wing

- **Content**: Add 3D grid scaling to airfoil code. Wingtip boundary conditions: CH / simple.
- **Exit Criteria**:
  - $C_L$ in linear AoA sweep within **20% of Helmbold slope** (3D must **not exceed** Helmbold)
  - Wingtip vortices emerge spontaneously (observable horseshoe vortex pattern)
- **Risk**: Wingtip BC choice affects results. Must run **all 3 conditions** (CH / simple / none) and report variance.

### Stage 2: Dynamic (Moving Wall) + 6DoF Coupling

- **Content**: Treat aircraft as moving wall; solve 6DoF equations every step. Inter-layer (6DoF ⇔ CFD) data exchange **bidirectional** (forces/moments to 6DoF, attitude/velocity to CFD).
- **Exit Criteria**:
  - Coupled evaluation converges under same initial conditions (Savitzky–Golay extrapolated value difference < 1%)
  - Stage 1 steady solution reproduced as **quasi-steady solution** (time-averaged excluding initial transient within 2%)
  - **R-5**: Drag increase at stall appears with **same sign and order** as 6DoF model (Hoerner flat plate)

### Stage 3: Turbulence Model Impact Assessment

- **Content**: Run 2 RANS turbulence models (k-ε, k-ω SST); evaluate airframe shape dependence.
- **Exit Criteria**:
  - $C_L$ difference between 2 models **< 5%**; if exceeded, record as "turbulence model uncertainty" in ledger and reflect in 6DoF `model_valid`-equivalent flag

### Stage 4: Aeroelastic Coupling

- **Content**: Express paper aircraft quasi-static deflection (`aeroelastic_flex` / `aeroelastic_twist`) via CFD deforming grid.
- **Exit Criteria**: $C_L$ difference between no-deformation / with-deformation **< 1%** (confirm deformation effect is small in low-speed regime)

### Stage 5: Uncertainty Quantification & Publication

- **Content**: Compile turbulence seed, grid, boundary condition variance into **uncertainty budget** for publication.
- **Exit Criteria**: Comparison table of 6DoF model uncertainty vs CFD uncertainty in ledger.

---

## 3. Validation Cases

| Case | Purpose | Judgment |
|---|---|---|
| V-1 NACA 0012 (2D, Re 3e6) | Basic code validity | $C_L$ diff < 5% vs experimental |
| V-2 Flat Plate (2D, AoA 0–30°) | Separation & reattachment | Qualitative match to experimental $C_L$/$C_D$ curves |
| V-3 Subsonic Wing (3D, AR 4) | Wingtip vortex, induced drag | $C_{Di}$ within 20% of $C_L^2/(\pi e AR)$ |
| V-4 Paper Aircraft Shape (3D, Re 1e4) | This simulator's shape | $C_L$ diff < 20% vs 6DoF panel method (**record any discrepancy**) |
| V-5 Stall (AoA 12–25°) | Separation reproduction | Drag increase same sign & order as 6DoF |

---

## 4. Compute Budget

| Stage | Grid Size (est.) | Wall Time / Case | Notes |
|---|---|---|---|
| 0 | 2D 1e5 cells | Minutes–tens of mins | ~1 core equiv, hundreds of steps to converge |
| 1 | 3D 1e6 cells | Hours | Cluster 32 cores, 1 node |
| 2 | 3D 1e6 cells × thousands of steps | Days | Coupling dominated by timestep |
| 3 | Same × 2 turbulence models | Weeks | Parallel execution assumed |

**Budget Summary**: Stage 2+ **cannot run on personal workstations**. Cluster required. Only Stages 0–1 run **reproducibly**.

---

## 5. Relationship with Existing 6DoF Model

| Aspect | 6DoF Panel Method (Current) | 3D CFD (This Doc) |
|---|---|---|
| Role | **Search** (GA/optimization fitness, 100-plane batch) | **Verification** (model validity, separation/vortex understanding) |
| Compute | ~0.1s/individual | Hours/individual |
| Turbulence | Isotropic spectrum (intentional retention A-7) | RANS |
| Separation | Hoerner flat plate (`docs/PHYSICS_MODELS.md` §3) | Emerges from solution |
| Tag | `rigid_body_6dof` | `cfd_v1` (proposed) |

**Usage Principle**: **GA fitness stays 6DoF**. CFD introduction is to **"inspect 6DoF model validity via another solution method,"** not to replace search with CFD (compute budget precludes this).

---

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Large 6DoF discrepancy | Cannot verify "which is correct" | **Do not hide** discrepancies; record in ledger. Do not declare either "true." |
| Compute budget | Stage 2 won't run | Complete Stages 0–1 first. Stage 2 requires cluster. |
| Code distribution constraints | Cannot depend on external solvers | **Self-implemented** (finite volume) assumed. No dependency on external CFD software. |
| Implementation stall | Development halts | Define exit criteria **numerically** for each stage; if unmet, return to literature review. |

---

## 7. Current Status Summary (2026-11-04)

- `physics/cfd/grid3d.py` generates structured grids and geometric metrics for finite wings; `physics/cfd/solver3d.py` implements a prototype solver for compressible Euler equations on structured hexahedral grids using Roe numerical flux and explicit RK2 with CFL-based timestep control.
- This solver lacks viscous stress and heat conduction; no wall boundary layer, viscous friction drag, viscous separation, turbulence/transition. Therefore it is **not** a Navier–Stokes / RANS solver. Aircraft wall conditions and aero coefficient physical accuracy do not meet target spec verification standards.
- Existing tests check grid positive cell volumes, metric closure, finite values, mass residual, local CFL, etc. They are **not** evidence of meeting exit criteria for coefficient comparison with experimental/independent CFD solutions, grid convergence, or physical accuracy. Stages 0–1 exit criteria are unmet.
- Current status: "3D Euler prototype code exists, limited validity verification, viscous CFD not implemented." No GA/normal flight path integration.
- Existing `model_valid` flag (`physics/flight_sim.py`) represents **6DoF model validity range**, not Euler prototype solver validity flag.

---

*Related: `docs/PHYSICS_MODELS.md` (6DoF model canonical), `docs/SPECIFICATION.md` §1·§5 (specs & literature registry), `no-finish.md` (implementation ledger).*