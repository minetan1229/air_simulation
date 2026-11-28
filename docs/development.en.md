# Development & Testing Guide

This document describes the codebase maintenance and development workflow, unit test and comprehensive smoke test execution procedures, and coding conventions.

---

## 1. Core Development Principles

This project applies the following strict development policies:

1. **Zero-Placeholder Policy (No Stubs/Empty Implementations)**:
   `TODO`, `pass`, unimplemented empty functions, and dummy placeholders are completely prohibited. All functions, formulas, and APIs must be implemented as 100% working code.
2. **Small-Step & Verification Protocol (Staged Verification)**:
   After any code modification, syntax checks and unit tests (`pytest`) must be executed in the local environment, and verification must pass before proceeding to the next task.
3. **Safe Fallback Design**:
   Even in environments where GPU (CUDA / CuPy / PyTorch) is unavailable, the system must maintain a configuration that transparently falls back to CPU implementations (e.g., NumPy) without halting with exceptions.

---

## 2. Test Execution

### 2.1 Syntax Check
Syntax validation via Python bytecode compilation:

```powershell
python -m compileall physics app.py
```

### 2.2 Unit Tests (pytest)
Numerical verification for each physics module (aerodynamics, atmosphere, wind field, stress, 6DoF motion, mesh analysis, optimization, job management).

```powershell
# Quick run
python -m pytest tests -q

# Run specific domain tests
python -m pytest tests/test_aero.py -q
python -m pytest tests/test_mesh.py -q
python -m pytest tests/test_jobs.py -q
```

> [!NOTE]
> For domains with analytical solutions (ISA atmosphere, induced drag, Sutherland viscosity, mesh geometric properties), strict numerical tests with relative error $10^{-6} \sim 10^{-3}$ are performed.

### 2.3 Integrated Smoke Test
Verifies Flask API connectivity, optimization job execution with ETA calculation, and 3D model automatic analysis pipeline:

```powershell
python smoke_test.py
```

---

## 3. Git Conventions

Commit messages follow the prefix convention below:

- `feat(scope): ...` : New feature addition
- `fix(scope): ...` : Bug fix
- `refactor(scope): ...` : Refactoring (no functional change)
- `test(scope): ...` : Test addition/modification
- `docs(scope): ...` : Documentation improvement/fix

---

## 4. Discord Webhook Notification Script

A script is provided to notify Discord of work status (start, subagent active, complete, fail, warning, etc.):

```powershell
# Usage
python tools/notify_discord.py <start|subagent|end|fail|warning|question> <title> <summary> [extra_info]

# Example: Completion notification
python tools/notify_discord.py end "Refactoring Complete" "Documentation split and link verification all passed successfully."
```

---

## 5. Related Documents

- [Quick Start (docs/getting-started.md)](./getting-started.md)
- [System Architecture (docs/architecture.md)](./architecture.md)
- [REST API Specifications (docs/api.md)](./api.md)
- [Troubleshooting (docs/troubleshooting.md)](./troubleshooting.md)

---

[Back to README](https://github.com/minetan1229/air_simulation/blob/master/README.md)