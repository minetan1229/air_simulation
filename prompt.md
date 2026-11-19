# 3D Paper Airplane Simulator — Execution Prompt v4 (High Precision)

You are a senior engineer and project manager proficient in aeronautical engineering, fluid dynamics, numerical analysis, GPU programming, and full-stack development. Following all rules of this project's specification `agents.md` (equivalent to AGENTS.md), **design, implement, and verify fully working code without omissions within 1 session**.

---

## 0. Mandatory Requirements Added in This Version (Diff from v3)

| # | Added Requirement | Implementation Location |
|---|-------------------|------------------------|
| A | **All Physics Parameters Externally Variable** (air density, viscosity, gravity, drag multiplier, turbulence, etc. freely changeable from UI/API) | `physics/atmosphere_domain.py`, `physics/config_schema.py` |
| B | **Optimization Engine** (optimal solution search for aircraft geometry, folding, trim, mass; parallel evaluation, Pareto front) | `physics/optimizer_domain.py` |
| C | **3D Model Import** (auto-compute area, span, aspect ratio, CG, inertia tensor from glTF/GLB/OBJ/STL) | `physics/mesh_domain.py`, `static/js/model_importer.js` |
| D | **ETA Display** (server-side management of per-job progress, remaining time, estimated completion; display on UI) | `physics/job_manager.py`, `static/js/eta_display.js` |
| E | **Parallel Implementation via Subagents** (parallel execution per assignment table, avoiding file conflicts) | All phases |

**Above A–E: No omissions, no deferrals, no pseudo-implementation. All steps completed simultaneously.**

---

## 1. Strict Execution Principles (Violations Prohibited)

### 1. Zero-Placeholder Policy
- No omissions via `...`, `TODO` / `FIXME`, unimplemented empty functions, `pass`-only bodies, stub APIs.
- Physics formulas, GPU shaders, parallel processing, optimization algorithms, progress management, DOM manipulation, UI events — all 100% executable code.

### 2. Contract-First (Dependency Fixation)
- You alone **first finalize** the data structures shared by all agents (`physics/config_schema.py` dataclasses / JSON schemas). All subsequent implementations must strictly follow these types. Prevent other agents from breaking due to type changes.

### 3. Small-Step & Verification
- Execute one module at a time, verify results, then proceed. At minimum, run the following every time and **report actual measured output**:
  - `python -m pytest tests -q` (physics, optimization, mesh numerical verification)
  - `python -m compileall physics app.py` (syntax verification)
  - `python smoke_test.py` (end-to-end: API connectivity + optimization job + ETA retrieval + mesh analysis)

### 4. No Silent Fallback
- In environments without GPU (CuPy / torch CUDA), must prove normal operation via NumPy fallback in tests. On fallback, explicitly indicate `backend: "numpy"` in response.

### 5. Git Protocol
- Commit only after verification passes. Format: `feat(aero): ...` / `feat(optimizer): ...` / `feat(mesh): ...` / `feat(ui): ...` / `fix(<scope>): ...`.
- Include in `.gitignore`: `__pycache__/`, `*.pyc`, `.venv/`, `node_modules/`, `*.glb`, `*.gltf`, `*.stl`, `*.obj`, `runs/`, `jobs/`.

### 6. Physical Validity (Core of Precision)
- For domains with analytical solutions (linear lift $C_L = C_{L\alpha}\alpha$, pre-stall induced drag $C_{D0} + C_L^2/(\pi e AR)$, Reynolds dependence, ISA atmosphere, geometric computation of area/CG/inertia tensor), **pytest must verify against analytical solutions with relative error 1e-6 to 1e-3**. Unverifiable parts must be explicitly labeled "verified / unverified".

---

## 2. High-Precision Physics Model Requirements (Formulas That Cannot Be Omitted)

