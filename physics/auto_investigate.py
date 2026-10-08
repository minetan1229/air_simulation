"""Auto-Investigate domain for the 3D Paper Airplane CFD Simulator.

This module implements the "Auto Investigation / Tuning Mode" which:
1. Freezes immutable physical properties (mass, paper GSM, stiffness, planform area, wingspan)
   because for a given real-world folded paper plane, those cannot be altered.
2. Focuses exclusively on variables that an experimenter can actually modify by hand:
   - Trim & fold angles: elevon (elevator), dihedral, trailing fold, tip fold, aileron, rudder, CG clip offset.
   - Throw technique: launch angle (elevation), launch roll, launch speed (push force).
3. Performs a baseline 6-DoF diagnostic flight to identify flight failure modes
   (nose dive, stall dive, spiral dive, loft energy waste, shallow glide).
4. Runs a 1-D parametric sensitivity scan across hand-adjustable variables to rank
   which adjustment has the highest leverage on distance or endurance.
5. Runs a multi-variable local tuning pass to produce a recommended configuration.
6. Generates actionable, plain-language origami and throwing advice explaining
   *why* and *how* to fold or throw the plane to make it fly significantly farther.
"""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    FlightLogSample,
    LaunchConfig,
    SimulationSettings,
    as_dict,
    clamp,
    deg2rad,
    derived_properties,
    from_dict,
    rad2deg,
    safe_div,
)
from .flight_sim import FlightSimulator, flight_from_aircraft

__all__ = [
    "AdjustableVariable",
    "ADJUSTABLE_VARIABLES",
    "DiagnosisReport",
    "SensitivityPoint",
    "SensitivityResult",
    "TuningAdvice",
    "InvestigationResult",
    "diagnose_flight",
    "run_sensitivity_scan",
    "run_focused_tuning",
    "generate_advice",
    "investigate_airplane",
]


# --------------------------------------------------------------------------
# Adjustable Variables Definition (Hand-modifiable parameters)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AdjustableVariable:
    """A parameter that an experimenter can physically modify on an existing plane."""

    name: str
    category: str  # "trim", "fold", "balance", "launch"
    label_en: str
    label_ja: str
    unit: str
    low: float
    high: float
    step: float
    target_object: str  # "aircraft" or "launch"
    description_ja: str


ADJUSTABLE_VARIABLES: Tuple[AdjustableVariable, ...] = (
    AdjustableVariable(
        name="elevon_deg",
        category="trim",
        label_en="Elevon Deflection",
        label_ja="エレボン角（昇降舵）",
        unit="deg",
        low=-4.0,
        high=18.0,
        step=1.0,
        target_object="aircraft",
        description_ja="主翼後縁を上下に反らせる角度。ピッチ姿勢と滑空迎角を直接制御します。",
    ),
    AdjustableVariable(
        name="dihedral_deg",
        category="trim",
        label_en="Dihedral Angle",
        label_ja="上反角（バンザイ角）",
        unit="deg",
        low=0.0,
        high=18.0,
        step=2.0,
        target_object="aircraft",
        description_ja="主翼をV字形に持ち上げる角度。横すべり時の復元力とスパイラル安定性を確保します。",
    ),
    AdjustableVariable(
        name="fold_trailing_deg",
        category="fold",
        label_en="Trailing Fold",
        label_ja="後縁キャンバー折り",
        unit="deg",
        low=0.0,
        high=25.0,
        step=2.5,
        target_object="aircraft",
        description_ja="翼の後縁全体の折り曲げ。翼型キャンバーと揚力係数に影響します。",
    ),
    AdjustableVariable(
        name="fold_tip_deg",
        category="fold",
        label_en="Winglet / Tip Fold",
        label_ja="翼端折り（ウイングレット）",
        unit="deg",
        low=0.0,
        high=40.0,
        step=5.0,
        target_object="aircraft",
        description_ja="主翼先端を上に立てる折り。直進安定性（フィン効果）を高め翼端渦を抑制します。",
    ),
    AdjustableVariable(
        name="aileron_deg",
        category="trim",
        label_en="Aileron Bias",
        label_ja="エルロン差動（ロール偏り補正）",
        unit="deg",
        low=-5.0,
        high=5.0,
        step=1.0,
        target_object="aircraft",
        description_ja="左右の翼の非対称な反り。機体の曲がり癖やロール傾きを矯正します。",
    ),
    AdjustableVariable(
        name="rudder_deg",
        category="trim",
        label_en="Rudder / Crease Angle",
        label_ja="ラダー角（垂直折り目）",
        unit="deg",
        low=-5.0,
        high=5.0,
        step=1.0,
        target_object="aircraft",
        description_ja="胴体後部やキールの方向舵角。旋回癖の矯正とヨー安定性を調整します。",
    ),
    AdjustableVariable(
        name="cg_offset_x",
        category="balance",
        label_en="CG Offset (Clip)",
        label_ja="重心オフセット（クリップ前後）",
        unit="m",
        low=-0.015,
        high=0.015,
        step=0.002,
        target_object="aircraft",
        description_ja="クリップ等の位置による重心の前後移動（+が後方、-が前方）。ピッチ安定余裕を微調整します。",
    ),
    AdjustableVariable(
        name="launch_angle_deg",
        category="launch",
        label_en="Launch Pitch Angle",
        label_ja="射出仰角（投げる角度）",
        unit="deg",
        low=-5.0,
        high=30.0,
        step=2.5,
        target_object="launch",
        description_ja="手から放すときの投射角度。上向きすぎると失速し、下すぎると早く接地します。",
    ),
    AdjustableVariable(
        name="launch_roll_deg",
        category="launch",
        label_en="Launch Bank Angle",
        label_ja="射出ロール角（投げる傾き）",
        unit="deg",
        low=-15.0,
        high=15.0,
        step=2.5,
        target_object="launch",
        description_ja="投射時の機体の水平度。傾いていると旋回しながらエネルギーを失います。",
    ),
    AdjustableVariable(
        name="launch_speed",
        category="launch",
        label_en="Launch Speed",
        label_ja="投擲初速（投げる勢い）",
        unit="m/s",
        low=5.0,
        high=24.0,
        step=1.5,
        target_object="launch",
        description_ja="手から押し出す初速。力加減によって到達高度と滑空開始条件が決まります。",
    ),
)


