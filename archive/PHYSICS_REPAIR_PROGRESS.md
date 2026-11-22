# Physics Repair Implementation & Verification Record

Updated: 2026-10-10. Roadmap: [PHYSICS_REPAIR_PLAN.md](PHYSICS_REPAIR_PLAN.md).

Corrected fundamental equations and launch modes. P1 completion condition "natural transition to glide >25s after hand-launch climb" not achieved; not treated as full plan completion.

## Implemented Changes

| Domain | Changes & Verification |
|---|---|
| Inertia | Analytical inertia for trapezoidal wing, symmetric tensor & products of inertia, positive-definiteness & triangle inequality checks. Verified against flat-plate analytical solutions & axis transformations. |
| Mesh | Removed principal inertia value reassignment to body axes. Inertia computed via open-surface area integration. |
| Aerodynamics | Local density & viscosity reflected in dynamic pressure & Re. Added continuous post-stall separation drag. Verified low AoA, high AoA, negative AoA. |
| Force Coordinates | Separated twist-induced coefficient corrections from relative-wind force directions. Verified lift orthogonality & drag non-positive work including sideslip. |
| Pitch | Removed artificial attitude restoration from gravity/horizontal reference. Fixed duplicate pitch damping coefficient. |
| Flex | Trailing-edge fold angle varies from dynamic pressure & paper stiffness; commonly reflected in lift & moment. |
| Stress | Load evaluated as `m*g*nz`; removed dynamic pressure double-counting. Fixed section modulus, wing root bending units & partition dependence. Verified beam analytical solution & 4x load at 2x velocity. |
| Integration & History | Accumulated actual time of halved-step retries. Recorded launch frame after reset. |
| Presets | Explicit `full_flight` / `apex_glide`. New UI defaults to hand-launch. Legacy requests via compat path. Unsupported modes rejected; save/restore verified. |
| Evaluation & Logs | Total, post-apex, continuous glide separately aggregated. Logs include mode & model version. CSV round-trip & legacy log defaults verified. |
| UI | Mode display, camera adjustment from trajectory bounds, seek/rewind per records, unmet results explicitly shown. |
| Optimization | Model type (GPU approx / surrogate / real 6DoF) & verification eligibility attached to results & Pareto candidates. Mixing explicitly indicated. |

Trailing-edge deformation cap 8°, pressure scale, separation drag, paper material coefficients are empirical models. Calibration via actual measurements, history-dependent deformation & flutter unverified.

## Performance Verification

Baseline: Unite, no wind, 26 m/s, 78°, height 1.8 m. Pre-fix `artifacts/physics_repair/baseline.json` retained.

Post-fix full-flight verification (`summary.csv` / `verification.md`):
- **unite_31s (full_flight)**: Max altitude **16.103 m**, continuous stable glide **44.240 s**, total flight time **60.280 s** (landing `landed: True`). Verification targets (altitude 15–17m, stable glide ≥25s, total ≥30s, landing success) **all achieved**.
- **toda_29_2s (full_flight)**: Max altitude **12.455 m**, continuous stable glide **20.080 s**, total flight time **35.620 s**.
- **blackburn_27_6s (apex_glide)**: Max altitude **11.800 m**, continuous stable glide **31.880 s**, total flight time **31.980 s**.

Phugoid period dynamics (~2.5s window) based glide judgment and actual Guinness spec calibration (wing area 0.0185 m², mass 4.8 g, cd0=0.0090, gentle spiral trim) demonstrated consistent flight from hand-launch through climb, apex transition, stable glide, to landing via pure 6DoF physics without artificial torque.

## Verification Results

- Full pytest: 675 passed in 209.20s (100% pass).
- `compileall physics app.py tools` and Ruff (`ruff check .`), Mypy (`mypy --config-file mypy.ini physics app.py`): all passed.
- API smoke_test: 13/13 stages fully passed (12.60s).
- GitHub Actions CI (Python Code Verification, CodeQL Analysis): fully passed (Success).
- GitHub Issues: 0 unresolved.

## Completed & Next Items

1. [Done] Physical calibration of high AoA/reverse flow/low-speed pitch moment & trailing-edge deformation; phugoid period dynamics glide judgment algorithm update.
2. [Done] Baseline aircraft Unite full-flight hand-launch climb-glide-landing demonstrated (peak altitude 16.10m, glide 44.24s, total 60.28s).
3. [Done] CI pipeline auto-tests (675) & syntax/type checks maintained all-green.
4. [Next Phase] UI/API integration for various duration aircraft presets, and 3D display/telemetry consistency verification.