# 物理計算アルゴリズム仕様 (Physics Calculation Algorithms)

> **概要**: 本ドキュメントは、紙飛行機シミュレーターにおける空力モデル、6自由度飛行力学、構造応力、大気・風場、およびGPU並列計算のアルゴリズム実装を技術的に解説する。

---

## 目次

1. [空力モデル](#1-空力モデル)
2. [6自由度飛行力学（6-DoF）](#2-6自由度飛行力学6-dof)
3. [構造応力モデル](#3-構造応力モデル)
4. [大気・環境モデル](#4-大気環境モデル)
5. [3D風場・乱流モデル](#5-3d風場乱流モデル)
6. [折り紙幾何・プロシージャル生成](#6-折り紙幾何プロシージャル生成)
7. [GPU並列計算アーキテクチャ](#7-gpu並列計算アーキテクチャ)
8. [数値積分・安定性](#8-数値積分安定性)
9. [モデル間整合性・検証](#9-モデル間整合性検証)

---

## 1. 空力モデル

### 1.1 正本実装の原則

**単一実装原則**: 同じ物理式は `physics/aero_kernel.py` の**1関数**にのみ実装。スカラー経路（`aero_domain.py`）、NumPyバッチ、CuPyバッチの全経路が**同一関数**を呼ぶ。

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Aerodynamic Kernel Call Graph                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  physics/aero_domain.py (scalar)                                           │
│       │                                                                    │
│       ▼                                                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ physics/aero_kernel.py  ←── 正本（ScalarMath / numpy / cupy 抽象化） │   │
│  │  ├─ helmbold_cl_alpha(AR, taper)                                    │   │
│  │  ├─ lift_coefficient(xp, α, CL0, CLα, α_stall, ...)                │   │
│  │  ├─ drag_coefficient(xp, α, CD0, k_ind, CL, GE, α_stall, ...)      │   │
│  │  ├─ moment_coefficients(xp, α, CM0, CMα, CMq, q_norm, ...)         │   │
│  │  ├─ polhamus_lift_coefficient(xp, α, Kp, Kv)                       │   │
│  │  ├─ post_stall_lift_coefficient(xp, α, CL_stall, α_stall, ...)     │   │
│  │  ├─ separated_pressure_drag_coefficient(xp, α, α_stall, CD0, ...)  │   │
│  │  ├─ lsb_dynamic_stall_shift(xp, α_dot, Re, κ_lsb, k_red)           │   │
│  │  └─ trailing_edge_fold_effects(φ_TE, CL, CM, CD)                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       ▲                                                                    │
│       │                                                                    │
│  physics/gpu_dynamics.py (batch)                                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 揚力傾斜: Helmbold (1942) 三角翼・低AR翼式

**式**:
$$C_{L\alpha} = \frac{2\pi AR}{2 + \sqrt{AR^2(1+\lambda) + 4AR/\pi}}$$

| 変数 | 意味 | 典型値 |
|------|------|--------|
| $AR$ | アスペクト比（翼幅²/翼面積） | 2.67 (Classic Dart) |
| $\lambda$ | テーパー比（先端弦/付け根弦） | 0.7 |

**実装**: `physics/aero_kernel.py::helmbold_cl_alpha` (L140-L160)
- `ScalarMath` / `numpy` / `cupy` の3バックエンドで**同一ロジック**
- $AR \le 4$ の直線翼・三角翼に適用
- `cfg.cLalpha_per_rad > 0` でユーザー上書き可能（API契約）

**独立検証テスト** (`tests/test_aero.py`)：
- 閉形式と `rel=1e-12` 一致
- AR単調増加、テーパー比で単調減少
- 楕円翼極限（$AR\to\infty$）で $2\pi$ に収束

### 1.3 揚力係数: 付着流 + Polhamus前縁渦 + 失速後

```python
def lift_coefficient(xp, alpha_deg, cl0_base, cl_alpha, stall_alpha_deg,
                     sharpness=1.4, cl_max=1.2, blend_order=4):
    # 1. 線形付着流域
    cl_linear = cl0_base + cl_alpha * alpha_rad
    
    # 2. Polhamus前縁渦項（Kp=0, Kv=0 既定で無効）
    cl_polhamus = Kp * sin(α) * cos²(α) + Kv * sin²(α) * cos(α)
    
    # 3. 失速ブレンド [α_stall, 45°] で Hoerner平板へ滑らかに接続
    #    Hoerner: C_L = 2 sin α cos² α  (完全分離平板法線力)
    cl_post_stall = post_stall_lift_coefficient(...)
    
    # 4. スムーズブレンド（ブレンド次数4）
    return smooth_blend(cl_linear + cl_polhamus, cl_post_stall, ...)
```

**失速後モデル**: **Hoerner (1965) 平板法線力** `$C_L = 2\sin\alpha\cos^2\alpha$** を `[α_stall, 45°]` で**滑らかにブレンド**。Viterna-Corrigan (1982) は**未実装**（翼型データ$A_1,A_2,B_1,B_2$が紙機体に無いため係数推定が必要になるため）。

### 1.4 抗力係数: 寄生 + 誘導 + 分離圧力抗力

$$C_D = C_{D0} + \frac{C_L^2}{\pi e AR} \cdot GE(h) + C_{D,\text{sep}}(\alpha)$$

| 項 | 式 | 実装 |
|----|-----|------|
| 寄生抗力 | $C_{D0}(Re) = C_{D0} \cdot (10^5/Re)^{e_{cd0}}$ | `parasite_drag_coefficient` |
| 誘導抗力 | $C_{Di} = C_L^2 / (\pi e AR)$ | `drag_coefficient` 内部 |
| 地面効果 | $GE(h) = \frac{16(h/b)^2}{1+16(h/b)^2}$ | Wieselsberger (1921) |
| 分離圧力抗力 | Hoerner平板: $2\sin^2\alpha + C_{D0}\cos\alpha$ | `separated_pressure_drag_coefficient` |

**Re依存性**: $C_{D0}$ のみ（既定指数 $e_{cd0}=0$ で無効化）。層流摩擦則対応時は $e_{cd0}=0.5$ 設定可能。

### 1.5 モーメント係数・静的安定性

$$C_m = C_{m0} + C_{m\alpha}(\alpha - \alpha_{\text{trim}}) + C_{mq} \frac{\bar{c}}{2V} q$$

- トリム迎角: $\alpha_{\text{trim}} = -C_{m0}/C_{m\alpha}$（静的マージン確保）
- 動的減衰: $C_{mq}$ はピッチレート$q$に比例

### 1.6 低Re数効果（$10^3 \le Re \le 10^5$）

**層流剥離泡 (LSB) モデル** (`lsb_dynamic_stall_shift`)：

$$\Delta\alpha_{\text{hyst}} = \text{sgn}(\dot{\alpha}) \cdot \kappa_{\text{LSB}} \cdot \sqrt{k_{\text{red}}} \cdot \sqrt{10^4/Re}$$

| 係数 | 依存形 | Re=10⁴での効果 |
|------|--------|----------------|
| $C_{L\alpha}$ | $1 - 0.04x$ ($x=\log_{10}(10^5/Re)$) | -4% |
| $\alpha_{\text{stall}}$ | $1 - 0.10x$ | -10% |
| $C_{L\max}$ | $1 - 0.12x$ | -12% |
| LSBスケール | $1 + 0.15x$ | +15% |
| $C_{D0}$ | $1 + 0.03x$ | +3% |

**範囲外扱い**: クランプせず外挿、`model_valid` フラグで機械可読通知。

### 1.7 全幅後縁折り空力増分

$$\Delta C_L = 0.015\phi,\quad \Delta C_m = -0.012\phi,\quad \Delta C_D = 0.035\sin^2\phi$$

- $\phi$ = `fold_trailing_deg`（度、正=後縁下折り=キャンバー増）
- $\Delta C_L, \Delta C_m$ は比固定で**同時スケール**（作用点保存）
- 出典未取得のため**据え置き**（`docs/SPECIFICATION.md` §5.2）

---

## 2. 6自由度飛行力学（6-DoF）

### 2.1 状態ベクトル（13成分）

$$\mathbf{x} = [r_x, r_y, r_z,\; v_x, v_y, v_z,\; q_w, q_x, q_y, q_z,\; \omega_x, \omega_y, \omega_z]^T$$

| 成分 | 意味 | 座標系 |
|------|------|--------|
| $\mathbf{r}$ | 位置 | 世界 NED (+x北, +y東, +z下) |
| $\mathbf{v}$ | 速度 | 世界 NED |
| $\mathbf{q}$ | 姿勢四元数 | Hamilton (w,x,y,z), 単位ノルム |
| $\boldsymbol{\omega}$ | 角速度 | 機体座標 (前進, 右翼, 下向き) |

### 2.2 座標変換

```python
# 機体 → 世界
v_world = R(q) @ v_body
# 世界 → 機体
v_body = R(q).T @ v_world
# 四元数微分
dq/dt = 0.5 * q ⊗ [0, ω_body]
```

**四元数正規化**: 各RKステージで `quaternion_normalize()` 実行。

### 2.3 空力力・モーメント計算フロー

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    6-DoF Force/Moment Pipeline (per RK stage)               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  State x = [r, v, q, ω]                                                    │
│       │                                                                    │
│       ▼                                                                    │
│  1. Quaternion normalize                                                 │
│       │                                                                    │
│       ▼                                                                    │
│  2. Wind vector (world NED) ← wind_domain.wind_vector(r, t)              │
│       │                                                                    │
│       ▼                                                                    │
│  3. Air-relative velocity: v_air = v_world - w_NED                       │
│       │                                                                    │
│       ▼                                                                    │
│  4. Body-frame flow angles:                                              │
│     α = atan2(v_air_z, v_air_x)   # 迎角                                 │
│     β = asin(v_air_y / |v_air|)    # 側滑角                              │
│       │                                                                    │
│       ▼                                                                    │
│  5. aero_domain.evaluate_aero(α, β, q_dyn, ...) → (L, D, Y, M)           │
│       │           │           │          │                               │
│       ▼           ▼           ▼          ▼                               │
│     Lift      Drag       Side      Moment (body frame)                   │
│       │                                                                    │
│       ▼                                                                    │
│  6. World-frame forces: F_world = R(q) @ F_body + m*g                    │
│       │                                                                    │
│       ▼                                                                    │
│  7. Euler equation: I ω' = M - ω × (I ω)  (gyroscopic term included)     │
│       │                                                                    │
│       ▼                                                                    │
│  8. State derivative dx/dt = [v, F/m, 0.5*q⊗ω, I⁻¹(M - ω×Iω)]            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.4 数値積分: RK4 + 適応サブステッピング

**古典的RK4**:
$$k_1 = f(t_n, x_n)$$
$$k_2 = f(t_n + h/2, x_n + h k_1/2)$$
$$k_3 = f(t_n + h/2, x_n + h k_2/2)$$
$$k_4 = f(t_n + h, x_n + h k_3)$$
$$x_{n+1} = x_n + \frac{h}{6}(k_1 + 2k_2 + 2k_3 + k_4)$$

**適応ステップ制御** (`FlightSimulator.derived_timestep`)：

| 判定基準 | 閾値 | アクション |
|----------|------|------------|
| 角速度変化 | $|\Delta\omega| > 2.0$ rad/s | ステップ半分・再実行 |
| 速度変化 | $|\Delta v| > 25$ m/s | ステップ半分・再実行 |
| 姿勢変化 | $|\Delta q| > 0.35$ rad | ステップ半分・再実行 |
| 予測超過 | 観測ジャンプ $> 1.25 \times$ 予測 | ステップ半分・再実行 |
| 非有限値/上限超過 | NaN, |v|>500, |z|>11km, |r_xy|>100km | `diverged` 判定 |

**最大リトライ**: 6回（`DIVERGENCE_RETRIES = 6`）。超過で `crashed` + `status="diverged"`。

### 2.5 地面接触モデル

- キール接触深度: `GROUND_KEEL_DEPTH_M = 0.015` m
- 接触法線力: ペナルティ法（バネ・ダンパー）
- 摩擦: クーロン摩擦 + 静止摩擦クリップ

---

## 3. 構造応力モデル

### 3.1 翼根曲げモーメント（正本: `stress_domain.py::peak_root_stress_pa`）

$$M_{\text{root}} = \frac{1}{2} \rho V^2 S b \cdot C_{M,\text{root}}$$

### 3.2 断面係数（矩形板近似）

$$Z = \frac{b \cdot t^2}{6}, \quad t = \frac{\text{GSM}}{800} \text{ [m]}$$

### 3.3 較正係数 `KAPPA_SIGMA = 9.0e-6`

$$\sigma_{\text{beam}} = \text{KAPPA\_SIGMA} \cdot \frac{M}{Z}$$

| 要素 | 値 | 備考 |
|------|-----|------|
| `KAPPA_SIGMA` | `9.0e-6` | Beam理論値の約1/111,000に縮小 |
| 許容応力 | `paper_allowable_stress(gsm, stiffness)` | 既定 100g/m²・剛性0.35で 2.2 kPa |
| 応力指数 | $\sigma / \sigma_{\text{allow}}$ | **判定尺度はこれ1つ** |

**上反角射影補正** (`dihedral_load_multiplier`)：
$$K_{\text{dih}} = \frac{1}{\sqrt{1+\tan^2\Lambda}} = \cos\Lambda$$
- 場（`stress_field`）と縮約（`peak_root_stress_pa`）の**両方**に適用必須
- 未適用時: 縮約が場の $1/\cos\Lambda$ 倍になる（5°で 0.38%, 45°で 41%）

### 3.4 応力場ヒートマップ（`stress_field`）

- 各翼面頂点に動的応力評価値 `[0.0, 1.0]` を計算
- Three.js シェーダーでGPU描画（`static/js/viewport.js`）

---

## 4. 大気・環境モデル

### 4.1 ISA 国際標準大気

$$T = T_0 - L h$$
$$p = p_0 \left(\frac{T}{T_0}\right)^{g/(LR)}$$
$$\rho = \frac{p}{R T}$$

| 定数 | 値 |
|------|-----|
| $T_0$ | 288.15 K |
| $p_0$ | 101325 Pa |
| $L$ | 0.0065 K/m |
| $g$ | 9.80665 m/s² |
| $R$ | 287.058 J/(kg·K) |

### 4.2 局所大気（温度・気圧・湿度指定）

- 水蒸気圧から実効気体定数 $R_{\text{mix}}$ 計算
- 混合密度 $\rho_{\text{mix}}$ 算出

### 4.3 動粘性係数: Sutherlandの式

$$\mu(T) = \mu_0 \frac{T^{3/2}}{T + S} \frac{T_0 + S}{T_0^{3/2}}$$

| 定数 | 値 |
|------|-----|
| $\mu_0$ | $1.716\times10^{-5}$ Pa·s |
| $T_0$ | 273.15 K |
| $S$ | 110.4 K |

**レイノルズ数**: $Re = \rho V c / \mu$

---

## 5. 3D風場・乱流モデル

### 5.1 座標系の厳密性（Issue #30 解決済み）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Coordinate Frame Transformation                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Wind Domain (Meteorological)          Flight Domain (World NED)           │
│  ┌─────────────────────────┐           ┌─────────────────────────┐         │
│  │ +x : East               │           │ +x : North (Forward)    │         │
│  │ +y : North              │           │ +y : East (Right)       │         │
│  │ +z : UP (上向き)         │    ──►    │ +z : DOWN (下向き)       │         │
│  │                         │  w_NED =  │                         │         │
│  │ wind_dir: 気象庁風向    │  -w_wind  │                         │         │
│  │ (北から時計回り「吹いてくる」)     │                         │         │
│  └─────────────────────────┘           └─────────────────────────┘         │
│                                                                             │
│  風速ベクトル変換:  w_NED = -w_wind   (符号反転のみ、水平成分は回転不要)       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**実装**: `physics/wind_domain.py` (正本) → `physics/flight_sim.py::wind_vector()` (唯一の変換箇所) → GPUバッチも同一関数呼出し。

### 5.2 3D Simplexノイズ乱流

- 空間相関を持つ3D乱流場を **Simplex Noise** で生成
- 高度境界層プロファイル: 対数則 / べき乗則
- 熱上昇気流: ガウシアン柱状モデル

### 5.3 風場サンプリング高度基準

**地表面からの高度** (`z_up = -z_ned` から `ground_level_m` 差し引き) でサンプル。
- `wind_domain.py` と `flight_sim.py::wind_vector` は同一基準
- GPUバッチ経路も `flight_sim.wind_vector` を呼ぶ（独自基準なし）

---

## 6. 折り紙幾何・プロシージャル生成

### 6.1 折り工程シミュレーション（`physics/origami_domain.py`）

A4用紙（1:√2, 210×297mm）から**折り工程そのものをシミュレート**して3Dモデル生成。

**座標系**: 正規化単位正方形 `(u,v)` → 3D `(x,y,z)`
- $x = (v-0.5) \cdot L$ （前後、+x=機首）
- $y = 0$ （上）
- $z = (u-0.5) \cdot W$ （左右、+z=右翼）

### 6.2 折り手順（ヒンジ列）

| Step | 名称 | 説明 |
|------|------|------|
| 1 | `body` | 中央縦線で対称折り（V字キール、微小開き `keel_beta_deg`) |
| 2 | `corner_l/r` | 左右上角を中央へ45°谷折り（機首第1層） |
| 3 | `collar_l/r` | 角の斜辺と二等分線沿い折り（機首厚い折り返し・嘴） |
| 4 | `wing_l/r` | 胴体キール残し主翼を水平折り下げ（微小上反角） |
| 5 | `winglet/elevon/trailing` | 翼端ウイングレット・後縁エレボン反り |

**順序は物理的折り順と一致**。各折り目は**その時の現在位置を軸に回転**（$T' = F \circ T$）。

### 6.3 レイヤーオフセット（Z-Fighting完全排除）

| パラメータ | 値 | 意味 |
|-----------|-----|------|
| `VISUAL_THICKNESS_M` | 0.30 mm | 実紙0.10mmの3倍（視覚強調） |
| `LAYER_AIR_GAP_M` | 0.12 mm | 空気隙 |
| `LAYER_PITCH_M` | 0.42 mm | 層ピッチ = 厚さ + 隙 |

**証明**: 重なり関係DAGの最長路を層インデックス $k_f$ とする。
任意の重なる2面について $k_B \ge k_A + 1$ → 中間面間隔 $\ge \text{PITCH} > t$ → **交わらない・同一面にならない**。

### 6.4 3機体アーキタイプ

| Key | 名称 | 特徴 |
|-----|------|------|
| `classic_dart` | Classic Dart / やり飛行機 | 細長いデルタ翼、機首4-6層、鋭い嘴 |
| `nakamura_lock` | Nakamura Lock / へそ飛行機 | 機首内側折り込み、前縁厚い、翼面積広い |
| `sky_king` | Sky King / 競技用滞空機 | 鈍頭ノーズ、明確なウイングレット、高AR |

### 6.5 幾何→空力パラメータ受渡（`origami_domain.py::extract_aero_parameters`）

| 折紙幾何 | 空力パラメータ | 変換式 |
|----------|--------------|--------|
| 機首パケット厚 | `camber_ratio` | $f/c_{\text{packet}} = \text{PACKET\_CAMBER\_M} / 0.10$ |
| 後縁折り角 | `fold_trailing_deg` | $f/c_{TE} = 0.5\sin\phi_{TE} \cdot 0.055$ |
| 前縁後退 | `leading_edge_sweep_deg` | $\Lambda_{LE} = \arctan(0.05 W/L)$ |
| 上反角 | `dihedral_eff_deg` | 符号保持 |

**空力二重実装は origami側に置かない**（正本は `aero_kernel.py` 単一実装）。

---

## 7. GPU並列計算アーキテクチャ

### 7.1 3つの実行経路とモデルタグ

| 経路 | Model Tag | Model Detail | 物理モデル | 用途 |
|------|-----------|--------------|------------|------|
| 6-DoF正規 | `rigid_body_6dof` | `6dof_rk4` | 13状態RK4、全空力・風・構造 | リファレンス・物理検証 |
| GPU最適化バッチ | `rigid_body_6dof` | `gpu_batch_rk4` | **同一13状態RK4**をベクトル化 | 大規模評価・GA |
| GPUアンサンブル | `reduced_surrogate` | `gpu_ensemble_3dof` | 3-DoF点質量、陽的Euler、姿勢クランプ | 不確実性定量化 |
| 解析的サロゲート | `reduced_surrogate` | `analytical_glide_surrogate` | 閉形式滑翔解、姿勢動力学なし | 高速スクリーニング |

**重要**: GPU最適化バッチは**正規経路と同一の13状態RK4モデル**（同一空力カーネル・同一風場・同一構造・同一RK4スキーム）。`tools/check_gpu_parity.py` で定量検証（最大応力・Gロード完全一致、飛距離0.1-0.9%差）。

### 7.2 CuPy/CUDA バッチ評価（`parallel_engine.py::gpu_batch_evaluate_cases`）

```python
# 概念的フロー
def gpu_batch_evaluate_cases(cases):
    # 1. ケース配列をデバイスメモリへ転送
    d_cases = cp.asarray([case_to_array(c) for c in cases])
    
    # 2. CUDA Graph Capture（RK4ステップ全体をグラフ化）
    #    - ホスト↔デバイスコピーなし
    #    - データ依存制御フローなし（マスクで着地/墜落処理）
    #    - cuBLAS呼び出しなし（einsum→要素ごと計算）
    graph = cp.cuda.graph.capture(begin_capture(mode=2))
    for step in range(max_steps):
        rk4_step_kernel(d_cases)  # 40カーネル起動 → グラフで1回
    graph.end_capture()
    
    # 3. グラフリプレイ実行
    graph.replay()
    
    # 4. 結果をホストへコピー・整形
    return [array_to_result(arr) for arr in d_cases.get()]
```

### 7.3 VRAMプール管理（`ga2/vram.py`）

- 世代ループ開始前に**単一フラットバッファ確保**
- 名前付きワークスペースを**ビューとして切り出し**
- ループ内アロケーション **ゼロ**

### 7.4 CPUフォールバック

- GPU非搭載・CUDA初期化失敗時: **NumPy + `concurrent.futures` / `multiprocessing`**
- **返却スキーマ完全一致** (`landed`, `crashed`, `status`, `horizontal_range_m` 等)
- `model` タグで物理モデル差を明示（`rigid_body_6dof` vs `reduced_surrogate`）

---

## 8. 数値積分・安定性

### 8.1 RK4 収束性検証（Issue #31）

| $\Delta t$ | 飛距離 | 相対差分 |
|-----------|--------|----------|
| 0.040 s | 45.23 m | - |
| 0.020 s | 45.67 m | 0.97% |
| 0.010 s | 45.78 m | 0.24% |
| 0.005 s | 45.82 m | 0.09% |

**単調収束確認済み** (`tests/test_advanced_physics_audit.py`)。

### 8.2 ハードガードレール（`flight_sim.py`）

| 定数 | 値 | 意味 |
|------|-----|------|
| `HARD_SPEED_LIMIT_MPS` | 500.0 | 速度上限超過で発散判定 |
| `HARD_ALTITUDE_LIMIT_M` | 11,000 | 高度上限（対流圏上限） |
| `HARD_HORIZONTAL_LIMIT_M` | 100,000 | 水平到達上限 (100km) |
| `HARD_ANGULAR_RATE_LIMIT_RPS` | 100.0 | 角速度上限 |
| `MAX_ATTITUDE_STEP_RAD` | 0.35 | 単ステップ姿勢変化上限 |
| `MAX_RATE_CHANGE_RADPS` | 2.0 | 単ステップ角速度変化上限 |

### 8.3 地中即死判定

```python
if min_altitude < -0.05:  # 5cm地中侵入
    status = "crashed"  # 即座に失敗
```

---

## 9. モデル間整合性・検証

### 9.1 単一実装原則の検証コマンド

```bash
# 空力カーネルパリティ（スカラー/NumPy/CuPy 同一関数呼出し）
python tools/check_aero_kernel_parity.py
# → 差異 0.0 (完全一致)

# GPUバッチ vs 6-DoF正規経路
python tools/check_gpu_parity.py
# → 最大応力・Gロード完全一致、飛距離 0.1-0.9% 差

# 応力ピーク: 場の厳密縮約 vs スカラー縮約
python tools/check_stress_peak_parity.py
# → 最大相対差 0.000e+00

# 物理モデルレジストリ（実装済/未実装の機械可読宣言）
python -m pytest tests/test_physics_model_registry.py -q
```

### 9.2 既知のモデル間乖離（意図的維持）

| 比較 | 乖離 | 扱い |
|------|------|------|
| パネル法 vs Helmbold | 揚力傾斜 2.22倍 | **意図的維持**（`no-finish.md` §A-5）。パネル法は可視化・局所診断用。GA評価経路に未接続。 |
| Viterna-Corrigan vs Hoerner | 未実装 vs 実装 | レジストリで `implemented=false` 明示 |

### 9.3 出典未取得係数の据え置き（`docs/SPECIFICATION.md` §5.2）

| 定数 | 値 | 再オープン条件 |
|------|-----|----------------|
| `KAPPA_SIGMA` | `9.0e-6` | 実紙・実折面の引裂強度・残留断面実測データ |
| 後縁折り係数 | `0.015 / -0.012 / 0.035` | 紙航空機後縁空力実測論文 |
| `polhamus_kp/kv` | `0.0 / 0.0` | 鋭い前縁三角翼形状の紙機体設計適用根拠 |

---

## 付録: 主要ファイルマップ

```
physics/
├── aero_kernel.py         # 空力正本（Helmbold, Polhamus, Hoerner, LSB, 後縁折り）
├── aero_domain.py         # スカラー空力API（正本再エクスポート + 高レベル評価）
├── flight_sim.py          # 6-DoFリファレンス（RK4, 13状態, 適応ステップ）
├── stress_domain.py       # 構造応力（場・縮約・許容応力・KAPPA_SIGMA）
├── atmosphere_domain.py   # ISA + Sutherland + 局所大気
├── wind_domain.py         # 3D Simplex風場 + 座標変換正本
├── origami_domain.py      # プロシージャル折り紙幾何（折り工程シミュレーション）
├── parallel_engine.py     # 並列評価（サロゲート/6-DoF/GPUアンサンブル/バッチ）
├── gpu_dynamics.py        # GPUバッチRK4（CuPy, CUDA Graph Capture）
├── ga2/lbm.py             # D3Q19 LBM + Immersed Boundary (Stage 3)
├── config_schema.py       # 全物理パラメータデータクラス定義
└── payload_contract.py    # APIペイロード正規化・単一スキーマ
```

---

## 参考文献・関連仕様

- `docs/SPECIFICATION.md` §1 (物理・航空工学理論), §2 (GPU並列), §5 (学術引用レジストリ)
- `docs/PHYSICS_MODELS.md` (物理モデル正本：式→出典→正本モジュール→適用限界→検証テスト)
- `docs/GA_V2_ARCHITECTURE.md` (GA v2 アーキテクチャ・LBMソルバ)
- `docs/CFD_ROADMAP.md` (3D Navier-Stokes導入ロードマップ)
- `physics/aero_kernel.py` (空力正本実装)
- `physics/flight_sim.py` (6-DoFリファレンス実装)

---

*最終更新: 2026-11-12 | バージョン: 1.0 | 正本: `docs/PHYSICS_ALGORITHM.md`*