### 2.1 Atmosphere & Fluid Parameters (Requirement A: All Variable)
- **ISA Standard Atmosphere**: Troposphere $T = T_0 - L h$, $p = p_0 (T/T_0)^{g/(L R)}$, $\rho = p/(R T)$.
- **Local Atmosphere Override**: Compute density from `temperature_K`, `pressure_Pa`, `humidity_rel` (humid air mixing ratio correction, $R_{air}=287.05$, $R_{v}=461.5$).
- **Dynamic Viscosity**: Sutherland formula $\mu = \mu_0 (T/T_0)^{1.5} (T_0+S)/(T+S)$, $\nu = \mu/\rho$.
- **Reynolds Number**: $Re = V c / \nu$ computed every step.
- **Environment Scales for Drag etc. (UI Sliders)**:
  - `drag_scale` (0.0–5.0, default 1.0): multiplier for all drag coefficients
  - `gust_intensity` (0.0–1.0), `thermal_strength` (m/s), `wind_speed` (m/s), `wind_direction_deg` (0–360)
  - `gravity` (m/s², default 9.80665), `ground_friction` (0.0–2.0)
  - `ground_effect_enabled`, `compressibility_enabled` (Prandtl-Glauert correction at $M = V/a$)
- All must be immediately changeable via `POST /api/v1/environment/atmosphere`, and **test must prove UI change → server → reflected in next step calculation**.

### 2.2 Airfoil Aerodynamics (Requirement A: Aircraft Side)
- Linear regime: $C_L = C_{L0} + C_{L\alpha}\,\alpha$
- Stall: Viterna-equivalent nonlinear transition (sigmoid type, smooth descent from $C_{L,\max}$, post-stall $C_L$ linear decrease).
- Drag: $C_D = C_{D0} + C_L^2/(\pi e AR)$, $C_{D0} = f(Re)$ (increases as Re decreases).
- **Drag Polar API Exposure**: Numerically find and return $C_{D,\min}$ and $\alpha_{L/D,\max}$.
- Delta wing (taper ratio $\lambda$) aero properties: $AR = b^2/S$, $c_{mac}$, $C_{L\alpha} = 2\pi AR \big/ \big(2 + \sqrt{AR^2(1+\lambda) + \tfrac{4}{\pi} AR}\big)$.
- Total lift $L = \tfrac12 \rho V^2 S C_L$, drag $D = \tfrac12 \rho V^2 S C_D$, pitching moment $M = q S c C_m$.
- **Trim / Elevon**: Trailing-edge fold angle $\delta$ provides $\Delta C_m$, $\Delta C_L$, $\Delta\alpha_{trim}$.
- **Numerical Integration Verification**: Include pytest test comparing wing circulation $\Gamma = 2 q S C_L/(\rho V)$ from numerical integration of lift coefficient over $\alpha$ vs Helmbold single-plate extreme value.

### 2.3 6DoF Integration (High Precision)
- State: Position $\mathbf{r}$, velocity $\mathbf{v}$ (explicit body↔inertial frame conversion), attitude quaternion $\mathbf{q}$, angular velocity $\boldsymbol{\omega}$.
- Forces: Aerodynamic (lift, drag, pitching/yawing/rolling moments), gravity, attitude-stabilizing restoring torque.
- **RK4 Integration + Adaptive Sub-stepping**: $\Delta t$ variably determined from local error estimation (angular velocity & airspeed based limits) and CFL wind condition, clamped to $\Delta t \in [10^{-5}, 2\times10^{-2}]$ s. Rollback to previous step on numerical divergence detection (NaN / Inf / speed limit exceeded).
- **Inertia Coupling**: $I\dot{\omega} = M - \omega \times I\omega$. Inertia tensor auto-computed from 3D mesh.
- Verification: Free fall without drag matches $h(t) = h_0 - \tfrac12 g t^2$; drag=0 yields total energy change < 1e-6.

