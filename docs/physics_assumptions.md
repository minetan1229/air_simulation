# Physics Assumptions & Computational Models

This document records the **as-implemented** aerodynamic, inertial, and ground-contact assumptions of the paper-airplane 6-DoF simulator.  Every statement below is grounded in the current repository source (primarily `physics/aero_kernel.py`, `physics/aero_domain.py`, `physics/flight_sim.py`, `physics/gpu_dynamics.py`, `physics/ground_contact.py`, and `tools/calibrate_flight_data.py`).  No aspirational or speculative material is included.

---

## 1. Quasi-Steady Aerodynamic Assumptions

### 1.1 Thin-Plate & Delta-Wing Polars (`physics/aero_kernel.py`)

* **Lift-curve slope**: Helmbold (1942) triangular-wing theory
  ```
  C_Lα = 2π·AR / (2 + √(AR²(1+λ) + 4·AR/π))
  ```
  The GPU paths previously used a truncated form that dropped the taper term and replaced `4·AR/π` by `4` — an 11.3 % slope error.  The single implementation in `aero_kernel.helmbold_cl_alpha` removes that discrepancy.

* **Attached-flow branch**: `C_L = C_L0 + C_Lα·α` (radians), **capped at `cl_max`**.  The cap is enforced before the stall blend so the linear extrapolation never runs past the airframe's own stall value.

* **Post-stall blend**: C¹ smoothstep (Hermite) from the capped attached value at `α_stall` to the Hoerner fully-separated flat-plate limit at `POST_STALL_PLATE_BLEND_DEG = 45°`:
  ```
  C_L,plate = 2 sin(α) cos(α) |cos(α)|          (Hoerner 1965)
  ```
  The blend target is the plate value **at the blend angle**, not the moving plate curve.  Blending toward the moving curve produced a spurious dip near 30° followed by a secondary peak near 41°.  With a fixed target the composite is strictly decreasing from stall to the blend angle and joins the plate curve exactly, so the lift never collapses onto an exact `C_L = 0` band (the defect that produced a lift-free, drag-only deep stall).

* **Separated pitching moment**: `C_M = -0.5 sin(α)` (broadside-plate approximation).

* **Drag floor**: `MIN_DRAG_COEFFICIENT = 0.0`.  Total `C_D` is clamped to this floor to guarantee non-negative drag and prevent unphysical self-propulsion under any trim/fold configuration.

---

### 1.2 Polhamus Vortex Lift & Induced Drag (`physics/aero_kernel.py`)

* **Attached branch** (`polhamus_attached_branch`):
  ```
  C_L,attached = K_p sin(α) cos²(α) + K_v sin²(α) cos(α) sgn(sin(α))   (K_p > 0 replaces Helmbold)
  C_L,attached = C_L0 + C_Lα α + K_v sin²(α) cos(α) sgn(sin(α))         (K_p = 0, default)
  ```
  The `K_p` term is the 2-D leading-edge suction of a sharp-edge delta wing; when `K_p > 0` it **replaces** the 3-D Helmbold branch (a low-AR wing must not receive the 2-D slope on top of the finite-wing correction).  The `K_v` term is the antisymmetric vortex-lift increment and is always **added on top** of the chosen attached branch.

* **Vortex-induced drag** (`polhamus_vortex_drag`):
  ```
  ΔC_D,vortex = K_v sin²(α) |sin(α)|
  ```
  Derived from the suction analogy: the vortex lift acts purely normal to the chord, so its drag resolution is `|C_L,v tan(α)| = K_v sin²(α) |sin(α)|`.

* **Potential-flow induced drag** (scaled by ground effect):
  ```
  C_Di,pot = C_L,pot² / (π·e·AR)          where  C_L,pot = C_L - C_L,vortex
  ```
  Ground effect (`ground_effect_factor`) multiplies **only the potential-flow component**; the vortex drag increment is independent of ground proximity.

---

