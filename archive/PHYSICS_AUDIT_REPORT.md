# Physics Model & Verification Audit Report

This document is a technical audit report that organizes and systematizes the physics model validity, verification contracts, applicability limits, assumptions, and independent verification results raised in GitHub Issues #9–#23.

---

## 1. Aerodynamic Models (Issues #9, #19, #20)

### 1.1 Lift, Drag, & Stall Model Formulation & Sources
- **Linear Regime (Pre-stall)**:
  - Helmbold / Jensen corrected lift slope for delta wings and low-aspect-ratio rectangular wings:
    $$C_{L\alpha} = \frac{2\pi AR}{2 + \sqrt{AR^2 (1 + \lambda) + 4 AR / \pi}}$$
  - Zero-AoA lift $C_{L0}$ and trim AoA from elevon/trailing-edge trim fold angle $\delta_{\text{elevon}}$:
    $$C_L(\alpha) = C_{L0} + C_{L\alpha}\alpha + \Delta C_{L,\text{elevon}}\delta_{\text{elevon}}$$
- **Stall Transition**:
  - Smooth blend from linear lift to flat-plate separation limit via sigmoid weighting function based on Viterna / Corrigan method.
  - Beyond stall angle $\alpha_{\text{stall}}$ (default $14^\circ$), lift slope gradually decreases, reaching max lift coefficient $C_{L,\max}$, then transitions to separated-flow regime.
- **Drag Polar**:
  - Zero-lift drag $C_{D0}(Re) = C_{D0,\text{ref}} \cdot (Re_{\text{ref}} / Re)^{0.15}$
  - Induced drag coefficient $C_{Di} = \frac{C_L^2}{\pi e AR} \cdot \kappa_{\text{ground}}(h/b)$
  - Post-separation flat-plate drag: Hoerner formula $C_{D,\text{sep}} = 2 \sin^2\alpha + C_{D0}\cos\alpha$
- **Coordinate Axis Definitions**:
  - Wind axes: Drag $D$ defined anti-parallel to relative velocity vector $\mathbf{V}_{\text{rel}}$; lift $L$ and side force $Y$ defined on plane orthogonal to AoA and sideslip angle.
  - Body axes: Forward $+x$, right wing $+y$, downward $+z$ (NED convention).

### 1.2 High AoA, Sideslip, & Reverse Flow Boundaries (Issue #20)
- $C_L, C_D, C_Y$ clamped/wrapped as continuous functions to avoid discontinuities, singularities, or NaN across $\alpha \in [-180^\circ, 180^\circ]$, $\beta \in [-90^\circ, 90^\circ]$.
- Reverse flow regime ($\alpha \approx \pm 180^\circ$): Lift asymptotically approaches zero; drag maintains positive value as blunt flat-plate drag.

### 1.3 Explicit Applicability Limits & Unverified Ranges
- **Applicable Range**: Reynolds number $10^3 \le Re \le 10^5$, Mach number $Ma < 0.3$ (incompressible to subsonic Prandtl-Glauert correction).
- **Unverified / Empirical Assumptions**:
  - Hysteresis loops from laminar separation bubbles (LSB) specific to ultra-low Reynolds numbers.
  - Quantitative calibration with actual wind tunnel measurements. Current parameters comply with thin-plate theory and known literature.

---

## 2. Attitude Stability & Pitch Moment Mechanics (Issues #10, #18)

### 2.1 Pitch Moment Physics Model
- Artificial attitude-restoring torques and horizontal-reference torques completely removed.
- Aerodynamic moment formulation:
  $$M = q S \bar{c} \cdot \left[ C_{m0} + C_{m\alpha}\alpha + C_{mq}\frac{q_{\text{pitch}}\bar{c}}{2V} + \Delta C_{m,\text{elevon}}\delta_{\text{elevon}} \right] + (x_{\text{cg}} - x_{\text{cp}}) \cdot L \cos\alpha$$
  - Static longitudinal stability condition: $C_{m\alpha} < 0$ and aerodynamic center $x_{\text{cp}}$ located aft of CG $x_{\text{cg}}$.
  - Pitch damping $C_{mq} < 0$: Pure damping torque proportional to angular velocity $q_{\text{pitch}}$.
- **Low-Speed Apex Pushover**:
  - At climb apex in ultra-low-speed regime ($V < 2.0\text{ m/s}$), aerodynamic torque smoothly converges to zero at zero relative wind speed limit to prevent moment divergence. Natural pushover (nose-down) via gravity and minute relative wind permitted.

