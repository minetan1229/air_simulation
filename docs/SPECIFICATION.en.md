# 3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine Specification
> [← Back to README](https://github.com/minetan1229/air_simulation/blob/master/README.md) / [docs Index](index.md)

> **Role of This Document**: Canonical source for this project's technical specifications, physics laws, constants, and API. Individual formula provenance and applicability limits are in [PHYSICS_MODELS.md](./PHYSICS_MODELS.md); future 3D CFD plans in [CFD_ROADMAP.md](./CFD_ROADMAP.md).
> **Read First**: [architecture.md](./architecture.md) (overview) → [PHYSICS_MODELS.md](./PHYSICS_MODELS.md) (physics canonical per quantity)
> **Structure**: 1. Physics & Aero Theory / 2. GPU & Parallel / 3. REST API (§3.1–3.7) / 4. Security / 5. Academic Citation Registry / 6. Audit Resolution History / 7. Module Structure / 8. Agent Roles / 9. Verification & CI Guide

This document is a comprehensive technical specification covering the theoretical physics models, GPU acceleration & parallel architecture, REST API specifications, security standards, and academic references for the "3D Paper Airplane Simulator" based on aerodynamics and structural mechanics.

---

## 1. Physics & Aero-Theoretical Models

This project is **not** a "volumetric mesh Navier-Stokes CFD solver" that discretizes and solves the continuity and Navier-Stokes equations on a volumetric mesh. Instead, it is a **high-precision, high-speed simulator combining AeroSandbox-compliant 6-DoF flight dynamics equations, Helmbold/Polhamus delta-wing/low-aspect-ratio empirical aerodynamic models, Viterna stall transition, and static aerodynamic panel method** (Issue #29 explicitly stated).

### 1.1 Aerodynamic Models
- **Basic Aerodynamic Forces**:
  $$L = \frac{1}{2} \rho v^2 S C_L(\alpha), \quad D = \frac{1}{2} \rho v^2 S C_D(\alpha)$$
- **Lift Slope & Delta Wing Vortex Lift (Helmbold & Polhamus)**:
  For low-aspect-ratio paper airplanes ($AR \le 4$), nonlinear leading-edge vortex suction lift is superimposed on linear attached-flow lift:
  $$C_{L\alpha} = \frac{2\pi AR}{2 + \sqrt{AR^2 (1 + \lambda) + 4AR/\pi}}$$
  (Helmbold 1942 delta wing formula extended to tapered wings. **This formula is the single implementation** (`physics.aero_domain.helmbold_cl_alpha`); 6DoF path, GPU ensemble path, GPU optimization batch path, and structural module lift slope all reference this value. Legacy GPU formula with $4AR/\pi$ replaced by constant $4$ (+11.3%) is deprecated.)
  $$C_L(\alpha) = K_p \sin\alpha \cos^2\alpha + K_v \sin^2\alpha \cos\alpha$$
  (Polhamus 1966/1971 public formula. When $K_p$ explicitly specified via `polhamus_kp`, used as-is; when unspecified (default 0), the aircraft's own 3D Helmbold slope serves as $K_p$, and only the $K_v$ vortex suction increment is superimposed.)
- **High AoA & Post-Stall Transition (Viterna-Corrigan Method)**:
  Beyond stall AoA $\alpha_{stall}$, deep stall and reverse-flow regimes smoothly connect to flat-plate separated-flow limit ($C_{D,\max} \approx 1.8 \sim 2.0$).
- **Induced Drag & Ground Effect (Wieselsberger)**:
  Induced drag reduction based on height $h$, wingspan $b$, Oswald efficiency factor $e$:
  $$C_{Di}(h) = \frac{C_L^2}{\pi e AR} \cdot \left[ \frac{16(h/b)^2}{1 + 16(h/b)^2} \right]$$
- **Paper Properties & Quasi-Steady Aeroelastic Morphing**:
  Dynamic-pressure $q = \frac{1}{2}\rho v^2$ dependent trailing-edge trim deformation model. Suppresses abrupt nose-up looping in high dynamic pressure launch regime ($q \gg q_{ref}$), auto-restores to original trim angle in apex/glide regime ($q \to 0$):
  $$\delta_{eff}(q) = \delta_{trim} \cdot \left[ \frac{1}{1 + (q / q_{aeroelastic})^{n_{morph}}} \right]$$
  This model is an algebraic quasi-steady approximation without elastic state; passivity not proven (`aeroelastic_compliance>0` opt-in, default OFF).
- **Apex Low-Speed Pushover Dynamics**:
  At sharp climb apex ($V < 2.0\,\text{m/s}$), prevents moment singularity from dynamic pressure drop, generates nose-down moment from center-of-pressure $x_{cp}$ aft shift and CG eccentricity. Achieves natural pushover (transition to glide attitude) via gravity and minute relative wind.
- **Low Reynolds Number (Re $10^3$–$10^5$) Thin Plate Steady LSB Effects** (`physics/aero_kernel.py`): Laminar Separation Bubble (LSB) is a thin-plate low-Re phenomenon; primary effects are lift slope reduction, stall advancement, $C_{D0}$ increase, pressure center shift.
  Coordinate $x = \log_{10}(10^5 / Re)$ (`_log_Re`, $0$ at $Re\ge10^5$, $2$ at $Re\le10^3$, saturation prevents divergence):
  $$f_{C_{L\alpha}} = 1 - 0.04x,\quad f_{\alpha_{stall}} = 1 - 0.10x,\quad f_{C_{Lmax}} = 1 - 0.12x,\quad f_{LSB} = 1 + 0.15x,\quad f_{CD0} = 1 + 0.03x$$
  Implementation: `re_cl_alpha_factor`, `re_stall_factor`, `re_cl_max_factor`, `re_lsb_scale`, `re_cd0_factor` (all clamped). At $Re=10^4$, $x=1$: $f_{C_{L\alpha}}=0.96$, $f_{\alpha_{stall}}=0.90$, $f_{C_{Lmax}}=0.88$, $f_{LSB}=1.15$, $f_{CD0}=1.03$.
  Sources: Pelletier & Mueller 2000 (AIAA J. 38, 1836); Sunada et al. 2002 (J. Aircraft 39, 569); Westphal et al. 2003 (J. Aircraft 40, 546); O'Meara & Mueller 1987 (LSB hysteresis amplitude).
  **Applicability Limits**: $Re<10^3$, turbulent $Re>10^5$, thick airfoil, high-speed (Mach $>0.3$), unsteady are out of model scope. `low_re_enabled=False` reverts to legacy behavior bit-for-bit (verified: `tools/check_aero_kernel_parity.py` diff 0.0, `tests/test_low_re_aerodynamics.py`·`test_issue68_reynolds_audit.py` 26 passed). Pressure center shift appears via $C_L$-weighted decambering.
- **Full-Span Trailing-Edge Fold Aerodynamic Increments**:
  For full-span trailing-edge fold angle $\phi$ = `fold_trailing_deg` (positive = fold trailing edge down = camber increase), add following increments to aerodynamic coefficients:
  $$\Delta C_L = 0.015\,\phi, \quad \Delta C_m = -0.012\,\phi, \quad \Delta C_D = 0.035\,\sin^2\phi$$
  ($\phi$ in degrees. Positive fold increases lift, generates nose-down moment. Negative fold (reflex) decreases lift, generates nose-up trim. $\Delta C_L$ and $\Delta C_m$ are **simultaneously** scaled to satisfy $|\Delta C_L| \le C_{L,\max}$ (their ratio $\Delta C_m/\Delta C_L$ = incremental action point at trailing edge); $\Delta C_D$ increases independently via $\sin^2\phi$. Implemented in `physics/aero_kernel.py::trailing_edge_fold_effects` (vectorized kernel); 6DoF regular path (`physics/aero_domain.py`), GPU optimization batch path (`physics/gpu_dynamics.py`), Drag Polar / Trim paths all use **same shared kernel `evaluate_coefficients`** for identical coefficients. **Coefficient values per §5.2 are retained** (sources not obtained).

### 1.2 Atmosphere & Environment Models
- **Atmosphere Model**:
  - ISA (International Standard Atmosphere): $T = T_0 - L h$, $p = p_0 (T/T_0)^{g/(L R)}$, $\rho = p/(R T)$.
  - Local Atmosphere: Compute vapor pressure from temperature/pressure/relative humidity; derive effective gas constant and density from dry air/vapor mixing ratio.
  - Dynamic viscosity $\mu(T)$ via Sutherland's formula; Reynolds number $Re = \rho V c / \mu$ computation.
- **3D Wind Field Model (Wind Field & Turbulence)**:
  - Spatially correlated turbulence via 3D Simplex noise, altitude boundary layer profiles (log law / power law), thermal updraft columns.
  - **Coordinate Transformation Rigor**: Wind field domain defines vertical axis as $+z$ up, horizontal compass axes as north=$+y$ / east=$+x$ (`physics/wind_domain.py` canonical); `wind_direction_deg` is meteorological wind direction (clockwise from north "blowing from" direction; flow vector points opposite). At handoff to flight dynamics domain (world NED: $+x$ north/forward, $+y$ east/right, $+z$ down), sign conversion $w_{\text{NED}} = -w_{\text{wind}}$ is strictly executed (Issue #30 consistency verified).
- **Geometry→Aerodynamics Handoff Spec** (`physics/origami_domain.py::extract_aero_parameters` / `apply_aero_parameters`): Extract only dimensionless parameters from origami geometry and write to `AircraftConfig` (`camber_ratio`, `leading_edge_sweep_deg`, `dihedral_eff_deg`, `camber_forward`). **No aerodynamic dual implementation on origami side** (canonical is `physics/aero_kernel.py` single implementation). $f/c_{packet}=PACKET\_CAMBER\_M/0.10$, $f/c_{TE}=0.5\sin\phi_{TE}\cdot0.055$, $\Lambda_{LE}=\arctan(0.05 W/L)$, dihedral preserves sign. Verified: `tests/test_origami_aero_coupling.py` (5 passed), `tools/check_aero_kernel_parity.py` diff 0.0.
- **Structural Stress Model**:
  - Compute wing root bending moment from dynamic pressure $q$, AoA $\alpha$, and acting acceleration (G-force).
  - Compute dynamic stress evaluation (0.0: safe ~ 1.0: failure limit) at each wing vertex; visualize as 3D heatmap.
  - **Stress index normalization scale is single**: `stress_index = sigma / sigma_allow` where denominator is aircraft-specific allowable folding stress `paper_allowable_stress(gsm, stiffness)` (default 100 g/m², stiffness 0.35 → 2.2 kPa). 6DoF path, heatmap, GPU optimization batch, structural failure judgment all share this scale (legacy 25 kPa / 120 kPa / 2.0e4·gsm three systems deprecated). Load distribution min-max shape published separately via `stress_shape` channel.
  - **Stress Calibration Coefficient `KAPPA_SIGMA`**: From wing root bending moment $M$ and rectangular plate section modulus $Z = b\,t^2/6$ ($b$ = local chord, $t$ = deployed sheet thickness = `gsm/800`), stress computed as $\sigma = \texttt{KAPPA\_SIGMA}\,M/Z$. `physics/stress_domain.py::KAPPA_SIGMA = 9.0e-6` is a **dimensionless empirical calibration coefficient multiplied onto Beam theory value**, shrinking reported stress to ~1/111000 of Beam theory value (both `stress_field` / `section_modulus_batch`). No dimensional meaning; defines numerator of `stress_index` in §1.3. **Value retained per §5.2** (source not obtained).

### 1.3 6DoF Equations of Motion Integration (Flight Dynamics Engine)
- Quaternion representation for singularity-free attitude update.
- 4th-order Runge-Kutta (RK4) numerical integration.
- Variable timestep adaptive sub-stepping for abrupt behavior changes, and systematic convergence guarantee for fixed-timestep analysis (Issue #31 convergence verified).

### 1.4 Procedural Origami Geometry Model
`physics/origami_domain.py` generates 3D paper airplane models from A4 paper (aspect ratio 1:√2, 210 × 297 mm) as a **simulation of the folding process itself**. Not an analytical combination of flat polygons, but a multi-layer solid obtained by cutting and folding a crease pattern.

**Coordinate System**: Plane is normalized unit square `(u, v)` (`u` = short edge direction, `v` = long edge direction, nose at `v = 1`).
3D mapping: `x = (v - 0.5) * L` (fore-aft, +x = nose), `y = 0` (up), `z = (u - 0.5) * W` (left-right, +z = right wing). This frame is right-handed but distinct origin from NED simulation frame (renderer converts via `Rx(+90°)` in `static/js/viewport.js`).

**Folding Sequence (Hinge Array)**:
1. `body`: Center longitudinal baseline (symmetry fold, $\pm(180°-3°)/2$). Left/right body form ~177° open V-shaped keel (tiny V-gap `keel_beta_deg` at centerline).
2. `corner_l/r`: Left/right upper corners folded down to centerline at 45° valley fold (nose 1st layer overlap). Creases defined on actual paper dimensions (long edge 297 mm / short edge 210 mm) so corners land precisely on centerline.
3. `collar_l/r`: Creases along bisector of folded corner hypotenuse and centerline. Creates thick leading-edge foldback and sharp nose (beak); nose overlaps 3 layers per side, max 6 layers total.
4. `wing_l/r`: Fold main wings horizontal (with slight dihedral) leaving fuselage keel.
5. `winglet_l/r`, `elevon_l/r`, `trailing`: Wingtip winglets and trailing-edge elevon camber.

Fold sequence matches physical folding order; each fold rotates around **current crease position** at that time (`T' = F ∘ T`). Layers spanned by same fold share same 3D line, so folds bend continuously as overlaps.

**Layer Offsets (Z-Fighting Elimination Proof)**:
- Paper thickness $t$ = `VISUAL_THICKNESS_M` = 0.30 mm (3× visual emphasis vs real 0.10 mm).
- Air gap `LAYER_AIR_GAP_M` = 0.12 mm, layer pitch `LAYER_PITCH_M` = $t$ + gap = 0.42 mm.
- Only mutually parallel faces with overlapping projections are clustered into same **stack**; ordered by signed depth $d$ along stack normal, **longest path in overlap DAG** = layer index $k_f$.
- Any two overlapping faces satisfy $k_B \ge k_A + 1$ (by longest path definition); post-displacement mid-surface spacing $\ge (k_B - k_A) \cdot \mathrm{PITCH} \ge \mathrm{PITCH} > t$. Thickness occupies only $\pm t/2$ from mid-surface, so **two layers cannot intersect or become coplanar** (Z-Fighting structurally impossible). Nose has 3 layers/side, 5–6 total contact (max stack layer index = 8 in implementation).
- Offset increases 0 → 1 proportional to fold angle $\alpha$; at $\alpha = 0$ (flat sheet) all layers become coplanar.

**Solid Generation**: Each face output as **closed slab** with top/bottom skims and sidewalls (cut edges); paper cross-section becomes area-possessing rectangle. Folds closed as **roll folds** (cubic Bézier), giving tangent-continuous normals gradient (crease highlights, cavity shading). Folds split in two, baked as `crease_weight` vertex attribute.

**Vertex Attributes** (`BufferGeometry` custom attributes):
| Attribute | Meaning |
|---|---|
| `aLayer` | Paper layer count at position (1 = single, 4–6 = nose packet) |
| `aCrease` | Crease proximity 0..1 (cavity shading, highlights) |
| `aElastic` | Wing-tip direction weight (passive aeroelastic bend, fuselage keel = 0) |
| `aTwist` | Chord direction weight (passive aeroelastic twist) |
| `aSide` | Render face front (+1) / back (-1) (translucency attenuation) |

**Passive Aeroelastic**: During flight, `aeroelastic_dihedral_deg` / `aeroelastic_twist_deg` passed as uniforms to paper shader; wing-tip bending proportional to $\mathrm{elastic}^2$, twist proportional to `aTwist` applied as vertex displacement (keel rigid).

**3 Aircraft Archetypes**:
| key | Name | Features |
|---|---|---|
| `classic_dart` | Classic Dart / Yarigami | Slender delta wing, nose 4–6 layers, sharp beak |
| `nakamura_lock` | Nakamura Lock / Hesogami | Belly fold tucking nose inward, thick leading edge, large wing area |
| `sky_king` | Sky King / Competition Duration | Blunt nose (horizontal foldback), distinct winglets, high aspect ratio |

---

## 2. GPU & Parallel Compute Architecture

### 2.1 Backend Parallel & GPU Acceleration
- **CuPy / CUDA Batch Parallel Simulation**:
  - 3D wind field grid array batch vector computation.
  - Multiple flight cases (Monte Carlo, sensitivity, optimization population) batch parallel evaluation.
  - **Per-Case `duration_s` Reflection**: In batch simulation, per-case different cutoff time limits `duration_s` accurately maintained and judged; timeout and landing time computed individually (Issue #28 resolved).
- **Safe CPU / NumPy Fallback**:
  - In GPU-less or CUDA-init-failure environments, auto-transition to CPU parallel via NumPy and `concurrent.futures` / `multiprocessing`.
  - **Response Schema Match** (`landed`, `crashed`, `status`, `horizontal_range_m`, etc.) guaranteed.
  - **Per-path `model` tag explicitly declares physics model differences** (Issue #35 "full match" was overclaim):
    - `model = "rigid_body_6dof"` … Regular 6DoF RK4 path, and **GPU optimization batch path vectorizing the identical 13-state rigid body model (same aero kernel, wind field, structural model, RK4 scheme) on GPU** (`model_detail = "gpu_batch_rk4"`). Both validated quantitatively via `tools/check_gpu_parity.py` and `tests/test_gpu_kernel_parity.py` (measured: max stress & G-load exact match, range 0.1–0.9% diff).
    - `model = "reduced_surrogate"` … Reduced model. Specifically **GPU Monte Carlo ensemble path** (3-DoF reduced integration, `model_detail = "gpu_ensemble_3dof"`) and **Analytical Glide Surrogate** (closed-form without attitude dynamics, `model_detail = "analytical_glide_surrogate"`). These are `approximate = true` / `physics_verification_eligible = false`, excluded from 6DoF re-verification.
    - Legacy GPU path (3-DoF point mass, explicit Euler, attitude clamp) showed 1261× max stress difference vs 6DoF path, even `status` differed (`flying` vs `crashed`). This overclaim removed.

### 2.2 Frontend GPU Rendering
- Three.js Custom Vertex & Fragment Shaders for wing surface dynamic stress heatmap.
- GPGPU / Transform Feedback particle system for 100,000+ wind streamline particles real-time.
- Web Workers for telemetry buffering and async processing.

---

## 3. Comprehensive REST API Specification

All endpoints managed by Blueprint separation; invalid input returns HTTP 400 with detail message, internal exceptions return HTTP 500 with traceable `error_id`, stack traces concealed (Issue #34).

### 3.1 Meta API (`api/routes/meta.py`)
- `GET /api/v1/meta/health`: Server status, GPU availability, backend info
- `GET /api/v1/meta/schema`: All config parameter dataclass / JSON schema definitions
- `GET /api/v1/meta/physics-references`: Academic citation & theoretical source registry (Helmbold, Viterna, Wieselsberger, etc. applicability & sources, Issue #32)
- `GET /api/v1/meta/test-fault`: Fault injection endpoint for error handling / traceback concealment verification (Issue #34)

### 3.2 Physics API (`api/routes/physics.py`)
- `POST /api/v1/aerodynamics/calculate`: Lift, drag, moment, stall state, $C_{D,\min}$, $(L/D)_{\max}$
- `POST /api/v1/aerodynamics/polar`: Drag Polar characteristic curves vs AoA $\alpha$ sweep
- `POST /api/v1/environment/atmosphere`: Environment atmosphere parameter setting + immediate return of density, kinematic viscosity, speed of sound
- `GET /api/v1/environment/current`: Current environment parameters
- `POST /api/v1/wind/field`: Wind velocity vector field for specified 3D spatial coordinates (GPU/CPU)
- `POST /api/v1/stress/analysis`: Wing dynamic pressure, G-force stress heatmap data

### 3.3 Simulation API (`api/routes/simulation.py`)
- `POST /api/v1/simulation/step`: 6DoF 1-step EOM update (variable $\Delta t$)
- `POST /api/v1/simulation/reset`: Simulation state initialization
- `POST /api/v1/simulation/batch`: Multi-parameter batch parallel simulation (GPU/CPU fallback)

### 3.4 Mesh API (`api/routes/mesh.py`)
- `POST /api/v1/mesh/import`: 3D model file (glTF/GLB/OBJ/STL) or URL import (SSRF prevention, Issue #37)
- `GET /api/v1/mesh/models`: Imported model list
- `POST /api/v1/mesh/select`: Simulation target model selection
- `GET /api/v1/mesh/metrics/<model_id>`: Area, span, CG, inertia tensor, panel division data

### 3.5 Jobs & Optimization API (`api/routes/jobs.py`)
- `POST /api/v1/optimizer/run`: Multi-objective optimization start (non-blocking, immediate `job_id` return)
- `GET /api/v1/optimizer/status/<job_id>`: Progress, ETA, estimated completion (exponential moving average smoothing)
- `POST /api/v1/optimizer/cancel/<job_id>`: Interrupt running job
- `GET /api/v1/optimizer/result/<job_id>`: Pareto solutions and optimal parameters
- `POST /api/v1/jobs/investigate`: Flight diagnostics (stall, spiral, excessive trim) + Japanese auto-advice generation job
- `GET /api/v1/jobs`: All background job list

### 3.6 Records & Data API (`api/routes/records.py`)
- `GET /api/v1/records/presets`: Registered aircraft preset list (Toda Takuo duration record, Suzanne, Dart, etc.)
- `GET /api/v1/records/preset/<name>`: Specific preset detailed parameters
- `GET /api/v1/records/guinness`: Guinness world record aircraft (29.2s duration) benchmark spec
- `POST /api/v1/data/export`: Flight history log export (CSV/JSON)
- `POST /api/v1/data/import`: External config/parameter import
- `GET /api/v1/records/errors`: Server internal error history

### 3.7 Origami API (`api/routes/origami.py`)
- `GET /api/v1/origami/archetypes`: 3 supported aircraft archetypes (key/label/shape_flag) and physical constants (paper thickness, layer pitch, etc.)
- `POST /api/v1/origami/mesh`: Return folded aircraft mesh.
  - Input: `archetype` (`classic_dart` / `nakamura_lock` / `sky_king`), `alpha` (fold progress 0..1), `wingspan_m` (scale to wingspan), `keel_beta_deg` / `dihedral_deg` / `elevon_deg` / `fold_tip_deg` / `fold_trailing_deg` / `fold_leading_deg`.
  - Output: `positions` / `normals` / `uvs` / `indices` (flat arrays) and `layer_counts` / `crease_weights` / `elastic_weights` / `twist_weights` / `paper_sides`, `paper_thickness_m` / `layer_pitch_m` / `max_layers` / `bounds` / `stats`.
  - Viewer uses same generator (`physics/origami_domain.py`), so render output and unit test geometry always match.

---

## 4. Security & Robustness Standards

1. **Exception Traceback Client Blocking (Information Leakage Prevention, Issue #34)**:
   - Production API responses must never include internal implementation details (file paths, call stacks) on unhandled exceptions.
   - Response includes only unique `error_id`; detailed traceback logged to server log only.
2. **SSRF (Server-Side Request Forgery) Prevention (Issue #37)**:
   - On 3D mesh import from external URLs, block private IPs (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.1`), link-local (`169.254.0.0/16`), cloud metadata endpoints (AWS/GCP/Azure), and non-standard ports.
   - Enforce multi-stage validation on HTTP redirects.
3. **Coordinate System Consistency & Transformation Verification (Frame Transformation Integrity, Issue #30)**:
   - Strict sign conversion at wind field ($+z$ up) / world NED ($+z$ down) handoff boundary; verify consistency via unit tests in calm and each wind condition.

---

## 5. Academic Citation & Literature Source Registry

Formulas and physics models adopted in this simulator conform to the following aerospace/fluid dynamics literature (Issue #32 / published at `/api/v1/meta/physics-references`):

1. **Helmbold (1942)**: Low-aspect-ratio straight wing lift slope empirical formula ($AR \le 4$).
2. **Polhamus (1966, 1971)**: Delta wing leading-edge vortex suction lift concept (Leading-Edge Suction Analogy).
3. **Viterna & Corrigan (1982)**: Wind turbine & airfoil deep stall/post-stall aerodynamic model.
4. **Wieselsberger (1921)**: Ground effect induced drag reduction theory based on span & height ratio.
5. **Hoerner (1965)**: Turbulent boundary layer skin friction & form drag empirical data (*Fluid-Dynamic Drag*).
6. **Sutherland (1893)**: Gas dynamic viscosity temperature dependence formula.
7. **AeroSandbox (Bremwand et al.)**: 6DoF flight dynamics & small perturbation stability derivative formulation.

### 5.0 Per-Model Canonical Sources & Applicability (Issue #48 / #49 / #50 / #51 / #52)

Above table lists **citations**; **implementation canonical** is in separate docs.
**Which models are implemented** (`/api/v1/meta/physics-references` entries' `implemented` / `canonical_module` / `applicability` are machine-readable declarations), **which formulas are duplicated**, **Reynolds number dependence**, **post-stall model adoption rationale** are consolidated in:

- **`docs/PHYSICS_MODELS.md`** — Per-model "Formula → Source → Canonical Module → Applicability → Verification Tests". Resolves Issue #48 (Helmbold formula & independent verification), #49 (deep stall model & transition continuity), #50 (Re dependence & out-of-range handling), #51 (duplicate definition resolution), #52 (Polhamus default validity).
- **`docs/CFD_ROADMAP.md`** — Issue #47 (3D Navier–Stokes solver requirements & phased introduction plan). Separate layer from 6DoF panel method; search fitness remains 6DoF.

**Critical Distinction**: Citation 3 (Viterna & Corrigan) is **not implemented**. Implemented post-stall model is Citation 5 (Hoerner flat plate); its adoption rationale & applicability in `docs/PHYSICS_MODELS.md` §3. Registry makes this distinction explicit via `implemented` flag (`tests/test_physics_model_registry.py` verifies).

### 5.1 Grip Filter Threshold Rationale (G9-1, Ergonomics & International Standards)

`physics/origami_genome.py` GripThresholds defaults (min height 8 mm / fore-aft length 30 mm / min 2 layers / CG tolerance 15 mm / grip half-width 12 mm) conform to public anthropometric/ergonomics standards targeting adult two-point precision pinch (thumb + index finger).

**Standards & Sources**:
1. **ISO 7250-1:2017 / ISO/TR 7250-2:2010**:
   - Clause 6.3.6 (index fingertip width: female 14–15 mm, male 15–18 mm) & Clause 6.3.16 (thumb width: male 95th percentile 23.8 mm). Grip half-width 12 mm (full width 24 mm slab) matches adult fingertip grip centerline width.
2. **AIST Anthropometric Database / HQL Japanese Hand Dimensions (2010)**:
   - Index distal phalanx length (22.5–24.8 mm), thumb distal phalanx length (28.0–31.2 mm), index pulp thickness (10.5–12.0 mm).
3. **MIL-STD-1472H (2020) Section 5.7.3**:
   - Pinch tab minimum protrusion height 6.4–12.7 mm. Current min height 8 mm (`min_grip_height_m = 0.008`) is safe lower bound ensuring >half pulp thickness contact to prevent slip.
4. **NASA-STD-3000 Section 3.3**:
   - Opposed pinch contact length 18–25 mm. Fore-aft 30 mm (`min_grip_length_m = 0.030`) fully covers contact zone, ensures throw margin. CG tolerance 15 mm matches contact half-length, prevents pitch reversal moment at release.
5. **Min 2 Layers**: Prevents single-layer out-of-plane buckling of standard paper (70–100 g/m²).

All thresholds configurable via constructor and API.

### 5.2 Unreferenced Physical Constants Retention Rationale (G-17)

Same handling as §5.1 but for physics model **calibration coefficients**. **None of these values are revised**. No new coefficients without sources (AGENTS.md §1.5 / §2). **Values stated here = implementation values**; implementation side not modified.

| Constant | Value | Physical Meaning | Retention Reason | Source |
|---|---|---|---|---|
| `KAPPA_SIGMA` | `9.0e-6` (`physics/stress_domain.py::KAPPA_SIGMA`) | Dimensionless empirical calibration coefficient multiplied onto Beam theory stress $\sigma = \texttt{KAPPA\_SIGMA}\,M/Z$ derived from wing root bending moment $M$ and rectangular plate section modulus $Z = b\,t^2/6$ ($t$ = deployed sheet thickness `gsm/800`). Defines numerator of `stress_index = sigma/sigma_allow` in §1.3.<br>**Formula/Derivation**: $\sigma_{beam}=M/Z_{flat}$, $Z_{flat}=b t^2/6$, $t=gsm/800\,\mathrm{mm}=gsm/8\mathrm{e}5\,\mathrm{m}$ so $Z\propto gsm^2$, $\sigma_{beam}\propto gsm^{-2}$. 80 g/m² $t=0.10\,\mathrm{mm}$, 100 g/m² $0.125\,\mathrm{mm}$ (KAPPA_SIGMA_REFERENCE_GSM=80, GSM_EXPONENT=-2). **Physical correction**: Model uses flat plate thickness $t$ but real folded wing has keel/box fold depth $h\sim10$–$30\,\mathrm{mm}$ (ROOT_SECTION_HEIGHT_FRACTION=0.40·MAC) so $Z_{fold}\sim bh^2/6$ is $(h/t)^2\sim2.5\mathrm{e}4$ times larger. Helper `kappa_sigma_derivation(gsm,h)` computes $t,(h/t)^2$, residual $KAPPA\_SIGMA\cdot(h/t)^2\ (\approx0.36$ @80 g/m²$) \cdot KAPPA\_SIGMA\cdot M/Z_{flat}\rightarrow 10^2$–$10^3\,\mathrm{Pa}$. **Applicability**: Valid only as quasi-static bending relative ranking indicator; not for shear, torsion, buckling, dynamic/impact, creep, raw fiber failure prediction. Absolute scale in kPa range (2500 Pa prior), not MPa (20–40) | No source corresponding to actual paper/real folded surface material constants (tear strength, residual cross-section at fold) exists in **any audited paper**. Changing value simultaneously alters all stress values and failure judgments, so **no numeric revision until source obtained**. Note `paper_allowable_stress` baseline 2200 Pa also not actual paper tensile strength 20–40 MPa; meaningful **only in combination with `KAPPA_SIGMA`** | `no-finish.md` §A-1 (48 lines) / `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §2-H-11 (lines 462–479), constant table (line 1248), Appendix B H-11 (line 1314) |
| `trailing_edge_fold_effects` coefficients | `0.015` / `-0.012` / `0.035` (`physics/aero_kernel.py::trailing_edge_fold_effects`) | Aerodynamic coefficient increments vs full-span trailing-edge fold angle $\phi$: $\Delta C_L = 0.015\,\phi$, $\Delta C_m = -0.012\,\phi$, $\Delta C_D = 0.035\,\sin^2\phi$ (§1.1). Positive fold = lift increase + nose-down, negative fold = lift decrease + nose-up | No specific paper formula exists for aerodynamic changes from trailing-edge fold (trailing-edge drag increase + trailing-edge vortex formation). Local geometry → local lift panel method approximation would add **same source-less coefficients as A-1** to regular path (Helmbold/Hoerner) | `no-finish.md` §A-2 (lines 55–65) / `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §2-H-19 (lines 681–699 "coefficients 0.015 / −0.012 / 0.035 no source"), Appendix B H-19 (line 1322) |

**Implementation Status Note**: `no-finish.md` §A-2 and Audit §H-19 record "`trailing_edge_fold_effects` caller missing" state, but trailing-edge aero connection restored in `7639895 fix(physics): restore the fold-integration contract and the payload keys` (`no-finish.md` lines 363–365). **Fact that coefficient sources unobtained remains unchanged**, so value retention continues (connection presence vs coefficient source are separate issues).

**Re-open Conditions**: Obtain measured paper on trailing-edge fold aerodynamics (trailing-edge drag increase, trailing-edge vortex lift/moment contribution), or measured paper material tear strength & fold-face residual cross-section geometry. Until then, **do not revise values**.

---

## 6. Audit Resolution History

Major issues raised in project quality audit (Issue #28–#37) are all resolved & tested:

| Issue | Category | Problem | Resolution & Verification Test |
|---|---|---|---|
| **#28** | Bug | GPU batch case-specific `duration_s` not reflected in `landing_time` | Per-case max time array retention, individual timeout & landing judgment implemented. Verified by `tests/test_issue28_gpu_duration.py`. |
| **#29** | Physics | Distinction between 6DoF flight dynamics and volumetric CFD | README and this spec explicitly state AeroSandbox-compliant 6DoF + panel method model. |
| **#30** | Physics | Wind field (+z up) / World NED (+z down) coordinate conversion consistency | Vertical wind sign conversion path rigorized. `tests/test_security_and_frames_audit.py` verifies updraft/downdraft/calm. |
| **#31** | Numerics | Systematic convergence verification of fixed-timestep RK4 integration | Timestep refinement test $\Delta t = 0.04 \to 0.005\,\text{s}$ implemented, monotonic convergence proven. Verified by `tests/test_advanced_physics_audit.py`. |
| **#32** | Aero | Independent verification & publication of aero coefficient theory/literature sources | `/api/v1/meta/physics-references` endpoint created, academic citation registry published. Unit test verified. |
| **#33** | Wind Model | 3D wind field bounded divergence, spatial correlation, temporal continuity verification | Turbulence field bounded divergence, distance-dependent correlation decay, temporal continuity numerically evaluated. Verified by `tests/test_advanced_physics_audit.py`. |
| **#34** | Security | Internal traceback leakage prevention on unexpected exceptions | `api_error()` revised; production returns only `error_id`. Verified by `tests/test_security_and_frames_audit.py`. |
| **#35** | GPU | CPU/GPU computation results & response schema full match | `gpu_batch_evaluate_cases` CPU fallback unified schema. **Response schema match & fallback behavior verified**. "Physical computation full match" was overclaim, removed; instead GPU optimization batch path rewritten to **regular path's identical 13-state RK4 model**, quantitatively validated via `tools/check_gpu_parity.py` / `tests/test_gpu_kernel_parity.py` (§2.1). Remaining reduced paths explicitly tagged via `model` (`rigid_body_6dof` / `reduced_surrogate`) and `model_detail`. |
| **#36** | Mesh | Panel method mesh resolution dependence & lift consistency verification | Asymptotic convergence of lift/drag/moment with panel refinement proven. Verified by `tests/test_advanced_physics_audit.py`. |
| **#37** | Security | SSRF prevention on 3D mesh import | Private IPs, loopback, cloud metadata, non-standard ports blocked. Verified by `tests/test_security_and_frames_audit.py`. |

---

## 7. Module & Directory Structure

```
.
├── AGENTS.md                  # Agent Core Protocol OS (project-independent)
├── .agents/                   # Autonomous Execution Loop OS Protocol
│   ├── AGENTS.md              # Context minimization, Texas Sharpshooter exclusion, 5-phase autonomous loop
│   ├── Project.md             # Project Goal & Current Loop State (Loop State)
│   ├── Knowledge-important.md # Resident Constraints & Key Knowledge List (≤50 lines)
│   ├── Knowledge.md           # On-Demand Reference Knowledge Base
│   └── skills/                # Standard Procedures (loop-handover.md, etc.)
├── docs/                      # Design Docs, Academic Research, Detailed Specs
│   ├── SPECIFICATION.md       # This Spec (Detailed Physics Models, API, Security)
│   └── discord-bridge.md      # Discord Remote Bridge Spec
├── api/                       # Split REST API Package
│   ├── __init__.py
│   ├── core.py                # Session state, common helpers, error response (traceback concealment)
│   └── routes/                # Responsibility-separated Blueprint routing (meta, physics, simulation, mesh, jobs, records)
├── physics/                   # Physics & Simulation Core Engine
├── tests/                     # Comprehensive Test Suite (700+ tests)
├── tools/                     # Dev, Ops, Verification Tools (notify_discord, discord_bridge, etc.)
├── templates/index.html       # Google Clean UI Console
├── static/                    # Three.js 3D Viewer & UI Scripts
├── requirements.txt           # Runtime Deps (CPU default, gpu/bridge as extras)
├── pyproject.toml             # Project Metadata & Optional Deps
├── setup.sh                   # Environment Bootstrap Script
├── app.py                     # Flask Application Entry Point
├── smoke_test.py              # End-to-End Smoke Test (14 Stages)
├── config/                    # Genetic Research Config (genetic_research.yaml, etc.)
├── no-finish.md               # Outstanding Issues Ledger
└── README.md                  # Project Overview
```

---

## 8. Agent Role Division (v4)

When delegating to subagents or making module-specific fixes, adhere to these responsibility boundaries:

| # | Agent Name | Responsible Files | Mandatory Implementation & Responsibilities |
|---|---|---|---|
| 0 | `Manager` | `physics/config_schema.py`, `physics/geometry_utils.py`, `app.py`, `smoke_test.py`, `AGENTS.md` | Contract definitions, API Blueprint integration, UI, E2E tests, Git control |
| 1 | `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | Airfoil, stall, Drag Polar, Trim, passive aeroelastic, low-speed vortex lift |
| 2 | `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | ISA, Sutherland, full variable environment atmosphere, 3D Simplex wind field, coordinate transform integrity |
| 3 | `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | Dynamic pressure, G-force, wing root bending moment, vertex stress heatmap |
| 4 | `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | glTF/GLB/OBJ/STL analysis, inertia tensor, panel method, SSRF prevention |
| 5 | `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | 6DoF RK4, quaternion, adaptive/fixed timestep, ring buffer |
| 6 | `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | GPU batch evaluation, per-case duration_s, CPU exact-match fallback, NSGA-II |
| 7 | `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | JobManager, EMA ETA, progress management, JSON/CSV export |
| 8 | `ui-gpu-viz` | `templates/index.html`, `static/css/console.css`, `static/js/*.js` | Google Clean UI, Three.js shaders, timeline, telemetry |
| 9 | `physics-proposal` | `AGENTS.md`, `physics/*`, `docs/*` | On physics investigation: actively research current model improvement room & realistic high-fidelity options (with compute cost) and propose |

---

## 8.1 milcy Algorithm (Morphogenetic Quality-Diversity Search Architecture)

The formal specification of the morphogenetic search pipeline originally developed as v3. Unlike generation-elimination GA approaches, milcy simultaneously discovers high-quality and diverse aircraft via **MAP-Elites (Multi-dimensional Archive of Phenotypic Elites)**.

### Core Components
1. **20-step Parametric Origami Grammar (MCTS Origami)**:
   - `geometry.milcy_origami` (compat: `geometry.v3_origami`)
   - Uses Monte Carlo Tree Search (MCTS) to generate valid origami fold graphs, crease angles, layer orders, and launch/grip constraints.
2. **3D MAP-Elites Behavioral Archive (Quality-Diversity)**:
   - `optimization.map_elites`
   - Maintains elite aircraft across a 1,000-niche (10x10x10) grid spanning Wing Loading, Static Stability Margin, and Aspect Ratio.
3. **Continuous Gliding Trim Optimization (Nelder-Mead Local Trim)**:
   - `optimization.trim`
   - Optimizes elevon (-4° to +8°) and dihedral (0° to 8°) angles for each elite via Nelder-Mead simplex search.
4. **Multi-dimensional Robustness Stress Testing (Robustness Grid)**:
   - `optimization.eval`
   - Evaluates elites under Dryden turbulence (MIL-F-8785C) and release perturbations, computing robustness scores and exporting .obj, .json, and .jsonl artifacts.

---

## 9. Verification Commands & CI/CD Protocol

On code changes / feature additions, **always pass the following staged verification locally**:

```powershell
# 1. Syntax Validation
python -m compileall physics api tools app.py scripts/smoke_test.py

# 2. Static Analysis & Code Quality
ruff check .
mypy physics api app.py scripts/smoke_test.py

# 3. Full Unit & Audit Test Run
python -m pytest tests -q

# 4. End-to-End Integration Test (API chain, optimization job, ETA, 3D model analysis)
python scripts/smoke_test.py
```

### GitHub Actions & Git Operations
1. Before work, always create dedicated topic branch (`git checkout -b <type>/<description>`).
2. After changes/fixes, verify all above tests pass locally.
3. Commit, push branch, merge to master.
4. After master integration, auto-push to remote (`git push`).
5. Confirm all GitHub Actions jobs (Ubuntu/Windows × Python 3.10/3.11/3.12, CodeQL, pip-audit) are green (`tools/check_runs.py`).
6. Check GitHub Issues (`tools/check_issues.py` / `tools/read_issues.py`), fix/resolve remaining issues, auto-close (`tools/close_resolved_issue.py`), and terminate autonomous loop normally (HALT).