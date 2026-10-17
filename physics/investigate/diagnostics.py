"""Diagnosis engine for 6-DoF paper airplane flight trajectories."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ..config_schema import AircraftConfig, EnvironmentConfig, FlightLogSample, LaunchConfig
from .models import DiagnosisReport


def compute_flight_metrics(
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


def diagnose_flight(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    summary: Dict[str, Any],
    samples: List[FlightLogSample],
) -> DiagnosisReport:
    """Diagnose the primary aerodynamic failure or inefficiency of the current flight."""
    stall_alpha = abs(float(aircraft.stall_alpha_deg))
    metrics = compute_flight_metrics(summary, samples, stall_alpha)

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
