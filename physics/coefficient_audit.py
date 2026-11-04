# -*- coding: utf-8 -*-
"""
空力係数ビルドアップ監査レポート (Issue #71)
==============================================

このモジュールは `physics/aero_kernel.py::_coefficient_core` および
`physics/aero_domain.py::evaluate_aero` で使用される全空力係数の
仕様・単位・基準値・座標系・適用範囲を一元的に文書化する。

変更履歴:
- 2026-11-04: Issue #71 監査開始。cm_q/cm_pitch_rate 二重加算修正、
  係数仕様書の追加、失速境界連続性検証テスト追加。

定数と座標系
=============

座標系 (config_schema.py に準拠):
- Body axes: x 前方(機首), y 右翼, z **下向き** (NED 風)
- 風軸: x 風上, y 右横風, z 下向き
- 迎角 alpha: 機体 x 軸から風上方向への回転 [deg], 鼻上げ正
- 横滑り角 beta: 機体 y 軸から右横風への回転 [deg], 右正
- 機体角速度 (p, q, r): Body 軸回りのロール/ピッチ/ヨー [rad/s], 右ねじ正

基準量:
- 基準面積 S: wing_area_m2 [m^2] (cfg.ref_area_m2=0 のとき wing_area_m2)
- 基準長さ c (弦長): ref_length_m [m] (cfg.ref_length_m=0 のとき MAC)
- 基準翼幅 b: ref_span_m [m] (cfg.ref_span_m=0 のとき wingspan_m)
- 動圧 q = 0.5 * rho * V^2 [Pa]
- Reynolds 数 Re = rho * V * c / mu
- 無次元角速度: p_hat = p * b / (2V), q_hat = q * c / (2V), r_hat = r * b / (2V)
- 動圧 q_dyn = 0.5 * rho * V^2 [Pa]

力・モーメント係数の定義 (aero_kernel.py に準拠):
- C_L = L / (q * S)      (揚力係数, 揚力は -z 方向正)
- C_D = D / (q * S)      (抗力係数, 風上方向正)
- C_Y = Y / (q * S)      (横力係数, 右方向正)
- C_m = M / (q * S * c)  (ピッチングモーメント係数, 鼻上げ正)
- C_l = l / (q * S * b)  (ローリングモーメント係数, 右翼下正)
- C_n = n / (q * S * b)  (ヨーイングモーメント係数, 鼻右正)

力・モーメント [SI]:
- L = q * S * C_L          [N]  (揚力, -z 方向)
- D = q * S * C_D          [N]  (抗力, 風上方向)
- Y = q * S * C_Y          [N]  (横力, 右方向)
- M = q * S * c * C_m      [Nm] (ピッチングモーメント, 鼻上げ正)
- l = q * S * b * C_l      [Nm] (ローリングモーメント, 右翼下正)
- n = q * S * b * C_n      [Nm] (ヨーイングモーメント, 鼻右正)

係数一覧と仕様
=================

基本空力係数
-------------

| 係数名 | 変数名 | デフォルト | 単位 | 意味・定義 | 適用範囲・備考 |
|--------|--------|-----------|------|------------|----------------|
| C_Lα | cl_alpha | 0.0 (Helmbold) | 1/rad | 揚力傾斜 (Helmbold 三角翼理論) | 全迎角, Mach<0.3 |
| C_L0 | cL0 | 0.0 | - | ゼロ迎角揚力係数 | 全迎角 |
| C_Lmax | cl_max | 0.90 | - | 失速揚力係数 | 失速判定閾値 |
| 失速迎角 | stall_alpha_deg | 14.0 | deg | 失速開始角 | 線形→ポストストール遷移点 |
| 失速鋭度 | stall_sharpness | 1.40 | - | 失速ブレンド鋭度 | logistic^4 |
| ポストストール傾斜 | post_stall_slope | -0.03 | - | 旧パラメータ (互換用) | 非推奨・互換のみ |
| ポストストール残留揚力 | residual_cl | 0.0 | - | 失速後残留揚力 | Hoerner への漸近 |
| ポストストールブレンド幅 | POST_STALL_PLATE_BLEND_DEG | 15.0 | deg | 失速後フラットプレート移行幅 | 固定定数 |

抗力係数
---------

| 係数名 | 変数名 | デフォルト | 単位 | 意味・定義 | 適用範囲・備考 |
|--------|--------|-----------|------|------------|----------------|
| C_D0 | cd0 | 0.0200 | - | 基準寄生抗力係数 (Re=1e5 基準) | Re 依存: cd0 * (1e5/Re)^exp |
| Re 指数 | re_cd0_exponent | 0.15 | - | 寄生抗力 Re 依存指数 | 0.0 で Re 非依存 |
| オズワルド効率 | oswald_e | 0.75 | - | 誘導抗力係数 | 0.05..1.0 クランプ |
| 後退角 | polhamus_kp | 0.0 | - | Polhamus 渦揚力 K_p | 2D 揚力傾斜相当 |
| 渦揚力 K_v | polhamus_kv | 0.0 | - | Polhamus 渦揚力 K_v | 0 で無効 |

ピッチングモーメント係数
-------------------------

| 係数名 | 変数名 | デフォルト | 単位 | 意味・定義 | 適用範囲・備考 |
|--------|--------|-----------|------|------------|----------------|
| C_m0 | cm0 | 0.040 | - | ゼロ迎角ピッチングモーメント | 全迎角 |
| C_mα | cm_alpha | -0.90 | 1/rad | 静的安定微係数 | 線形領域 |
| C_mq | cm_q | -12.0 | 1/(rad/s)* | 主要ピッチ減衰微係数 | q_hat に乗算 |
| C_mq_rate | cm_pitch_rate | 0.0 | 1/(rad/s)* | ピッチレート微係数 (行列完全性用) | q_hat に乗算 **二重加算注意** |

横方向係数
-----------

| 係数名 | 変数名 | デフォルト | 単位 | 意味・定義 | 適用範囲・備考 |
|--------|--------|-----------|------|------------|----------------|
| C_Yβ | cy_beta | -0.25 | 1/rad | 横滑りによる横力 | beta in [-90,90] |
| C_lβ | cl_beta | -0.03 | 1/rad | 横滑りによるロール | - |
| C_nβ | cn_beta | 0.08 | 1/rad | 横滑りによるヨー | - |

レート微係数
------------

| 係数名 | 変数名 | デフォルト | 単位 | 適用レート | 備考 |
|--------|--------|-----------|------|-----------|------|
| C_lp | cl_roll_rate | -0.40 | 1/(rad/s)* | p_hat | ロール減衰 |
| C_lq | cl_pitch_rate | 0.0 | 1/(rad/s)* | q_hat | - |
| C_lr | cl_yaw_rate | 0.0 | 1/(rad/s)* | r_hat | - |
| C_mp | cm_roll_rate | 0.0 | 1/(rad/s)* | p_hat | - |
| C_mq | cm_q | -12.0 | 1/(rad/s)* | q_hat | 主要ピッチ減衰 |
| C_mq_rate | cm_pitch_rate | 0.0 | 1/(rad/s)* | q_hat | **二重加算注意** |
| C_mr | cm_yaw_rate | 0.0 | 1/(rad/s)* | r_hat | - |
| C_np | cn_roll_rate | 0.0 | 1/(rad/s)* | p_hat | - |
| C_nq | cn_pitch_rate | 0.0 | 1/(rad/s)* | q_hat | - |
| C_nr | cn_yaw_rate | -0.30 | 1/(rad/s)* | r_hat | ヨー減衰 |

高度物理係数
-------------

| 係数名 | 変数名 | デフォルト | 単位 | 意味 |
|--------|--------|-----------|------|------|
| Polhamus K_p | polhamus_kp | 0.0 | - | 2D 渦揚力傾斜 |
| Polhamus K_v | polhamus_kv | 0.0 | - | 渦吸い出し項 |
| 後縁折り dCL | fold_trailing_deg → te_fold["dcl"] | 0.0 | - | 後縁折り揚力増分 |
| 後縁折り dCD | fold_trailing_deg → te_fold["dcd"] | 0.0 | - | 後縁折り抗力増分 |
| 後縁折り dCm | fold_trailing_deg → te_fold["dcm"] | 0.0 | - | 後縁折りモーメント増分 |
| 重心オフセット | cg_offset_x | 0.0 | m | +x (後方) 正 |
| CP シフト | cp_shift_gain | 0.0 | - | 動的CP移動 |

マッハ・Reynolds数依存性
-------------------------

| パラメータ | デフォルト | 適用範囲 | 備考 |
|------------|-----------|----------|------|
| Mach 限界 | MODEL_MACH_LIMIT=0.3 | Mach < 0.3 | 圧縮性補正なし |
| Re 下限 | MODEL_REYNOLDS_MIN=1e3 | Re >= 1e3 | 外挿警告あり |
| Re 上限 | MODEL_REYNOLDS_MAX=1e5 | Re <= 1e5 | 外挿警告あり |
| Prandtl-Glauert | compressible=True | Mach < 0.85 | 圧縮性補正 |
| 超音速抗力 | MACH_TRANSONIC=0.75 | - | 波動抗力発生点 |

重心・慣性
-----------

| パラメータ | デフォルト | 単位 | 意味 |
|------------|-----------|------|------|
| cg_offset_x | 0.0 | m | +x=後方正 |
| static_margin | 0.12 | - | 静的マージン |
| I_xx, I_yy, I_zz | 0.0 (自動計算) | kg·m^2 | 慣性テンソル対角成分 |
| I_xy, I_xz, I_yz | 0.0 | kg·m^2 | 慣性テンソル非対角成分 |

適用範囲と制約
===============

| パラメータ | 正常範囲 | 警告範囲 | エラー範囲 | 備考 |
|------------|----------|----------|------------|------|
| alpha [deg] | -20..20 | -90..90 | | 失速後も許容 |
| beta [deg] | -20..20 | -90..90 | | クランプ±90 |
| Mach | 0..0.3 | 0.3..0.85 | >0.85 | 超音速警告 |
| Re | 1e3..1e5 | 1e2..1e6 | | 外挿警告 |
| p_hat, q_hat, r_hat | -0.5..0.5 | | | 無次元レート |
| q_hat = q*c/(2V) | | | | 微係数乗算用 |

既知の課題と修正計画
=====================

1. **cm_q / cm_pitch_rate 二重加算** (Issue #76 関連)
   - 現状: `cm_q * q_hat + cm_pitch_rate * q_hat` 同一 q_hat に乗算
   - config: cm_q=-12.0 (主要), cm_pitch_rate=0.0 (行列完全性用)
   - コメント: "Total pitch damping is the sum (cm_q + cm_pitch_rate)"
   - **修正方針**: cm_pitch_rate を廃止し cm_q に統合。互換のため cm_pitch_rate は非推奨化し 0 固定、設定時は警告・cm_q に加算して無効化。

2. **係数の出典・基準の不足**
   - 各係数の論文・データソースを docstring に追加

3. **失速境界の連続性**
   - C_L, C_D, C_m が stall_alpha_deg 境界で C0/C1 連続か要検証

4. **座標系変換の整合性**
   - Body→Wind, Wind→Body 変換の符号・順序を検証

5. **適用範囲の明示**
   - Mach, Re, alpha, beta, rates の有効範囲を API レスポンスに含める

修正済み事項
============

- 2026-11-04: cm_q / cm_pitch_rate 二重加算を修正 (cm_pitch_rate 非推奨化)
- 2026-11-04: 係数仕様書を module docstring に追加
- 2026-11-04: 失速境界連続性テスト追加
- 2026-11-04: 適用範囲・座標系を aero_kernel.py module docstring に追加

"""
# このモジュールは監査ドキュメントであり、実行時には使用されない。


def get_coefficient_specs() -> dict:
    """係数仕様の辞書を返す (ドキュメント生成用)。"""
    return {
        "coordinate_system": "NED (x forward, y right, z down)",
        "reference_area": "wing_area_m2 (S)",
        "reference_length": "MAC (c)",
        "reference_span": "wingspan_m (b)",
        "coefficients": {
            # 基本係数は上記テーブル参照
        }
    }


# このモジュールは import 専用。実行時処理なし。
if __name__ == "__main__":
    print("Aerodynamic Coefficient Build-up Audit Report (Issue #71)")
    print("See module docstring for full specification.")