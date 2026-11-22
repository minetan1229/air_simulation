# Documentation Index (docs Index)

> [← Back to README](../README.md)

Document list in this directory. Canonical sources are consolidated into 3 documents:

- **Specification Canonical**: [`SPECIFICATION.md`](./SPECIFICATION.md) (Comprehensive theory, API, constants, literature sources)
- **Physics Models Canonical**: [`PHYSICS_MODELS.md`](./PHYSICS_MODELS.md) (Formula → Source → Canonical Module → Applicable Limits → Verification Tests)
- **Setup & Operation Canonical**: [`getting-started.md`](./getting-started.md) + [`usage.md`](./usage.md)

Others are categorized by role. File names follow historical inheritance (lowercase = legacy UI guides, uppercase = specs/audits) and are not renamed to prevent link breakage.

---

## Reading Guide (Routes by Purpose)

| You Are | Read In This Order |
| :--- | :--- |
| First Time User | [getting-started](./getting-started.md) → [usage](./usage.md) → [troubleshooting](./troubleshooting.md) |
| Developer / Fixer | [development](./development.md) → [architecture](./architecture.md) → [api](./api.md) → [SPECIFICATION §3](./SPECIFICATION.md) |
| Physics Modifier | [architecture §2](./architecture.md) → [PHYSICS_MODELS](./PHYSICS_MODELS.md) → [PHYSICS_ALGORITHM](./PHYSICS_ALGORITHM.md) → [SPECIFICATION §1](./SPECIFICATION.md) |
| GA Modifier | [architecture §3](./architecture.md) → [GA_ALGORITHM](./GA_ALGORITHM.md) → [GA_V2_ARCHITECTURE](./GA_V2_ARCHITECTURE.md) |
| High School / Beginner | [GA_HIGH_SCHOOL](./GA_HIGH_SCHOOL.md) → [PHYSICS_HIGH_SCHOOL](./PHYSICS_HIGH_SCHOOL.md) → [getting-started](./getting-started.md) |
| Operations / Server | [discord-bridge](./discord-bridge.md) → [REMOTE_COMPUTE_ENVIRONMENT](./REMOTE_COMPUTE_ENVIRONMENT.md) |

---

## 1. Getting Started & Operation (User-Facing)

| Document | When to Read | Status |
| :--- | :--- | :--- |
| [getting-started.md](./getting-started.md) | Setup / Launch / System Requirements | Current |
| [usage.md](./usage.md) | Screen Operation / Parameters / Optimization / I/O | Current |
| [troubleshooting.md](./troubleshooting.md) | Launch Failure / GPU Not Recognized / Rendering & Optimization Issues | Current |

## 2. Development & Operations (Developer / Operator)

| Document | When to Read | Status |
| :--- | :--- | :--- |
| [development.md](./development.md) | Testing / Git Conventions / Contribution | Current |
| [api.md](./api.md) | Calling REST Endpoints / Spec Verification | Current (Summary) |
| [architecture.md](./architecture.md) | Overall Structure / Data Flow / Physics Equations Overview | Current (Overview; formulas canonical in PHYSICS_MODELS) |
| [discord-bridge.md](./discord-bridge.md) | Discord Remote Control / Routing / Quota | Current |
| [REMOTE_COMPUTE_ENVIRONMENT.md](./REMOTE_COMPUTE_ENVIRONMENT.md) | Remote GPU Compute Node Build & Maintenance | Current (Secrets in `.env` only. No actual hosts, IDs, or passwords in tracked files) |

## 3. Physics & Specifications (Canonical & Design Documents)

| Document | When to Read | Status |
| :--- | :--- | :--- |
| [SPECIFICATION.md](./SPECIFICATION.md) | Cross-check specs, constants, laws (highest priority canonical) | Canonical |
| [PHYSICS_MODELS.md](./PHYSICS_MODELS.md) | Investigate coefficient/formula provenance and applicable limits | Canonical (Supplementary) |
| [GA_ALGORITHM.md](./GA_ALGORITHM.md) | Genetic Algorithm integrated architecture technical details | Canonical (New) |
| [PHYSICS_ALGORITHM.md](./PHYSICS_ALGORITHM.md) | Physics computation algorithms (aero, 6-DoF, stress, atmosphere, GPU) | Canonical (New) |
| [GA_HIGH_SCHOOL.md](./GA_HIGH_SCHOOL.md) | High-school level GA mechanism explanation (no formulas, diagram-focused) | Intro (New) |
| [PHYSICS_HIGH_SCHOOL.md](./PHYSICS_HIGH_SCHOOL.md) | High-school level physics simulation explanation (basic physics level) | Intro (New) |
| [CFD_ROADMAP.md](./CFD_ROADMAP.md) | Future plan for 3D Navier–Stokes addition (Issue #47) | Design Doc (no code changes) |

## 4. Optimization & Data

| Document | When to Read | Status |
| :--- | :--- | :--- |
| [GA_V2_ARCHITECTURE.md](./GA_V2_ARCHITECTURE.md) | Large-scale GA search (`physics/ga2/`) structure | Current |
| [PAPER_REGISTRY_PIPELINE.md](./PAPER_REGISTRY_PIPELINE.md) | Paper registry (2,000 entries) collection & operation | Current |
| [`../search/INDEX.md`](../search/INDEX.md) | Paper record index (40 chunks) | Data Index |

## 5. Audits & History (Reference Documents)

| Document | When to Read | Status |
| :--- | :--- | :--- |
| [CODE_REVIEW_FINDINGS.md](./CODE_REVIEW_FINDINGS.md) | Past reviews (374 items / 69 re-aggregated) records | History (911 lines, with TOC) |
| [`../PHYSICS_PAPER_CONFORMANCE_AUDIT.md`](../PHYSICS_PAPER_CONFORMANCE_AUDIT.md) | Paper conformance audit records (P0–P3 completion ledger) | History (placed at repo root) |
| [`../archive/`](../archive/) | Legacy audit/repair records (3 items) | Archive |

## 6. Agent Protocols & Legacy Wiki (Development Foundation)

| Document | When to Read | Status |
| :--- | :--- | :--- |
| [`../AGENTS.md`](../AGENTS.md) | Autonomous loop resident protocol (priority) | Current |
| [`../.agents/Project.md`](../.agents/Project.md) | Current State / Loop State | Current (for agent work) |
| [`../.agents/Knowledge.md`](../.agents/Knowledge.md) | Past Knowledge / Workarounds | Current (for agent work) |
| [`../skill/README.md`](../skill/README.md) | Skill Index (standard procedures) | Current |
| [`../wiki-docs/`](../wiki-docs/) | Legacy Wiki Mirror (Home / Getting-Started / Architecture / Development-Guide) | **Legacy**. Duplicate storage; prefer docs/ for reference |

---

## Screen Images

9 images in `images/`. README 2×2 table shows representative screens only; the following are also available.

| File | Content |
| :--- | :--- |
| `01_main_console.png` / `ui_main_console.png` | Main Console |
| `02_flight_trajectory.png` / `ui_flight_simulation.png` | Flight Trajectory |
| `03_wind_tunnel_mode.png` | Wind Tunnel Mode |
| `04_workbench_investigate.png` / `05_workbench_optimizer.png` / `06_workbench_3d_mesh.png` | Workbench (Investigate / Optimizer / 3D Mesh) |
| `07_ensemble_100_planes.png` | Ensemble 100 Planes |

---

[Back to README](../README.md)