# --------------------------------------------------------------------------
# Diagnosis Data Structures
# --------------------------------------------------------------------------
@dataclass
class DiagnosisReport:
    issue_code: str  # "NOSE_DIVE", "STALL_DIVE", "SPIRAL_DIVE", "LOFT_STALL", "LOW_GLIDE", "STABLE_GLIDE"
    headline_ja: str
    headline_en: str
    description_ja: str
    flight_time_s: float
    distance_m: float
    max_altitude_m: float
    avg_ld_ratio: float
    max_aoa_deg: float
    min_aoa_deg: float
    max_roll_deg: float
    stall_occurred: bool
    stall_time_s: float


@dataclass
class SensitivityPoint:
    value: float
    score: float
    distance_m: float
    flight_time_s: float


@dataclass
class SensitivityResult:
    name: str
    category: str
    label_en: str
    label_ja: str
    unit: str
    baseline_val: float
    best_val: float
    impact_score_pct: float
    description_ja: str
    curve: List[Dict[str, float]]


@dataclass
class TuningAdvice:
    priority: int
    category: str
    target_param: str
    title_ja: str
    action_ja: str
    reason_ja: str
    orig_val: float
    recommended_val: float
    delta_str: str
    unit: str


@dataclass
class InvestigationResult:
    target_metric: str
    baseline: Dict[str, Any]
    diagnosis: Dict[str, Any]
    sensitivities: List[Dict[str, Any]]
    recommended: Dict[str, Any]
    advice_list: List[Dict[str, Any]]
    summary: Dict[str, Any]


# --------------------------------------------------------------------------
# Flight Simulation Runner Helper
# --------------------------------------------------------------------------
def _simulate_trajectory(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration_s: float = 8.0,
    dt_max: float = 0.02,
) -> Tuple[Dict[str, Any], List[FlightLogSample]]:
    """Run full 6-DoF simulator and return summary and sample history."""
    settings = SimulationSettings(
        dt_max=dt_max,
        dt_min=min(1.0e-5, dt_max),
        max_substeps=64,
        duration_s=duration_s,
        record_interval_s=dt_max,
        time_scale=1.0,
        adaptive=True,
        collision_enabled=True,
        restitution=0.15,
        ring_buffer_capacity=max(1024, int(duration_s / dt_max) + 64),
    )
    sim = FlightSimulator(aircraft, environment, launch, settings)
    summary = sim.simulate(duration_s=duration_s, record=True)
    samples = sim.history()
    return summary, samples