### 1.3 Low-Reynolds-Number Scaling (`physics/aero_kernel.py`, `physics/aero_domain.py`)

* **Validated envelope**: `1e3 ≤ Re ≤ 1e5` (`low_re_enabled` flag).

* **Bounded log-Re gains** (`_log_Re = clamp(log10(1e5/Re), 0, 2)`):
  | Parameter | Formula | Bounds |
  |-----------|---------|--------|
  | `cl_alpha` | `1 - 0.04·x` | [0.90, 1.05] |
  | `stall_alpha` | `1 - 0.10·x` | [0.75, 1.05] |
  | `cl_max` | `1 - 0.12·x` | [0.70, 1.05] |
  | `cd0` | `1 + 0.03·x` | [1.0, 1.12] |
  | `lsb_scale` | `1 + 0.15·x` | [0.9, 1.5] |

  Sources: Pelletier & Mueller 2000 (AIAA J. 38, 1836); Sunada et al. 2002 (J. Aircraft 39, 569); Westphal et al. 2003 (J. Aircraft 40, 546) — real corrections vanish rapidly above 1e5.

* **Laminar Separation Bubble (LSB) dynamic stall delay/hysteresis** (`lsb_dynamic_stall_shift`, `lsb_dynamic_stall_hysteresis`):
  * Pitch-up (`α̇ > 0`): convective lag delays separation, raising effective stall angle.
  * Pitch-down (`α̇ < 0`): turbulent bubble reform lag delays reattachment, lowering effective stall angle.
  * Produces a physical counter-clockwise hysteresis loop in `C_L(α)`.
  * Scaled by `low_re_lsb_gain` (default 0.15) and Reynolds factor `√(1e4/Re)`.
  * **Freestream turbulence suppression** (Hoffmann 1991): `exp(-Tu / 0.015)` — hysteresis collapses for `Tu > 1.5 %`.

---

### 1.4 Kirchhoff / Munk Added-Mass (`physics/aero_kernel.py`, `physics/flight_sim.py`, `physics/gpu_dynamics.py`)

* **Diagonal apparent masses/inertias** (thin planar wing, Blevins / Newman):
  ```
  m_add,z   = (π/4)  ρ S  mac
  I_add,yy  = (π/128) ρ S  mac³
  I_add,xx  = (π/48)  ρ b³  mac²
  I_add,zz  = (π/128) ρ h_keel  mac⁴   (vertical keel/fin plate about z-axis)
  ```

* **Passive Coriolis/Munk formulation** (Lamb §124, Milne-Thomson §17.40):
  ```
  F_app = -ω × P_A         where  P_A = [0, 0, m_add,z·w]
  M_app = -v_rel × P_A
  F_app·v + M_app·ω ≡ 0    (identically zero net mechanical work)
  ```

* **Opt-in via `munk_coupling_enabled`** (default `False`).  Enabling it alongside circulation polars causes exogenous pitch divergence; the flag prevents silent activation.  In heterogeneous GPU batches the terms are gated per-element by `params.apparent_active` and `params.munk_active`.

---

### 1.5 Ground Contact Passivity (`physics/flight_sim.py::_apply_ground_contact`, `physics/ground_contact.py`)

* **Continuous Collision Detection (CCD)**: When the airframe sweeps from `min_alt_prev > 0` to `min_alt_curr ≤ 0`, the contact instant `τ ∈ [0, 1]` is linearly interpolated:
  ```
  τ = min_alt_prev / (min_alt_prev - min_alt_curr)
  ```
  Position, velocity, quaternion, and body rates are interpolated to `τ`; the deepest station is clamped exactly to ground level.

