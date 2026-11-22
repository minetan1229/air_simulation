---
name: verification-pipeline
description: Standard verification runbook covering syntax check, unit/physics verification, E2E smoke test, and Discord notification for the 3D Paper Airplane Simulator. Trigger on code changes, pre-commit quality gate checks, or "run tests"/"syntax check"/"smoke test" instructions.
prerequisites:
  - Python 3.11+
  - pytest
  - tools/notify_discord.py
---

# Verification Pipeline Skill

Standard verification runbook for the 3D Paper Airplane Simulator covering syntax validation, unit/physics verification, E2E integration test, and Discord notification.

## 1. Prerequisites
- Virtual environment (Python 3.11+) activated
- Required dependencies (`requirements.txt`) installed
- (Optional) `DISCORD_WEBHOOK_URL` set in `.env`

## 2. Trigger Conditions
- Code changes in `tests/`, `physics/`, `app.py`, `static/`, etc.
- Pre-commit/push quality gate check
- Instructions like "run tests", "syntax check", "run smoke test"

## 3. Step-by-Step Workflow

### Step 1: Syntax Check (Syntax Compilation)
Batch-verify no syntax errors or broken imports across all Python source code.
```powershell
python -m compileall physics app.py smoke_test.py tools
```
- Verify exit code is 0.

### Step 2: Lightweight Smoke Tests (Fast Smoke Tests)
Quick check of major API routing, schema, and atmosphere model consistency.
```powershell
python smoke_test.py --stage health+schemas --stage environment
```

### Step 3: Full E2E Smoke Test (Full E2E Smoke Test)
Verify end-to-end connectivity including optimization jobs, mesh import, and 6DoF simulation.
```powershell
python smoke_test.py
```

### Step 4: Physics & Domain Unit Tests (Domain Pytest)
Run specific subdomains or full test suite as needed.
```powershell
# Specific domain quick test examples
python -m pytest tests/test_aero.py tests/test_flight.py -q

# Full suite (takes several minutes due to 600+ physics numerical integrations; async or staged execution recommended)
python -m pytest tests -q
```

### Step 5: Discord Notification (Notification)
Notify critical test results or work completion status.
```powershell
# Success
python tools/notify_discord.py end "Test Verification Complete" "All syntax checks and E2E smoke tests passed."

# Failure
python tools/notify_discord.py fail "Test Verification Failed" "Assertion error occurred in specific stage."
```

## 4. Constraints & Prohibitions
- **No Test Skipping**: Must not weaken assertions or comment them out to force pass when errors occur.
- **Zero-Placeholder Policy**: No `TODO` or dummy functions left in tests or scripts.
- **Async Command Management**: Full pytest includes 600+ physics numerical integrations and may take several minutes. Use PowerShell `WaitMsBeforeAsync` settings and task management tools to avoid duplicate execution and machine load.