def _compute_flight_metrics(
    summary: Dict[str, Any], samples: List[FlightLogSample], stall_alpha_deg: float
) -> Dict[str, Any]:
    """Compute rich trajectory metrics from simulation samples."""
    distance = 0.0
    max_alt = 0.0
    sum_ld = 0.0
    count_ld = 0
    max_aoa = -999.0
    min_aoa = 999.0
    max_roll = 0.0
    stall_occurred = False
    stall_time_s = -1.0

    prev: Optional[FlightLogSample] = None
    for s in samples:
        if prev is not None:
            distance += math.hypot(s.x_m - prev.x_m, s.y_m - prev.y_m)
        prev = s

        if s.altitude_m > max_alt:
            max_alt = s.altitude_m

        if s.speed_mps > 1.0 and abs(s.ld_ratio) < 50.0:
            sum_ld += abs(s.ld_ratio)
            count_ld += 1

        if s.alpha_deg > max_aoa:
            max_aoa = s.alpha_deg
        if s.alpha_deg < min_aoa:
            min_aoa = s.alpha_deg

        roll_mag = abs(s.roll_deg)
        if roll_mag > max_roll:
            max_roll = roll_mag

        if not stall_occurred and s.alpha_deg >= stall_alpha_deg and s.time_s > 0.05:
            stall_occurred = True
            stall_time_s = s.time_s

    flight_time = float(summary.get("flight_time_s", 0.0))
    avg_ld = (sum_ld / count_ld) if count_ld > 0 else 0.0
    if max_aoa == -999.0:
        max_aoa = 0.0
    if min_aoa == 999.0:
        min_aoa = 0.0

    return {
        "distance_m": float(distance),
        "flight_time_s": float(flight_time),
        "max_altitude_m": float(max_alt),
        "avg_ld_ratio": float(avg_ld),
        "max_aoa_deg": float(max_aoa),
        "min_aoa_deg": float(min_aoa),
        "max_roll_deg": float(max_roll),
        "stall_occurred": bool(stall_occurred),
        "stall_time_s": float(stall_time_s),
        "status": str(summary.get("status", "landed")),
        "sample_count": len(samples),
    }