* **Multi-station Delassus impulse solver**:
  * Contact stations: nose, left tip, right tip, keel (line contact under CG), tail.
  * World-frame Delassus tensor: `K = (1/m)I₃ - [r]× I⁻¹ [r]×`.
  * Normal impulse: `j_n = -(1+e)·m_eff,n·v_n`.
  * Coulomb friction cone: `|J_t| ≤ μ |J_n|` (tangential impulse limited by effective tangential mass and available friction).
  * Angular-rate safety cap: `0.9·HARD_ANGULAR_RATE_LIMIT_RPS` (90 rad/s) applied after each station resolution.

* **Strict post-impact energy non-increase**:
  ```
  E_post = ½ m v² + ½ ω·I·ω
  if E_post > E_pre·(1 + 1e-9):
      scale = √(E_pre / E_post)    (velocity & rates scaled down uniformly)
  ```
  The previous implementation authorised a fixed 25 % energy retention; the current bound is strict.

* **Deep-penetration guardrail**: if `altitude_cg < 0` or `min_alt < -0.05 m` (and not resolved by CCD), the airframe is immediately crashed with all velocities arrested — no teleporting upwards.

* **Impact structural failure criterion**: crash if `impact_speed > 60 m/s` OR impact stress index ≥ 1 (wing-root bending stress reaches allowable).  Impact stress uses `n = 1 + v²/(2 g d_stop)` with `d_stop = 31 mm` (configurable).

---

## 2. GPU Batching & CUDA-Graph Assumptions (`physics/gpu_dynamics.py`)

* **`sample_wind_stages=False` (default)**: Wind velocity is sampled **once per integration step** at the mean time of active cases, rather than at intermediate RK4 stages `k2, k3, k4`.  When enabled, each stage samples the wind field at its updated position and time, matching the reference 6-DoF integrator — but this prevents CUDA graph capture because the wind field varies per stage.

* **Scalar `mean_time` for CUDA graph compatibility**:
  ```
  mean_time = float(xp.mean(acc["time"][active]))
  wind_batch(xp, params, y[:, 0:3], mean_time, out=wind)
  ```
  Active cases with heterogeneous adaptive step sizes share a single scalar `mean_time` per step so CUDA Graphs maintain fixed kernel signatures and replay without stream-ordering hazards.  The wind field is written into a pre-allocated `(N, 3)` buffer (`out=wind`) that the captured graph reads from a fixed address.

* **Fused CUDA kernel parity** (`physics/aero_kernel.py`):
  * Compiled with `--fmad=false` (`_fma_contraction_disabled`) to suppress fused multiply-add contractions that would change the last bit.
  * Verified against eager evaluation within `rtol=1e-14, atol=1e-15` (1-ULP tolerance).
  * Boolean fields such as `low_re_enabled` are preserved through the fusion tracer (via `_flag_array`).

* **Elementwise 6×6 inertia inversion** (`_invert_3x3_batch`): Analytic vectorised inversion without cuBLAS / `numpy.linalg.inv` to permit CUDA graph capture without device synchronisation or host copies.  Scaled by the maximum element per matrix so that tiny determinants (paper-airplane inertia ~1e-16) do not underflow.

* **No `einsum` in captured path**: Matrix-vector contractions (`_contract3`) are written out element-by-element.  CuPy lowers `einsum` to cuBLAS, which cannot be captured into a CUDA graph.

* **Private memory pool for graph capture**: The capture warm-up runs in a private `cupy.cuda.MemoryPool` so that the pool already holds every block the graph needs; `cudaMalloc` is not a stream-ordered operation and must not occur during capture.

---

## 3. Proactive Improvement Proposals (per `AGENTS.md` §3.1) with Computational Cost Estimates

