"""Data models, variables, and common structures for Auto Investigation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


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