---

## 3. Aeroelastic & Paper Deformation Models (Issues #11, #21)

### 3.1 Quasi-Steady Morphing Model
- Trailing-edge trim deflection damping per dynamic pressure $q = \frac{1}{2}\rho V^2$:
  $$\delta_{\text{eff}}(q) = \delta_{\text{trim}} \cdot \left[ \frac{1}{1 + (q / q_{\text{ref}})^{n_{\text{morph}}}} \right]$$
- **Adopted Assumptions**:
  - Current implementation adopts "quasi-static equilibrium" assumption.
  - Dynamic flutter (self-excited vibration), time delays, and hysteresis based on paper mass, inertia moments, and structural damping are not implemented (explicitly noted as constraints in Issue #21).

---

## 4. Atmosphere, Environment, & Wind Field Models (Issues #15, #16)

### 4.1 ISA Standard Atmosphere Model Applicability & Consistency
- **Temperature, Pressure, Density Consistency**:
  $$T(h) = T_0 - L h, \quad p(h) = p_0 \left(1 - \frac{L h}{T_0}\right)^{\frac{g}{L R_{\text{air}}}}, \quad \rho(h) = \frac{p(h)}{R_{\text{air}} T(h)}$$
  - Tropopause ($h = 11,000\text{ m}$) and above treated as isothermal layer; lower/upper bounds guarded to prevent non-finite values (NaN, Inf) even at negative or extreme altitudes.
  - Dynamic viscosity independently computed from $T$ only via Sutherland formula.

### 4.2 Wind Field, Turbulence, & Coordinate Systems
- **World Coordinate System**: $+x$ = East, $+y$ = North, $+z$ = Down.
- Wind direction angle $\psi_{\text{wind}}$ fully complies with meteorological convention (direction wind "blows from", clockwise from North).
- Identical random seed (`turbulence_seed`) guarantees full numerical reproducibility under identical initial conditions.

---

## 5. Ground Effect, Grounding, & Boundary Behavior (Issue #14)

### 5.1 Ground Effect
- Induced drag reduction coefficient based on McCormick model:
  $$\kappa_{\text{ground}}(h/b) = \frac{16 (h/b)^2}{1 + 16 (h/b)^2}$$
  - Induced drag asymptotically approaches zero as $h \to 0$; $\kappa \to 1.0$ as $h \gg b$; smooth bounded function.

### 5.2 Grounding Judgment & Rebound/Friction
- Grounding judged when aircraft lowest surface reaches ground surface ($z_{\text{ground}}$).
- Normal velocity damped rebound via restitution coefficient $e_{\text{restitution}} \in [0, 1]$; tangential velocity decelerated via friction coefficient $\mu_{\text{friction}}$ doing non-positive work. Artificial kinetic energy increase strictly excluded.

---

## 6. Numerical Integration, Timestep Convergence, & Surrogate Consistency (Issues #12, #13)

### 6.1 Timestep Convergence
- 6DoF integration via 4th-order Runge-Kutta (RK4).
- Comparison of fixed timesteps $\Delta t \in \{0.02, 0.01, 0.005\}\text{ s}$; retry steps (step halving) may occur in hand-launch mode with abrupt attitude changes. Full convergence criteria (within 1%) achievement status documented in `verification.md`.

### 6.2 Optimization Surrogate Model Consistency
- Distinction made between simplified evaluations used by optimization engines (NSGA-II, DE, Surrogate) during search and full 6DoF flight simulator evaluations.
- Final selected aircraft candidates must always be re-verified via full 6DoF simulation to eliminate objective function/constraint discrepancies.

---

## 7. Added Mass & Unsteady Aerodynamics Audit (Issue #17)

- **Audit Result**: Current code does **not implement** added mass or unsteady vortex theory (Theodorsen / Wagner functions).
- **Rationale**:
  - In paper airplane representative mass ratios and deceleration/acceleration regimes, introducing added mass terms carries high risk of double-counting with rigid body equations of motion mass matrix, potentially compromising computational stability. Current version maintains quasi-steady aerodynamics + damping derivative model.

---

## 8. Parameter Sensitivity & Uncertainty Evaluation (Issue #22)

- `physics/auto_investigate.py` and `tests/test_sensitivity_audit.py` compute sensitivity gradients (Jacobian) of flight metrics (distance, max altitude, flight time) to individual/combined perturbations of mass $m$, span $b$, elevon angle $\delta$, CG position $x_{\text{cg}}$, evaluating conclusion robustness.