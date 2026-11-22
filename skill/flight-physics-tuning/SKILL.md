---
name: flight-physics-tuning
description: Procedure for 6DoF flight dynamics, aerodynamic models (stall, ground effect, morphing, apex pushover), preset tuning, and physics bug analysis/diagnosis. Trigger on flight behavior anomaly reports or physics parameter tuning requests.
prerequisites:
  - physics/aero_domain.py
  - physics/flight_sim.py
  - physics/config_schema.py
  - Diagnostic scripts in tools/
---

# Flight Physics Tuning Skill

Procedure for diagnosing and tuning aerodynamic (Aero), 6-DoF equations of motion, passive aeroelastic morphing, and apex pushover behavior in the Paper Airplane Simulator.

## 1. Prerequisites
- Understanding of `physics/config_schema.py` physical dataclass structure
- Understanding of `physics/aero_domain.py` and `physics/flight_sim.py` computation flow
- Ability to utilize diagnostic scripts in `tools/`

## 2. Trigger Conditions
- Physics bug reports: "flight reverses unnaturally mid-air", "fails to transition to glide at apex", "stall behavior is off"
- Aircraft preset (Classic Dart, Suzanne, White Wings, etc.) parameter adjustments
- Instructions to improve math logic for morphing damping model, $C_m$, $C_L$, $C_D$

## 3. Step-by-Step Workflow

### Step 1: Current Behavior Timeline Extraction & Diagnosis
Run diagnostic scripts in `tools/` to verify time-series of pitch angle, AoA, velocity, morphing deformation.
```powershell
python tools/inspect_timeline.py
python tools/diagnose_horizontal_transition.py
```
- Verify $x_{cp}$ movement and morphing recovery $\delta_{\text{eff}}(q)$ function correctly at apex ($V < 2.0\text{ m/s}$).

### Step 2: Preset-Based Simulation Evaluation
Batch-simulate and score all presets for flight distance, flight time, max altitude.
```powershell
python tools/evaluate_all_presets.py
```

### Step 3: Physics Domain Unit Verification
Run tests for modified domains (aero, flight dynamics) to maintain theoretical consistency.
```powershell
python -m pytest tests/test_aero.py tests/test_flight.py -v
```

### Step 4: Physics Audit Test Execution
Verify physics law consistency (energy conservation, ground penetration prevention, drag minimum, stability derivatives, etc.).
```powershell
python -m pytest tests/test_physics_audit.py tests/test_physical_laws_audit.py -q
```

## 4. Constraints & Prohibitions
- **No Hardcoded Magic Numbers**: Physical parameters and correction coefficients must be defined in `config_schema.py` and be variable/transparent from API/UI.
- **No Fixed Clamping of Deformation Angle**: Passive aeroelastic morphing must maintain smooth sigmoid damping model based on material nonlinearity (per AGENTS.md §2.1).
- **Singularity Protection**: Must not cause division by zero in center-of-pressure calculation at $V \to 0$ or ultra-low speeds.