| Proposal | Description | Estimated Runtime Cost | Implementation Scope |
|----------|-------------|------------------------|----------------------|
| **LSB Dynamic Stall Hysteresis Coupling** | Propagate stateful `is_stalled_prev` across 6-DoF time steps / 5-station spanwise strips using `lsb_dynamic_stall_hysteresis`.  Currently the hysteresis is evaluated pointwise per aerodynamic call without memory of the prior stalled state. | ~1.05×–1.15× | `aero_kernel.lsb_dynamic_stall_hysteresis` signature + 6-DoF state carrier + GPU batch per-station propagation |
| **Full 6×6 Added-Mass Cross-Coupling** | Extend diagonal apparent mass/inertia to a full symmetric 6×6 added-mass matrix `M_A` (capturing heave-pitch cross-coupling for cambered/dihedral origami airframes).  Use elementwise 6×6 solves for CUDA-Graph compatibility. | ~1.1×–1.3× | `aero_kernel.compute_added_mass` → 6×6 `M_A` + `aero_kernel.compute_apparent_forces` + `gpu_dynamics._evaluate_state` + elementwise 6×6 LU in fused kernel |
| **Experimental Drop-Test & Wind-Tunnel Calibration** | Identify empirical parameters (`cd0`, `oswald_e`, `low_re_lsb_gain`, `low_re_cd0_gain`, trailing-edge fold increments `0.015 / -0.012 / 0.035`) from physical flight trajectories and low-Re wind-tunnel balance measurements via `tools/calibrate_flight_data.py`.  Currently the tool calibrates `cd0`, `oswald_e`, `elevon_deg`, `polhamus_kv`, `cL0`, `unsteady_k_circ`, `lsb_kappa`, `residual_cl`, `aeroelastic_tau_s`. | ~10–60 CPU-minutes offline per airframe family; 1.0× runtime cost | Extend `PARAM_SPECS` in `calibrate_flight_data.py` + add low-Re wind-tunnel data ingestion + joint trajectory/balance objective |
| **Optional Multi-Fidelity VLM / RANS-FSI Verification Stage for Top Elites** | Retain quasi-steady 6-DoF / GPU batch for MCTS, MAP-Elites, and GA exploration.  Add an **optional post-search verification stage** for top-K Pareto elites using:<br>• Unsteady VLM (~10×–50× CPU cost; seconds–minutes per elite)<br>• Steady/unsteady RANS-FSI (~10³×–10⁴× cost; minutes–hours per elite) | Offline only (post-search) | New `multifidelity` verification pipeline; `physics/multifidelity.py` already exists as a stub.  No change to core integrator or GPU batch. |

> **Note**: The first two proposals are **in-model** improvements (they modify the quasi-steady 6-DoF / GPU batch physics).  The third is an **offline calibration** workflow.  The fourth is a **multi-fidelity verification** layer that leaves the exploration model unchanged.  All are consistent with the coefficient-base準定常 model framework and do not require full CFD/FSI in the inner loop.

---

## 4. Reference Implementation Pointers

| Assumption Group | Primary Source Files |
|------------------|----------------------|
| Thin-plate / delta polars, stall blend, Hoerner post-stall | `physics/aero_kernel.py`: `helmbold_cl_alpha`, `lift_coefficient`, `post_stall_lift_coefficient`, `flat_plate_lift_coefficient`, `separated_pressure_drag_coefficient`, `drag_coefficient` |
| Polhamus vortex lift & induced drag separation | `physics/aero_kernel.py`: `polhamus_attached_branch`, `polhamus_vortex_drag`, `lift_coefficient`, `drag_coefficient` |
| Low-Re scaling & LSB hysteresis | `physics/aero_kernel.py`: `_log_Re`, `re_cl_alpha_factor`, `re_stall_factor`, `re_cl_max_factor`, `re_cd0_factor`, `re_lsb_scale`, `lsb_dynamic_stall_shift`, `lsb_dynamic_stall_hysteresis`; `physics/aero_domain.py`: `_low_re_factors` |
| Kirchhoff/Munk added-mass | `physics/aero_kernel.py`: `compute_added_mass`, `compute_apparent_forces`; `physics/flight_sim.py`: `_apply_ground_contact` (apparent forces integration); `physics/gpu_dynamics.py`: `_evaluate_state` (apparent mass branch) |
| Ground contact CCD, Delassus solver, energy non-increase | `physics/flight_sim.py`: `_apply_ground_contact`; `physics/ground_contact.py`: `compute_ground_stations`, `world_wall_contact`; `physics/gpu_dynamics.py`: `step()` (contact resolution mirrored) |
| GPU batching, CUDA graph, wind sampling | `physics/gpu_dynamics.py`: `wind_batch`, `simulate_batch`, `_capture_step`, `_rk4`, `_step_size`, `FlightParams.sample_wind_stages` |
| Calibration tool | `tools/calibrate_flight_data.py`: `PARAM_SPECS`, `calibrate_parameters`, `evaluate_trajectory_loss` |