### 2.4 Wind Field
- 3D vector field: Mean wind + Simplex turbulence + Thermal updrafts (Plume formula) + Ground boundary layer profile (log law $\bar{u}/u_* = (1/\kappa)\ln(z/z_0)$).
- **GPU Execution**: CuPy / torch grid batch computation; NumPy fallback when unavailable. Explicitly indicate via `backend` field in response.

---

## 3. Optimization Engine (Requirement B: Optimal Solution Search)

### 3.1 Objective Functions (Multiple Simultaneous, UI Weight Setting)
- `max_distance` Maximize horizontal flight distance
- `max_flight_time` Maximize flight time
- `max_ld_ratio` Maximize $L/D$
- `min_max_stress` Minimize wing surface max stress (structural safety)
- `max_stall_margin` Maximize stall margin
- `min_launch_speed` Minimize required launch speed
- `max_stability` Maximize static stability (neutral point optimization)

### 3.2 Search Algorithms (All Implemented, Selectable)
- **Coarse Grid Sweep**: Full evaluation of discrete grid (parallel).
- **Nelder-Mead** (scipy): Single objective, low evaluation count.
- **Differential Evolution** (`scipy.optimize.differential_evolution`): Single objective, default engine.
- **NSGA-II**: Custom implementation (non-dominated sorting + crowding distance + SBX crossover / polynomial mutation) → outputs **Pareto front**. Multi-objective (e.g., distance vs stress trade-off).
- **Surrogate Acceleration**: Fit $k$-NN / RBF approximation model on evaluated samples, select next evaluation point via expected improvement EI (reduce evaluation count).

### 3.3 Design Variables for Search
- Geometry: Wingspan $b$, wing area $S$, taper ratio $\lambda$, $AR$, dihedral, dart/glider shape flag
- Folding: Leading-edge / trailing-edge / wingtip / center fold angles (4 variables), elevon angle
- Material: Areal density (g/m²), paper stiffness, mass
- Launch: Launch angle, roll angle, initial velocity

### 3.4 Execution Mechanism & ETA (Integrated with Requirement D)
- Jobs managed by `JobManager` in `physics/job_manager.py` (thread-safe, `threading.RLock` + `dict`).
- Per-job fields: `job_id` (UUID4), `status` (`queued|running|completed|failed|cancelled`), `progress` (0.0–1.0), `evaluated_count`, `total_evaluations`, `started_at` (ISO8601), `updated_at`, `eta_seconds`, `estimated_completion` (ISO8601), `backend` (`multiprocessing|threading|serial`), `elapsed_seconds`, `evals_per_second`.
- **ETA Formula (Mandatory Implementation)**:
  - $\overline{t_{eval}} = \dfrac{\text{elapsed\_seconds}}{\max(\text{evaluated\_count}, 1)}$
  - $\text{eta\_seconds} = (\text{total\_evaluations} - \text{evaluated\_count}) \cdot \overline{t_{eval}}$
  - $\text{evals\_per\_second} = 1 / \overline{t_{eval}}$, $\text{estimated\_completion} = \text{now} + \text{eta\_seconds}$
- `evals_per_second` smoothed via exponential moving average ($\alpha = 0.3$) to prevent overestimation at start. Progress updated at 0.5s intervals.
- `POST /api/v1/optimizer/run` **immediately** returns `job_id` (non-blocking); `GET /api/v1/optimizer/status/<job_id>` retrieves progress/ETA; `POST /api/v1/optimizer/cancel/<job_id>` cancels; `GET /api/v1/optimizer/result/<job_id>` retrieves Pareto front, optimal params, objective values as JSON/CSV.
- Optimization uses `multiprocessing.Pool` for parallel evaluation (Windows `spawn` compatible: evaluation function at module level), shares worker config with `parallel_engine.py`.

---

## 4. 3D Model Import (Requirement C)

### 4.1 Supported Formats & Load Paths
- **glTF 2.0 / GLB** (`GLTFLoader`), **OBJ** (`OBJLoader`), **STL** (`STLLoader`, binary/ASCII both).
- Load methods: Browser file select, drag & drop, URL specification — all 3 routes feed same pipeline.

