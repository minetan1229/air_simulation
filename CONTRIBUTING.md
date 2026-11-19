# Contributing Guide

This project targets "professional engineering product" standards, prioritizing physical rigor and reproducibility. Please review the following principles and procedures before contributing.

---

## 1. Commitments (Agreed Upon by Proposal)

1. **Zero-Placeholder Policy**: `TODO`, empty functions, and dummy placeholders are prohibited. All functions, formulas, and APIs must be implemented as 100% working code.
2. **Single Implementation Principle**: The same physics formula exists only in **one canonical function** in `physics/aero_kernel.py`. The scalar path (`aero_domain.py`) and GPU batch path (`gpu_dynamics.py`) must call the same function. Duplicate definitions are detected by CI parity checks.
3. **Physical Integrity (Texas Sharpshooter Fallacy Prevention)**: Explicitly declare hypotheses and falsifiable acceptance criteria in advance. Accepting accidental behavior as complete and introducing hardcoded constants for reward hacking (intentionally tuned values) are prohibited.
4. **Safe Fallback**: Even in environments without GPU (CUDA / CuPy / PyTorch), the system must not halt with exceptions but must transparently fall back to NumPy / `multiprocessing`. The response schema (`landed`, `crashed`, `status`, `horizontal_range_m`, etc.) must be maintained equivalently between GPU and CPU paths.
5. **Branch Isolation**: During the `genetic-project` phase, direct commits, merges, cherry-picks, and PR creation to `master` / `main` are prohibited (enforced by `tools/branch_guard.py`). All missions must be conducted on `mission/<slug>` branches and merged only upon completion.

---

## 2. Issue Filing

### 2.1 Bug Report

Please include all of the following:

- **Environment**: OS / Python version / GPU availability (result of `curl /api/v1/system/gpu`)
- **Reproduction Steps**: Minimal reproduction payload (`aircraft` / `environment` / `launch` JSON)
- **Expected vs Actual Behavior** (numerical discrepancies shown as relative error)
- **Related**: Relevant `id` from `GET /api/v1/meta/physics-references` (e.g., `helmbold_1942`)

### 2.2 Feature Request

- Clearly state the motivation, use cases, differences from alternatives, and impact scope on the physics model.

### 2.3 Physics Model Proposal

- **Formula** (LaTeX), **Source** (paper, NASA TN, etc.), **Canonical Module** candidate, **Applicable Limits** (Re / AR / Mach range), and **Verification Tests** (closed-form or comparison with measured values) are mandatory.
- Do not create new coefficients without sources. For values that cannot be implemented, record the reason for deferral and re-opening conditions in `docs/PHYSICS_MODELS.md`.

---

## 3. Pull Request Procedure

1. **Branch**: Create and work on `mission/<slug>` (or `mission/<slug>-<issue-number>` if an issue number exists).
2. **Plan & Todo First**: Before starting, establish and declare a Plan (policy and overview) and a Todo list (granular, concrete tasks).
3. **Commit Message Convention**:
   - `feat(scope): ...` New feature
   - `fix(scope): ...` Bug fix
   - `refactor(scope): ...` Refactoring (no functional change)
   - `test(scope): ...` Test addition/modification
   - `docs(scope): ...` Documentation improvement
4. **Verification (Mandatory)**:
   ```powershell
   python -m compileall physics api tools app.py scripts/smoke_test.py
   ruff check .
   mypy physics api app.py scripts/smoke_test.py
   python -m pytest tests -q
   python scripts/smoke_test.py
   ```
5. **PR Template**: Include changes, verification results, V&V impact, and screenshots (for UI changes).

---

## 4. Code Conventions

- **Type Annotations**: Public APIs (`physics/*`, `api/*`) must have type annotations (`mypy --config-file mypy.ini`).
- **Matrix Operations**: Fundamental physical quantities (mass, inertia tensor, etc.) must use `physics/geometry_utils.py`; do not replace matrices with principal values.
- **Coordinate System Conventions (Critical Traps)**:
  - Wind field (+z up) ⇔ World NED (+z down) sign conversion: $w_{\text{NED}} = -w_{\text{wind}}$ (`flight_sim.py::wind_vector` is canonical).
  - Wind is sampled at height from ground surface (`z_up = -z_ned`) (both 6DoF path and GPU batch path use the same convention).

---

## 5. Test Strategy

- For domains with analytical solutions (ISA atmosphere, induced drag, Sutherland viscosity, mesh geometric properties), perform strict numerical tests with relative error $10^{-6} \sim 10^{-3}$.
- Verify **parity** between GPU/CPU paths is bit-identical or within physical tolerance (e.g., `tools/check_aero_kernel_parity.py`).
- New physics models must include comparison tests against **independent reference solutions** (closed-form or wind tunnel data).

---

## 6. Discord / External Notifications

```powershell
python tools/notify_discord.py <start|subagent|end|fail|warning|question> "<title>" "<summary>"
```

See [docs/discord-bridge.md](./docs/discord-bridge.md) for details.

---

## 7. Related Documents

- [docs/development.md](./docs/development.md) Development & Testing Details
- [docs/PHYSICS_MODELS.md](./docs/PHYSICS_MODELS.md) Physics Models Canonical Reference
- [docs/VALIDATION.md](./docs/VALIDATION.md) Verification and Validation (V&V)
- [SECURITY.md](./SECURITY.md) Security Policy
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) Code of Conduct
- [CITATION.cff](./CITATION.cff) Academic Citation