---

## 5. Model Validity Envelope

* **Mach**: `≤ 0.3` (`MODEL_MACH_LIMIT`); Prandtl-Glauert factor held at `MACH_CRITICAL = 0.85` above.
* **Reynolds**: `1e3 ≤ Re ≤ 1e5` (`MODEL_REYNOLDS_MIN/MAX`); extrapolation outside is flat (clamped log-Re gains).
* **Angle of attack**: Post-stall branch valid to `POST_STALL_PLATE_BLEND_DEG = 45°`; beyond that the Hoerner plate curve is used directly.
* **Mass ratio**: `μ = m / (ρ S mac) ≥ 0.5` for quasi-steady added-mass assumption; warning at `μ < 5`.
* **Horizontal range**: `≤ 100 km` (`HARD_HORIZONTAL_LIMIT_M`); world wall contact clamps position.
* **Altitude**: `≤ 11 km` ISA ceiling (`HARD_ALTITUDE_LIMIT_M`).
* **Ground penetration**: `min_alt < -0.05 m` → immediate crash (no teleport).

---

## 6. Constants (Single Source of Truth)

| Constant | Value | Location |
|----------|-------|----------|
| `RE_REFERENCE` | `1.0e5` | `aero_kernel.py` |
| `STALL_BLEND_ORDER` | `4` | `aero_kernel.py` |
| `MODEL_MACH_LIMIT` | `0.3` | `aero_kernel.py`, `flight_sim.py` |
| `MODEL_REYNOLDS_MIN` | `1.0e3` | `aero_kernel.py`, `flight_sim.py` |
| `MODEL_REYNOLDS_MAX` | `1.0e5` | `aero_kernel.py`, `flight_sim.py` |
| `ELEVON_LIFT_PER_DEG` | `0.020` | `aero_kernel.py`, `aero_domain.py`, `flight_sim.py` |
| `ELEVON_MOMENT_PER_DEG` | `0.030` | `aero_kernel.py`, `aero_domain.py`, `flight_sim.py` |
| `MAX_ABS_LIFT_COEFFICIENT` | `4.0` | `aero_kernel.py`, `aero_domain.py` |
| `POST_STALL_PLATE_BLEND_DEG` | `45.0` | `aero_kernel.py`, `aero_domain.py` |
| `POST_STALL_DRAG_BLEND_DEG` | `15.0` | `aero_kernel.py`, `aero_domain.py` |
| `MIN_DRAG_COEFFICIENT` | `0.0` | `aero_kernel.py`, `aero_domain.py` |
| `MACH_TRANSONIC` | `0.75` | `aero_kernel.py`, `aero_domain.py` |
| `MACH_CRITICAL` | `0.85` | `aero_kernel.py`, `aero_domain.py` |
| `GROUND_KEEL_DEPTH_M` | `0.015` | `flight_sim.py`, `gpu_dynamics.py`, `ground_contact.py` |
| `CRASH_IMPACT_SPEED_MPS` | `60.0` | `flight_sim.py`, `gpu_dynamics.py`, `ground_contact.py` |
| `HARD_HORIZONTAL_LIMIT_M` | `1.0e5` | `flight_sim.py`, `ground_contact.py` |

---

*Generated from repository source.  Do not edit by hand — update the source files and regenerate.*