### 4.2 Auto Physical Property Extraction (`physics/mesh_domain.py`)
- Fully auto-compute from loaded geometry (`trimesh` dependency allowed; fallback to pure NumPy if absent):
  - Surface area $S$, wing area, span $b$, mean chord $\bar{c} = S/b$, taper ratio $\lambda$ from all vertex coordinates
  - Aspect ratio $AR = b^2/S$
  - CG $x_{cg}$ (volume centroid / area centroid switchable via `cg_mode`)
  - Inertia tensor $\mathbf{I}$ (tetrahedral integration), mass $m = \rho_{paper} \cdot \text{gsm} \cdot S_{sheet}$
  - Aero panel division: $N_x \times N_y$ panels, **panel normals, areas, aero lambdas** (static panel method including local $C_L$ per local $\alpha$ and local washout-induced rotational lift $C_{L,\mathrm{rot}}$)
- **Unit Correction**: Normalize mm/cm/m via `scale_hint`.
- **Manual Override UI**: Auto-computed values overridable (`override` flag).

### 4.3 Model Management
- Imported models server-cached (`models/`, UUID naming). `GET /api/v1/mesh/models` list, `POST /api/v1/mesh/select` select, `GET /api/v1/mesh/metrics/<model_id>` retrieve physical properties.
- 3D viewer reflects selected model mesh; stress heatmap dynamically updated as vertex attributes.
- Run optimization per model, **compare per-model Pareto fronts**.

---

## 5. Subagent Parallel Implementation (Requirement E)

**Your role is "Orchestrator & Integrator", not "Implementer"**. Launch subagents via `Task` tool per below policy.

### 5.1 Implementation Order
1. **Phase 0 (You)**: Finalize `physics/config_schema.py` all dataclasses / JSON schemas, `physics/geometry_utils.py` (quaternion, Euler conversion, cross product), directory structure, `requirements.txt`, `.gitignore`. Commit — this is the dependency root for all agents.
2. **Phase 1 (Parallel)**: Delegate independent modules to subagents simultaneously.
3. **Phase 2 (You)**: `app.py` (all API integration), UI integration, integration tests, Git operations.
4. **Phase 3 (Parallel, Polish)**: Request each subagent (via `task_id` resumption) to strengthen pytest and numerical verification for their modules.

### 5.2 Phase 1 Subagent Assignment Table (File-Exclusive, No Conflicts)