# --------------------------------------------------------------------------
# Diagnostic Engine
# --------------------------------------------------------------------------
def diagnose_flight(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    summary: Dict[str, Any],
    samples: List[FlightLogSample],
) -> DiagnosisReport:
    """Diagnose the primary aerodynamic failure or inefficiency of the current flight."""
    stall_alpha = abs(float(aircraft.stall_alpha_deg))
    metrics = _compute_flight_metrics(summary, samples, stall_alpha)

    ftime = metrics["flight_time_s"]
    dist = metrics["distance_m"]
    max_alt = metrics["max_altitude_m"]
    avg_ld = metrics["avg_ld_ratio"]
    max_aoa = metrics["max_aoa_deg"]
    min_aoa = metrics["min_aoa_deg"]
    max_roll = metrics["max_roll_deg"]
    stalled = metrics["stall_occurred"]
    stall_t = metrics["stall_time_s"]

    # 1. NOSE DIVE: Early crash (< 1.6s) with continuous nose-down pitch / negative AoA
    if ftime < 1.6 and (max_aoa < 3.0 or min_aoa < -4.0):
        return DiagnosisReport(
            issue_code="NOSE_DIVE",
            headline_ja="急激な頭下げ墜落（ピッチ不足）",
            headline_en="Nose Dive (Insufficient Pitch Trim)",
            description_ja=(
                f"機体が射出直後（{ftime:.2f}秒）に頭から地面に突き刺さっています。"
                "エレボンの跳ね上げ角が不足しているか、重心が前方に偏りすぎて十分な揚力迎角が得られていません。"
            ),
            flight_time_s=ftime,
            distance_m=dist,
            max_altitude_m=max_alt,
            avg_ld_ratio=avg_ld,
            max_aoa_deg=max_aoa,
            min_aoa_deg=min_aoa,
            max_roll_deg=max_roll,
            stall_occurred=stalled,
            stall_time_s=stall_t,
        )

    # 2. SPIRAL DIVE: Severe roll / bank angle exceeding 40 degrees resulting in rapid spiral crash
    if max_roll > 40.0 and ftime < 3.0:
        return DiagnosisReport(
            issue_code="SPIRAL_DIVE",
            headline_ja="スパイラル横転墜落（ロール不安定）",
            headline_en="Spiral Dive (Roll / Dihedral Instability)",
            description_ja=(
                f"最大ロール角が{max_roll:.1f}°に達し、横転しながら墜落しています。"
                "主翼の上反角が不足しているため横すべり時の復元力が働いておらず、左右のアンバランスによって急旋回降下を起こしています。"
            ),
            flight_time_s=ftime,
            distance_m=dist,
            max_altitude_m=max_alt,
            avg_ld_ratio=avg_ld,
            max_aoa_deg=max_aoa,
            min_aoa_deg=min_aoa,
            max_roll_deg=max_roll,
            stall_occurred=stalled,
            stall_time_s=stall_t,
        )

    # 3. STALL DIVE: Severe stall early in flight leading to loss of lift
    if stalled and stall_t < 1.5:
        return DiagnosisReport(
            issue_code="STALL_DIVE",
            headline_ja="初期失速と高度喪失（迎角過大）",
            headline_en="Early Stall Dive (Excessive Pitch-up)",
            description_ja=(
                f"射出後{stall_t:.2f}秒で迎角が失速限界（{stall_alpha:.1f}°）を超過し、揚力を失って落下しています。"
                "エレボンの上げすぎ、または発射仰角が高すぎるために機首が上がりすぎています。"
            ),
            flight_time_s=ftime,
            distance_m=dist,
            max_altitude_m=max_alt,
            avg_ld_ratio=avg_ld,
            max_aoa_deg=max_aoa,
            min_aoa_deg=min_aoa,
            max_roll_deg=max_roll,
            stall_occurred=stalled,
            stall_time_s=stall_t,
        )

    # 4. LOFT STALL: Thrown too high, wasting kinetic energy climbing vertically then stalling
    if launch.launch_angle_deg > 20.0 and stalled:
        return DiagnosisReport(
            issue_code="LOFT_STALL",
            headline_ja="急上昇による運動エネルギー散逸",
            headline_en="Loft Energy Waste & Apex Stall",
            description_ja=(
                f"投射角が{launch.launch_angle_deg:.1f}°と高すぎるため、上空高く打ち上がった頂点で速度ゼロになり失速しています。"
                "前進力に変えるべき速度エネルギーを無駄な上昇で失っています。"
            ),
            flight_time_s=ftime,
            distance_m=dist,
            max_altitude_m=max_alt,
            avg_ld_ratio=avg_ld,
            max_aoa_deg=max_aoa,
            min_aoa_deg=min_aoa,
            max_roll_deg=max_roll,
            stall_occurred=stalled,
            stall_time_s=stall_t,
        )

    # 5. LOW GLIDE: Flight is stable but glide ratio is mediocre (< 4.5)
    if avg_ld < 4.5:
        return DiagnosisReport(
            issue_code="LOW_GLIDE",
            headline_ja="滑空比不足・沈下率過大",
            headline_en="Low Glide Ratio (Suboptimal Lift-to-Drag)",
            description_ja=(
                f"飛行姿勢は比較的安定していますが、平均滑空比が{avg_ld:.2f}と低く、すぐに接地してしまいます。"
                "トリム迎角が最良滑空角からずれており、翼の効率を十分に引き出せていません。"
            ),
            flight_time_s=ftime,
            distance_m=dist,
            max_altitude_m=max_alt,
            avg_ld_ratio=avg_ld,
            max_aoa_deg=max_aoa,
            min_aoa_deg=min_aoa,
            max_roll_deg=max_roll,
            stall_occurred=stalled,
            stall_time_s=stall_t,
        )

    # 6. STABLE GLIDE: Good flight
    return DiagnosisReport(
        issue_code="STABLE_GLIDE",
        headline_ja="安定した滑空飛行",
        headline_en="Stable Gliding Flight",
        description_ja=(
            f"飛行時間{ftime:.2f}秒、飛距離{dist:.2f}mで安定して飛行しています。"
            "さらなる飛距離・滞空時間の延伸のため、微小なトリムと投擲角度の最適化余地を探索します。"
        ),
        flight_time_s=ftime,
        distance_m=dist,
        max_altitude_m=max_alt,
        avg_ld_ratio=avg_ld,
        max_aoa_deg=max_aoa,
        min_aoa_deg=min_aoa,
        max_roll_deg=max_roll,
        stall_occurred=stalled,
        stall_time_s=stall_t,
    )


# --------------------------------------------------------------------------
# Parametric Sensitivity Scan (1-D Sweeps)
# --------------------------------------------------------------------------
def _get_target_metric(metrics: Dict[str, Any], target_metric: str) -> float:
    if target_metric == "flight_time":
        return float(metrics.get("flight_time_s", 0.0))
    # Default is horizontal distance
    return float(metrics.get("distance_m", 0.0))


