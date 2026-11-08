# Physics Roadmap — Low-Re, Panel Method, Aeroelastics

This document is the forward plan. Current canonical implementations live in
`docs/PHYSICS_MODELS.md` and `docs/SPECIFICATION.md` (no duplication of formulas here).

## Priorities

| ID | Item | Priority |
|---|---|---|
| R-P1 | Low-Re LSB table interpolation (replace logistic) | P1 direct |
| P-P2 | Low-order panel / VLM multi-fidelity final check | P2 post-GA |
| A-P3 | Thin-paper equivalent torsional-stiffness beam + divergence | P3 future |

## R-P1: Reynolds dependence

Current: `physics/aero_kernel.py::_log_Re` + 5 factors (Re 1e3–1e5, zero ≥1e5).
Verified: `tools/check_aero_kernel_parity.py` 0.0, `test_low_re_aerodynamics.py` + `test_issue68_reynolds_audit.py` 26 passed.
Next: monotonic cubic table (Pelletier & Mueller Fig. 5 data) for
`cl_alpha, alpha_stall, cl_max, cd0`, with extrapolation clamp identical to
`_log_Re` (flat outside envelope). Acceptance: table vs logistic max diff <2%,
parity 0.0 preserved. Sources: Pelletier & Mueller 2000; Sunada et al. 2002.

## P-P2: Low-order panel method (VLM)

Cost: `O(N^2)` dense influence solve. N=200 panels → 4e4 entries, ~ms on CPU;
N=2000 → 4e6 entries, ~0.1–1 s. For GA (≥1e4 evals) this is prohibitive.
Proposal: multi-fidelity — GA runs on the current 6DoF/Helmbold kernel,
panel/VLM runs **only on the final elite** (top K≤20) as cross-check.
No GA fitness change. Panel code must call `aero_kernel` boundary conditions,
not duplicate them. Acceptance: elite `|ΔCL|<0.05`, `|ΔCD|<0.005`.

## A-P3: Aeroelasticity — equivalent torsional-stiffness beam

Current: quasi-steady morphing + `aeroelastic_flex/twist/compliance` gains
(unvalidated empirical). Next: model each wing as a cantilever beam with
`GJ = E·J`, `J ≈ (b·t^3)/6` (thin closed/keel section approximate), modal
truncation (first bending + first torsion), and quasi-static divergence
check `q_div ~ GJ / (e·S·c^2)`. Requires real `E_MD/E_CD` and fold-section
`J` measurement — do not invent constants (AGENTS §2). Acceptance: flutter
onset not predicted (quasi-static only); divergence Mach/Re reported as
indicator, not a certified limit.

## Guardrails

- Single aero implementation: `physics/aero_kernel.py` (AGENTS §1/§6).
- No source-less constants; every new coefficient needs a cited paper or measurement.