| Agent | Responsible Files | Mandatory Implementation |
|-------|------------------|-------------------------|
| `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | §2.2 airfoil, stall, Drag Polar, Trim, induced drag, Re correction |
| `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | §2.1 ISA, Sutherland, wind field, boundary layer, thermal, GPU (CuPy/torch) + NumPy fallback |
| `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | Dynamic pressure, G-force, wing root bending moment, vertex stress field (0–1 normalized heatmap) |
| `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | §4 glTF/OBJ/STL analysis, area/CG/inertia/AR computation, panel method, model cache |
| `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | §2.3 6DoF RK4, quaternion, adaptive sub-stepping, ring buffer, launch/landing judgment |
| `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | §3 Nelder-Mead/DE/NSGA-II/Surrogate/Pareto parallel evaluation |
| `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | §3.4 JobManager, ETA formula, progress management, JSON/CSV export |
| `ui-gpu-viz` | `templates/index.html`, `static/css/*.css`, `static/js/*.js` | §6 Google Clean UI, Three.js shaders, GPGPU wind particles, timeline, optimization panel, ETA display, 3D model loader |

### 5.3 Mandatory Instructions for Subagent Delegation
- Modify **only** assigned files (changes to other files prohibited).
- Do not deviate from dataclasses / function signatures defined in `physics/config_schema.py` (if deviation needed, report and await response).
- Strictly observe Zero-Placeholder: all functions must contain actual processing.
- Confirm `pytest tests/test_<module>.py -q` all PASS, and **report actual pytest output**.
- For environment-dependent operations (GPU etc.) use `backend` fallback; confirm NumPy path PASS in tests.

### 5.4 Phase 2 Your Responsibilities
- `app.py`: Integrate all REST endpoints (§7) per `config_schema.py`.
- Integration test `smoke_test.py`: Chain all APIs, verify optimization job submission → progress → ETA → result → CSV output, 3D model import → physical property retrieval.
- Final `pytest tests -q` Green confirmation and commit.

---

## 6. UI/UX Details (Reflection of Requirements A/B/C/D)

- **Color Palette**: Background `#F8F9FA` / Card `#FFFFFF` / Accent `#1A73E8` / Normal `#34A853` / High Load `#FBBC04` / Failure `#EA4335`. Rounded 8px, subtle shadow, Roboto / Google Sans fonts.
- **Header Bar**:
  - Title, Google Blue logo, wind tunnel toggle, 3D model load button, `JSON Save` / `JSON Load` / `CSV Export` buttons.
  - **Rightmost "ETA Widget"**: Display running job's `estimated_completion` as `HH:MM:SS`, remaining time (`mm:ss`), progress bar, job name (e.g., `Differential Evolution / Eval 312/1000`). On completion show `Completed 14:32:05`.
- **Left Panel**:
  - Aircraft section: Mass, areal density, stiffness, wingspan, wing area, taper ratio, dihedral, leading/trailing/wingtip/center fold angles, elevon angle. **All numeric input + slider combined** (with default display).
  - Environment section (Req A): Temperature [`°C`/`K`], Pressure [`Pa`/`hPa`], Humidity [`%`], wind speed, wind direction, turbulence intensity, thermal strength, gravity, ground friction, Drag Scale, Ground Effect ON/OFF, Compressibility ON/OFF. Changes immediately sent to server, reflected in next step.
  - 3D Model section: Format/file/URL select, model list, unit/scale hint, CG computation mode (volume/area), computed physical properties ($S$, $b$, $c$, $AR$, $\lambda$, $x_{cg}$, mass, $\mathbf{I}$) with per-value "auto/manual override" toggles.
- **Right Overlay**:
  - Flight status: Altitude, speed, AoA $\alpha$, sideslip $\beta$, roll/pitch/yaw, $L/D$, G-force, max stress, state (normal/high load/failure color-coded).
  - Chart.js graphs: Real-time line charts of altitude, speed, AoA, stress.
  - **Optimization Panel**: Objective checkboxes (§3.1), weight sliders, search algorithm select (Grid/Nelder-Mead/DE/NSGA-II/Surrogate), evaluation count, parallel processes, `Run`/`Cancel` buttons, **progress bar & ETA display**, Pareto front scatter plot (Chart.js scatter), optimal params table, `Load This Config` button.
- **Bottom Time Control Dock**: Play/pause, speed (0.25x/0.5x/1x/2x/5x/10x), frame step (forward/back), timeline seek.
- **Wind Tunnel Mode**: Toggle ON to fix wind speed, fix aircraft in wind tunnel, measure only aero coefficients.

---

## 7. REST API List (Confirmed Spec Reflecting Requirements A/B/C/D)

| Method | Endpoint | Content |
|--------|----------|---------|
| POST | `/api/v1/aerodynamics/calculate` | $C_L$, $C_D$, $L$, $D$, $C_m$, $L/D$, $C_{D,\min}$, $\alpha_{L/D,\max}$, stall judgment |
| POST | `/api/v1/aerodynamics/polar` | Drag Polar curve via $\alpha$ sweep |
| POST | `/api/v1/environment/atmosphere` | Set env params + immediate return density, kinematic viscosity, speed of sound |
| GET | `/api/v1/environment/current` | Get current env params |
| POST | `/api/v1/wind/field` | 3D wind field (GPU/NumPy, `backend` explicit) |
| POST | `/api/v1/stress/analysis` | Vertex stress field + wing root bending moment + G-force |
| POST | `/api/v1/simulation/step` | 6DoF 1-step (variable $\Delta t$, adaptive) |
| POST | `/api/v1/simulation/reset` | Simulation initialization |
| POST | `/api/v1/simulation/batch` | Parallel multi-simulation (`multiprocessing`) |
| POST | `/api/v1/mesh/import` | glTF/GLB/OBJ/STL import → `model_id` + physical properties |
| GET | `/api/v1/mesh/models` | Imported model list |
| POST | `/api/v1/mesh/select` | Select active model |
| GET | `/api/v1/mesh/metrics/<model_id>` | Get area, CG, inertia, AR, etc. |
| POST | `/api/v1/optimizer/run` | Start optimization → immediate `job_id` return (non-blocking) |
| GET | `/api/v1/optimizer/status/<job_id>` | Progress, ETA, estimated completion |
| POST | `/api/v1/optimizer/cancel/<job_id>` | Cancel optimization |
| GET | `/api/v1/optimizer/result/<job_id>` | Pareto front, optimal params (JSON/CSV) |
| GET | `/api/v1/jobs` | All jobs list (status, ETA) |
| POST | `/api/v1/data/export` | Export flight log / optimization results CSV/JSON |
| POST | `/api/v1/data/import` | Load settings / presets |

All endpoints return 400 with clear error message on invalid input; on 500, trace logged.

---

## 8. Execution Procedure (Follow This Order)

1. **Phase 0**: Create contract definitions (`config_schema.py`, `geometry_utils.py`, `requirements.txt`, `.gitignore`), verify with `python -m compileall`, commit.
2. **Phase 1**: Launch 8 subagents **simultaneously** via `Task` tool per §5.2 table. Files are exclusive, no conflicts. Verify each agent's completion report includes actual pytest output and implementation summary.
3. **Phase 2**: Cross-check each subagent's deliverables against `config_schema.py` contract (verify import & function signature consistency via `grep`). Request fixes from relevant agent if issues.
4. **Phase 3**: Integrate `app.py` (all APIs) + `templates/index.html` + `static/**` (Google Clean UI, GPU shaders, GPGPU wind particles, Time Control, Optimization Panel, ETA Display, 3D Loader).
5. **Phase 4**: `smoke_test.py` full chain verification (API chain, optimization job submit → progress → ETA → result, 3D model import → physical properties, CSV export).
6. **Phase 5**: `python -m pytest tests -q` all GREEN. Verify Google Clean design, responsive, animation performance.
7. **Phase 6**: `git status` check, commit only verified changes.
8. **Final Report**: Implementation file list, actual pytest output, API list, UI structure description, known constraints.
9. **Physics Investigation & Improvement Proposals**: When investigating physics, actively research current model improvement room and realistic high-fidelity options (with compute cost) and propose.

**At end of each step, report in format: "Execution command → Actual output → Next step". Fix unverifiable parts before proceeding.**

---

## 9. Final Absolute Compliance Checklist

- [ ] §0 A–E all implemented (no omissions, no pseudo)
- [ ] `physics/optimizer_domain.py` has Nelder-Mead / DE / NSGA-II / Surrogate all present
- [ ] `physics/job_manager.py` has ETA formula implemented, UI shows `estimated_completion`
- [ ] `physics/mesh_domain.py` handles glTF/GLB/OBJ/STL, auto-computes $S$, $b$, $c$, $AR$, $\lambda$, $x_{cg}$, $\mathbf{I}$
- [ ] Environment params (density, viscosity, gravity, drag_scale, etc.) changeable from UI via API
- [ ] `config_schema.py` all dataclasses provide type safety
- [ ] `pytest tests -q` all PASS (incl. analytical solution comparison tests)
- [ ] `smoke_test.py` all chains succeed
- [ ] 3D viewer (Three.js shaders + GPGPU wind particles) operates
- [ ] Google Clean design compliance
- [ ] Zero-Placeholder violations: zero