def run_sensitivity_scan(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    target_metric: str = "distance",
    sample_steps: int = 5,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[SensitivityResult]:
    """Perform a 1-D sweep for every adjustable variable to evaluate sensitivity."""
    results: List[SensitivityResult] = []

    # Baseline run
    base_summary, base_samples = _simulate_trajectory(aircraft, environment, launch, duration_s=6.0)
    base_metrics = _compute_flight_metrics(
        base_summary, base_samples, abs(float(aircraft.stall_alpha_deg))
    )
    base_score = _get_target_metric(base_metrics, target_metric)

    total_tasks = len(ADJUSTABLE_VARIABLES) * sample_steps
    completed = 0

    for var in ADJUSTABLE_VARIABLES:
        # Determine sweep points
        cur_val = getattr(
            aircraft if var.target_object == "aircraft" else launch, var.name
        )
        cur_val = float(cur_val)

        # Generate sample values across [var.low, var.high], ensuring current value is included
        span_vals = np.linspace(var.low, var.high, sample_steps).tolist()
        if not any(abs(v - cur_val) < 1e-4 for v in span_vals):
            span_vals.append(cur_val)
        span_vals = sorted(set(round(v, 4) for v in span_vals))

        curve_points: List[Dict[str, float]] = []
        best_val = cur_val
        best_score = -1.0
        min_score = 1e9
        max_score = -1e9

        for val in span_vals:
            # Create mutated configs
            mod_air = copy.deepcopy(aircraft)
            mod_lau = copy.deepcopy(launch)

            if var.target_object == "aircraft":
                setattr(mod_air, var.name, val)
            else:
                setattr(mod_lau, var.name, val)

            summary, samples = _simulate_trajectory(mod_air, environment, mod_lau, duration_s=6.0)
            met = _compute_flight_metrics(
                summary, samples, abs(float(mod_air.stall_alpha_deg))
            )
            score = _get_target_metric(met, target_metric)

            curve_points.append(
                {
                    "value": float(val),
                    "score": float(score),
                    "distance_m": float(met["distance_m"]),
                    "flight_time_s": float(met["flight_time_s"]),
                }
            )

            if score > best_score:
                best_score = score
                best_val = val
            if score > max_score:
                max_score = score
            if score < min_score:
                min_score = score

            completed += 1
            if progress_callback:
                progress_callback(
                    completed, total_tasks, f"Scanning {var.label_ja} ({val}{var.unit})"
                )

        # Impact score percentage: how much variation this single knob causes
        score_spread = max(0.0, max_score - min_score)
        impact_pct = (
            (score_spread / max(base_score, 0.5)) * 100.0 if base_score > 0 else 0.0
        )

        results.append(
            SensitivityResult(
                name=var.name,
                category=var.category,
                label_en=var.label_en,
                label_ja=var.label_ja,
                unit=var.unit,
                baseline_val=cur_val,
                best_val=best_val,
                impact_score_pct=round(impact_pct, 1),
                description_ja=var.description_ja,
                curve=curve_points,
            )
        )

    # Sort descending by impact percentage
    results.sort(key=lambda r: r.impact_score_pct, reverse=True)
    return results


# --------------------------------------------------------------------------
# Multi-Variable Focused Optimization (Local Tuning)
# --------------------------------------------------------------------------
def run_focused_tuning(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    target_metric: str = "distance",
    max_evaluations: int = 50,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[AircraftConfig, LaunchConfig, Dict[str, Any], List[FlightLogSample]]:
    """Run a focused local optimizer only modifying adjustable variables."""
    # We focus on the most influential core knobs: elevon, dihedral, cg_offset_x, launch_angle, launch_speed
    tuning_vars = [
        v
        for v in ADJUSTABLE_VARIABLES
        if v.name
        in (
            "elevon_deg",
            "dihedral_deg",
            "fold_tip_deg",
            "cg_offset_x",
            "launch_angle_deg",
            "launch_speed",
        )
    ]

    base_air = copy.deepcopy(aircraft)
    base_lau = copy.deepcopy(launch)

    # Vector packing
    init_x = []
    bounds = []
    for v in tuning_vars:
        val = getattr(
            base_air if v.target_object == "aircraft" else base_lau, v.name
        )
        init_x.append(float(val))
        bounds.append((float(v.low), float(v.high)))

    init_x = np.array(
        [clamp(init_x[i], bounds[i][0], bounds[i][1]) for i in range(len(init_x))],
        dtype=float,
    )

    eval_count = 0
    best_score = -1.0
    best_x = init_x.copy()

    def objective(x: np.ndarray) -> float:
        nonlocal eval_count, best_score, best_x
        eval_count += 1

        mod_air = copy.deepcopy(base_air)
        mod_lau = copy.deepcopy(base_lau)

        for i, v in enumerate(tuning_vars):
            clipped = clamp(float(x[i]), bounds[i][0], bounds[i][1])
            if v.target_object == "aircraft":
                setattr(mod_air, v.name, clipped)
            else:
                setattr(mod_lau, v.name, clipped)

        summary, samples = _simulate_trajectory(mod_air, environment, mod_lau, duration_s=6.0)
        met = _compute_flight_metrics(
            summary, samples, abs(float(mod_air.stall_alpha_deg))
        )
        score = _get_target_metric(met, target_metric)

        if score > best_score:
            best_score = score
            best_x = x.copy()

        if progress_callback:
            progress_callback(
                eval_count,
                max_evaluations,
                f"Tuning iteration {eval_count}/{max_evaluations}",
            )

        # Negate for minimization
        return -float(score)

    # Quick Nelder-Mead with clipped parameters
    from scipy.optimize import minimize

    try:
        minimize(
            objective,
            init_x,
            method="Nelder-Mead",
            bounds=bounds,
            options={"maxiter": max_evaluations, "disp": False},
        )
    except Exception:
        # Fallback to init if Nelder-Mead fails
        pass

    # Build final recommended configs
    rec_air = copy.deepcopy(base_air)
    rec_lau = copy.deepcopy(base_lau)
    for i, v in enumerate(tuning_vars):
        final_val = clamp(float(best_x[i]), bounds[i][0], bounds[i][1])
        if v.target_object == "aircraft":
            setattr(rec_air, v.name, round(final_val, 2))
        else:
            setattr(rec_lau, v.name, round(final_val, 2))

    rec_summary, rec_samples = _simulate_trajectory(rec_air, environment, rec_lau)
    return rec_air, rec_lau, rec_summary, rec_samples


# --------------------------------------------------------------------------
# Natural Language Advice Generation
# --------------------------------------------------------------------------
def generate_advice(
    diagnosis: DiagnosisReport,
    base_air: AircraftConfig,
    rec_air: AircraftConfig,
    base_lau: LaunchConfig,
    rec_lau: LaunchConfig,
    sensitivities: List[SensitivityResult],
) -> List[TuningAdvice]:
    """Generate human-oriented, actionable origami and throw tuning guidance."""
    advice_list: List[TuningAdvice] = []
    priority_counter = 1

    # 1. Elevon Advice
    d_elevon = rec_air.elevon_deg - base_air.elevon_deg
    if abs(d_elevon) >= 0.5:
        if d_elevon > 0:
            action = f"主翼の後縁（エレボン）を約 +{abs(d_elevon):.1f}° 上向きに反らせる"
            reason = (
                "翼の後ろ側を指の腹や爪で軽く上に反らせてください。"
                "上向きのピッチングモーメントが生まれ、頭下げ墜落を防いで良好な滑空迎角を保ちます。"
            )
        else:
            action = f"主翼の後縁（エレボン）の跳ね上げを約 {abs(d_elevon):.1f}° 平らに戻す"
            reason = (
                "エレボンが立ちすぎて失速を起こしています。"
                "少し平らにならすことで過剰な迎角と抗力を抑え、スピードに乗った伸びのある滑空になります。"
            )
        advice_list.append(
            TuningAdvice(
                priority=priority_counter,
                category="fold",
                target_param="elevon_deg",
                title_ja="エレボン（主翼後縁）の反り調整",
                action_ja=action,
                reason_ja=reason,
                orig_val=base_air.elevon_deg,
                recommended_val=rec_air.elevon_deg,
                delta_str=f"{d_elevon:+.1f}°",
                unit="°",
            )
        )
        priority_counter += 1

    # 2. Dihedral Advice
    d_dihedral = rec_air.dihedral_deg - base_air.dihedral_deg
    if abs(d_dihedral) >= 1.0:
        if d_dihedral > 0:
            action = f"主翼の上反角を約 +{abs(d_dihedral):.1f}° 深くする（V字にする）"
            reason = (
                "主翼を両端から少し持ち上げ、浅いV字形（バンザイの形）にします。"
                "横すべりが発生した際に自動で水平に戻る復元力が高まり、横転墜落（スパイラルダイブ）を強力に防止します。"
            )
        else:
            action = f"主翼の上反角を約 {abs(d_dihedral):.1f}° 平らに近づける"
            reason = (
                "翼を少し水平に戻します。"
                "過剰な上反角による横揺れ抵抗を減らし、純粋な直進揚力を最大化します。"
            )
        advice_list.append(
            TuningAdvice(
                priority=priority_counter,
                category="fold",
                target_param="dihedral_deg",
                title_ja="上反角（主翼のV字角）の調整",
                action_ja=action,
                reason_ja=reason,
                orig_val=base_air.dihedral_deg,
                recommended_val=rec_air.dihedral_deg,
                delta_str=f"{d_dihedral:+.1f}°",
                unit="°",
            )
        )
        priority_counter += 1

    # 3. Launch Pitch Angle Advice
    d_pitch = rec_lau.launch_angle_deg - base_lau.launch_angle_deg
    if abs(d_pitch) >= 1.5:
        if d_pitch < 0:
            action = f"投げる角度を {abs(d_pitch):.1f}° 下げ、水平前方に押し出す"
            reason = (
                f"現在は斜め上に投げすぎて失速しています。目線とほぼ平行〜わずかに上（{rec_lau.launch_angle_deg:.1f}°）を狙い、"
                "前へスッと押し出すように投げることで、初速エネルギーを効率よく前進力に変えられます。"
            )
        else:
            action = f"投げる角度を +{abs(d_pitch):.1f}° 少し高くして押し出す"
            reason = (
                f"少し斜め上（約{rec_lau.launch_angle_deg:.1f}°）を狙って押し出してください。"
                "初期高度を稼ぐことで、その後の滑空時間を大幅に延ばすことができます。"
            )
        advice_list.append(
            TuningAdvice(
                priority=priority_counter,
                category="launch",
                target_param="launch_angle_deg",
                title_ja="投擲仰角（投げる角度）の調整",
                action_ja=action,
                reason_ja=reason,
                orig_val=base_lau.launch_angle_deg,
                recommended_val=rec_lau.launch_angle_deg,
                delta_str=f"{d_pitch:+.1f}°",
                unit="°",
            )
        )
        priority_counter += 1

    # 4. Launch Speed Advice
    d_speed = rec_lau.launch_speed - base_lau.launch_speed
    if abs(d_speed) >= 1.0:
        if d_speed > 0:
            action = f"腕の振りを強め、初速を +{abs(d_speed):.1f} m/s 勢いよく投げる"
            reason = (
                "機体に十分な揚力を発生させるため、しっかり指先で挟んで前方に押し出してください。"
                "速度に乗せることで翼が本来の滑空性能を発揮します。"
            )
        else:
            action = f"力を抜き、初速を {abs(d_speed):.1f} m/s 抑えてふわりと押し出す"
            reason = (
                "力みすぎて機体が空中で暴れています。力を抜き、"
                "紙飛行機を滑らせるように柔らかくリリースしてください。"
            )
        advice_list.append(
            TuningAdvice(
                priority=priority_counter,
                category="launch",
                target_param="launch_speed",
                title_ja="投擲スピード（投げる力加減）の調整",
                action_ja=action,
                reason_ja=reason,
                orig_val=base_lau.launch_speed,
                recommended_val=rec_lau.launch_speed,
                delta_str=f"{d_speed:+.1f} m/s",
                unit="m/s",
            )
        )
        priority_counter += 1

    # 5. Center of Gravity (Clip) Advice
    d_cg = rec_air.cg_offset_x - base_air.cg_offset_x
    if abs(d_cg) >= 0.002:
        d_mm = d_cg * 1000.0
        if d_cg < 0:
            action = f"機首側にゼムクリップ等をつけて重心を約 {abs(d_mm):.1f} mm 前に出す"
            reason = (
                "先端に小さなクリップを挟むか、折り返しを厚くして重心をわずかに前に移してください。"
                "機首上げによる失速癖を根本から抑え、安定した直線飛行になります。"
            )
        else:
            action = f"重心を約 {abs(d_mm):.1f} mm 後ろにずらす（先端の重りを少し減らす）"
            reason = (
                "重心が前過ぎて滑空角が急になっています。先端の重みを少し後ろ寄りに分散させることで、"
                "沈下率が下がりフワリとしたロングフライトになります。"
            )
        advice_list.append(
            TuningAdvice(
                priority=priority_counter,
                category="balance",
                target_param="cg_offset_x",
                title_ja="重心位置（クリップ・おもり）の調整",
                action_ja=action,
                reason_ja=reason,
                orig_val=base_air.cg_offset_x,
                recommended_val=rec_air.cg_offset_x,
                delta_str=f"{d_mm:+.1f} mm",
                unit="mm",
            )
        )
        priority_counter += 1

    # If no major delta was triggered, provide general guidance
    if not advice_list:
        advice_list.append(
            TuningAdvice(
                priority=1,
                category="general",
                target_param="elevon_deg",
                title_ja="微細なトリム調整",
                action_ja="現在のセッティングはほぼ最適です。左右の対称性を丁寧に点検してください",
                reason_ja="主翼の左右の歪みがないか機首正面から見通し、ねじれを直すことでさらに直進安定性が高まります。",
                orig_val=base_air.elevon_deg,
                recommended_val=base_air.elevon_deg,
                delta_str="±0.0",
                unit="°",
            )
        )

    return advice_list


# --------------------------------------------------------------------------
# Main Entry Point: Complete Auto-Investigation Orchestrator
# --------------------------------------------------------------------------
def investigate_airplane(
    aircraft: AircraftConfig,
    environment: Optional[EnvironmentConfig] = None,
    launch: Optional[LaunchConfig] = None,
    target_metric: str = "distance",
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> InvestigationResult:
    """Run full automated investigation pipeline: Diagnosis -> Sensitivity -> Tuning -> Advice."""
    env = environment if environment is not None else EnvironmentConfig()
    lau = launch if launch is not None else LaunchConfig()

    # Step 1: Baseline Trajectory & Diagnosis
    base_summary, base_samples = _simulate_trajectory(aircraft, env, lau)
    base_metrics = _compute_flight_metrics(
        base_summary, base_samples, abs(float(aircraft.stall_alpha_deg))
    )
    diagnosis = diagnose_flight(aircraft, env, lau, base_summary, base_samples)

    if progress_callback:
        progress_callback(10, 100, f"診断完了: {diagnosis.headline_ja}")

    # Step 2: Parametric Sensitivity Scan (1-D Sweeps)
    def scan_cb(curr: int, tot: int, msg: str) -> None:
        if progress_callback:
            # Map 0..tot -> 10..60%
            pct = 10 + int((curr / max(tot, 1)) * 50)
            progress_callback(pct, 100, msg)

    sensitivities = run_sensitivity_scan(
        aircraft, env, lau, target_metric=target_metric, progress_callback=scan_cb
    )

    # Step 3: Focused Multi-Variable Tuning
    def tune_cb(curr: int, tot: int, msg: str) -> None:
        if progress_callback:
            # Map 0..tot -> 60..90%
            pct = 60 + int((curr / max(tot, 1)) * 30)
            progress_callback(pct, 100, msg)

    rec_air, rec_lau, rec_summary, rec_samples = run_focused_tuning(
        aircraft, env, lau, target_metric=target_metric, max_evaluations=40, progress_callback=tune_cb
    )
    rec_metrics = _compute_flight_metrics(
        rec_summary, rec_samples, abs(float(rec_air.stall_alpha_deg))
    )

    # Step 4: Advice Generation
    advice_list = generate_advice(
        diagnosis, aircraft, rec_air, lau, rec_lau, sensitivities
    )

    if progress_callback:
        progress_callback(100, 100, "調査完了")

    # Metric improvements
    base_dist = base_metrics["distance_m"]
    rec_dist = rec_metrics["distance_m"]
    dist_gain_pct = (
        ((rec_dist - base_dist) / max(base_dist, 0.1)) * 100.0 if base_dist > 0 else 0.0
    )

    base_time = base_metrics["flight_time_s"]
    rec_time = rec_metrics["flight_time_s"]
    time_gain_pct = (
        ((rec_time - base_time) / max(base_time, 0.1)) * 100.0 if base_time > 0 else 0.0
    )

    # Samples downsampled for lightweight JSON payload (up to 120 points)
    def downsample(samples: List[FlightLogSample], max_pts: int = 120) -> List[Dict[str, float]]:
        if not samples:
            return []
        step = max(1, len(samples) // max_pts)
        pts = samples[::step]
        if pts[-1] != samples[-1]:
            pts.append(samples[-1])
        return [
            {
                "time_s": round(s.time_s, 3),
                "x_m": round(s.x_m, 3),
                "y_m": round(s.y_m, 3),
                "altitude_m": round(s.altitude_m, 3),
                "speed_mps": round(s.speed_mps, 2),
                "alpha_deg": round(s.alpha_deg, 2),
                "roll_deg": round(s.roll_deg, 2),
                "pitch_deg": round(s.pitch_deg, 2),
                "ld_ratio": round(s.ld_ratio, 2),
            }
            for s in pts
        ]

    baseline_payload = {
        "aircraft": as_dict(aircraft),
        "launch": as_dict(lau),
        "metrics": base_metrics,
        "track": downsample(base_samples),
    }

    recommended_payload = {
        "aircraft": as_dict(rec_air),
        "launch": as_dict(rec_lau),
        "metrics": rec_metrics,
        "track": downsample(rec_samples),
    }

    summary_payload = {
        "target_metric": target_metric,
        "distance_gain_pct": round(dist_gain_pct, 1),
        "time_gain_pct": round(time_gain_pct, 1),
        "base_distance_m": round(base_dist, 2),
        "rec_distance_m": round(rec_dist, 2),
        "base_flight_time_s": round(base_time, 2),
        "rec_flight_time_s": round(rec_time, 2),
        "primary_issue": diagnosis.headline_ja,
    }

    return InvestigationResult(
        target_metric=target_metric,
        baseline=baseline_payload,
        diagnosis=as_dict(diagnosis),
        sensitivities=[as_dict(s) for s in sensitivities],
        recommended=recommended_payload,
        advice_list=[as_dict(a) for a in advice_list],
        summary=summary_payload,
    )
