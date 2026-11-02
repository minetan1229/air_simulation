# GitHub Copilot Instructions for Paper Airplane Simulation Project

## 1. Project Overview & Architecture
This project is an ultra-high precision 6DoF flight dynamics and aerodynamic simulation system for paper airplanes, featuring genetic algorithm (GA) optimization, WebGPU/GPU batch acceleration, and multi-fidelity aerodynamic models.

- **Primary language:** Python 3.11
- **Key modules:**
  - `physics/`: 6DoF RK4 integrator, aerodynamics, low-Reynolds strip theory, atmosphere model.
  - `api/`: FastAPI endpoints for simulation & GA job execution.
  - `static/`: Three.js / WebGPU visualization and interactive flight analysis.

## 2. Critical Physics & Engineering Constraints (Must Enforce)
When reviewing code, pay special attention to these strict physical and numerical rules:

1. **Coordinate Systems & Wind Conventions:**
   - World frame follows **NED (North-East-Down)** convention ($+z$ is downwards).
   - Wind vector is conventionally $+z$ upwards; vertical velocity conversion must strictly follow $w_{NED} = -w_{wind}$.
   - Altitude calculation must be relative to ground ($z_{up} = -z_{NED}$).
2. **Anti-Fallacy & Energy Conservation:**
   - Numerical compensation constants (hardcoded offsets to disguise simulation failures or energy blowups) are strictly prohibited.
   - Energy clamping must reflect true aerodynamic dissipation or ground contact restitution.
   - Sub-ground penetration check: $\text{altitude} < -0.05$ m must trigger immediate collision/landing dissipation.
3. **Multi-Fidelity Flight Pipeline:**
   - Low Reynolds number laminar separation bubble (LSB) hysteresis and strip theory must preserve physical parameters.
   - RANS surrogate models and dynamic damping derivatives must adhere to non-dimensionalized stability axes.
4. **Zero-Placeholder Policy:**
   - Never accept stub functions, `TODO`, `pass` placeholders, or mock return values in production simulation modules.

## 3. Code Quality & Verification Standards
- Maintain strict typing where applicable and pass `ruff check .` without ignoring errors.
- Ensure all public functions and mathematical formulas have clear docstrings explaining physical units (SI: m, s, rad, kg, N, N·m).
- When suggesting changes, provide concrete, production-ready unified diffs or code snippets.
