"""Advice generation for paper airplane folding and throwing techniques."""

from __future__ import annotations

from typing import List

from ..config_schema import AircraftConfig, LaunchConfig
from .models import DiagnosisReport, SensitivityResult, TuningAdvice


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
