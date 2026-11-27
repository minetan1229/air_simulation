# Physics Freeze & Slim Parameter Surface (`v1-frozen`)

## 1. Purpose

`air_simulation` is now scoped as a **lightweight, deterministic 6-DoF paper-airplane flight simulator and visualizer**.
Morphogenetic Quality-Diversity exploration (**MILCY**) has been separated into its own dedicated repository: [`minetan1229/Milcy-algorithm`](https://github.com/minetan1229/Milcy-algorithm).

To stop endless low-order aerodynamic whack-a-mole, the quasi-steady 6-DoF aerodynamic model in `physics/aero_kernel.py` is **frozen** as `v1-frozen`.

## 2. Parameter Partition of `AeroParams` (48 → 14 Public Core)

| Tier | Count | Fields | Policy |
| :--- | :---: | :--- | :--- |
| **Core Public (`CORE_AERO_FIELDS`)** | **14** | `area`, `chord`, `span`, `aspect`, `oswald`, `cL0`, `cl_alpha`, `cl_max`, `stall_alpha_deg`, `cd0`, `cm0`, `cm_alpha`, `cm_q`, `elevon_deg` | Primary user-facing & calibrated parameters (`CoreAeroParams`) |
| **Internal / Derived (`INTERNAL_AERO_FIELDS`)** | **13** | `cl_beta`, `cl_roll_rate`, `cn_beta`, `cn_yaw_rate`, `cy_beta`, `aileron_deg`, `rudder_deg`, `elevon_authority`, `stall_sharpness`, `ground_effect_enabled`, `lift_scale`, `drag_scale`, `asymmetry_cl` | Retained for 6-DoF lateral/directional stability and calibration hooks; not primary tuning knobs |
| **Ablation-Verified / Deprecated (`ABLATION_CANDIDATE_AERO_FIELDS`)** | **21** | `compressible`, `low_re_enabled`, `low_re_cl_alpha_gain`, `low_re_stall_gain`, `low_re_cl_max_gain`, `low_re_lsb_gain`, `low_re_cd0_gain`, `cm_roll_rate`, `cm_yaw_rate`, `cl_pitch_rate`, `cl_yaw_rate`, `cn_roll_rate`, `cn_pitch_rate`, `polhamus_kp`, `polhamus_kv`, `fold_trailing_deg`, `re_cd0_exponent`, `cg_offset_x`, `cp_shift_gain`, `ground_cushion_lift`, `ground_pitch_coupling` | Zeroed / disabled in `AeroParams.frozen_profile()`; kept only for backward compatibility with legacy tests |

## 3. Freeze Gate Policy

Do **not** add new analytical aerodynamic correction terms to `physics/aero_kernel.py`.
Modifications to the physics core are permitted **only** when one of the following holds:

1. **Conservation / Exploit Defect**: Negative drag, energy creation on ground contact, NaN/Inf divergence, or CPU/GPU parity failure.
2. **Sim-to-Real Falsification**: Real A4 flight measurements contradict predicted flight ranking and cannot be resolved via parameter calibration.

## 4. Verification

Run the ablation and freeze test suite:

```bash
python3 tools/ablation_aero_params.py
pytest tests/test_aero_ablation_freeze.py
```
