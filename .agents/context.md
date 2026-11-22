# Project Context Knowledge (Context Definition)
<!--
[Usage]
This file is a "fill-in template" for describing project-specific preconditions and constraints.
AI agents must reference this file before starting work to understand the project context and constraints.
When reusing for another project, rewrite this file's content for that project.
-->

## 1. Project Basic Information
- **Project Name**: paper_airplanes / 6DoF Flight Simulator v3
- **Overview & Purpose**: Building a 6-degree-of-freedom (6DoF) paper airplane flight simulation foundation based on physical laws, with optimization via Genetic Algorithm (GA)
- **Targets & Use Cases**: Simulation API, Web 3D Visualizer, Automated Design Optimization Pipeline
- **Primary Languages & Runtimes**: Python 3.10+, PowerShell, Node.js (Web Frontend)

## 2. Directory & Key Structure
- `physics/`: 6DoF physics computation engine, aerodynamics calculation, wind field models, GA optimization modules
- `api/`: Backend API for simulation execution and data streaming
- `tools/`: Operations & maintenance, branch monitoring, process hygiene, Discord integration scripts
- `app.py`: Application entry point
- `scripts/smoke_test.py`: Integrated smoke test execution script
- `docs/SPECIFICATION.md`: Detailed physics models & API specifications (design canonical source)

## 3. Project-Specific Guardrails & Traps
- **Branch Isolation**:
  - During `genetic-project` development phase, direct commits, merges, and PR creation to `master` / `main` are prohibited (automatically enforced by `tools/branch_guard.py`).
- **Physics & Coordinate System Rules**:
  - Sign conversion convention between wind field (+z up) and world NED coordinate system (+z down) ($w_{NED} = -w_{wind}$).
  - Wind is sampled at height from ground surface ($z_{up} = -z_{ned}$).
  - Horizontal distance 100km limit, instant death underground (`min_alt < -0.05`), CCD interpolation, wingtip dissipation cap maintenance.
- **Evaluation Conventions**:
  - Do not arbitrarily relabel failed evaluations to `timeout` or `crashed` etc. (reward hacking prevention).
  - GA defaults consolidated in `physics/ga_config.py`.

## 4. Verification & Execution Command Quick Reference
- **Syntax & Static Type Check**: `python -m compileall physics api tools app.py scripts/smoke_test.py` / `ruff check .` / `mypy physics api app.py scripts/smoke_test.py`
- **Unit & Integration Tests**: `python -m pytest tests -q` / `python scripts/smoke_test.py`
- **Process Management**: `python tools/process_hygiene.py --list` / `--apply`