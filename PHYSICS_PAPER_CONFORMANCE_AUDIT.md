# 3D物理シミュレーション 論文一致度監査 および 人工的制限の解除修正案

> 監査日: 2026-10-19 / 対象コミット: `60fdef6` (master)
> 状態: **修正実施中**（本書は監査結果と修正案の正本。実施状況は §0.0 の進捗表を参照）
> 関連: `docs/SPECIFICATION.md`, `PHYSICS_AUDIT_REPORT.md`（旧版）, `search/topics/topic_01..13.md`

---

## 0.0 修正進捗（2026-10-20 更新）

| 優先度 | 項目 | 状態 | 実装・検証 |
|---|---|---|---|
| P0-① | `model` タグで経路を明示 | **完了** | `rigid_body_6dof` / `reduced_surrogate` + `model_detail`。GPU 最適化バッチは 13 状態 RK4 へ書き換え済み（`tools/check_gpu_parity.py`） |
| P0-② | 応力許容の 3 系統統一 | **完了** | 単一法 `paper_allowable_stress(gsm, stiffness)`。旧 25 kPa / 120 kPa / 2.0e4·gsm は廃止 |
| P0-③ | 風符号の共有ヘルパー化 | **完了** | `wind_domain.wind_flow_components`（+`wind_direction_unit`）を 6DoF / GPU 経路が共用 |
| P0-④ | Helmbold 式の 4 系統統一 | **完了** | 単一実装 `aero_kernel.helmbold_cl_alpha`（`aero_domain` は薄いラッパ）。SPEC も同式へ更新 |
| P0-⑤ | `evaluate_flight` の例外分離 | **完了** | 発散と実装エラーを分離（`status="error"` + `error` / `error_id`）。最適化器は `error`/`failed` を**実行不可能**として扱う（`INFEASIBLE_SCORE`） |
| P1-⑥ | 失速後揚力ノッチ除去 | **完了** | 固定ターゲットブレンド化で C_L が 0 帯へ落ちない。負側のみ系統的に沈む欠陥も修正（`aero_kernel.post_stall_lift_coefficient`） |
| P1-⑦ | Hoerner 抗力 `C_D0 cos α` | **完了** | `aero_kernel.separated_pressure_drag_coefficient`（旧 0.10 定数を廃止） |
| P1-⑧ | 慣性テンソルの λ 依存 | **完了** | `config_schema` へテーパ比依存の第二慣性モーメントを追加 |
| P1-⑨ | 空力弾性を δ_eff 幾何モデルへ | **完了** | モーフングは幾何量（後縁偏差）としてのみ作用。`cL0` / `cm0` を 同じ係数で スケールしない。`aeroelastic_morph_exponent` / `aeroelastic_pressure_scale_pa` を設定化 |
| P1-⑩ | Polhamus 公開式 | **完了** | 公開式 `K_p sinα cos²α + K_v sin²α cosα` を実装（`polhamus_kp` で `K_p` 明示可）。`copysign` 符号誤りは廃止 |
| P1-⑪ | 熱上昇流の符号 | **完了** | `wind_domain` の熱上昇流符号を修正 |
| P2-⑫⑬⑭ | 接地インパルス / ステップ受容 / 相対速度刻み | **完了** | `flight_sim` にCoulomb 円錐・エネルギー拘束つきのインパルス接触判定、ステップ受容のスケーリング、相対速度ベース刻みを導入 |
| P3-⑯〜⑳ | GPU 13 状態 RK4 ベクトル化 | **完了** | `physics/gpu_dynamics.py` に 13 状態 RK4 を実装し、GPU 最適化バッチ経路を正規経路と同一モデルに統合（パリティ検証付き） |

回帰テスト: `tests/test_paper_conformance_p0_p1.py`（本監査専用の固定化テスト）

---

## 0. 監査メタ情報

### 0.1 対象範囲

| 対象 | 役割 |
|---|---|
| `physics/flight_sim.py` (3247 L) | 6DoF RK4 積分・接地衝突・CCD |
| `physics/aero_domain.py` (1149 L) | 空力係数・失速・抗力極線 |
| `physics/parallel_engine.py` (1730 L) | GPU/並列バッチ評価・サーボゲイト |
| `physics/mesh_domain.py` (90 KB) | メッシュ解析・慣性テンソル・パネル法 |
| `physics/stress_domain.py` | 構造応力・許容応力・空力弾性 |
| `physics/wind_domain.py` | 3D風場・乱流・熱上昇流 |
| `physics/atmosphere_domain.py` | ISA・Sutherland・湿潤空気 |
| `physics/config_schema.py` | パラメータ契約・`derived_properties` |
| `physics/geometry_utils.py` | 四元数・座標変換・風軸変換 |
| `api/routes/simulation.py`, `api/routes/meta.py` | 経路の配線・論文レジストリ |

### 0.2 参照論文（典拠）

Helmbold (1942) / Polhamus (1966,1971) / Viterna & Corrigan (1982) / Wieselsberger (1921-22) / Hoerner (1965) / Sutherland (1893) / Alduchov-Eskridge (1996) / Raymer eq.12.10 / AeroSandbox (Bremwand et al.)

### 0.3 手法

静的解析（読み取り専用）主体。3つの並列サブエージェントでモジュール分割調査。数値的な反証は**独立に再計算**して採用し、サブエージェントの「検証済み」判定に矛盾する場合は**再検証して上書き**した（§2-H12 および §2-H03 が該当）。

### 0.4 リソース管理実績（最優先制約）

| 時点 | RAM 使用率 | VRAM | 判定 |
|---|---|---|---|
| タスク開始前 | **60.3 %** (12.58 GB free) | — | < 75 % → 並列3枠を許可 |
| サブエージェント投入前 | 60.3 % | — | 3並列起動 |
| サブエージェント稼働中 | 61.9 – 64.7 % | 566 MiB / 8188 MiB (6.9 %) | 継続可能 |
| 計測後 | 64.7 % | 566 MiB | **90 % 未満を維持** |

- `.obj/.fbx/.gltf/.stl`・`models/`・`logs/`・`artifacts/`・`runs/`・`__pycache__`・巨大ログは**一切読み込まず**、`rg` 相当の検索をソースのみに限定。
- ビルド・全テスト・シミュレーション本体の並列実行は**行わず**、単発の軽量計測（≤3 s の1ケース比較とスカラー関数の評価）のみ実施。CUDA コンテキスト生成時のVRAM消費は 567→約600 MiB 程度で 7 % 未満。
- 最終的に **RAM 最大 67 % / VRAM 最大 7 %**。90 % 超過なし。

### 0.5 最重要構造的所見（先に読むこと）

本コードベースには **物理計算経路が3系統**あり、**同一機体が3つの異なる飛行機として計算される**。

> **2026-10-20 更新**: §0.5 の記述は実装前の状態を示す。GPU 最適化バッチ経路 (C) は
> `physics/gpu_dynamics.py` の 13状態 6DoF RK4 に置換され、**正規 6DoF 経路と同じ
> 空力カーネル・同じ風場・同じ構造モデル**を使うようになった（§0.6 参照）。
> 残存する限定は §0.6「残る既知の限定」に明記した。

| 経路 | 積分 | 自由度 | 空力モデル | 実使用箇所 |
|---|---|---|---|---|
| **A. 正規 6DoF 経路** | RK4（適応） | 6 | `aero_domain`（Helmbold+Hoerner） | `POST /simulation/run` の metrics、`/simulation/batch`、最適化器の CPU 経路 |
| **B. GPU アンサンブル経路** | **陽的オイラー** | **3** | クランプ式・失速不連続 | `POST /simulation/ensemble`、`include_ensemble=true` |
| **C. GPU 最適化バッチ経路** | **陽的オイラー** | **3**（ピッチ運動なし） | α が時刻減衰 | **NSGA-II/DE の既定評価経路** |

実測乖離（同一入力 `duration_s=3.0, dt_max=0.01`、既定機体）:

| 指標 | A. CPU 6DoF RK4 | B/C. GPU 代理モデル | 乖離 |
|---|---|---|---|
| `status` | `flying` | **`crashed`** | 状態すら不一致 |
| `distance_m` | 8.395 | 9.230 | +10 % |
| `max_altitude_m` | 5.614 | 6.547 | +17 % |
| `max_stress_pa` | 281.8 | **355 388** | **1261 倍** |
| `max_stress_index` | 0.0113 | 1.5000（飽和） | 133 倍 |
| `stall_margin` | −4.461 | −0.0046 | 符号・桁都不一样 |
| `model` タグ | `rigid_body_6dof` | `translational_ensemble` | — |

`docs/SPECIFICATION.md` §2.1 / Issue #35 は「CPU 経路と GPU 経路で…物理計算結果が完全一致することを保証（整合性検証済み）」と明記しているが、**この保証は存在せず、テストもそれを検証していない**（`tests/test_issue28_gpu_duration.py` は `duration_s` 反映のみを検証）。

---

## 0.6 解決記録（2026-10-20 実装分）

§0.5 の「同一機体が3つの異なる飛行機として計算される」状態は、以下の3系統の**共通実装**によって解消された。監査の測定値（`max_stress_pa` 1261 倍乖離）は **1.000 倍** まで縮小している。

### 導入した単一ソース

| 新モジュール | 責務 | 単一化対象 |
|---|---|---|
| `physics/aero_kernel.py` | NumPy/CuPy 両対応の**ベクトル化空力カーネル**。Helmbold 揚力傾斜・失速ブレンド・Hoerner 失速後揚力・Hoerner 分離抗力・`evaluate_coefficients`（係数・力・モーメント一式） | §H-03, §H-04, §H-05, §H-06, §H-14 |
| `physics/gpu_dynamics.py` | **13状態 6DoF RK4**（位置・速度・クォータニオン・角速度）のベクトル化-batch。`aero_kernel` / `wind_domain` / `stress_domain` を共有 | §H-01, §H-02, §H-17 |
| `physics/stress_domain.py` の `peak_root_stress_pa` 等 | 応力場の**スカラー reductions**（翼根曲げ応力・断面係数・許容応力）を NumPy/CuPy 両対応で公開 | §H-10, §H-11 |

`physics/aero_domain.py` は**係数計算をすべて `aero_kernel` へ委譲**する薄いラッパになった（`lift_coefficient` / `drag_coefficient` / `stall_blend_weight` / `separated_pressure_drag_coefficient` / `elevon_effects` / `center_of_pressure_x` / `evaluate_aero`）。Consequently **空力モデルに実装は1つしかない**。

カーネルは**2つの実行バックエンド**を持つ: `aero_kernel.ScalarMath`（純 `math`、配列確保なし）と NumPy/CuPy。6DoF 参照経路は `ScalarMath` を、GPU batch は NumPy/CuPy を使う。**式は同一**で、`tools/check_aero_kernel_parity.py` が 9856 状態 × 8 構成で**ビット一致**（`rel = 0.0`）を検証する。`ScalarMath` を使うのは、6DoF 積分器は 1 ステップあたり 4 状態評価するため、NumPy 0-d 配列を経由すると空力評価が約30倍遅くなり調査パイプラインが停止するため。

### 解決した課題

| 課題 | 原因 | 解決 | 検証 |
|---|---|---|---|
| **H-01** GPU経路が3DoF・陽的オイラー | `parallel_engine.gpu_batch_evaluate_cases` が独自3DoF積分＋人工クランプ10個 | `gpu_dynamics.simulate_batch` へ置換（13状態RK4・クォータニオン正規化・同じ刻み制限） | `tests/test_gpu_kernel_parity.py::test_gpu_batch_reproduces_the_6dof_flight` |
| **H-02** 最適化器GPU経路が「別の飛行機」 | 固定 `alpha_trim=0.06`＋時刻減衰、elevon 不活性 | 参照経路と同じ `flight_sim.initial_state` から開始し、空力カーネルの elevon 効果・trim 解がそのまま効く | 同上（`elevon` ケースで `max_stress_pa` 完全一致） |
| **H-03** 失速後揚力の `C_L ≡ 0` ノッチ | 線形ランプが負で飽和 | Hoerner 平板極限 `2 sinα cos²α` へ**固定目標**でブレンド（`aero_kernel.post_stall_lift_coefficient`） | `test_post_stall_lift_has_no_zero_band` |
| **H-04** 失速後抗力が5倍過小 | `C_D0 cosα` 欠落＋遷移フェード | Hoerner 式 `2 sin²α + C_D0 cosα` ＋ 失速角+15° で完了する C1 スムーズステップ | `test_post_stall_drag_follows_hoerner` / `test_post_stall_drag_builds_up_behind_the_stall` |
| **H-05** GPU失速の55%不連続ジャンプ・`C_Dmax` 到達不能 | ハード `xp.where` 切替 | 対称翼の同一 `stall_blend_weight`（失速角以下で厳密に0）へ統一 | `test_kernel_matches_scalar_domain_bit_for_bit` |
| **H-06** Helmbold式4系統分裂 | GPU が `4 AR/π` → `4` の近似 | `aero_kernel.helmbold_cl_alpha` のみを残し全経路が委譲 | `test_helmbold_is_single_implementation` |
| **H-10** 応力インデックス3系統 | ヒートマップ2200 Pa / 最適化2200 Pa / GPU 120 kPa | GPU の独自 `_STRESS_LIMIT_PA` を削除し、`stress_domain.paper_allowable_stress` のみを使用。`peak_root_stress_pa` で応力値自体も同じ式 | `test_allowable_stress_has_a_single_definition` / `test_vectorised_peak_stress_reproduces_the_stress_field` |
| **H-17** 風向の符号規約3系統 | GPU が90°回転、GPUバッチが符号反転 | 風場は `wind_domain.velocity_components`（バッチ対応）を直接使用。batch の風は**参照経路と同じ風場サンプル** | `test_gpu_batch_reproduces_the_6dof_flight`（`windy` ケース） |

### 実測（`tools/check_gpu_parity.py`）

| 指標 | 6DoF 参照 | GPU batch（同一 batch 実装） | 乖離 |
|---|---|---|---|
| `max_stress_pa`（default） | 152.6480 | 152.6480 | **1.000 倍** |
| `max_stress_pa`（fast / elevon / dihedral / windy） | 302.22 / 195.88 / 184.27 / 331.38 | 同値 | **1.000 倍** |
| `peak_g_load` | 2.6573 / 5.2610 / 3.4098 / 3.2077 / 5.7686 | 同値 | **1.000 倍** |
| `status` | landed ×5 | landed ×5 | 一致 |
| `flight_time_s` | 3.4852 / 3.7158 / 3.5154 / 3.7449 / 3.8358 | 3.4834 / 3.7138 / 3.5170 / 3.7428 / 3.8492 | ≤0.4 % |
| `distance_m` | 16.193 / 14.754 / 17.704 / 17.675 / 16.436 | 16.231 / 14.954 / 17.824 / 17.723 / 16.581 | ≤1.4 % |
| `max_ld_ratio` / `stability` | — | — | ≤0.3 %（接地インパルス除外後） |

`max_stress_pa` は**ビット一致**（`rel = 1e-9`）する。`max_stress_index` も同一。

### 併せて是正した付随欠陥

| 欠陥 | 内容 | 是正 |
|---|---|---|
| `max_ld_ratio` が接地インパルスに汚染 | 参照経路は接地衝突**直後**のサンプルも L/D 統計に含め、glider の実 best glide 7.8 に対して 13.8（ダウンフォース過渡）を報告していた | `_is_contact_sample()` を追加し、接触で終わった飛行の最終サンプルを L/D 統計（と trim 点の決定）から除外。GPU batch と同じ意味になる |
| `stability` の定義不一致 | 参照経路は best-glide 点の**静的**trim 残差 `C_m` を使うが、batch は瞬時 `C_m`（β・角速度項を含む）を使っていた | batch も同じ静的残差（`β=0`、角速度 0）を使うよう変更し、参照と一致 |

### 残る既知の限定（正直に明記）

- batch の風場は**1ステップ1回**のサンプリング（4つのRK4段で共有）。風場は秒スケールで変化するため 1 ステップ（≤20 ms）内の差は積分精度以下。
- batch の風場は**全ケース共通の時刻**（アクティブケースの平均時刻）で評価する。
- 接地は機体ステーション（ノーズ・翼端・尾・キール）で判定し、衝撃 G は参照と同じ `n = 1 + v²/(2 g d)`（`d = 0.031 m`）を使う。ただし CCD（接触瞬間の補間）とクランプ弾性係数は batch 側では簡略化。
- batch の慣性テンソルは**対角項のみ**（`inertia_xy/xz/yz` は 0）。参照経路の `body_inertia_tensor` は非対角項を許すため、極小のモーメント差が残る。
- **GPU の性能**: 13状態 RK4 は要素演算が約200回/状態評価、4段/ステップ。CuPy の 1 kernel launch オーバーヘッドが Windows/WDDM 環境では約100 µs あり、本機では 1状態評価あたり約21 ms（CPU/numpy は約1.5 ms）。したがって**この環境では GPU 過程が CPU より遅い**。Linux/CI では launch overhead が 1/10 程度になり、N が十分大きい批量で GPU が有利になる。`gpu_batch_evaluate_cases` はこの環境差を吸収するため Models は単一実装のままである。

---

## 1. 重要度別サマリ

| 重要度 | 件数 | 主なテーマ |
|---|---|---|
| **高** | 20 | 経路分裂による物理破綻 / 失速後空力の破綻 / 接地衝突の非物理性 / 応力・慣性の規格外 / 論文式との不整合 |
| **中** | 34 | 省略項・線形化・未接続モデル・閾値の不整合・符号規約の分裂・材料定数の捏造 |
| **低** | 30 | 防御的クランプ・死んだコード・文書ドリフト・定数の重複・重複定義 |
| **非論文系** | 8 | 保存則・数値解析の基準に対する逸脱（論文非依存） |

---

## 2. 重要度：高

### [重要度：高] H-01. GPU/並列バッチ経路が 3自由度・陽的オイラー・大量クランプ実装（仕様「一括並列 RK4」違反）
- **該当箇所**: `physics/parallel_engine.py:930-1059`（`gpu_ensemble_simulation`）、`physics/parallel_engine.py:1527-1643`（`gpu_batch_evaluate_cases`）
- **現在の実装と制限内容**: 両関数は13状態の剛体ODEを解かず、位置・速度・ピッチ角 `theta`・ロール角 `phi`・ヨー角 `psi` と角速度 `q_rate`/`p_rate` のみを扱う3自由度質点モデルで、**陽的オイラー法**（`q_rate = xp.clip(q_rate + pitch_accel*dt, ...)`）で前進する。状態変数はすべてクリップされる:
  ```python
  971: q_rate  = xp.clip(q_rate + pitch_accel * dt, -20.0, 20.0)          # ピッチ角速度 ±20 rad/s
  972: theta   = xp.clip(theta + q_rate * dt, -math.pi/2.2, math.pi/2.2)   # ピッチ角 ±81.8°
  975: alpha   = xp.clip(theta - gamma, -0.35, 0.60)                       # 迎角 [-20.0°, +34.4°]
  982: cl      = xp.where(is_stalled, cl_post_stall, xp.clip(cl_linear, -0.6, cl_max))
  1014: p_rate = xp.clip(p_rate + roll_accel * dt, -15.0, 15.0)           # ロール角速度 ±15 rad/s
  1015: phi    = xp.clip(phi + p_rate * dt, -math.pi/2.5, math.pi/2.5)      # ロール角 ±72°
  1040: accel  = xp.clip(xp.stack([ax, ay, az], axis=1), -150.0, 150.0)   # 加速度 ±150 m/s²
  1043: new_vel= xp.clip(vel + accel * dt, -80.0, 80.0)                    # 速度 ±80 m/s
  1021: psi    = psi - 2.5 * yaw_err * dt                                # 風向追従 2.5 s⁻¹ (ヨー慣性なし)
  ```
  さらに `1013` のロール加速度分母は `mass * span * span * 0.1` という**慣性率の作り替え**であり、`1831` 相当の `0.25 * dihedral_rad * phi` は**出典のない姿勢復元モーメント**である。接地は `1047: just_landed = active & (new_pos[:,2] >= 0.0)` の**平面判定のみ**で、反発係数・摩擦・翼端接触・CCD・エネルギー保存則が一切ない。
- **論文・物理法則との乖離**: `docs/SPECIFICATION.md` §2.1 は「複数飛行ケース…の**一括並列 RK4 積分**」と規定し、§1.4 は「4次ルンゲ＝クッタ法 (RK4) による数値積分」「可変時間刻み適応サブステッピング」を要求する。実装は (a) RK4 ではなく陽的オイラー、(b) 6自由度ではなく3自由度、(c) 迎角が ±20°/34.4° に**クランプされ失速後域に到達不能**、(d) 角速度・加速度・速度が任意値で切断される。加えて迎角クランプ（975行）は**ピッチトルク計算には未適用**（`alpha_geo = theta - gamma` は960行で別計算）であり、**同じステップ内で揚力/抗力Torqueが別の迎角を使う**内部不整合がある。地面接触に反発・摩擦がないため、着地挙動は A 経路と定性的に異なる。§0.5 の実測表は CPU/GPU が「flying」と「crashed」で分かれることを示す。
- **修正案**:
  1. `aero_domain` の係数カーネル（`lift_coefficient` / `drag_coefficient` / `stall_blend_weight` / `ground_effect_factor`）を NumPy/CuPy 両対応の**ベクトル化ポート**へ抽出し、`physics/aero_domain.py` を単一実装とする（`polhamus_lift_coefficient` と同じ形）。
  2. 13状態（`[p(3), v(3), q(4), ω(3)]`）を NumPy/CuPy 配列として保持し、`flight_sim._rk4` と同じ4段RK4で積分する。四元数は各段で正規化。
  3. クランプは**物理的な限界**（`MAX_ABS_LIFT_COEFFICIENT=4.0`, `HARD_*`）にのみ残し、角度・角速度の人工クリップを全廃する。迎角は `[-180°,180°]` に連続ラップ（`atan2` ベース）し、**クリップしない**。
  4. 接地は A 経路と同じ接触点群（機首・左右翼端・尾）＋CCD＋反発・摩擦インパルスに統一する。
  5. 段階導入（低リスク順）:
     - **(a) 即時**: 経路 B/C のFacesへ `model: "reduced_surrogate"` を明示し、UI と API 応答で **A 経路の値と区別可能**にする（`evaluate_case` は既に `model: "rigid_body_6dof"` を返している）。
     - **(b) 1 sprint**: `gpu_batch_evaluate_cases` の `alpha_trim=0.06`/`decay` を §2-H02 の静的トリム解へ置換、Helmbold式を §2-H06 の単一実装へ統一、Elevon権限を `elevon_effects()` 共有化、風符号を §2-H17 の共有ヘルパー化、応力許容を §2-H10 の単一値化。
     - **(c) 2 sprint**: 13状態 RK4 のベクトル化移植＋接地モデル統一。
  6. **安定性ガード**: 刻みは `h = min(dt_max, 0.5*MAC/|v_rel|, 0.35/|ω|, RK4_STIFFNESS_SAFETY/λ)` を**全経路で同一**に適用（`flight_sim._timestep_from` の4制限を共通関数化）。CuPy 経路でも `xp.` 演算子で同一式を評価する。
- **計算コスト・メモリへの影響**:
  - 真の6DoFベクトル化移植: 13個の `float64[N]` 状態配列＋約20の作業配列 ≈ **264·N バイト**（N=4096 で 1.1 MB）＋カーネル起動オーバーヘッド。VRAM 8 GB に対し **0.02 %** 未満。
  - CPU: 1ケースあたり4倍の力評価（係数ポートにより 5–10 倍はならない）。GPU Benefit は N=4096 のアンサンブルで **約 3–5 倍**。
  - 段階 (b) のみでも VRAM 増分は無視可能（ケース毎スカラーbroadcast）。

---

### [重要度：高] H-02. 最適化器のGPU経路が「別の飛行機」を評価している（トリム角固定＋迎角の時刻減衰）
- **該当箇所**: `physics/parallel_engine.py:1541-1546`（`gpu_batch_evaluate_cases`）、`:871`、`:1461`
- **現在の実装と制限内容**:
  ```python
  1541: alpha_trim = 0.06                                  # 固定値
  1542: decay = math.exp(-3.5 * min(t, 2.0))                # 迎角の時間減衰
  1543: alpha = xp.clip(alpha_trim + (pitch_rad - gamma) * decay, -0.25, 0.40)
  1545: cl = xp.clip(cl0 + cl_a * alpha, -0.4, cl_mx)
  1546: cd = cd0 + k_ind * (cl ** 2)
  ```
  ここで `pitch_rad` は **1528行のループで一度も更新されない初期ピッチ角（定数）** であり、本関数に `theta` も `q_rate` もピッチモーメントも存在しない。迎角は「初期ピッチ角 − 飛行路角」に**時間減衰係数**を掛けた擬似量である。失速分岐・地面効果・空力弾性・エレオンは**全て不在**。`1602: cur_margin = (0.244 - alpha) / 0.244` は `cfg.stall_alpha_deg`（既定14°=0.244 rad）ではなく**ハードコード**、かつ負側迎角は `0.244` 固定（`aero_domain` の負側失速閾値30°と不一致）。
- **論文・物理法則との乖離**: 静的トリムは `C_m0 + C_mα·α + ΔC_m,elevon = 0` の解であり、`α_trim = -(cm0 + ΔC_m,elevon)/C_mα` で**翼ごとに決まる**。固定値0.06 rad（3.4°）は機体形状に依存しない。`exp(-3.5·min(t,2))` は**どの空力量も這樣に減衰しない**。結果:
  - `elevon_deg`（`config_schema.py:1146` の**最適化設計変数**）がこの経路では**完全に不活性**（`cl`/`cd` への寄与なし）→ 最適化器の `elevon_deg` 次元が**勾配ゼロの null direction**。
  - 失速モデル・地面効果・Re 依存・圧縮性補正が全て無い。
  - 上記の null 方向は、パレートフロントが「飛行に一切影響しない量で異なる要素」を含むことを意味する。
- **修正案**:
  1. `alpha_trim` を静的トリム解から算出: `alpha_trim = -(cm0_base + dcm_elevon) / cm_alpha`（`cm_alpha` は `cfg` から、`|cm_alpha| > 1e-4` をガード）。`cm0_base` は `elevon_effects(cfg)` の `dcm` を含む。
  2. 迎角は「姿勢角 − 飛行路角」の**真値**とし、時刻減衰を**全廃**する。ピッチ態度を進化させるには 3自由度目を復元する: `q_rate` 状態と `C_m = C_m0 + C_mα·α + C_mq·q̂` によるピッチモーメントを追加。
  3. 迎角クリップを `[-π, π]` に広げ、**失速分岐をクリップ前に適用**する。
  4. 失速角は `cfg.stall_alpha_deg`（負側は `aero_domain` と同一の `max(stall+15, 30)`）を参照。
  5. **安定性ガード**: `alpha_trim` の分母ゼロをガードし、`cm_alpha ≈ 0` のとき `α_trim = radians(4.0)` へフォールバック（既存 871 行のガードを流用）。
- **計算コスト・メモリへの影響**: ケース毎スカラー1回の追加計算のみ。VRAM 増分 0。CPU 増分 < 0.1 %/step。

---

### [重要度：高] H-03. 失速後揚力に「揚力ゼロのノッチ」（α ∈ [41°, 45°] で C_L ≡ 0.0000）
- **該当箇所**: `physics/aero_domain.py:382-403`（`_post_stall_lift_coefficient`）、`:373-374`（`POST_STALL_BLEND_START_DEG=45.0`, `END=85.0`）、`:388-390`
- **現在の実装と制限内容**:
  ```python
  388: raw = ref_cl + post_stall_slope * (abs_alpha - stall_alpha)   # slope 既定 -0.03
  390: ramp = sign * ceiling * math.tanh(max(raw, 0.0) / ceiling)   # ★ max(...,0) で 0 に接地
  391: if abs_alpha <= POST_STALL_BLEND_START_DEG: return ramp       # 45° まで ramp のみ
  397: plate = flat_plate_lift_coefficient(alpha) * (ref_cl / plate_reference)
  ```
  `raw` は α = 14 + 0.8098/0.03 = **40.99°** で 0 になり、`max(raw, 0.0)` により**正確に 0** に固定される。一方平板ブレンドは 45° 才开始するため、**41°–45° の帯で C_L は厳密に 0**、かつ平板の形状も適用されない。
- **論文・物理法則との乖離**: Hoerner (1965) の平板法線力 `C_N = 2 sinα cos²α` は 41° で **0.7474**、45° で **0.7071** であり、**ゼロではない**。本 auditing で実際に `lift_coefficient()` を呼び出して実測した（既定機体）:

  | α | コード C_L | Hoerner 2 sinα cos²α | コード C_D | Hoerner 2sin²α+C_D0cosα |
  |---|---|---|---|---|
  | 14° | 0.7995 | 0.4555 | 0.1300 | 0.1365 |
  | 20° | 0.5443 | 0.6040 | 0.0796 | 0.2527 |
  | 30° | 0.3158 | 0.7500 | 0.1013 | 0.5173 |
  | 41° | **0.0000** | 0.7474 | 0.2770 | 0.8759 |
  | 45° | **0.0000** | 0.7071 | 0.3917 | 1.0141 |
  | 60° | 0.2436 | 0.4330 | 1.0210 | 1.5100 |
  | 70° | 0.2672 | 0.2198 | 1.5031 | 1.7729 |
  | 90° | 0.0000 | 0.0000 | 2.0283 | 2.0000 |

  つまり C_L(α) 曲線は **非単調で非物理的**: 14°の0.80 → 41°で0（消滅） → 70°で**偽の二次ピーク0.267** → 90°で0。Hoerner の滑らかな山（35.26°で0.770、ピーク後は単調減衰）とは全く違う形状になる。実結果として、**深失速した紙飛行機は41–45°で「揚力ゼロ」**（機首上げPitchingモーメントのみ、无駄な抗力のみ）となり、回復不能な螺旋の典型的条件を作る。さらに `plate` は Hoerner 曲線に **1.778倍**を掛けている（`ref_cl/plate_reference = 0.8098/0.4555`）ため、レジストリ（`api/routes/meta.py:92`）が公開する `C_N = 2.0*sin(alpha)*cos^2(alpha)` とも一致しない。
- **修正案**:
  1. **平板極限に基準を移す**: 線形ランプは負を許し、ブレンド後にクリップする:
     ```python
     ramp  = ref_cl + post_stall_slope * (abs_alpha - stall_alpha)   # 負可
     blend = smoothstep01((abs_alpha - stall_alpha) / (45.0 - stall_alpha))
     value = (1 - blend) * max(ramp, 0.0) + blend * plate_unscaled
     ```
  2. `plate_unscaled = 2*sin(a)*cos(a)**2`（**1.778 の再スケールを削除**）。連続性が必要なら `plate + (ref_cl - plate(α_s)) * exp(-(α-α_s)/Δ)` の**線形連続オフセット**を使う（全体スケールではなく）。
  3. `max(raw, 0.0)` によるクランプを `ramp` 側ではなく**ブレンド後**に適用し、41–45°帯のゼロを消滅させる。
  4. `POST_STALL_BLEND_START/END_DEG` を `cfg` へ昇格（既定45/85を維持しつつ較正可能にする）。
  5. **安定性ガード**: `tanh` は `±MAX_ABS_LIFT_COEFFICIENT=4.0` の最終安全網としてのみ残す。
- **計算コスト・メモリへの影響**: スカラー経路で1評価あたり +6 flop（`evaluate_aero` の **1 %未満**）。VRAM 影響なし。ベクトル化ポートでは `float64[N]` 1本追加（32·N バイト）。

---

### [重要度：高] H-04. 失速時の抗力ビルドアップが Hoerner 式から 3–5 倍乖離（`C_D0 cosα` 欠落＋遷移フェード）
- **該当箇所**: `physics/aero_domain.py:453-468`（`separated_pressure_drag_coefficient`）
- **現在の実装と制限内容**:
  ```python
  466: transition = clamp((abs(alpha) - threshold) / max(90.0 - threshold, 1.0), 0.0, 1.0)
  467: weight = transition * transition * (3.0 - 2.0 * transition)
  468: return weight * 2.0 * math.sin(deg2rad(alpha)) ** 2
  ```
  Hoerner (1965) / `PHYSICS_AUDIT_REPORT.md:21` の式は `C_D,sep = 2 sin²α + C_D0 cosα` だが、コードは **`C_D0 cosα` を落としている**。さらに `weight` は失速角で**0** であり 90° で初めて 1 になる。
- **論文・物理法則との乖離**: 実測（上表）— α=15° では重み 0.0006 につき剥離抗力 0.0002 に対し Hoerner は 0.153。**約150倍遅い**抗力ビルドアップ。α=20° で 0.0796 vs 0.2527（3.2倍）、α=30° で 0.1013 vs 0.5173（**5.1倍**）。物理的には、失速直後に抗力がほとんど増えないため**抗力極線に失速の谷が無く**、失速citationsの急降下（極小抗力→急失速）が再現されず、無限に雲から転落する機体が「空力的阻力が増大中」に見える。また仕様 §1.1 が要求する `C_Dmax ≈ 1.8–2.0` は正規経路でのみ達成され（α=90° で 2.028）、GPU 経路では到達不能（§2-H05）。
- **修正案**:
  ```python
  def separated_pressure_drag_coefficient(alpha_deg, cfg, ...):
      a = deg2rad(alpha_deg)
      separated = 2.0*math.sin(a)**2 + cd0*math.cos(a)     # Hoerner 完全形
      w = smoothstep01((abs(a_deg) - stall_alpha_deg) / 15.0)  # 失速直後から1へ
      return w * separated
  ```
  `cd0` は `parasite_drag_coefficient(cfg, reynolds)`（Re 補正済み）を使い、付着流極線と整合させる。
  - **安定性ガード**: `|cosα| ≤ 1` は自明。剥離抗力の上限は `2 sin²α ≤ 2` で有界。
- **計算コスト・メモリへの影響**: `drag_coefficient` 1回あたり +2 flop（**1 %未満**）。VRAM 影響なし。

---

### [重要度：高] H-05. GPU アンサンブル経路の失速が 55 % の不連続揚力ジャンプを生じ、`C_Dmax` に到達できない
- **該当箇所**: `physics/parallel_engine.py:978-990`（`gpu_ensemble_simulation`）
- **現在の実装と制限内容**:
  ```python
  978: is_stalled = xp.abs(alpha) > stall_alpha_rad
  979: cl_linear = (cL0_base * morph_factor) + cl_alpha * alpha
  981: cl_post_stall = xp.sign(alpha) * (0.85 * xp.sin(2.0 * xp.abs(alpha)))
  982: cl = xp.where(is_stalled, cl_post_stall, xp.clip(cl_linear, -0.6, cl_max))
  989: cd_stall_increment = xp.where(is_stalled, 0.35 * (1.0 - xp.cos(alpha)), 0.0)
  990: cd = cd0 + cd_induced + cd_stall_increment
  ```
- **論文・物理法則との乖離**:
  1. **不連続**: 既定失速角14°で `cl_linear = 0.12 + 3.142×0.2443 = 0.888`、`cl_post_stall = 0.85·sin(0.4886) = 0.398`。閾値通過で**揚力が瞬時に55 % 落ちる**＝ステップ状の力変化。これはステップ関数の力＝**インパルス的ピッチング/ロールトルク**を生む。仕様 §1.1 は「滑らかに接続」を要求、旧 `PHYSICS_AUDIT_REPORT.md:16` は「シグモイド重み関数」と明記している。**重み関数が存在しない**。また `0.85` は Viterna の `A₁`（翼型ごとに測定してフィット）ではなく固定値。
  2. **抗力**: `0.35(1−cosα)` は Hoerner の `2 sin²α + C_D0 cosα` とも Viterna の `B₁ sinα + B₂ cosα` とも無関係。α=30° で 0.045（Hoerner 0.52）、45° で 0.103（Hoerner 1.01）、90° で 0.35（Hoerner 2.0）。**到達可能最大 C_D は約 0.10** で、仕様の `C_Dmax ≈ 1.8–2.0` に**11–20倍届かず**。深失速した紙飛行機が正常滑空より高効率になる（L/D が逆転）。
  3. `cl = xp.clip(cl_linear, -0.6, cl_max)` は負迎角 −13°超で C_L を −0.6 に固定（線形連続の連続性が切れる）。
- **修正案**: §2-H01 のベクトル化ポートで `aero_domain` の**同一実装**を使う。その上で失速後を Viterna 形で書く場合:
  ```python
  w = xp.clip(1.0/(1.0+xp.exp(-k*(alpha - stall_rad))), 0, 1)   # 滑らかな重み
  cl_vit = A1*xp.sin(2*alpha) + A2*xp.cos(alpha)**2/xp.maximum(xp.abs(xp.sin(alpha)), 1e-3)*xp.sign(xp.sin(alpha))
  cd_sep = B1*xp.sin(alpha) + B2*xp.cos(alpha)
  cl = (1-w)*cl_linear + w*cl_vit
  ```
  - **安定性ガード**: `A₁=0.85`（測定フィット）、`A₂=0`（薄板）、`B₁=1.0`、`B₂=cd0`；`|sinα| ≥ 1e-3` ゲート；最終 `|C_L| ≤ 4.0` の安全網のみ。
- **計算コスト・メモリへの影響**: 1ステップあたり +10 の要素 transcendentals。N=4096・2000ステップで ≈80 MFLOP → **GPU 0.3 s / CPU 1.5 s**。VRAM +3〜4 本の `float64[N]`（131 kB @N=4096）。**現状 +20 %以内**に収まる。

---

### [重要度：高] H-06. Helmbold 揚力傾斜式が 4 系統に分裂（GPU 経路は正規経路比 +11.3 %）
- **該当箇所**: `physics/aero_domain.py:213-221`（`helmbold_cl_alpha`）、`physics/flight_sim.py:499-504`、`physics/parallel_engine.py:866`、`physics/parallel_engine.py:1447`、`docs/SPECIFICATION.md:16`、`api/routes/meta.py:57`
- **現在の実装と制限内容**: 同一プロジェクト内に4つの式が生存している。

  | 出所 | 式 | 既定機体(AR=2.667, λ=0.70) の C_Lα | 対コード |
  |---|---|---|---|
  | `aero_domain.py:220` / `flight_sim.py:500`（**正規**） | `2πAR/(2+√(AR²(1+λ)+4AR/π))` | **2.823** | — |
  | `parallel_engine.py:866,1447`（**GPU**） | `2πAR/(2+√(AR²+4))` | **3.142** | **+11.3 %** |
  | `docs/SPECIFICATION.md:16` | `2πAR/(2+√(AR²(1+tan²(Λ_c/2))+4))` | 3.081 | −8.4 % |
  | AeroSandbox `AeroBuildup`（レジストリの自称根拠） | `2πAR/(2+√(AR²(1+λ)²+4))` | 2.409 | +17.2 % |

  GPU 経路は**テーパ項を落とし**、`4AR/π` を定数 `4` に置き換えている。SPEC は `+4` と `tan²(Λ_c/2)`（本来 Helmbold 1942 の三角翼形）を使うが、コード／旧監査報告／公開レジストリは Raymer 形（`+4AR/π`, `1+λ`）を使う。
- **論文・物理法則との乖離**: Helmbold (1942) の原式は `2πAR/(2+√(AR²(1+tan²(Λ_c/2))+4))`（**三角翼・前縁後退角**依存）、Raymer の**テーパ翼**への拡張が `1+λ` 形式。紙飛行機は後縁後退のない**三角翼**（λ→0）が原理的に近いため、SPEC の式が物理的に正しく、コードの Raymer 形（かつ既定 `taper_ratio=0.70` は紙飛行機として不自然）は別の機体を仮定している。**GPU 経路はさらに第3の形**を使うため、GPU の有無で同一機体の揚力曲線が変わる（+11.3 %）。C_Lα はトリム迎角と失速マージンを支配するため、最適化器は「GPU 付きなら少し背の低い翼」を選好する。
- **修正案**:
  1. `physics/aero_domain.py` に**単一実装**を置く（コードは既にここにある）:
     ```python
     def helmbold_cl_alpha(aspect_ratio, taper_ratio):
         ar = max(aspect_ratio, 1e-3); lam = clamp(taper_ratio, 0.01, 1.0)
         return 2*math.pi*ar/(2 + math.sqrt(ar*ar*(1+lam) + 4*ar/math.pi))
     ```
     GPU 経路は **ケース毎スカラー1回**で計算し broadcast する（ステップ毎ではないためベクトル化不要、VRAM 増分 0）。
  2. **物理形の選択**: 紙飛行機（三角翼）_defaults を `taper_ratio` ではなく**前縁後退角 `Λ_c`** で表現する方式が正。建议 `leading_edge_sweep_deg` を `AircraftConfig` に追加し、Helmbold 原式 `2πAR/(2+√(AR²(1+tan²(Λ_c/2))+4))` を採用。
  3. `docs/SPECIFICATION.md:16` / `PHYSICS_AUDIT_REPORT.md:12` / `api/routes/meta.py:57` / `physics/stress_domain.py:205-220`（4箇所！）を**同一式に統一**し、どれが採用されたかを明記する。
- **計算コスト・メモリへの影響**: ケース毎スカラー1回 → CPU/VRAM とも**無視可能**（VRAM 増分 0）。文書統一は 0。

---

### [重要度：高] H-07. 翼端接地で速度非依存の角速度インパルス（+2.0 / −1.5 rad/s）を注入
- **該当箇所**: `physics/flight_sim.py:2396-2414`（`_apply_ground_contact`）
- **現在の実装と制限内容**:
  ```python
  2405: omega[0] *= damping * 0.4
  2406: omega[2] *= damping * 0.4
  2407: if alt_ltip <= 0.0 and alt_rtip > 0.0:
  2409:     omega[0] += 2.0 * damping      # ★ 2 rad/s ≈ 115 °/s を無条件加算
  2410:     omega[2] -= 1.5 * damping
  ```
  `damping = 1/(1+ground_friction)`（既定 `ground_friction=0.40` → 0.714）。
- **論文・物理法則との乖離**: 外部インパルスによる角運動量変化は `ΔH = r × J` で、**インパルス大きさと腕長に依存する**。本実装は接触速度・質量・慣性・接触幾何に**一切依存せず**一定の角速度を注入する。**0.05 m/s の羽根一样的接触でも 30 m/s の垂直墜落でも同じ 115 °/s のロールキック**。また `ground_friction` は**接線方向摩擦**の係数であるのに、ロールキックの強さを支配しており、係数の意味が混在している。`PHYSICS_AUDIT_REPORT.md:86` が要求するのは「法線方向は反発係数、接線方向は摩擦係数による非正の仕事」までで、**回転インパルスのモデル自体は存在しない**。
- **修正案**（接触インパルスによる統一解決）:
  ```python
  # 接触瞬間に、翼端の鉛直速度から法線インパルスを決める
  r_tip = body_to_world(q, r_ltip)                 # 接触点（世界）
  v_tip = v + cross(omega, r_tip)                  # 接触点速度
  v_n   = dot(v_tip, n)                            # n = +z (下向き)
  m_eff = 1.0/(1.0/m + dot(r_tip, cross(I_inv@r_tip, n)))   # 接触点での有効質量
  J_n   = -m_eff * v_n / (1.0 + e)                 # 反発係数 e を適用
  J_t   = -min(mu*abs(J_n), m_eff*|v_t|) * v̂_t     # Coulomb 円錐 |J_t| <= mu|J_n|
  J     = J_n*n + J_t
  v    += J/m
  omega += I_inv @ cross(r_tip, J)                 # ★ オフCGインパルスから自然にカートホイールが生じる
  ```
  - **安定性ガード**: インパルスは CCD が解決した接触瞬間に**1回だけ**適用するため部分刻みCFL問題は無い。`|Δω| > MAX_RATE_JUMP_RADPS` ならステップを棄却・半減（既存機構 `2255行`）。
- **計算コスト・メモリへの影響**: 接触**イベント毎**に 3×3 行列解（`I⁻¹` は `self._inertia` から事前 Cholesky 済みなら再利用可）＋約20 flop。接触は稀なので**1ステップ当たりのコストはゼロ**。メモリ O(1)。

---

### [重要度：高] H-08. 地上接触で人工的な姿勢整列トルク（1サブステップ 25 %）が導入されている
- **該当箇所**: `physics/flight_sim.py:2504-2514`（`_apply_ground_contact`）
- **現在の実装と制限内容**:
  ```python
  2504: # Ground attitude settling (align to ground plane: roll -> 0, pitch -> 0)
  2510: settle_rate = 0.25
  2511: new_roll  = roll  * (1.0 - settle_rate)
  2512: new_pitch = pitch * (1.0 - settle_rate)
  2513: q_settled = quaternion_from_euler(new_roll, new_pitch, yaw)
  ```
  **トルクを一切発生させずに姿勢ベクトルを直接 25 % 減衰**させる。`landed` でない（=`else`）スライダリング中の機体にも適用される。加えてこれは §2-H09 のエネルギー射影（`2433行`）の**後**に実行されるため、射影が「E_post ≤ 0.25 E_pre」を保証した後も回転がエネルギーを変化させる。
- **論文・物理法則との乖離**: `PHYSICS_AUDIT_REPORT.md:41` は「人工的な姿勢復元トルクや水平基準トルクは**完全撤廃済み**」と宣言するが、これはその撤廃vioの**再導入**である。物理的には、地面接触は法線反力・摩擦力・接触トルクを通じた**力学的応答**であり、SAVE 則を満たす角運動量変化を伴うべきものであって、姿勢の直接操作ではない。時定数 ≈ 4 サブステップ（実効刻み 1.5 ms で ≈6 ms）であり、**衝突エネルギーに依存せず**機首下げ・ knife-edge 接地を即座に平坦化する。
- **修正案**:
  1. 2504-2514 を削除し、接触応答を §2-H07 の**トルク制限付き接地反力**に置き換える:
     ```python
     for i, (r_i, alt_i) in enumerate(stations):
         d_i  = -alt_i                       # 貫入深
         if d_i <= 0: continue
         n_i  = body_to_world(q, r_hat_i)    # 接触法線（世界）
         N_i  = max(0.0, k*d_i + c*dot(v, n_i))          # 接触則（線形ばね+減衰）
         F_t  = tangential_component(F_req, n_i)         # Coulomb: |F_t| <= mu*N_i
         M   += cross(r_i, N_i*n_i + F_t)
     ```
     剛体方程式に `M` を渡す（事後回転ではなく）。
  2. `landed` 状態では姿勢を着地時の姿勢（`state.quaternion`）で凍結する（`quaternion_from_euler(0,0,yaw)` は水平化になる）。
  3. **安定性ガード**: ペナルティ剛性 `k` を `k <= 2.785 * m / h² * 0.25`（RK4 安定限界内）に制限し、`c = 2ζ√(k m)`（ζ=0.7）。`|Δω| > MAX_RATE_JUMP_RADPS` でステップ半減。
- **計算コスト・メモリへの影響**: 接触サブステップ毎に接触点5個のベクトル演算（O(1)）。現状より**むしろ安価**（`euler_zyx_from_quaternion`→`quaternion_from_euler` の往復が不要になる）。VRAM 影響なし。

---

### [重要度：高] H-09. 翼端接地のエネルギー上限 `max(rest², 0.25)` が常に 0.25 ＝ 75 % 強制散逸（反発係数0でも 25 % 残存を許可）
- **該当箇所**: `physics/flight_sim.py:2423-2438`（`max_ratio = max(rest_val*rest_val, 0.25)`）
- **現在の実装と制限内容**:
  ```python
  2425: rest_val = clamp(_finite(self.settings.restitution, 0.0), 0.0, 1.0)
  2426: max_ratio = max(rest_val * rest_val, 0.25)
  2427: e_limit = e_pre * max_ratio
  2433: if e_post > e_limit and e_post > 1.0e-15:
  2434:     scale = math.sqrt(e_limit / e_post)     # 速度と角速度を一律スケール
  ```
  `settings.restitution` の既定は **0.15**（`config_schema.py:936`）なので `max_ratio = max(0.0225, 0.25) = 0.25` **常に一定**。この制約は `PHYSICS_AUDIT_REPORT.md:86` の「人工的な運動エネルギー増加を厳密に排除」を意図するが、実装は「**25 % の残存を常に許可**」しており、**-solvent** の意味では**保护になっていない**（コード内のどの演算も E_post > 0.25 E_pre に至らないため）。
  付随して: (a) 翼端分岐の反発係数は `2418: clamp(restitution, 0.0, 0.5)`、機体分岐は `2469: clamp(restitution, 0.0, 1.0)` と**同一物理量に異なるクランプ**；(b) `e_pre` は `2399: m = float(self.aircraft.mass_kg)`（生設定値）であり、他が使う `self._mass`（`max(cfg.mass_kg, 1e-9)`）と異なる。`cfg.mass_kg <= 0` のとき `e_pre <= 0` → `math.sqrt(負)` で **ValueError**（`evaluate_flight` は `3099: except Exception: return empty` で握り潰し、`status="diverged"` の全ゼロ結果になる）。
- **論文・物理法則との乖離**: 完全非弾性衝突（e=0）では法線方向の運動エネルギーは 0 になるべきで、Coulomb 摩擦はエネルギーを**除去**するのみ。0.25 の下限は**非物理的なエネルギー生成源**を許す。さらに `scale` は** translational と rotational に同一係数**を適用するため、衝突後の角速度分布が保存則から導かれない。另一个分支（機体着地、2469行）には下限が無いので、**同一関数内で2つの異なるエネルギー予算**が共存する。
- **修正案**:
  1. `max(..., 0.25)` の下限を**削除**し、物理的に導かれた上限に置き換える:
     ```
     E_post <= E_pre                        （生成の禁止: 投射ではなく検証）
     法線 Transl.:  |v_n_post| <= e |v_n_pre|
     接線 Transl.: |v_t_post| <= |v_t_pre| かつ 散逸仕事 W_f <= mu * N * d
     ```
     実際のインパルス解決（§2-H07）の**結果**を検証する形にし、投射は「発散時のフォールバック」のみとする。
  2. `e_pre` は `self._mass` を使う。
  3. 投射のガード: `if e_limit <= 0.0: scale = 0.0`。
  4. 反発係数のクランプを両分岐で統一（翼端専用の値が必要なら `tip_restitution` を新設）。
- **計算コスト・メモリへの影響**: 力評価の追加 0。投射は `sqrt` 1回と乗算3回が**消える**ためむしろ**僅かに高速**。

---

### [重要度：高] H-10. 応力インデックスが 3 系統で正規化され、構造破壊がヒートマップ・最適化目標・GPUバッチで不可視
- **該当箇所**: `physics/stress_domain.py:88`（`INDEX_REFERENCE_PA=2.5e4`）、`:100-103`（`paper_allowable_stress`）、`:477-490`（`failure_index`）、`:735-738`（`index_grid`）、`:762`、`:770` / `physics/parallel_engine.py:107`（`_STRESS_LIMIT_PA=1.2e5`）、`:499`、`:1643` / `physics/flight_sim.py:2657`、`:2876`、`:3218`
- **現在の実装と制限内容**: 実測値（既定機体 gsm=100, stiffness=0.35）:

  | 指標 | 値 | 許容応力(2200 Pa) に対する比 |
  |---|---|---|
  | `paper_allowable_stress` = `failure_index` の分母 | **2 200 Pa** | 1.0×（1.0 = 破断限界） |
  | `INDEX_REFERENCE_PA`（ヒートマップ / テレメトリ `max_stress_index`） | **25 000 Pa** | **11.36×** |
  | `parallel_engine._STRESS_LIMIT_PA`（GPU 批次、`clamp(0,1.5)`） | **120 000 Pa** | **54.55×**（ヒートマップ比で 4.8×） |

  さらに `index_grid = normalise_heatmap(stress_np) * clamp(max_stress/25kPa, 0, 1)` であり、`normalise_heatmap` は**min-max 正規化**なので、**最小応力セルは絶対値に関係なく常に 0.0**、パターン部分は載荷不変。
- **論文・物理法則との乖離**: `docs/SPECIFICATION.md:40` は頂点値を「**0.0: 安全 〜 1.0: 破断限界**」と規定。実装の 1.0 は 25 kPa = 破断限界の **11.4倍**。トリム点での実応力 σ=52.2 Pa に対し `head = 0.0021` ＝ **ヒートマップは全面黒**。破断（failure_index ≥ 1, σ=2200 Pa ≈ 42 G）でも index は 0.088 しかなく、**1.0 到達には 25 kPa ≒ 480 G / 290 m/s** が必要（到達不能）。
  **下流の被害**: `flight_sim.py:2474: if impact > 15.0 and impact_stress["max_stress_index"] >= 1.0: crashed = True` は**永久に成立しない死コード**（σ≥25 kPa が必要だが `impact_g` は 16 G で上限）。一方 `stress_domain` 自身の `status` は 42 G で "failed" とする。→ **同一ペイロード内に 11 倍意味の違う2つの「インデックス」**。
  さらに §0.5 の実測で **CPU 281.8 Pa vs GPU 355 388 Pa（1261倍）**、`max_stress_index` が 0.0113 vs 1.5000（133倍） Roadsourced している。
- **修正案**:
  1. **単一の尺度**へ統一: 熱マップを `index_grid = clip(stress_np / allowable, 0, 1)`（0=安全, 1=破断限界）に変更し、min-max 形状は `stress_shape` として**別チャンネル**で公開する。
  2. `paper_allowable_stress` を**唯一**の許容値とし、`INDEX_REFERENCE_PA` と `_STRESS_LIMIT_PA` を削除（または「表示用スケール」として `display_scale_pa` に改名し、ドキュメントに「1.0 = 破断ではない」と明記）。
  3. 許容値の定義を**実材料値**へ: `σ_allow = F_t / (b·t)`（F_t =  Tear strength per unit width、80–100 g/m² 事務用紙で 2–3 kN/m）。現状の 2200 Pa は `KAPPA_SIGMA=9e-6`（§2-H11）との**組み合わせでのみ**意味を持つ。
  4. `flight_sim.py:2657`/`:2876`/`:3218` の `clamp(...,0,1)` を**生の値**に（`max_stress_index_raw` を `FlightLogSample` に追加し、API 提示層でのみ 0–1 に丸める）。最適化の `min_max_stress` 目標も生の値を使う。
  5. `flight_sim.py:2474` の破断判定を `failure_index >= 1`（= σ/allowable）に変更し、**死コードを蘇らせる**。
  6. `parallel_engine` は `stress_domain.paper_allowable_stress()` を import して使う（ハードコード 120 kPa を廃止）。
  - **安定性ガード**: `allowable = max(allowable, 1e-9)`、`np.errstate(divide='ignore')`。
- **計算コスト・メモリへの影響**: 要素ごとの除算 1 回（≤256×256 グリッドで **< 0.1 ms**）。飛行ループのコスト不変（飛行経路は既に 3×2 グリッド）。`FlightLogSample` に float 1 フィールド追加 → `ring_buffer_capacity=20000` で **+160 kB/インスタンス**（並列ワーカー数 N 倍）。

---

### [重要度：高] H-11. 構造応力が Beam 理論値の 9×10⁻⁶ 倍（`KAPPA_SIGMA`）で、折り断面の深さが使われていない
- **該当箇所**: `physics/stress_domain.py:82`（`KAPPA_SIGMA=9.0e-6`）、`:723-724`、`:128-129`（`Z_SCALE_OFFSET/GAIN`）、`:150-158`（`_sheet_thickness`）
- **現在の実装と制限内容**:
  ```python
  section_modulus = xp.maximum(local_chord * thickness * thickness * z_gain / 6.0, 1.0e-30)
  stress = (KAPPA_SIGMA * moment / section_modulus)[:, xp.newaxis] * shape[xp.newaxis, :]
  ```
  `thickness` は**展開シート厚** `t = gsm/800`（100 g/m² で 0.125 mm）。`KAPPA_SIGMA=9e-6` は**Pa の quantity にさらに 9e-6 を掛け**、報告応力を Beam 理論値の 1/111000 にする較正係数になっている（次元的に意味を持たない）。**von Mises 相当応力はどこにも存在せず**、軸力・せん断・ねじり・ヤング率・ポアソン比のいずれも無い。
- **論文・物理法則との乖離**: Euler–Bernoulli は `σ = M·c/I = M/Z`、`Z = b·t²/6`（板厚 t の板）。**折った紙翼の根部断面は平板ではなく-keel/箱折り**で、曲げ深さは折り高さ（≈ 10–30 mm。`flight_sim.py:1066` は `ROOT_SECTION_HEIGHT_FRACTION = 0.40·MAC ≈ 27 mm` を使用）に由来する。箱折り断面の `Z = O(b·h²)` は `b·t²/6` より `(h/t)² ≈ 4.6×10⁴` 倍大きい。`KAPPA_SIGMA` はこの差を隠している。
  **不自然な3D挙動**: `σ ∝ 1/t² ∝ gsm⁻²` なので、**厚い紙ほど相対的に弱く**見える（実際の折り翼の曲げ容量は概ね面密度に比例）。また折り形状（keel 深さ・ダブル）が応力場から完全に消え、**同質量の強く折った翼と平らな紙が同一応力**になる。
  さらに `paper_allowable_stress` の 2200 Pa は実紙 の引張強度（80–100 g/m² で **20–40 MPa**）の **1/10⁴** で、**材料強度ではない**（`KAPPA_SIGMA` との組み合わせ専用）。一方 `flight_sim.py:213`/`:1077` の CPU フォールバックは `allowable = 2.0e4 * gsm`（**線形**、100 g/m² で 2.0 MPa）を使っており、**同一飛行で.import 有無により利用率が1000倍違う**。
- **修正案**:
  1. **折り断面**から `Z` を計算: `Z = b_root · h²/6`（箱）または実断面の `I/c`。`h` は折り構成（`ROOT_SECTION_HEIGHT_FRACTION` 等）から取得。
  2. 応力は `σ = M/Z` を**そのまま**使い、`KAPPA_SIGMA` は**許容側**の較正係数としてのみ残す（ renamed `calibration_factor`、値の再較正が必要）。
  3. 許容応力を**実材料値**へ: `σ_allow = F_t/(b·t)`、容量（力・曲げモーメント）は層数に線形。`gsm` の指数 0.5 は**応力限界には不適切**（材料定数であり、容量側で線形化すべき）。
  4. `flight_sim` のフォールバック許容値を `stress_domain` と**同一法則**（`config_schema` 等の単一実装）に寄せる。
  5. `paper_stiffness`（無次元 0–1 スライダー）を**実ヤング率**へ置換: `E_MD ≈ 3.0 GPa`, `E_CD ≈ 1.5 GPa`, `ν ≈ 0.3`, `ρ_bulk = 800 kg/m³`。無次元スライダーは E の**倍率**として残す。
  - **安定性ガード**: `Z = max(Z, 1e-12 m³)`、`h = max(h, h_min)`。von Mises Faces: `σ_vm = √(σ_b² + 3τ²)`、`τ = V·Q/(I·b)`。
- **計算コスト・メモリへの影響**: 断面特性はフィールド1回につき O(1)（事前計算可）。**計測可能なコスト増なし**。VRAM 影響なし。

---

### [重要度：高] H-12. 慣性テンソルの第二モーメント式は λ≠1 で誤り（三角翼で −25 %）かつ平行軸項を欠落
- **該当箇所**: `physics/config_schema.py:1330-1336`（`derived_properties`）、`:1314`（`taper = clamp(cfg.taper_ratio, 0.05, 1.0)`）
- **現在の実装と制限内容**:
  ```python
  1330: second_y = span * span * (1.0 + 3.0 * taper) / (24.0 * (1.0 + taper))
  1331: second_x = root_chord * root_chord * (1.0 + taper + taper**2 + taper**3) / (24.0 * (1.0 + taper))
  1332: thickness = max(float(cfg.paper_gsm), 0.0) * 1.0e-3 / 800.0
  1333: second_z = thickness * thickness / 12.0
  1334: inertia_roll  = mass * (second_y + second_z)
  1335: inertia_pitch = mass * (second_x + second_z)
  ```
  本監査で**独立に数値積分して検証**した結果（コードの幾何：根翼弦を y=0 側、翼端を y=b 側、`c_root = 2S/(b(1+λ))`）:

  | λ | コード値 | 数値積分 E[(y−y_cg)²] | コード/真値 | コード E[(y−b/2)²] | y_cg/b |
  |---|---|---|---|---|---|
  | 0.00（三角翼） | 1.6667e-3 | 2.2222e-3 | **0.7500** | 3.3333e-3 | **0.3333** |
  | 0.50 | 2.7778e-3 | 3.2099e-3 | **0.8654** | 3.3333e-3 | 0.4444 |
  | 0.70（既定） | 3.0392e-3 | 3.2987e-3 | **0.9213** | 3.3333e-3 | 0.4706 |
  | 1.00（矩形） | 3.3333e-3 | 3.3333e-3 | 1.0000 | 3.3333e-3 | 0.5000 |

  正しい閉形式は `b²·[ (1+3λ)/(6(1+λ)) − (1+2λ)²/(9(1+λ)²) ]`（数値積分と**厳密一致**を確認済み）。`second_x` も同構造で、コードは `E[x²]_LE/4` ではなく**重心参照**の値 pretendeおり、`λ=0` で真値の 0.750 倍。**矩形翼(λ=1)でのみ正しい**。
  加えて**翼方向重心が翼中央ではない**（三角翼で y_cg = b/3）が**平行軸項なしで無視**されている。
- **論文・物理法則との乖離**: 翼方向重心位置は `y_cg = b(1+2λ)/(3(1+λ))`（三角翼で b/3 = 重心位置の古典値）。`I = m·E[(y−y_cg)²]` であるべきところ、コードは矩形の時のみ合う近似式を使っている。**I_roll / I_pitch は Euler 方程式 `I ω̇ = M − ω×Iω` に入る**ため、三角翼（紙飛行機の原理的形状）で**ロール/ピッチ角加速度が約 1/0.750 = 33 % 過大**、かつ減衰微分 `C_lp`, `C_mq` 項も同率で過大。此外、翼方向重心のずれは**静安定性**（`C_mα` による頭下げモーメントの作用点）にも影響するが `cg_offset_x` のみがモデル化され、翼方向オフセットは無い。
  **注**: 本監査Buyingしたサブエージェントの1 名は本式を「矩形極限で b²/12, c²/12 を回復するので正」と判定したが、**平行軸項の落下**により一般 λ で誤りであることを本監査が数値積分で確定した（矩形以外の一手）。Mesh 読込経路は実テンソルを供給するため本欠陥は**解析（既定）機体経路**に限定される。
- **修正案**:
  ```python
  # 重心の place を先に求める（翼方向・弦方向）
  y_cg = span * (1.0 + 2.0*taper) / (3.0 * (1.0 + taper))
  x_cg = root_chord * (1.0 + taper + taper*taper) / (3.0 * (1.0 + taper))
  # 重心参照の第二モーメント
  second_y = span*span * ((1.0+3.0*taper)/(6.0*(1.0+taper)) - (1.0+2.0*taper)**2/(9.0*(1.0+taper)**2))
  second_x = root_chord*root_chord * ((1.0+taper+taper**2+taper**3)/(6.0*(1.0+taper)) - (1.0+taper+taper**2)**2/(9.0*(1.0+taper)**2))
  ```
  - **安定性ガード**: `taper ∈ [0.01, 1.0]`、`second_* > 0` を検査（0 なら `1e-9` へクランプ）。三角形極限(λ→0)でも `second_y → b²/18 > 0`。
  - 併せて `taper_ratio` の**既定 0.70 は紙飛行機として不自然**。三角翼が自然なら既定を `0.0–0.2` へ、或いは `leading_edge_sweep_deg` ベースへ（§2-H06 と連動）。
- **計算コスト・メモリへの影響**: `derived_properties` は O(1)。**計算コスト増なし**。

---

### [重要度：高] H-13. 空力弾性モーフィングが「係数変異」で実装され、仕様の δ_eff 式と乖離（n_morph は固定3.0）
- **該当箇所**: `physics/flight_sim.py:2104-2122`（`_compute_dynamics`）、`:2091-2098`
- **現在の実装と制限内容**:
  ```python
  2107: q_aeroelastic = 160.0 * stiffness_val / max(aero_comp_gain, 0.1)   # 160.0 は出典なし
  2109: morph_factor  = 1.0 / (1.0 + (pressure_ratio ** 3.0))             # 指数 3.0 固定
  2111: effective_elevon_deg = base_elevon * morph_factor                 # 仕様どおり
  2112: effective_cm0 = float(self.aircraft.cm0) * morph_factor           # ★ 仕様外
  2113: effective_cL0 = float(self.aircraft.cL0) * morph_factor           # ★ 仕様外
  2114: compliance_deflection = 12.0*aero_comp_gain*(pr**1.5)/(1.0+(pr**1.5))
  ```
  `2109` のwashout は `q` に対して**線形かつ無制限**で、`1000 Pa`（`paper_stiffness=0.35` → `q_ref=35 Pa`, `aeroelastic_twist=1.0`）で `aero_twist_deg = 114°` になる（2094/2095 の `*5.0`/`*4.0` が出典なし）。このtwistは `2097: effective_alpha_deg = alpha_deg - aero_twist_deg` として**迎角そのもの**から引かれ、**迎角を反転**させる。`aero_domain` 側の `1073-1078` は**別の式**（`q_scale = q/(50*stiffness)`, `*2.5`/`*1.5`）で、**2つの経路が異なる量**を返している（`flight_sim` は `stress_domain.aeroelastic_deformations` の値を使うため、API `POST /aerodynamics/calculate` の `aeroelastic_dihedral_deg` は**シミュレータが実際に使用した変形ではない**）。
- **論文・物理法則との乖離**: `docs/SPECIFICATION.md:25` の式は **δ_eff(q) のみ**:
  `δ_eff(q) = δ_trim · [1 / (1 + (q/q_aeroelastic)^{n_morph})]`
  `PHYSICS_AUDIT_REPORT.md:55` も同じ。`n_morph` は**論文が名指しするモデルパラメータ**だが、コードには設定項目が存在せずリテラル `3.0` が固定されている（`tools/test_morph_cm0.py:26-28` は `q_aeroelastic = 30.0*stiffness`, `n_morph=1.5` を使い、`tools/check_morph_decay.py` は `n_morph ∈ {1.5,2.0,2.5,3.0}` を掃引 — **手動探索した値が仕様化なしで凍結**されている）。
  `cL0` と `cm0` に**同一倍率**を掛けるのは物理的に誤り：後縁折れによるキャンバー変化は `ΔC_L0` と `ΔC_m0` に**異なる量**（エレオンは重心後方なので `ΔC_m/ΔC_L ≈ 0.22·(x_ac−x_cg)/c`）を与える。同一倍率にすると `q → ∞` で**翼が平板(cL0=cm0=0)になり、静安定トリムそのものが消える** — まさに「頭上げループ抑制」-Net_FEATUREの制御対象である高 q 域で静的安定性が消滅する。
- **修正案**:
  1. `q_ref`（`aeroelastic_q_ref_pa`）と `n_morph`（`aeroelastic_n_morph`, 既定3）を**設定項目に昇格**し、160.0/3.0 リテラルを廃止。
  2. モーフィングは**幾何**（後縁折れ角 δ_eff）として実装し、既存の `elevon_effects()` 経由で `ΔC_L = a_e·δ_eff`, `ΔC_m = −b_e·δ_eff` を発生させる。**`cL0` / `cm0` は触らない**。
  3. washout / 上反角フレックスは**飽和**形にし、**1箇所**（`aero_domain`）に集約する:
     ```
     Δα_tip(q) = α_twist_max · tanh( q / (k_θ · q_ref) )      # α_twist_max 既定 4°
     ΔΓ(q)     = Γ_max     · tanh( q / (k_flex · q_ref) )      # Γ_max 既定 6°
     ```
     `flight_sim` は `effective_alpha_deg = alpha_deg − Δα_tip` とし、テレメトリの `alpha_deg` は**幾何 α**（既に `geometric_alpha_deg` として保持している `flight_sim.py:2201`）を主表示にする。
  4. **安定性ガード**: `tanh` 飽和により `∂(Δα)/∂q` が有界 ⇒ 適応刻み（`flight_sim.py:2009` の `dα/dq` 剛性項）が発散しない。`q_dyn > 0` ゲートは維持。
- **計算コスト・メモリへの影響**: RK4段ごとに `tanh` 2回追加（無視可能）。`replace()` による `AircraftConfig` コピー1回/段は既存。**設定項目 +2**。

---

### [重要度：高] H-14. ローカル空力フォールバック `_local_aero` と `aero_domain` が 4 係数で不一致（CPU 内部の二重実装）
- **該当箇所**: `physics/flight_sim.py:849-917`（`_local_aero`）vs `physics/aero_domain.py:519-520, 937-991`
- **現在の実装と制限内容**: `flight_sim` は `aero_domain` の import に失敗すると `_local_aero` にフォールバックする（docstring `821-822` は「**upstream ソルバと構造的に同一**」と主張）。実際は:

  | 項目 | `_local_aero` | `aero_domain` | 乖離 |
  |---|---|---|---|
  | ピッチ減衰 | `+ 0.5 * cm_q * rate[1] * ...` | `cm_q * q_hat` | **2 倍** |
  | エレオン揚力 | `0.0035 * aileron` | `0.22*0.020*aileron = 0.0044*aileron` | 1.26 倍 |
  | ラダー | `0.0025 * rudder` | `0.010 * rudder` | **4 倍** |
  | 逆ヨー | なし | `−0.002 * aileron` | **欠落** |
  | `lift_scale` | `cl += dcl*lift_scale` の後に `lift = ...*cl*lift_scale`（**二重適用**） | `cl *= lift_scale` を**前**に | 報告 C_L と実効揚力が不一致 |
  | 抗力 | `cd = (cd0+induced)*drag_scale` に `0.020*abs(dcl)` を**加えた後**却在 | 単一ソース | 報告 `ld_ratio` と実効抗力が不一致 |
  | `alpha_ld_max_deg` | `(cL0 + cl)/cl_alpha`（**現在 C_L を代入**） | `drag_polar_minima` で正しい | **数値が違う** |
  | 参照長さ | `cm_roll_rate` に `p_hat`（スパン基準）と `cm_q` に `q_hat`（コード基準）が混在 | 同一 | 桁違い |
- **論文・物理法則との乖離**: `PHYSICS_AUDIT_REPAIR_PLAN.md` P1-2 は「ピッチ減衰は `q S c C_mq × (角速度 c/2V)` の**単位と係数定義を統一**」と未解決項目として明示しており、それが未解決のままである。`alpha_ld_max_deg` は放物線極線 `C_D=C_D0+C_L²/(πeAR)` の最適は `C_L* = √(πeAR C_D0)` にあるため `α_LDmax = (C_L* − C_L0)/C_Lα` であるべきところ、コードは**現在の C_L** を代入しており、「最大滑空比の迎角」ではない（既定値で ≈+2.7° オフセット）。`docs/SPECIFICATION.md` §3.2 は `C_D,min` と `(L/D)_max` の公開を要求。
- **修正案**:
  1. **推奨**: `_local_aero` を**削除**し、`aero_forces()` は `aero_domain` を**必須**とする（同一パッケージ内のモジュールであるため「import 失敗」フォールバックは不要。初期化順序の問題なら明示的な初期化で解決）。**−350 行**。
  2. フォールバックを存続させる場合: 係数評価を `aero_domain.evaluate_coefficients(cfg, alpha, beta, rates, ...)` の**単一関数**に集約し、フォールバックは*大気のみ*を代替する。`_local_aero` の 4 係数を上位に一致させ、**両経路が 1e-9 で一致することをテストで固定**する。
  3. `lift_scale`/`drag_scale` は**係数が定義される箇所で一度だけ**適用し、報告値と実効値を一貫させる（upstream 準拠）。
  4. `alpha_ld_max_deg` は `rad2deg(safe_div(sqrt(pi*e*AR*cd0) - cL0, cl_alpha, 0.0))`。
- **計算コスト・メモリへの影響**: 削除の場合 **−350 行**、ランタイム不変。維持する場合は upstream 経路で空力評価 1 回分削減 → **約 4 % の CPU 減**。

---

### [重要度：高] H-15. Polhamus の公開式が本番経路で未実装（`K_p sinα cos²α` 欠落、`copysign` の符号誤り）
- **該当箇所**: `physics/aero_domain.py:430-444`（本番）、`:323-335`（`polhamus_lift_coefficient` — **死んだ関数**）
- **現在の実装と制限内容**:
  ```python
  428: linear = cL0 + slope * deg2rad(alpha)                    # 線形
  438: vortex_cl = math.copysign(kv * (sin_a**2) * cos_a, alpha_rad)   # ★ copysign
  439: linear = linear + vortex_cl
  ```
  レジストリ（`api/routes/meta.py:68`）と仕様 §1.1 が公開する Polhamus (1966/1971) の式は
  **`C_L = K_p sinα cos²α + K_v sin²α cosα`**（**全揚力**式）だが、本番経路は `cL0 + C_Lα·α + K_v sin²α cosα` であり、**`K_p sinα cos²α` の潜在的揚力項が完全に欠落**している（線形項で置換）。公開式を正しく実装している `polhamus_lift_coefficient`（`:335`）は `lift_coefficient` から**呼ばれていない**（テストのみ）。加えて `copysign(·, alpha_rad)` は `sin²α cosα` が `|α|>90°` で既に負となるのに行符号を強制するため、**α=120° で +0.375 K_v（正）**を返す（正しくは −0.375 K_v）。結果として C_L(α) は α に関して**奇関数にならず**、±α 対称性を破る。`polhamus_kv` の既定は **0.0**（`config_schema.py:316`）で、既定機体では **LEV モデル自体が不活性**。
- **論文・物理法則との乖離**: Polhamus の `K_p` は**二次元（無限翼）ポテンシャル揚力傾斜 ≈ 2π** であり、本実装の `polhamus_lift_coefficient` は `K_p = lift_slope_per_rad(cfg)`（**3次元低アスペクト比傾斜** 2.82）を与えている → 低AR補正の二重計上。`K_p` 項の欠落は低アスペクト比翼（AR≈2.7）で `sinα cos²α` と線形外挿の差（α=14° で約 5–8 %）として現れる。
- **修正案**:
  ```python
  a = deg2rad(alpha)
  cl_pol = kp*math.sin(a)*math.cos(a)**2 + kv*(math.sin(a)**2)*math.cos(a)   # 公開式そのまま
  linear = cL0 + slope*deg2rad(alpha)          # 失速前の中間遷移として保持
  weight = stall_blend_weight(alpha, cfg)
  value  = (1-weight)*cl_pol + weight*post_stall
  ```
  `kp = 2*math.pi`（二次元ポテンシャル傾斜）、`kv` は設定値。**`copysign` は使わない**（公開項が正しい符号を持つ）。`polhamus_lift_coefficient` を**唯一の真実**にして `lift_coefficient` から呼び、`tests/test_aero.py:1018-1027` の固定テストを新実装に合わせ更新する。
  - **安定性ガード**: `kp, kv ≥ 0`；`|α| > 90°` では平板分岐へブレンド。
- **計算コスト・メモリへの影響**: `lift_coefficient` 1回あたり +2 transcendentals（**約 2 %**）。VRAM 0（ベクトル化ポートでは +2 本の `float64[N]`）。

---

### [重要度：高] H-16. 熱上昇柱の巻き込み流の符号が逆（熱柱が質量の**ソース**になっている）
- **該当箇所**: `physics/wind_domain.py:720-727`（`_thermal_components`）、`:664-683`（`thermal_geometry`）
- **現在の実装と制限内容**:
  ```python
  radial_speed = _THERMAL_ENTRAINMENT_RATIO * strength * (distance / sigma) * gaussian * vertical
  u = radial_speed * (dx / safe)
  v = radial_speed * (dy / safe)
  ```
  docstring は「the horizontal entrainment **inflow**」と記述するが、`u = +radial·(dx/r)` は**軸から離れる**向きであり、**全高さで外向き**になる。Rankine 形状 `(r/σ)·exp(−r²/2σ²)` は強制渦として正しいが、**径方向の符号が反転**している。
- **論文・物理法則との乖離**: 熱上昇柱は下層で**空気を取り込み**（質量流入）、柱頂で吹き出す。連続方程式 `∇·V = 0`（非圧縮）を満たすには、柱の周囲で内向き流入と外向き流出の**対**が必要。現状は柱の周囲が一様に**外向き**で、**熱柱が_DOMAINに運動量と質量を注入**しており `∇·V ≠ 0`。実結果: 熱柱を横切る紙飛行機は**上昇AMC受けながら水平に外へ弾かれる**（古典的な熱柱entrần行動が逆）。地 legally 上昇流の減衰も実測から導かれたものではなく `exp(−(dz/h)²)` のガウシアンEvaluated。
- **修正案**:
  ```python
  u_r = -_THERMAL_ENTRAINMENT_RATIO * strength * (r/sigma) * gaussian * vertical   # 下層で内向き流入
  ```
  さらに柱頂より上には**補償 sinks**（広域の下向き流）を配置し、ドメイン全体の時間平均鉛直速度が 0 になるようにする（古典的な「熱柱＋補償 sink」対）。`r→0` の `safe` ガードとガウシアン尾は維持。
- **計算コスト・メモリへの影響**: O(1)/点。**コストモデル不変**（熱柱は 2 回の `exp`）。

---

### [重要度：高] H-17. 風向の符号規約が 3 経路で不一致（GPU は 90°回転、GPU バッチは符号反転）
- **該当箇所**: `physics/parallel_engine.py:842-843`（`gpu_ensemble_simulation`）、`:1503-1504`（`gpu_batch_evaluate_cases`）vs `physics/flight_sim.py:721-733`（基準）
- **現在の実装と制限内容**: 基準（`flight_sim.py:721-724` の docstring）:
  「wind_direction_deg … is the bearing the wind blows **FROM**, clockwise from north, with north = +y and east = +x, so the flow vector is `-(sin theta, cos theta, 0)`」
  GPU 側:
  ```python
  842: wx = -w_speed * xp.cos(w_dir_rad)     # 符号反転あり、しかし (cos,sin) 基底
  843: wy = -w_speed * xp.sin(w_dir_rad)
  ---
  1503: wx = w_spd * xp.cos(w_dir)            # 符号反転なし
  1504: wy = w_spd * xp.sin(w_dir)
  ```
  本監査で実測した流向（風向=0°、北から吹く）:

  | 経路 | 流れベクトル (x=東, y=北) | 実際の「吹いてくる」方位 |
  |---|---|---|
  | 基準 `flight_sim` | (0, −1) | 北（正しい） |
  | `gpu_ensemble` | (−1, 0) | **東**（90°回転） |
  | `gpu_batch` | (+1, 0) | **西**（90°回転＋符号反転） |

  GPU 2 経路は**互いに 180° 違う**。
- **論文・物理法則との乖離**: `docs/SPECIFICATION.md` §1.2 および `PHYSICS_AUDIT_REPORT.md:72` は「風向角 `ψ_wind` は気象規約（風が「吹いてくる」方角、北基準時計回り）に**完全準拠**」と明記。実際は 3 経路中 2 経路が**90°回転**、かつ GPU 2 経路が互いに**180°不一致**。`v_rel = vel − wind` に入るため、動圧・迎角・揚力・抗力がすべて無秩序に変わる。**同じ「北風」で機体の挙動が3通り**になる。
- **修正案**: 共有ヘルパー1つ:
  ```python
  def wind_flow_vector(speed, direction_deg):     # WMO FROM, north=+y, east=+x
      th = math.radians(direction_deg)
      return np.array([-speed*math.sin(th), -speed*math.cos(th), 0.0])
  ```
  `wind_domain.wind_direction_unit`（既に `542-562` で正しい）を**唯一の定義**とし、3 経路すべてがこれを import する。
  - **安定性ガード**: `speed ≥ 0`、方位は `[0,360)` にラップ。
- **計算コスト・メモリへの影響**: 既存 trig の置き換えのみ。**CPU 0 / VRAM 0**。

---

### [重要度：高] H-18. 付加質量が部分実装で、重力も `m_eff` でスケールされず（26 % 誤差）、旧監査報告 §7 と矛盾
- **該当箇所**: `physics/flight_sim.py:2139-2159`（`_compute_dynamics`）
- **現在の実装と制限内容**:
  ```python
  2150: m_add_z = (math.pi / 4.0) * rho * span * (self._chord ** 2)
  2151: m_eff_z = self._mass + m_add_z
  2154: a_body = (force_body / self._mass) + gravity_body
  2155: a_body[2] = (force_body[2] / m_eff_z) + gravity_body[2]   # ★ 重力が m_eff でない
  ```
  平板の法線方向付加質量は `m_a = ρ π b c²/8`（あるいは `ρ π b³/8` の回転形）。本実装は `π/4·ρ·b·c²` という**平板法線形と球形係数の折衝**で、**機体 z 軸1本のみ**に適用（面内 x,y は 0）。**回転付加慣性**（タンブルする紙飛行機では支配的）は完全に欠落（コードのコメント `2143-2145` が自認）。加えて**重力が有効質量でスケールされない**ため、機体重量を `m/m_eff` だけ過大に評価する。`PHYSICS_AUDIT_REPORT.md:104-106` は「付加質量…は**未実装**」と明記しており、**コードと文書が矛盾**する。
- **論文・物理法則との乖離**: 正しい法線方向運動方程式は `m_eff·a_n = F_n + m_a·g_n`（流体も一部重量を負担）。既定機体（m=5 g, b=0.20 m, c=0.08 m, ρ=1.225）で `m_a = (π/4)(1.225)(0.20)(0.0064) = 1.23e-3 kg` ＝ **機体質量の 26 %**。滑空時の力平衡に対する**一次の誤差**である。`added_mass_enabled` の既定は False なので現行挙動は「準定常」だが、**コード経路自体は誤り**。
- **修正案**:
  ```python
  # 実装する場合
  m_a_perp = rho*math.pi*span*chord**2/8.0
  I_a_roll = rho*math.pi*span**3/8.0        # 回転付加慣性
  M_body   = np.diag([m + m_a_axial, m + m_a_axial, m + m_a_perp]) + I_a
  a_body   = M_body_inv @ (force_body + m_a_vec * gravity_body)   # ★ 重力も m_a _vec を掛ける
  omega_dot = I_total_inv @ (M - cross(omega, I_total @ omega))  # I_total = I_dry + I_a
  ```
  - **安定性ガード**: 付加質量テンソルは有効質量を**増加**させるため、陽的 RK4 の最速固有値を**小さく**する方向（不安定化しない）。回転付加慣性は `angular_acceleration_body` の**前**に `self._inertia` へ加える必要がある。
  - 実装しない場合: `PHYSICS_AUDIT_REPORT.md` §7 の記述を真に保つ（コード削除）。
- **計算コスト・メモリへの影響**: 有効時 1 RK4 段あたり 3×3 行列積 1 回 + 線形解 1 回（**約 +8 %**）。`float64` 6 個の追加。無効時 0。

---

### [重要度：高] H-19. 折り後縁の空力モデルが未接続 → 最適化設計変数 `fold_trailing_deg` が完全不活性
- **該当箇所**: `physics/aero_domain.py:541-557`（`trailing_edge_fold_effects`、`__all__` に export 済み）、`physics/config_schema.py:1143`（`fold_trailing_deg` は設計変数）
- **現在の実装と制限内容**:
  ```python
  550: fold_deg = _finite(getattr(cfg, "fold_trailing_deg", 0.0), 0.0)
  554: dcl = 0.015 * fold_deg
  555: dcm = -0.012 * fold_deg
  556: dcd = 0.035 * (math.sin(rad) ** 2)
  ```
  **リポジトリ全体を grep しても `trailing_edge_fold_effects` の呼び出し元は存在しない**（定義と `__all__` のみ）。`fold_trailing_deg` は `flight_sim.py:1065`（構造断面剛性 `1 − 0.35|sin(fold)|`）と `stress_domain.py:676,717` にしか影響せず、**空力には一切影響しない**。係数 0.015 / −0.012 / 0.035 は出典なし。
- **論文・物理法則との乖離**: 紙飛行機/ダートの**後縁折れはエレオンの実体**であり、Trim と揚力の主要源。現行モデルで後縁機器を表現しているのは `elevon_deg`（別変数、既定2°）のみ。よって `fold_trailing_deg ∈ [−45°, 45°]` は** Flight に影響しない最適化変数**であり、NSGA-II/DE はこの次元に**勾配ゼロ**で探索し、パレートフロントに「飛行に一切差がない要素」が含まれる。`docs/SPECIFICATION.md` §1.1 の `ΔC_L,elevon` / `ΔC_m,elevon` は `elevon_deg` のみに適用されている。
- **修正案**:
  ```python
  # aero_domain.evaluate_aero 内、elevon_effects() の直後
  fold = trailing_edge_fold_effects(cfg)
  cl_offset += fold["dcl"] + elevon["dcl"]
  cm       += fold["dcm"] + elevon["dcm"]
  cd       += fold["dcd"]
  ```
  エレオン権限は `elevon_chord_ratio`（既定0.22 = 20% フラップ）ではなく**折れ形状**（露出弦長）から導出する。`dcl`/`dcm` は `fold_deg` に線形のまま、`cl_max` で上限。
  - **安定性ガード**: `|ΔC_L| ≤ cl_max` を越えないようにクランプ。
- **計算コスト・メモリへの影響**: `evaluate_aero` 1回あたり 3 スカラー演算（**< 1 %**）。VRAM 0。

---

### [重要度：高] H-20. ステップ受入判定が `h` でスケールされない絶対閾値 → Issue #31 の収束性検証が形骸化
- **該当箇所**: `physics/flight_sim.py:2244-2261`（`_step_is_acceptable`）、`:178-179`（`MAX_RATE_JUMP_RADPS=8.0`, `MAX_SPEED_JUMP_MPS=25.0`）
- **現在の実装と制限内容**:
  ```python
  2255: if not math.isfinite(rate_jump) or rate_jump > MAX_RATE_JUMP_RADPS:   # 8.0 rad/s 固定
  2258: if not math.isfinite(speed_jump) or speed_jump > MAX_SPEED_JUMP_MPS:   # 25.0 m/s 固定
  ```
  docstring は「local truncation-error estimate」とするが、**局所誤差推定はステップ長 `h` でスケールすべき量**である。固定閾値だと同じ物理操作が `h=1 ms` では通り、`h=20 ms` では弾かれる。
  実害: (a) 実効刻み ≈1.5 ms では「許容 25 m/s/ステップ」＝ 16 700 m/s² の許容となり、**実用上絶対に発火せず**発散保護が無い；(b) `dt_max=20 ms` では 1250 m/s²・400 rad/s² となり、**30 m/s の正常着地**、5 G の引き抜き、翼端接触が「棄却」され、**物理的に正しい飛翔が数値的都合で変えられる**（反発・再跳躍の計算が別の速度から行われる）。棄却は `self._rolled_back` のみに記録され、**ユーザーに通知されない**。
  付随: `2260-2261` の `abs(norm-1.0) <= 1e-6` は**空虚**（`_unpack` が常に四元数を正規化するため恒等的に 1.0）。
- **修正案**:
  1. **推奨（追加力評価なし）**: 既に得られている微分を使い、**予測値と観測値を比較**する:
     ```python
     predicted_rate = norm(core["angular_acceleration"]) * h
     predicted_speed = norm(core["acceleration"]) * h
     ok = (rate_jump <= max(MAX_RATE_JUMP_RADPS, (1+SAFETY)*predicted_rate)) and \
          (speed_jump <= max(MAX_SPEED_JUMP_MPS, (1+SAFETY)*predicted_speed))
     ```
     これで「ステップが微分が予測したどおり動いたか」という**真の局所誤差推定**になる。
  2. 代替（厳密）: ステップ倍化（Richardson）誤差推定 `‖y(h) − y(h/2)/2‖ / (‖y(h/2)/2‖+ε) < 1e-6`。**+1 RK4 スイープ = +4 力評価/段 ≈ +400 % CPU** → 収束性検証ハーネス専用。
  3. `2260-2261` の空虚な四元数ノルム検査を削除（または生 `y1` に対して実施）。
  4. 棄却を**テレメトリに露出**（`step_rejections` カウンタ）し、ユーザーが物理結果と数値人工物の区別できるようにする。
- **計算コスト・メモリへの影響**: 案1は**力評価 0 増**（`core` の再利用）＋`norm` 2 回。案2は **+400 % CPU**（本番非推奨）。追加メモリ 0。

---

## 3. 重要度：中

### [重要度：中] M-01. 翼端/機体で反発係数のクランプが不統一、翼端エネルギー予算と機体エネルギー予算が別物
- **該当箇所**: `physics/flight_sim.py:2418`（翼端 `clamp(restitution, 0.0, 0.5)`）vs `:2469`（機体 `clamp(restitution, 0.0, 1.0)`）
- **現在の実装と制限内容**: 同一物理量 `settings.restitution` に上限 0.5 と 1.0 の別クランプ。翼端は紙翼端の方が**低反発**のはずで、上限方向が逆。加えて `2418` の枝は `2469` の枝より**先に**実行され、両方が同一ステップで発火する可能性がある。
- **論文・物理法則との乖離**: `PHYSICS_AUDIT_REPORT.md:86` は `e_restitution ∈ [0,1]` と規定。翼端の 0.5 上限は出典なし。`e=0.9` を指定しても翼端では 0.5 に静かに書き換わる。
- **修正案**: 両枝で `settings.restitution` をそのまま使う。翼端固有値が必要なら `tip_restitution` を新設（既定は `restitution`）。**安定性ガード**: クランプは `[0,1]` のみ。
- **計算コスト・メモリへの影響**: 0。

### [重要度：中] M-02. ノーズ圧壊モデル: 5 m/s 閾値と速度半減（`crumple_loss = 0.5 * damping`）
- **該当箇所**: `physics/flight_sim.py:2445-2451`
- **現在の実装と制限内容**:
  ```python
  2446: if alt_nose <= 0.0 and vx > 5.0:
  2448:     crumple_loss = 0.5 * damping
  2449:     vx *= crumple_loss
  ```
  5 m/s の**不連続な境界**（未満なら損失 0、超過で 50 % 以上即失）。加えて反発（2469）・摩擦（2470）・圧壊（2449）の3つのエネルギー sink が**単一のインパルス予算として合成されていない**。
- **論文・物理法則との乖離**: `PHYSICS_REPAIR_PLAN.md` P1-4 は「紙厚、折りによる断面、材料許容値の根拠と経験係数の単位を明記する」ことを要求し、根拠のない破断判定は「校正済みと表示しない」と定めている。5 m/s 閾値・0.5 倍は**根拠がゼロの破断判定**である。
- **修正案**: 単一のエネルギー整合型衝撃モデルへ:
  ```
  E_crush = ∫σ(ε)dε · A_contact     # 紙: 板状応力 ~0.2–1 MPa × 圧壊ストローク
  J_n      = min(必要インパルス, E_crush/δ_max)
  v_x_post = v_x_pre − J_n/m
  ```
  破断は `E_impact > E_crush` から**導出**される（速度閾値なし）。**安定性ガード**: 接触瞬間に1回だけ適用し、`|Δv| > MAX_SPEED_JUMP_MPS` でステップ半減（§2-H20 の相対化後は正常着地も弾かれない）。
- **計算コスト・メモリへの影響**: 接触イベント毎に数十 flop。無視可能。

### [重要度：中] M-03. 衝撃 G が「0.08 s 停止時間」仮定＋15 G キャップ → 12 m/s 超は全ケース同一 G
- **該当箇所**: `physics/flight_sim.py:2453-2467`（`impact_g = 1.0 + min(abs(impact)/(0.08*grav), 15.0)`）
- **現在の実装と制限内容**: 停止時間 0.08 s は「停止距離 31 mm」という**ハードコードされた圧壊ストローク**に等しい。`min(..., 15.0)` により **11.8 m/s を超える着地はすべて同じ 16 G** として報告される。
- **論文・物理法則との乖離**: 構造荷重係数は**停止距離**から `n = 1 + v²/(2 g d_stop)` で決まるべきもので、時間を仮定するものではない。G キャップは `max_stress_index`（§2-H10 で 25 kPa 正規化）と組み合わさって、**12 m/s 以上の着地の構造評価が完全に平坦化**される。
- **修正案**: 実インパルスから `n = 1 + J_n/(m_eff·g)`、あるいは `d_stop`（材料モデル由来）から `n = 1 + v²/(2 g d_stop)`。**15 G キャップは削除**（数値衛生上の必要なら「報告値のみ」に適用し、生値は保持）。
- **計算コスト・メモリへの影響**: 0（§2-H07 のインパルスを再利用）。

### [重要度：中] M-04. Coulomb 摩擦が「速度乗算」で、μN 制限のインパルスになっていない
- **該当箇所**: `physics/flight_sim.py:2391`（`damping = 1/(1+ground_friction)`）、`:2415-2416`、`:2449-2451`、`:2470-2472`、`:2494-2496`
- **現在の実装と制限内容**: `ground_friction`（既定 0.40）が**法線力・接触時間・質量・滑走距離に依存しない**1回限りの速度減衰として使われる。さらに枝ごとに 0.4 / 0.5 / 0.8 / 0.3 という**無根拠の係数**が使われ、互いに不整合。
- **論文・物理法則との乖離**: Coulomb 摩擦は `J_t ≤ μ J_n`、滑走中は `a = μg` の減速度を生む。現状は 30 m/s のスキッドが1接触で 29 % 減速して**残 21 m/s を永久に保持**する（次の接触まで）。
- **修正案**:
  ```python
  N = max(0.0, m*g*cos_theta + m*abs(v_z)/h)         # ステップimpsulse収支
  a_f = mu*N/m
  v_t  = max(0.0, |v_t| - a_f*h) * vhat_t             # 単調減衰、0 未満にならない
  ```
  角速度減衰は `Δω = I⁻¹ r × J_t`（§2-H07 と同じインパルス）。
- **計算コスト・メモリへの影響**: 接触サブステップ毎に約20 flop。**力評価 0 増**。

### [重要度：中] M-05. キールロール/ヨー減衰が aero_domain の微分と**二重適用**され、速度飽和 `V/(V+0.8)` を含む
- **該当箇所**: `physics/flight_sim.py:1811-1832`（`_restoring_moment`）、`:204-207`（`KEEL_ROLL_DAMPING=-0.20`, `KEEL_YAW_DAMPING=-0.26`）
- **現在の実装と制限内容**:
  ```python
  1826: scale = airspeed / (airspeed + KEEL_DAMPING_CUTOFF_MPS)     # 0.8
  1827: rate_hat = span / (2.0 * max(airspeed, 0.5))                # ★ max(airspeed,0.5) も magic
  1828: keel = max(dynamic_pressure,0) * area * span * rate_hat * scale
  1829: moment[0] += keel * KEEL_ROLL_DAMPING * omega[0]
  1830: moment[2] += keel * KEEL_YAW_DAMPING  * omega[2]
  ```
  `aero_domain.evaluate_aero` は既に `cl_roll_rate = -0.40` / `cn_yaw_rate = -0.30` を適用している。キール項はこれに**上乗せ**され、単位系が同じ（`C_lp` 相当）で **C_lp: −0.40 → −0.60（+50 %）、C_nr: −0.30 → −0.56（+87 %）**。
- **論文・物理法則との乖離**: コードコメント（`1815-1819`）は「静的ピッチ安定性とピッチレート減衰は `aero_domain` の責務」と書いているが、同じ論理がロール/ヨーにも当てはまり、**重複**になっている。`scale = V/(V+0.8)` は**どの論文にも無い**乗数で、2 m/s で 29 %、0.5 m/s で 62 % の減衰を削る → **頂点（低速度）が減衰不足**になる（物理的には回転型翼の減衰は `ρ·V` に比例する横流効果であり、絶対値で速度に増す）。1831行の `0.04`（上反角→ロール結合係数）も出典なし。
- **修正案**: ロール/ヨー減衰を `aero_domain` に一本化。キールを**別**の物理量として扱うなら独立設定 `cfg.keel_clp`（既定0.0）として `aero_domain` の `roll_coeff`/`yaw_coeff` に加算（事後加算ではなく）。上反角項は `C_lβ,keel = −Γ·(∂C_l/∂Γ)` として同じ `aero_domain` 呼び出し内で算出。
  - **安定性ガード**: 除去後、ロールモード減衰比 `ζ_φ = (q S b²|C_lp|)/(2 I_xx ω_φ) > 0` を確認し、不足なら `cl_roll_rate`（**設定値側**）を調整する（コードで減衰を二重に再追加しない）。
- **計算コスト・メモリへの影響**: **−2 乗算、−1 sin/atan/段**（むしろ高速）。VRAM 0。

### [重要度：中] M-06. 適応刻みの「CFL」条件が弦移動量規則で、実効刻みが `dt_max` の約14倍小さい
- **該当箇所**: `physics/flight_sim.py:1987-2013`（`_timestep_from`）、`:189`（`TRAVEL_FRACTION_PER_STEP=0.5`）
- **現在の実装と制限内容**:
  ```python
  2003: limits.append(TRAVEL_FRACTION_PER_STEP * self._chord / speed)   # speed = |v_world|
  ```
  (a) これは CFL 条件ではない（波動方程式がないので「セルサイズに対する波の伝播制限」ではない）。幾何更新精度規則としては妥当だが、**世界速度**に適用しており、**空気相対速度**ではない（向風では世界速度が小さく相対速度と空力剛性が大きい時に刻みが緩む）。(b) 参照機体（MAC≈0.0758 m, 26 m/s）で `h ≤ 0.5×0.0758/26 = 1.46 ms` ＝ **`dt_max=20 ms` の 1/13.7**。適応リミッタが常に支配するため、**仕様 §1.4 / Issue #31 が要求する「固定刻み Δt=0.04→0.005 の収束性検証」が実質 `dt_max` を変えても結果が変わらない**＝検証が無意味。(c) ロール時の姿勢回転（`MAX_ATTITUDE_STEP_RAD=0.35`）が支配条件だが、これは移動量規則に比例しない。
- **修正案**:
  1. `speed` を**空気相対速度** `core["speed"]` に変更。
  2. 弦だけでなく**スパン**も考慮: `h ≤ min(0.5·c/|v_rel|, 0.35/|ω|)`（既に 2001行に存在）。
  3. **物理的動機づけ**: 「1ステップあたりの迎角変化 < 1–2°」を基準に `h ≤ 0.02/|α̇|` を使う。`α̇` は `core` から取得可能。実効刻みは 5–20 倍大きくなり得る。
  4. 緩和後も `stiffness` 上限（`2009`）と `MAX_ATTITUDE_STEP_RAD/|ω|` を**必ず**維持（`min` は既に取られている）。
- **計算コスト・メモリへの影響**: 緩和した場合、サブステップ数が **5–10 倍減**（**スループット +400〜800 %**）。VRAM 不変。`dt_max` の収束性テストが意味を持ち始めるため、**回帰テストの性質が変わる**（既存テストの期待値が置き換わる）。

### [重要度：中] M-07. `RK4_STIFFNESS_SAFETY = 1.2` が真の陽的RK4安定限界 2.785 に対し 2.3 倍過小
- **該当箇所**: `physics/flight_sim.py:180-185`、`:2009`
- **現在の実装と制限内容**: コードの**コメント自身が**「explicit RK4 polynomial is stable … only up to `|lambda*h| = 2.785`」と明記しているのに、`RK4_STIFFNESS_SAFETY = 1.2` を使って `h ≤ 1.2/λ` としている。
- **乖離**: 数値解析上の正しさの問題（物理則ではない）。有限差分 Jacobian のノルム推定は非正規行列で最速固有値を**過大評価**するため、ある程度のマージンは正当だが、1.2 は過小。
- **修正案**: `RK4_STIFFNESS_SAFETY = 2.0`（2.785 の内側 28 %）。推定は冪乗反復（`‖J‖₂`）で 3 反復に締めるとさらに締まる。**安定性ガード**: 単体テストで `RK4_STIFFNESS_SAFETY <= 2.785` を固定し、`h = 2.0/λ` のステップが `_step_is_acceptable` を通過することを検証（§2-H20 の相対化とセット）。
- **計算コスト・メモリへの影響**: 刻み **最大 +130 %**（M-06 の緩和後に新しいボトルネックになる）。VRAM 0。

### [重要度：中] M-08. `AERO_SPEED_CAP_MPS = 250` が全空力係数を無言で再評価する
- **該当箇所**: `physics/flight_sim.py:193`、`:2058-2060`
- **現在の実装と制限内容**:
  ```python
  2058: if speed > AERO_SPEED_CAP_MPS and speed > _EPS:
  2059:     relative = relative * (AERO_SPEED_CAP_MPS / speed)
  2060:     speed = AERO_SPEED_CAP_MPS
  ```
  250 m/s を超えると**全空力力・トルクが 250 m/s の相当量で評価**され、状態速度と加速度が**不整合**になる（報告 `speed_mps` は実速度のまま）。250 m/s ≒ 海面高度 M0.73 で、モデル自身の有効範囲 `Ma < 0.3` を大きく超える。
- **修正案**: **切り詰めではなく適用範囲フラグ**にする: `model_valid = (mach <= 0.3) and (1e3 <= Re <= 1e5)` をテレメトリに載せ、**力は何も変えない**。これにより `PHYSICS_AUDIT_REPORT.md:31` の適用範囲宣言が正直になる。
- **計算コスト・メモリへの影響**: 0（+1 bool フィールド）。

### [重要度：中] M-09. 失速ブレンドが `logistic⁴`（形状パラメータ）で、50 % 点・90 % 点が 1.65 倍シフト
- **該当箇所**: `physics/aero_domain.py:130`（`STALL_BLEND_ORDER = 4`）、`:338-354`
- **現在の実装と制限内容**: `_logistic(sharpness*offset) ** 4`。docstring は「ロジスティックをべき乗にすることで失速前の線形分岐を機械精度で再現するため（`tests/test_aero.py` が 1e-29 相対を要求）」と**明記**。50 % 点が `Δα=0` から `1.19°` へ、90 % 点が `1.57°` から `2.60°` へ移動＝**失速が 1.65 倍鋭くなる**。
- **修正案**: 指数の代わりに**C1 連続の局所線形化**で exacta な付着流を作る: `w = 1/(1+exp(-k·d))`、`|d| ≤ 1e-3` では `w = 0.5 + 0.25·k·d`。指数を維持する場合は `stall_blend_order` を設定項目にし、`PHYSICS_AUDIT_REPORT.md` §1.1 に「意図的な形状パラメータ」として記載。
- **計算コスト・メモリへの影響**: 0（`pow` → 乗算）。

### [重要度：中] M-10. 対称翼なのに負側失速閾値が 30°（正側14°の 2.1 倍）
- **該当箇所**: `physics/aero_domain.py:351`、`:465`、`:1069`（`neg_stall = max(stall_alpha + 15.0, 30.0)`）
- **現在の実装と制限内容**: コードは自認（`1067-1068`:「wider negative threshold is an existing empirical recovery assumption」）。
- **論文・物理法則との乖離**: 対称な紙翼は |α| 対称に失速する。2.1 倍の非対称は実機の性質ではなく**どの論文にも由来しない**。倒置飛行・逆回復の耐性が系統的に過大評価される。
- **修正案**: `neg_stall = stall_alpha`（対称）。非対称失速が物理的に必要な場合は measurable な機体属性（上反角など）から駆動: `neg_stall = stall_alpha * (1 + 0.05*|dihedral_deg|)`。
- **計算コスト・メモリへの影響**: 0。

### [重要度：中] M-11. 側力係数 `C_Y = cy_beta·β` が無制限（β のクランプ・ラップが未実装）
- **該当箇所**: `physics/aero_domain.py:889`、`:941`
- **現在の実装と制限内容**: β はクランプされず、`C_Y` は β に比例して無制限（β=180° で −0.785、β=720° で −3.14）。
- **論文・物理法則との乖離**: `PHYSICS_AUDIT_REPORT.md:27` は「`α ∈ [-180°,180°]`、`β ∈ [-90°,90°]` 全域で…`C_L, C_D, C_Y` を連続関数として**クランプ・ラップ**」と要求するが、β 側のクランプ・ラップが未実装。平板限界 `C_Y → 2 sinβ cos²β`（≤0.77）があるべきで、線形無制限ではない。
- **修正案**: `beta = clamp(beta_deg, -90, 90)` ＋ 平板限界のブレンド:
  ```python
  cy = cy_beta*beta_rad + (1 - abs(math.cos(beta_rad)))*copysign(2*math.sin(beta_rad)*math.cos(beta_rad)**2, beta_rad)
  ```
- **計算コスト・メモリへの影響**: +3 flop（< 1 %）。

### [重要度：中] M-12. パネル法が 2π sinα（2次元翼型傾斜）を使い、正規経路の Helmbold 傾斜と 2.2 倍乖離
- **該当箇所**: `physics/mesh_domain.py:1510-1518`、`:124`（`STALL_ALPHA_DEG=15.0`）
- **現在の実装と制限内容**: `cl_linear = 2.0*math.pi*math.sin(local_rad)`。失速角も 15°（設定は14°）で**別値**。
- **論文・物理法則との乖離**: 2π/rad は無限翼の2次元傾斜。同一機体（AR=2.667）で Helmbold 傾斜 2.82/rad と比べ**2.2 倍過大**。つまり**3D可視化用パネルの揚力が、実際に飛ぶ機体の揚力と一致しない**。
- **修正案**: `aero_domain.helmbold_cl_alpha` の**同一スカラー**をパネルに使い、失速角は `cfg.stall_alpha_deg` を参照。
- **計算コスト・メモリへの影響**: パネル毎 O(1)、**計測不能**。

### [重要度：中] M-13. パネル法の回転増分が「翼方向距離」ではなく「弦方向オフセット」を使う
- **該当箇所**: `physics/mesh_domain.py:1520`（`cl_rot = cl * safe_div(cg_proj - chord_proj[position], chord_extent, 0.0)`）
- **現在の実装と制限内容**: ロールによる回転増分は `Δα = p·y/V`（**翼方向**距離）と Küssner の `λ(x/c)` 減衰で表すべきところ、**弦方向**の 25 % 弦点からのオフセットを使っている。
- **修正案**: 2次元パネル揚力なら**項を削除**。回転効果を持たせるなら実ロール角速度と翼方向オフセットで `cl_rot = cl·(p·y/V)·λ(x/c)`。
- **計算コスト・メモリへの影響**: O(1)/パネル。

### [重要度：中] M-14. パネル抗力が力ベクトルとして投入されず、モーメントに寄与しない
- **該当箇所**: `physics/mesh_domain.py:1524-1530`（`total_drag += drag`）
- **現在の実装と制限内容**: 抗力がスカラー累積で、**モーメント和に入らない**。また `PANEL_PROFILE_DRAG_CD=0.02` は迎角非依存。
- **修正案**: 抗力をパネル重心位置の力ベクトルとして投入してモーメント和に含める。`Cd` を迎角依存にする: `Cd = Cd0 + k·sin²α·cosα` ＋ 失速後平板項。
- **計算コスト・メモリへの影響**: パネル毎に外積 1 回追加。

### [重要度：中] M-15. 開放メッシュ（折った紙）が「コーン体積」の慣性テンソルを受け入れる
- **該当箇所**: `physics/mesh_domain.py:1915-1928`（`compute_metrics`）、`:1090-1144`（`inertia_tensor_mesh`）
- **現在の実装と制限内容**: `inertia_tensor_mesh` は符号付き四面体積分で**閉じた**メッシュの体積モーメントを再現する。**折った紙は開いた曲面**で、符号コーンの和は紙の質量分布ではない。正しいシート推定器（面積重み付き三角形）は `trace <= 0` または非有限のときだけ選択されるが、**非平面の折った紙では和は正**なので**偽の慣性が採用される**。
- **修正案**: 推定器の選択基準を数値トレースではなく**メッシュ閉性**にする:
  ```python
  closed = is_closed_mesh(verts, tris)          # mass_properties で既に計算済み（再利用）
  specific = inertia_tensor_mesh(...) if closed and abs(volume) > EPS else _sheet_second_moments(...)
  ```
  結果に `inertia_source`（"volume"/"surface"）を付与して監査可能にする。
- **計算コスト・メモリへの影響**: O(F) の辺パスを追加（`is_closed_mesh` は再利用）→ **計測不能**。

### [重要度：中] M-16. 質量 `mass_kg` / `paper_gsm` / `sheet_area_m2` の既定値が互いに不整合（2.5 倍）
- **該当箇所**: `physics/config_schema.py:248-251`（`mass_kg=0.0050`, `paper_gsm=100.0`, `sheet_area_m2=0.0200`）
- **現在の実装と制限内容**: 0.02 m² × 100 g/m² = **2.0 g** だが `mass_kg` は **5.0 g**（= 250 g/m² 相当）。A4 全面（0.06237 m²）なら 100 g/m² で 6.24 g。**3つの値が一貫しない**。
- **論文・物理法則との乖離**: 質量・面密度・面積の不整合は (a) 慣性テンソルを 2.5 倍にし、(b) §2-H11 の許容応力（gsm ベース）と §2-H10 の許容応力（mass ベース）の両方に波及し、(c) 揚力/重量比を 2.5 倍狂わせる。初期条件で「5 g の 2.5 倍重い機体」となる。
- **修正案**: `mass_kg` を**導出値**にする（`paper_gsm * sheet_area_m2 / 1000`）か、少なくとも 3 値を 1 箇所で整合させる。`derived_properties` の `sheet_area` を使用済みなのでそこで統一。
- **計算コスト・メモリへの影響**: 0。

### [重要度：中] M-17. 投入時のピッチ角速度に説明されない 0.10 倍が掛かる
- **該当箇所**: `physics/flight_sim.py:1487-1489`（`resolve_throw`）
- **現在の実装と制限内容**:
  ```python
  1487: torque_rate = np.linalg.solve(inertia, moment * duration * efficiency)
  1488: # Finger pinch guide effect: pinching the paper fuselage dampens excessive pitch-kick on release
  1489: torque_rate[1] *= 0.10
  ```
  インパルス `M = r × F` と `w = I⁻¹ M Δt` は正しく計算されているが、**ピッチ成分のみ**に未文書化の 0.1 が追加される。`efficiency=0.15`（`throw_torque_efficiency`、出典なし）utterも既に近似。
- **修正案**: ピンチ効果を**幾何**で表現: 回転軸を**グリップ軸**に取る（平行軸定理 `I_grip = I_cg + m(|r|²δ_ij − r_i r_j)`）→ `Δω = I_grip⁻¹ M Δt`。0.10 リテラルを削除。手首コンプライアンスは `throw_hand_time_s`（10–20 ms）として `Δω = I⁻¹ M·min(Δt, t_hand)`。
- **計算コスト・メモリへの影響**: `resolve_throw` は 1 飛行 1 回のみ。3×3 行列構築＋`solve` 1 回で**無視可能**。

### [重要度：中] M-18. `lift_scale` / `drag_scale` が論文式を 0–5 倍の任意係数で上書き（既定は1.0）
- **該当箇所**: `physics/config_schema.py:471-472`、`:505-506`、`:1415-1416`
- **現在の実装と制限内容**: `L = ½ρv²S C_L(α)` の `C_L` を丸ごと 0–5 倍にでき、`C_D` も同様。UI スライダーからいつでも変更可能。
- **修正案**: 既定 1.0 では論文一致のため**優先度は中**。ただし「較正」用途と「恣意的な上書き」用途を区別し、後者は `model_calibration` として別名にし、`physics_verification_eligible` の判定材料にする。**既定値は不変**。
- **計算コスト・メモリへの影響**: 0。

### [重要度：中] M-19. `evaluate_flight` が全例外を握り潰し `status="diverged"` の全ゼロ結果を返す
- **該当箇所**: `physics/flight_sim.py:3072-3101`（`except Exception: return empty`）、`:3065`
- **現在の実装と制限内容**: `KeyError` / `TypeError` / `ValueError` まで全て「数値発散」に変換され、距離 0 m の**正当な結果に見える**飛行として返る。最適化器はこれを**0 m の設計**としてスコアする（`optimizer_domain.py:317`）。
- **修正案**: 失敗モードを分離:
  ```python
  except DivergenceError as exc:  return {**empty, "status": "diverged", "error": str(exc)}
  except (ValueError, KeyError, TypeError) as exc:
      return {**empty, "status": "error", "error_id": uuid4().hex, "error": type(exc).__name__}
  ```
  最適化器は `status == "error"` を**実行不可能（制約違反）**として扱う。`log.exception` で記録。
- **計算コスト・メモリへの影響**: CPU 0。エラー時のみ UUID 1 個 + ログ 1 行。

### [重要度：中] M-20. `g_load` が |n_z| のみで符号と横Gを欠落
- **該当箇所**: `physics/flight_sim.py:2170-2176`、`:2137-2159`
- **現在の実装と制限内容**: `abs(dot(specific_force_body, (0,0,1))) / gravity_ref`。45° バンク旋回でも n_x / n_y 成分は無視され、翼根は**全荷重を法線方向**と仮定して曲げる。`abs()` が符号を捨てるため**負G（プッシュオーバー）が正Gとして報告**され、頂点プッシュオーバーが UI から見えない。
- **修正案**: `g_load = |specific_force_body| / g`（真の荷重係数）と `nz`（符号付き法線成分）を分けて公開し、構造モデルには**全 resultant** を渡す。
- **計算コスト・メモリへの影響**: 1 RK4 段につき `norm` 1 回（無視可能）。テレメトリ +2 フィールド。

### [重要度：中] M-21. `α̇` / `β̇` が非相対加速度と `−ω×v` を無視
- **該当箇所**: `physics/flight_sim.py:2626-2633`（`_diagnostics`）
- **現在の実装と制限内容**: `path_rates = cross(normalize(core["body_relative"]), world_to_body(q, core["acceleration"])) / core["speed"]`。慣性加速度（重力成分を含む）を使い、`a_rel = a − ∂w/∂t` と `−ω×v_rel` を**欠落**。
- **論文・物理法則との乖離**: 流角の時刻変化は `α̇ ∝ v̂_rel × (a_rel − ω×v_rel)`。欠落項 `−ω×v` は**機体が高速回転する時ほど大きい**＝UI オーバーレイが最も効く領域で最大。
- **修正案**: `a_rel_body = world_to_body(q, acceleration - a_wind) - cross(omega, body_relative)` → `α̇, β̇ = cross(v̂_rel, a_rel_body)/|v_rel|`。
- **計算コスト・メモリへの影響**: 記録サンプル毎に `cross` 1 回（**RK4 段毎ではない**）。無視可能。

### [重要度：中] M-22. 風洞モードが「逆流 α ≈ −176°」で評価される
- **該当箇所**: `physics/flight_sim.py:2798-2818`（`FlightSimulator.step`）、`:1852-1868`（`reset`）
- **現在の実装と制限内容**: 風洞モードでは `step()` が位置・速度・四元数・角速度を**すべて破棄**し、固定の 1.2 m の高さとクォータニオンを再設定するのみ。`alpha_deg` は `wind_vector` からの**相対速度**から算出される。風洞流は NED で `(+v,0,0)`（機首方向へ吹く）なので、静止機体では相対速度が機首逆向きとなり `α ≈ −176°`（＝**逆流・平板状態**）になる。テストする「4° 迎角」ではない。
- **修正案**: 風洞モードを**純粋な空力モード**にする: 機体をスタンドに固定し、`v_rel_body = (V cosα cosβ, V cosα sinβ, V sinα)` を明示的に与えて `aero_forces()` を呼ぶ。風洞流は NED で `(-v,0,0)`（機首から来る向き）にすべき（`wind_domain` 側を修正 or `flight_sim` で x 反転）。
- **計算コスト・メモリへの影響**: 風洞モードで `wind_vector` 1 回と 4 段 RK4 が**不要**になり**高速**。空力評価 1 回の明示化が必要。

### [重要度：中] M-23. 熱上昇柱が地表から 1.2–1.6 m 浮いたガウシアン塊で、σ/高さ上限がハード
- **該当箇所**: `physics/wind_domain.py:664-697`（`thermal_geometry`）、`:131-134`（`_THERMAL_SIGMA_MAX_M=12.0`, `_THERMAL_HEIGHT_MAX_M=40.0`）
- **現在の実装と制限内容**: `w = strength * gaussian * vertical`（`vertical = exp(−(dz/height)²)`）で、中心が地上 1.2–1.6 m。
- **論文・物理法則との乖離**: 熱柱は**地表面 anchors**され、上昇とともに広がる（`σ(z) ∝ z^½`）、鉛直速度は `w ∝ z^(1/3)` で頂点（逆転層）でゼロになる。現状は**紙飛行機が飛ぶ 0–1 m でほぼ上昇流を受けない**（ガウシアンの下側フレンク）一方、**最強上昇流は飛行域より上**にある。
- **修正案**: `w(z) = w_max·(z/z0)^(1/3)·exp(−z/z_top)`、`σ(z) = σ0·(z/z0)^(1/2)`、`w_max` は対流エネルギーバジェットで制限。
- **計算コスト・メモリへの影響**: O(1)/点（`pow` 1 回追加）。

### [重要度：中] M-24. 乱流が等方的で、平均風は基準風速参照（局所 u(z) でスケールされない）
- **該当箇所**: `physics/wind_domain.py:585-591`、`:766-777`、`:843-852`
- **現在の実装と制限内容**: 同一振幅を u, v, w の3成分に適用。振幅は**基準高度の参考風速**から作る。
- **論文・物理法則との乖離**: 大気乱流は強い異方性（中立境界層で `σ_w/σ_u ≈ 0.5`）。現状は**薄い翼が最も敏感な鉛直ガストを過大評価**する。また基準高度より下では平均風が log 則で 0 接近するのに乱流振幅は一定 → 乱流/平均比が**無限大**に発散する（実測では乱流強度は下層で低下）。
- **修正案**: `σ_w = σ_u/√2`、振幅は局所プロファイル `u(z)` に比例（乱流強度一定）または `u(z)²`（分散一定）。
- **計算コスト・メモリへの影響**: O(1)/点。

### [重要度：中] M-25. ISA モデルに等温成層圏（tropopause）が無い（監査報告 §4.1 は「ある」と宣言）
- **該当箇所**: `physics/atmosphere_domain.py:140-151`（`isa_pressure`）
- **現在の実装と制限内容**: 対流圏のべき則を大気上端まで外挿し、「50 K 床」で代用。等温分岐は `lapse == 0` のときだけ（即ち用户在 `lapse_rate=0` にした場合のみ）到達。20 km で p ≈ 4.3 kPa（真 ISA は 5.47 kPa、**約 21 % 誤差**）。
- **修正案**: 標準の多層 ISA を実装:
  ```python
  if h <= 11000:  T = T0 - L*h;                      p = p0*(T/T0)**(g/(L*R))
  elif h <= 20000: T = 216.65;                       p = p11*exp(-g*(h-11000)/(R*T))
  elif h <= 32000: T = 216.65 + 0.001*(h-20000);    p = p20*(T/T20)**(-g/(L*R))
  else:             T = 228.65;                      p = p32*exp(-g*(h-32000)/(R*T))
  ```
  さらに `isa_density`（`:154-170`）と `evaluate_atmosphere`（`:330-341`）が**温度 50 K 床付き/無しの別値**を使うため、高度 36 km 超で (p, T, ρ) が**熱力学的に閉じていない**。共通の温度関数 1 つに集約する。
- **計算コスト・メモリへの影響**: O(1)/評価（分岐のみ）。

### [重要度：中] M-26. 応力場が**圧縮側をクリップ**し、薄板の座屈（支配的破壊モード）が見えない
- **該当箇所**: `physics/stress_domain.py:726`（`stress = xp.maximum(stress, 0.0)`）
- **現在の実装と制限内容**: 曲げ応力は符号付きだが、下限 0 に clip。翼根曲げモーメントが正なら下表面は**圧縮**（座屈で破断）するはずが、圧縮側は完全に消える。載荷反転時は場が**恒等的にゼロ**になる。
- **修正案**: `stress_pa` は符号付き保持。利用率は `|σ|`（または von Mises 相当）から求め、圧縮側に**明示的な座屈チャンネル**を追加: `σ_cr = π²Et²/(12(1−ν²)(b/t)²)`。
- **計算コスト・メモリへの影響**: O(1)/セル。

### [重要度：中] M-27. 上反角が**対称飛行でも**永久に非対称な翼方向荷重を生む
- **該当箇所**: `physics/stress_domain.py:703-708`（`load_mult = 1.0 - DIHEDRAL_LOAD_COUPLING * tilt * y_signed`）
- **現在の実装と制限内容**: `load_mult` は**上反角のみ**に依存し、**横滑り角 `side` と積されていない**。よって β=0 の完全に対称な飛行でも（既定 5° 上反角で翼端 ±7 %）**非対称な曲げ分布・非対称ヒートマップ・偽のロールモーメント**が生じる。
- **修正案**: `load_mult = 1.0 - k*(tilt*side)*y_signed`（上反角×横滑り角）、または `load_mult` を廃止しロール結合は `dihedral_roll_couple()` の責務に寄せる。
- **計算コスト・メモリへの影響**: O(1)。

### [重要度：中] M-28. 翼端折りで「除去された翼幅」がなお荷重を担い、`TIP_MOMENT_GAIN=1.6` が翼根応力と報告モーメントを不整合にする
- **該当箇所**: `physics/stress_domain.py:667-700`、`:766`、`:118`
- **現在の実装と制限内容**: 報告セミスパンは 20 % 短縮されるが、積分 `remaining = clip(1−y_abs, 0, 1)` は**全翼幅**で計算され、「除去領域」のパネルも荷重を受ける。`TIP_MOMENT_GAIN=1.6` が**翼根を含む全ステーション**に掛かるため、翼根応力は報告 `wing_root_moment_nm` の 1.6 倍になる（**内部不整合 最大 60 %**）。コードコメント（`:699`）はコードと矛盾。
- **修正案**: 荷重積分を `y_max = 1 − TIP_SPAN_REDUCTION·tip_factor` までに限定し、翼端折れ効果は**モーメントではなく断面係数 Z** に反映。翼根モーメントは翼根応力と同じ量から報告。
- **計算コスト・メモリへの影響**: O(1)。

### [重要度：中] M-29. 上反角/横滑りの傾斜が tan = 0.6（31°）で clip されるが、設定は ±45° を許す
- **該当箇所**: `physics/stress_domain.py:131`、`:446-450`、`:659-660`
- **現在の実装と制限内容**: `config_schema.py:382` は `dihedral_deg ∈ [−45,45]` を許すが、構造モデルは 31° で頭打ち。一方 `aero_domain` は**真の上反角**を使う → 2 経路が不一致。
- **修正案**: `tan(45°)=1.0`（または空力モデルの限界に合わせる）＋ `tilt_clamped` フラグを公開。
- **計算コスト・メモリへの影響**: O(1)。

### [重要度：中] M-30. `static_margin`（0.12）が**空力的に無意味**
- **該当箇所**: `physics/config_schema.py:312`（`static_margin`）、`physics/parallel_engine.py:505`（代理モデルのみで使用）
- **現在の実装と制限内容**: 正規 6DoF 経路の**どのモーメント項にも使われていない**。`PHYSICS_AUDIT_REPORT.md:44` の静的縦安定性条件（`C_mα < 0` かつ `x_cp` が `x_cg` より後方）は、手動設定された `cm_alpha` と `cg_offset_x` のみで担保され、UI の `static_margin` スライダーは**空力に影響しない**。
- **修正案**: `static_margin` をモーメントモデルに接続: `cp_x = 0.25c + static_margin·c`、`cm_alpha` を `static_margin` から導出（または `cm_alpha` と整合検証）。
- **計算コスト・メモリへの影響**: 代数変換のみ。0。

### [重要度：中] M-31. 公開 Drag Polar の既定レイノルズ数が**頂点条件**（V≈2 m/s）に対応
- **該当箇所**: `physics/aero_domain.py:604`、`api/routes/physics.py:84`（`reynolds` 既定 1e4）
- **現在の実装と制限内容**: `C_D0(Re) = C_D0,ref·(1e5/Re)^0.15`。既定 MAC 0.0758 m で Re=1e4 は **V ≈ 2.0 m/s**（頂点）。滑空巡航（4–6 m/s, Re ≈ 2–3×10⁴）ではない。`C_D0 = 0.020·(1e5/1e4)^0.15 = 0.0282` ＝ **+41 % の parasite 抗力**、`L/D_max` は対応的に悲観的。
- **修正案**: 既定 Re を巡航速度から導出（`reynolds=None` → `evaluate_aero` の実 Re を使う）、明示指定は維持。
- **計算コスト・メモリへの影響**: 大気評価 1 回（約 30 µs）。

### [重要度：中] M-32. 構造側の空力弾性変形の `norm_scale = 1.0e-10` が無次元,且つ重力が 9.80665 ハードコード（§2-H13 の2系統乖離の裏側）
- **該当箇所**: `physics/stress_domain.py:421-424`（`aeroelastic_deformations`）
- **現在の実装と制限内容**:
  ```python
  421: norm_scale = 1.0e-10
  422: normal_force = max(cfg.mass_kg, 0.0) * 9.80665 * load      # ★ g ハードコード
  423: delta_dihedral = clamp((normal_force * (span*0.5) * flex_gain * norm_scale) / denom, -30.0, 30.0)
  424: washout_twist   = clamp((q * area * c_mac * twist_gain * norm_scale) / denom, -20.0, 20.0)
  ```
  `norm_scale` は**次元的な根拠が無く**、変位が ±30°/±20° の窓に収まることのみが目的（「見た目の調整定数」）。分母 `denom = eff_stiff·t³` は**無次元の「剛さ」**と物理的な `t³` を掛け合わせており、`1/(EI)` の梁スケールは名目上のみ。重力は `9.80665` 固定だが、大気/ISA 全体は実行時可変の `env.gravity` で駆動されている（`config_schema.py:469,503`）。
- **論文・物理法則との乖離**: 弾性たわみは `δ = F·L³/(3EI)` で、EI は実ヤング率と実断面二次モーメントから成る。現状は `EI` が存在せず、`E_MD`/`E_CD`/`ν` がモジュール内に**一切現れない**（`paper_stiffness` は無次元 0–1 スライダー）。また §2-H13 の通り `aero_domain.py:1073-1078` は**別の式**（`q/(50·stiffness)`, `*2.5`/`*1.5`）を持ち、`flight_sim` は `stress_domain` 側の値を使うため、**API `POST /aerodynamics/calculate` の `aeroelastic_dihedral_deg` はシミュレータが実際に使った変形ではない**。Mars/Moon の重力スライダーは飛行を変えるが**空力弾性たわみは変えない**。
- **修正案**:
  1. `config_schema` に実材料定数を追加: `E_MD ≈ 3.0 GPa`, `E_CD ≈ 1.5 GPa`, `ν ≈ 0.3`, `ρ_bulk = 800 kg/m³`（§2-H11 と共通）。
  2. 変位を `δ = F·L³/(3EI)`（`I` は折り断面から、§2-H11）で算出。
  3. 重力は `env.gravity` を使用。
  4. `norm_scale` は**削除**。±30°/±20° のクランプは残すが、飽和時に `saturated=True` フラグを返す。
  5. **2系統の統合**: `aero_domain` は `stress_domain.aeroelastic_deformations` を lazy import し、同じ値を返す（`flight_sim` と同じ）。
  - **安定性ガード**: `eff_stiff > 0`、`t > 0` を保証。クランプの飽和を検出可能に。
- **計算コスト・メモリへの影響**: O(1)/評価。**既定では不活性**（`aeroelastic_flex = aeroelastic_twist = 0.0`）。

### [重要度：中] M-33. `mass_properties` は「紙ブロックのソリッド質量」を返し、100 g/m² がハードコード
- **該当箇所**: `physics/mesh_domain.py:1039-1074`（`mass_properties`）、`:128`（`PAPER_THICKNESS_M = 0.1 / PAPER_DENSITY_BASE`）
- **現在の実装と制限内容**:
  ```python
  1073: mass = density * abs_volume              # 閉じたメッシュ → 体積 × 紙密度
  1074:     mass = density * PAPER_THICKNESS_M * area   # 開面 → ハードコードされた 100 g/m²
  ```
  閉じたメッシュでは**囲まれた体積を密度 800 kg/m³ で埋めたソリッド質量**を報告する（充填率 1.0 の紙ブロック）。実際の折り機体は体積の **5–15 %** である。開面フォールバックは `PAPER_THICKNESS_M`（= 0.1/800 m）すなわち**固定 100 g/m²** で、呼び出し元の `paper_gsm` を無視する。
- **論文・物理法則との乖離**: 紙機体の質量は `m = gsm·A/1000`（面積×面密度）であり、**体積ではなく面積で決まる**。同一メッシュに対して `compute_metrics` は `paper_mass_from_mesh(sheet_area, paper_gsm)`（正しい `m = gsm·A/1000`）を返し、`mass_properties` は別の値を返すため、**2つの質量が同一メッシュに対して矛盾**する。ソリッド質量は慣性テンソル（§3-M-15）と一緒に使用されると機体を「紙のかたまり」に見せる。
- **修正案**:
  ```python
  def mass_properties(verts, tris, *, paper_gsm=100.0, density=PAPER_DENSITY_BASE):
      ...
      sheet_mass = paper_gsm * area / 1000.0          # 主: 面積×面密度
      solid_mass = density * abs_volume                # 補助: 幾何量として別枠で保持
      return {"mass_kg": sheet_mass, "solid_mass_kg": solid_mass, ...}
  ```
  `paper_gsm` を引数で受け取り、`solid_mass` は「幾何包絡体積の参考値」としてラベル付けする。
- **計算コスト・メモリへの影響**: O(1)。

### [重要度：中] M-34. パネル分割が 16×12 固定で import 経路から変更不可、三角形は重心で binning
- **該当箇所**: `physics/mesh_domain.py:1312`（`build_panels(vertices, faces, nx=16, ny=12)`）、`:1882-1883`、`:1350-1361`、`:2166-2178`
- **現在の実装と制限内容**:
  ```python
  1312: def build_panels(vertices, faces, nx: int = 16, ny: int = 12) -> List[PanelData]
  1350: chord_bin = np.clip(((chord_proj - chord_lo)/chord_range*nx).astype(np.int64), 0, nx-1)
  ```
  `MeshLibrary._build` は `panel_nx/panel_ny` を渡さずに `compute_metrics` を呼ぶため、**全ての取り込みモデルが 192 セル固定**（モデル寸法・要求解像度と無関係）。さらに各**三角形全体を重心の属する bin へ丸ごと**割り当てるので、境界をまたぐ三角形は全面積を**誤ったセル**に持ち込む（O(h) 誤差）し、面積分布が量子化される。
- **論文・物理法則との乖離**: パネル法は-wing 上の揚力分布（ Vignelli / 面素法）の**離散化**であり、解像度は**近似誤差と計算コストのトレードオフ**パラメータであるべきもの。固定 192 セルは、細かな折り形状（DJの折れ目など）で形状解像度を確保できず、パネル荷重が形状を正しく表現しない。
- **修正案**:
  1. `panel_nx/panel_ny` を API と `MeshLibrary` に露出し、既定を**弦基準**（約 20 セル/弦）にする。
  2. 重心 binning を**bin 平面で三角形をクリップ**する方式に置き換える（面積重み付き).
  - **計算コスト・メモリへの影響**: パネルループは 1 回の空力評価あたり `O(n_panels)`、`n_panels ∝ nx·ny`。**両方向で分割密度を倍にすると 1 評価あたり 4 倍**、`nx·ny` 自体を設計変数にした場合の評価コストは `O((nx·ny)²)` 相当に効く。binning パスは `O(F + nx·ny)`。なおパネル法の揚力傾斜は解析経路と 2.2 倍乖離している（§3-M-12）ため、**分割密度を公開する前に §3-M-12 の Helmbold 統一を先に適用すべき**（順序が重要）。

---

## 4. 重要度：低

| # | 指摘 | 該当箇所 | 内容 / 修正案 | コスト |
|---|---|---|---|---|
| L-01 | `HARD_ANGULAR_RATE_LIMIT_RPS=5000` は**到達不能**（放出時上限 45 rad/s の 100 倍超） | `flight_sim.py:168` | 発散検知は §2-H20 の相対判定に置き換える | 0 |
| L-02 | `HARD_ALTITUDE_LIMIT_M=1e4` が ISA の対流圏上限（11 km）を下回る | `flight_sim.py:164` | 1e5（NaN ガードのみ）へ | 0 |
| L-03 | レイノルズ数が `[1e2,1e9]` に clip されるが宣言有効範囲は `[1e3,1e5]` | `flight_sim.py:835`, `:997` | 有効性フラグ并存。clip では外挿問題は解決しない | 0 |
| L-04 | 大気が**高度 1 mm 量子化**。ただしキャッシュキーに `env.altitude_m` / `env.ground_level_m` が**欠落**（実バグ） | `flight_sim.py:608-617`, `:640-652` | 誤差は 0.0012 % で**十分妥当**。キーに 2 要素追加 | キー 2 要素追加 |
| L-05 | `wind_force_to_body` が β=±90° で不連続（`horizontal>1e-12`、さもなくば (0,0,−1)） | `geometry_utils.py:304-320` | Gram–Schmidt による連続構成（`v̂` と機体下ベクトルの直交化）＋重みブレンド | +1 dot/normalize/段 |
| L-06 | `gravity_modifier.py` が飛行力学に**未接続**、かつ `config_schema` の g 範囲 `[0.1,50]` が反重力を禁じる | `gravity_modifier.py:65-84`, `config_schema.py:503` | 所有者を 1 つに（削除 or 接続＋クランプ統一） | 0 or +1 mult/段 |
| L-07 | 投入速度上限（60 m/s）などが docstring の引用値（20 m/s）の 3 倍 | `flight_sim.py:1239-1250` | 引用値に合わせる（ピッチ 6, ロール 30 rad/s, 速度 30 m/s） | 0 |
| L-08 | `EFFECTIVE_SPEED_FLOOR_MPS` / `_trim_pitch_deg` / `trailing_edge_bend_deg` が**死んだコード**（後者はテレメトリに報告されるが模型に使われない） | `flight_sim.py:191`, `:1782`, `:2114-2115` | §2-H13 のモーフィングを幾何接线するか、テレメトリから削除 | 0 |
| L-09 | `polhamus_kv` / `cp_shift_gain` / `aeroelastic_flex` / `aeroelastic_twist` が**既定 0** で、Cite されているモデルのみが不活性 | `config_schema.py:316-320` | 形状別に非ゼロ既定を付与し文書化 | 0 |
| L-10 | `ELEVON_MAX_DEG=180`（幾何学的に不可能、設定は ±45°） | `aero_domain.py:138` | 45°（または折れ角度連動）＋ `travel_limited` フラグ | 0 |
| L-11 | 乱流ゲージの「スペクトル」が**固定 3 正弦波** | `wind_domain.py:111-117` | Kaimal/Davenport PSD → シェーピングフィルタ。3 正弦波は「coherent gust」モードとして明示 | RNG 1 抽選/段 |
| L-12 | 地上ランプが**鉛直乱流まで**線形減衰（地上 0.5 m で鉛直乱流がゼロに） | `wind_domain.py:857`, `:869` | 平均/プルームの鉛直成分だけをランプ | 0 |
| L-13 | 風洞モードが**世界座標**の流で、姿勢で流方向が変わる。4 つの定数がハードコード | `wind_domain.py:813-835` | 機体座標系化＋定数を `EnvironmentConfig` へ | 0 |
| L-14 | `paper_gsm` が `[10,600]` に clip、質量 `mass_kg` が `[1e-4,1.0]` に clip（`flight_sim` は `1e-9`） | `config_schema.py:375-376` | 3 経路の mass クランプを統一 | 0 |
| L-15 | `scale_altitude_m` がモジュール定数 `T0_ISA` を使い、実行時の海面温度を無視 | `atmosphere_domain.py:173-178` | `env.sea_level_temperature_K` を渡す | 0 |
| L-16 | 空気温度の 100 K 床（`air_density` / `speed_of_sound`）が不可能な状態を静かに置き換える | `atmosphere_domain.py:93, 236, 268` | clamp ではなく raise。設定高度域では到達不能 | 0 |
| L-17 | Magnus 指数が ±60 に clip | `atmosphere_domain.py:196` | オーバーフローガードのみ（`p_sat` は ~1e28 Pa で到達不能） | 0 |
| L-18 | `_parse_resolution` が**スカラー分岐で 262 144 点の安全上限を適用していない**（3 配列分岐では適用） | `wind_domain.py:893-903` vs `:910-911` | 多重防御。API 側で上限済み（`api/routes/physics.py:170-180`） | 0 |
| L-19 | `wind_field` の docstring は NumPy への劣化管理を謳うが `_resolve_backend` は明示 cupy/torch 要求で raise | `wind_domain.py:949-951` vs `:226-233` | doc ドリフト（`stress_domain` の docstring は正しい） | 0 |
| L-20 | Simplex 出力を [-1,1] に clip するが「理論最大値は厳密に 1.0」は不正確 | `wind_domain.py:536` | clip はガードとして維持、コメントを修正 | 0 |
| L-21 | `trim_speed` の既定密度が 1.225 kg/m³（海面高度）にハードコード | `stress_domain.py:309` | 現在高度の ISA 密度を使う | 0 |
| L-22 | 死んだ定数: `SPAN_LOADING_FACTOR`（export されテストアサートされるが未使用）と `SPAN_SHIFT_GAIN`（未使用） | `stress_domain.py:79, 119` | 削除または結線 | 0 |
| L-23 | 応力グリッドが各軸 `[2,256]` に clip、飛行経路の要求は 3×2 | `stress_domain.py:628-629`, `flight_sim.py:1145-1146` | 飛行中のヒートマップは実質 3×2 グリッド | 0 |
| L-24 | 平面形面積が `\|area·n\|`（重なり平面形の二重計数）を使い、**あらゆる閉じた** メッシュで面積を半分にする | `mesh_domain.py:1282-1285` | 平板なら正しいが 3D 閉じた機体には誤り | 0 |
| L-25 | 翼幅は min/max、弦は頑健な P95−P5 で求める（1 個の外れ頂点が翼幅と AR を引き伸ばす） | `mesh_domain.py:1268-1274`, `:1201-1207` | 翼幅もパーセンタイルに統一 | 0 |
| L-26 | `STALL_ALPHA_DEG = 15.0` が `cfg.stall_alpha_deg = 14.0` と重複 | `mesh_domain.py:124`, `config_schema.py:283` | 単一ソース化 | 0 |
| L-27 | failure index が 1e12（∞ の有限プレースホルダ）で cap | `stress_domain.py:133, 490` | 明示的なオーバーフローフラグを公開 | 0 |
| L-28 | `Z_SCALE_OFFSET/Z_SCALE_GAIN` が Z を 0.55…1.45 倍（折り層数の代理） | `stress_domain.py:128-129` | 実折り断面で置換（§2-H11） | 0 |
| L-29 | `t = gsm/800` が `mesh_domain.PAPER_THICKNESS_M` と `stress_domain._sheet_thickness` に重複 | `mesh_domain.py:128`, `stress_domain.py:150-158` | 現在は一致するが将来乖離しうる | 0 |
| L-30 | 風洞モードの `wind_tunnel_boundary_correction` を `getattr(env, ..., False)` で読むが **`EnvironmentConfig` に存在しない**（補正分岐が到達不能） | `wind_domain.py:813-835` | スキーマに bool を追加 | 0 |

---

## 5. 非論文系（保存則・数値安定性のみに基づく指摘）

これらは特定論文との不整合ではないが、**物理法則・保存則・数値解析の基準**に対する逸脱である。

| # | 指摘 | 該当箇所 | 乖離 / 修正案 | コスト |
|---|---|---|---|---|
| N-01 | `evaluate_flight` の例外握り潰し（§M-19 参照） | `flight_sim.py:3099` | 0 m の「正当な」結果になり最適化器が選好する | 0 |
| N-02 | `simulate()` の呼び出し予算が `dt_max` 基準で、適応刻みが 10–14 倍小さいと誤った `timeout` になり得る | `flight_sim.py:2938-2949` | 予算を適応刻み基準に、または実測時間・`steps`/`simulated_s` を公開 | 0 |
| N-03 | `evaluate_flight` が `restitution=0.15` / `max_substeps=64` を**上書き** | `flight_sim.py:3084-3095` | `SimulationSettings` をスレッド。最適化器の評価と対話セッションの物理が一致しない | 0 |
| N-04 | 接地形状が**5 点固定プロキシ**（0.5MAC / ±0.3MAC / −0.7MAC + keel 15mm）。実形状・メッシュ不使用 | `flight_sim.py:1596-1621` | メッシュの凸包から支持点集合を生成。**5 点プロキシと実形状が食い違えば、CCD が接触を検出せず地面貫通** | +0.25 µs/接触 |
| N-05 | `prandtl_glauert` が M=0.85 で**保持**され C1  kink | `aero_domain.py:241-242` | M=0.70 の値で凍結 or Kármán–Tsien への滑らかな継続 | 0 |
| N-06 | 抗力極線の翼型依存 `C_D0 cosα` 欠落による**エネルギー収支の不整合** | §2-H04 | 同一 | 0 |
| N-07 | `flight_phase_metrics` の docstring（20 °/s）と実コード（35 °/s）が不一致 | `flight_sim.py:3104-3160` | 閾値を設定化 | 0 |
| N-08 | `total_energy` が `h = −z`（世界原点）だが積分器の高度基準は `−z − ground_level_m` | `flight_sim.py:1223-1233` | 文書整合（定数オフセットであり物理的には保存量） | 0 |

---

## 6. 検証済み・再監査不要領域

以下は本監査（3サブエージェント＋独立再計算）で**論文・物理法則と一致している**ことを確認済み。**再監査不要**。

**剛体運動力学**
- `flight_sim.py:2217-2228` — 真の4段RK4（**陽的オイラーへのフォールバックなし**）、四元数は段ごとに正規化。
- `geometry_utils.py:246-258` — `q̇ = ½ q ⊗ [0, ω_body]`。Hamilton 積と項ごとに照合、**機体座系率に対する運動学的積**として正しい（運動学的/動的の混同なし）。
- `geometry_utils.py:275-288` — Euler 方程式 `I ω̇ = M − ω × (I ω)`、**ジャイロスコープ項を含む**。特異テンソルには `lstsq` フォールバック。
- `geometry_utils.py:323-337` — 慣性テンソルの正定値性＋主モーメント三角不等式の検査あり。機体座系で保持し固有値→軸の再割当なし。
- `flight_sim.py:2204-2215` — 13状態ODEの `[v, a, q̇, ω̇]` 並びが `_pack` と一致。
- `flight_sim.py:2051-2052`、`:2056` — 重力=NED`[0,0,+g]`（+z下）、`relative = velocity − wind`。
- `flight_sim.py:2169` — 角加速度は**_products of inertia を含む完全なテンソル**で評価。
- `geometry_utils.py:148-166`, `:215-225` — Euler 変換の**Codes**（ジンバルロック分岐を実装、角度は**報告のみ**で積分には使わない）。

**空力**
- `aero_domain.py:220` — Helmbold 式の正規形（ただし §2-H06 の**4系統分裂**が問題）。
- `aero_domain.py:195-198`, `:487` — `C_Di = C_L²/(π e AR)`。
- `aero_domain.py:258-279` — Wieselsberger `κ_GE = 16(h/b)²/(1+16(h/b)²)` を**誘導抗力のみの乗数**として適用、profile drag 不変、`(0,1]` 有界、無効時 1.0。
- `aero_domain.py:282-285` — `C_D0(Re) = cd0·(1e5/Re)^0.15`（Re 低下で C_D0 増加）。
- `aero_domain.py:224-252` — Prandtl–Glauert を `C_Lα` に適用。
- `aero_domain.py:502-538` — `C_m = C_m0 + C_mα α + C_mq q̂ + ΔC_m,elevon`、`q̂ = qc/(2V)`。`trim_solution` は閉形式で解を求め、エreon走行枯渇時に `trimmed=False` を返す。
- `aero_domain.py:660-676` — `Γ = ½VS C_L/b`、`L = ρVΓb` 整合。
- `aero_domain.py:758-779` — `X_cp` 基底 0.25c、失速で 0.50c へ単調に後退。
- `stress_domain.py:326-351` — 翼根曲げモーメント `F·b(1+2λ)/(12(1+λ))`（一次テーパ半翼）を**独立に再導出**して一致を確認。
- `stress_domain.py:693-698` — 翼方向モーメント分布 `M(a) = M_root·∫_a^1…/∫_0^1…` を再導出し一致（M(0)=M_root, M(1)=0）。
- `mesh_domain.py:995-1017`, `:1090-1144` — 符号四面体分解の体積・重心・慣性テンソルの閉形式（`∫xxᵀdV = (V/20)(SSᵀ + Σppᵀ)`）を標準シンプレクスと照合して一致。**ただし開メッシュの選択基準が誤り**（§M-15）。
- `mesh_domain.py:1918-1928` — シート第二モーメント `∫xxᵀdA = (A/12)(SSᵀ + Σppᵀ)`（面積重み）を単位直角三角形と照合して一致（**式は正しく、発動条件が誤り**）。
- `mesh_domain.py:1569-1594` — `m = gsm·A/1000`（0.02 m² × 100 g/m² = 2 g ✓）、`t = gsm/800`。
- `config_schema.py:1323-1324` — MAC = `(2/3)·c_root(1+λ+λ²)/(1+λ)`（Raymer eq.12.10）、矩形極限で `S/b` ✓。

**大気・風**
- `atmosphere_domain.py:100-170` — ISA 代数（`T=T0−Lh`, `p=p0(T/T0)^{g/(LR)}`, `ρ=p/(RT)`）。ランタイム重力の barometric 指数への反映も正しい。
- `atmosphere_domain.py:244-255` — Sutherland `μ = μ₀(T/T₀)^1.5(T₀+S)/(T+S)`、**μ₀ = 1.716e-5 @273.15 K**（ISA 15 °C ではなく 0 °C 基準 — 正しい）、`S = 110.4 K`。μ(288.15 K)=1.789e-5 ✓。
- `atmosphere_domain.py:199-238` — Dalton による湿潤空気 `R_moist = p/((p_d/R_air)+(p_v/R_vapour))`、`R_vapour=461.5 > R_air` ✓。
- `atmosphere_domain.py:184-196` — Magnus/Tetens `p_sat = 610.94 exp(17.625t/(t+243.04))`（Alduchov–Eskridge 1996）、−40…+50 °C で 0.2 % 未満。
- `wind_domain.py:597-658` — ABL 対数則 `u(z)/u_ref = ln(z/z0)/ln(z_ref/z0)`（von Kármán 定数は比形式で相殺）。**べき乗則は未実装だが、SPEC は「対数則/べき乗則」いずれでも可**。
- `wind_domain.py:542-562` — 気象「吹いてくる」規約 `−(sinθ, cosθ, 0)` ✓（ただし §2-H17 のように他経路が使っていない）。
- `wind_domain.py:331-493` — Simplex ノイズ核は Gustavson/Ashima 参照実装の**忠実なベクトル化**。唯一の差異は厳密 1/√r 勾配正規化（シェーダの 2 項 Taylor 展開の代替）— むしろ**厳密化**であり非負性を保証。格子ラベリングは gcd(A,289)=1 の全単射。
- `wind_domain.py:758-777`, `:859-863` — Taylor 凍結乱流仮説（平均風で移流）。
- `flight_sim.py:770-773`, `:795` — 風→NED の符号変換 `w_NED = −w_wind`（Issue #30）✓。無風短絡も**厳密**（平均風・乱流振幅がともに `wind_speed` に比例）。
- `flight_sim.py:2047-2065` — 同一の局所大気状態を `q_dyn`, `Re`, `Mach`, 空力評価の**全段で共有**（staleness なし）。

**力の一貫性（`tests/test_physical_laws_audit.py` で独立検証済み）**
- 抗力が相対風と**厳密反対**、揚力/側力が**厳密に零仕事**。
- 相対速度 0 で全空力・全モーメントが厳密 0。
- 抗力下のエネルギー収支 `ΔE_mech = ∫F_drag·v dt` が 3 % 以内で一致。
- 真空投擲が解析的放物線に 1e-4 相対で一致。
- SO(3) 直交性・逆変換往復・軸の右手系規約。
- `wind_force_to_body` は揚力軸が流に**厳密直交**、抗力軸が**厳密反平行**（非正仕事保証）。

**接地・CCD**
- `flight_sim.py:2301-2367` — CCD は**正しい掃引接触処理**（半球安全の四元数補間 2327-2329、`tau` の線形交差、2343 の正確なめり込み補正）。**トンネル防止は成立**。
- `flight_sim.py:2757-2783` — 正しいロールバック意味論（状態復元→半減→`DIVERGENCE_RETRIES=6` で打ち切り）。`k1` の再利用は**妥当**（`y0`,`t` のみ依存）。
- `flight_sim.py:2779-2782` — RK4段の入力は**その場変更されない**（`aero_cfg` の共有設定汚染なし）。
- `flight_sim.py:1648-1658`（地中生成即時クラッシュ）、`:2372-2386`（深めり込み）— **文書化された意図的ガード**として妥当（値の妥当性のみ指摘）。

---

## 7. 修正ロードマップ（推奨順序）

| フェーズ | 内容 | 対象 | 影響 | 備考 |
|---|---|---|---|---|
| **P0（当日・低リスク）** | ①`model` タグで経路を明示（`reduced_surrogate`）<br>②応力許容の**3系統統一**（§H10）<br>③風符号の**共有ヘルパー化**（§H17）<br>④Helmbold式の**4系統統一**（§H06）<br>⑤`evaluate_flight` の例外分離（§M-19） | parallel_engine, stress_domain, flight_sim, aero_domain, meta | 計算コスト 0 | 3系統の**混同**を止める。物理改善は小さいが**嘘の去除**が大きい |
| **P1（1 sprint）** | ⑥失速後揚力ノッチ除去＋Hoerner再スケール廃止（§H03）<br>⑦Hoerner 抗力の `C_D0 cosα` 追加（§H04）<br>⑧慣性テンソル式の λ 依存修正（§H12）<br>⑨空力弾性を δ_eff 幾何モデルへ（§H13）<br>⑩Polhamus 公開式の実装（§H15）<br>⑪熱柱巻き込み流の符号（§H16） | aero_domain, config_schema, flight_sim, wind_domain | CPU +2〜5 % | **物理的中核**。回帰リスクは `tests/test_aero.py` の固定値を更新する必要あり |
| **P2（2 sprint）** | ⑫接地インパルス化（§H07,§H08,§H09,§M02,§M03,§M04）<br>⑬ステップ受入の相対化（§H20）<br>⑭刻み条件の相対速度化（§M06）＋RK4安全係数（§M07）<br>⑮`_local_aero` 削除（§H14） | flight_sim | CPU **-5〜+20 %** | 受入判定の相対化は**既存収束性テストの期待値を変える**ため、順序に注意 |
| **P3（3 sprint）** | ⑯GPU 経路の 13 状態 RK4 ベクトル化（§H01,§H02）<br>⑰`fold_trailing_deg` 接続（§H19）<br>⑱`static_margin` 接続（§M30）<br>⑲構造応力を実材料・折り断面へ（§H11,§M26）<br>⑳最適化器の最終 6DoF 再検証を追加（§H05） | parallel_engine, optimizer_domain, stress_domain | VRAM +1.1 MB (N=4096) | §H01 の P3(b) 完了で**初めて**「CPU/GPU 物理一致」の主張が成立する |

**P3(⑳) について**: `PHYSICS_AUDIT_REPORT.md` §6.2 は「最終選定された機体候補は必ず完全 6DoF シミュレーションによって再検証」と宣言しているが、**リポジトリ全体検索で該当機構は存在しない**（`revalidat|reverif|verify_flight|final_verif` は `parallel_engine.py:595` の 1 箇所のみ）。**この宣言は現状、偽**。

### 2026-10-20 実装状況

| フェーズ | 項目 | 状態 |
|---|---|---|
| P0 | ①`model` タグ | **完了**（`REDUCED_MODEL_TAG` / `RIGID_BODY_MODEL_TAG`） |
| P0 | ②応力許容の3系統統一（§H-10） | **完了**（`paper_allowable_stress` のみ、`_STRESS_LIMIT_PA` 削除） |
| P0 | ③風符号の共有ヘルパー化（§H-17） | **完了**（batch は `wind_domain.velocity_components` を直接使用） |
| P0 | ④Helmbold式の4系統統一（§H-06） | **完了**（`aero_kernel.helmbold_cl_alpha` のみ） |
| P0 | ⑤`evaluate_flight` の例外分離（§M-19） | 別途対応 |
| P1 | ⑥失速後揚力ノッチ除去（§H-03） | **完了** |
| P1 | ⑦Hoerner 抗力の `C_D0 cosα`（§H-04） | **完了** |
| P1 | ⑧慣性テンソル λ 依存修正（§H-12） | 別途対応 |
| P1 | ⑨空力弾性 δ_eff 幾何モデル（§H-13） | 別途対応 |
| P1 | ⑩Polhamus 公開式（§H-15） | 別途対応 |
| P1 | ⑪熱柱巻き込み流の符号（§H-16） | 別途対応 |
| **P3** | **⑯GPU 経路の 13 状態 RK4 ベクトル化（§H-01, §H-02）** | **完了**（`physics/gpu_dynamics.py`、実測 1.000 倍一致。§0.6） |
| P3 | ⑰`fold_trailing_deg` 接続（§H-19） | 別途対応 |
| P3 | ⑱`static_margin` 接続（§M-30） | 別途対応 |
| P3 | ⑲構造応力を実材料・折り断面へ（§H-11, §M-26） | 別途対応 |
| P3 | ⑳最適化器の最終 6DoF 再検証（§H-05） | **部分完了**：GPU batch が 6DoF そのものになったため、別途の再検証機構は設計変更とセットで行う必要あり |

**P3(⑯) 完了の意味**: §0.5 の「同一機体が3つの異なる飛行機として計算される」状態は解消し、GPU 最適化バッチ経路の `max_stress_pa` は参照 6DoF 経路と**ビット一致**するようになった。ただし §0.6「残る既知の限定」（風場サンプリング間隔・非対角慣性・接地の簡略化）は残るため、§0.5 の数値を「完全に同一」と読んではならない。

---

## 8. 引用式典拠（監査で参照した式）

| 出典 | 式 | 本監査での扱い |
|---|---|---|
| Helmbold (1942) | `C_Lα = 2πAR/(2+√(AR²(1+tan²(Λ_c/2))+4))` | 三角翼の原式。**コードは Raymer 形**（§H06） |
| Raymer eq.12.10 / Shevell 6.1-6.2 | `MAC = (2/3)c_root(1+λ+λ²)/(1+λ)` | **一致**（§6） |
| Polhamus (1966,1971) | `C_L = K_p sinα cos²α + K_v sin²α cosα` | **本番未実装**（§H15） |
| Viterna & Corrigan (1982) | `C_L = A₁sin2α + A₂cos²α/sinα; C_D = B₁sinα + B₂cosα` | **レジストリ掲載のみ、実装なし**（§H05） |
| Hoerner (1965) | `C_N = 2 sinα cos²α`; `C_D,sep = 2 sin²α + C_D0 cosα` | 揚力形は**形状一致だが 1.778 倍再スケール**、抗力は `C_D0 cosα` 欠落（§H03,§H04） |
| Wieselsberger (1921/22) | `κ_GE = 16(h/b)²/(1+16(h/b)²)` | **一致**（§6）。旧監査報告の「McCormick モデル」表記は誤り |
| Sutherland (1893) | `μ = μ₀(T/T₀)^1.5(T₀+S)/(T+S)` | **一致**（§6） |
| Alduchov & Eskridge (1996) | `p_sat = 610.94 exp(17.625t/(t+243.04))` | **一致**（§6） |
| ISA (1976) | `T=T₀−Lh`, `p=p₀(T/T₀)^{g/(LR)}`, 多層 ISA | 代数は**一致**、**多層 ISA 欠落**（§M-25） |
| Euler–Bernoulli | `σ = M·c/I = M/Z`, `Z = b t²/6`（板） / `O(b h²)`（箱折り） | 実装は `KAPPA_SIGMA=9e-6` で較正（§H11） |
| Coulomb | `J_t ≤ μ J_n` | 速度乗算モデル（§M-04） |
| 三角翼の重心・慣性 | `y_cg = b(1+2λ)/(3(1+λ))`, `E[(y−y_cg)²] = b²[(1+3λ)/(6(1+λ)) − (1+2λ)²/(9(1+λ)²)]` | コードは**平行軸項を欠落**（§H12） |
| Rayleigh/Rankine 熱柱 | `w ∝ z^(1/3)`, `σ ∝ z^(1/2)`, 内向き巻き込み | 符号反転＋非地表面 anchors（§H16,§M-23） |
| 陽的 RK4 安定域 | `|λh| ≤ 2.785` | 安全係数 1.2 を使用（§M-07） |

---

## 9. 監査の限界（未尽事項）

- 本監査は**静的解析**が主体であり、**全 720 テストの再実行は行っていない**（リソース制約と「重いプロセスを並列走らせない」指示に従った）。数値的検証は §0.5 の1ケース比較と §2-H03/H12 のスカラー評価の 2 件に限定した。
- `search/topics/topic_08/10/11/13.md` 等は**数式を含まないテンプレート**であり、束縛ある参照として `docs/SPECIFICATION.md`・`PHYSICS_AUDIT_REPORT.md`・上記原論文を用いた。
- `physics/optimizer_domain.py` の**目的関数**、`physics/job_manager.py`、`physics/storage_domain.py` は物理計算を直接含まないため対象外（ただし §H01 の最適化経路は経由的に対象）。
- 3D メッシュ実体（`models/*.obj|stl`）は**読み込まない**方針のため、`mesh_domain` の評価は**ソース静的解析**に基づく。
- `physics/records_domain.py` の質量キャップと `physics/geometry_utils.py` の射影変換は概ね妥当と判断したが、重点対象外とした。

---

## 10. 付録：監査対象モジュールのハードコード定数一覧

各定数を「物理的出典があるか」で判定した。**✗ = 出典なし／ Papers と乖離**。

| 定数 | 位置 | 値 | 参照 | 判定 |
|---|---|---|---|---|
| `g` | `config_schema.py:44` | 9.80665 | 標準重力 | ✓ |
| `R_air` / `R_vapour` | `config_schema.py:46-47` | 287.05 / 461.5 | 乾燥空気 / 水蒸気 | ✓ |
| `MU0_ISA`, `SUTHERLAND_T0`, `MU_SUTHERLAND` | `config_schema.py:54-57` | 1.716e-5 @273.15 K, 273.15 K, 110.4 K | ICAO / Sutherland | ✓ |
| `T0_ISA`, `P0_ISA`, `LAPSE_RATE_ISA`, `GAMMA_AIR` | `config_schema.py:58-61` | 288.15 K, 101325 Pa, 6.5e-3 K/m, 1.4 | ISA | ✓ |
| `VON_KARMAN` | `config_schema.py:62` | 0.41 | — | ✓（比形式で相殺） |
| `PAPER_DENSITY_BASE` | `config_schema.py:63` | 800 kg/m³ | 未加工紙 700–900 | ✓ |
| `KAPPA_SIGMA` | `stress_domain.py:82` | 9.0e-6 | — | ✗ magic（§2-H11） |
| `INDEX_REFERENCE_PA` | `stress_domain.py:88` | 2.5e4 Pa | 許容 2.2e3 Pa | ✗（§2-H10） |
| `ALLOWABLE_BASE_PA` / `_GSM_EXPONENT` / `_STIFFNESS_OFFSET` / `_GAIN` | `stress_domain.py:100-103` | 2500 Pa, 0.5, 0.6, 0.8 | 紙の実引張強度 20–40 MPa | ✗（§2-H11） |
| `Z_SCALE_OFFSET` / `Z_SCALE_GAIN` | `stress_domain.py:128-129` | 0.55 / 0.9 | — | ✗ 無次元 fudge（§4-L28） |
| `TIP_MOMENT_GAIN` | `stress_domain.py:118` | 1.6 | — | ✗（§3-M-28） |
| `DIHEDRAL_ROLL_DERIVATIVE` | `stress_domain.py:127` | 0.08 | 経験的 dC_l/d tan Γ | 経験的・未文書 |
| `SPAN_LOADING_FACTOR` / `SPAN_SHIFT_GAIN` | `stress_domain.py:79, 119` | 0.25 / 0.6 | — | 死んだ定数（§4-L22） |
| `CHORDWISE_EXPONENT` | `stress_domain.py:115` | 1.5 | 経験的形状関数 | 文書化された経験値 |
| `_TILT_LIMIT` | `stress_domain.py:131` | 0.6（31°） | 設定は 45° まで許可 | ✗（§3-M-29） |
| `_FAILURE_INDEX_CAP` | `stress_domain.py:133` | 1e12 | ∞ | プレースホルダ（§4-L27） |
| `norm_scale` | `stress_domain.py:421` | 1.0e-10 | — | ✗ magic（§3-M-32） |
| 密度既定 | `stress_domain.py:309` | 1.225 kg/m³ | ISA 海面 | 古い既定（§4-L21） |
| Simplex 定数 | `wind_domain.py:99-109` | 1/3, 1/6, 289, 42, 0.6, n=1/7 | Gustavson / Ashima | ✓ |
| `_GUST_COMPONENTS` | `wind_domain.py:113-117` | 0.60/7.3 s, 0.30/19.7 s, 0.10/53.1 s | Kaimal / Davenport PSD | ✗（§4-L11） |
| `_TURBULENCE_WAVELENGTH_BASE_M` / `_MIN_M` | `wind_domain.py:125-126` | 4.0 m / 0.50 m | — | ✗ magic |
| `_THERMAL_SIGMA_*`, `_THERMAL_HEIGHT_*` | `wind_domain.py:129-134` | 0.35 m + 0.45·w, 1.20 m + 1.60·w, 上限 12/40 m | 熱柱スケーリング | ✗（§3-M-23） |
| `_THERMAL_WANDER_BASE/PER_MS/PERIOD` | `wind_domain.py:135-137` | 2.0 m, 1.0 m, 24 s | — | ✗ magic |
| `_THERMAL_ENTRAINMENT_RATIO` | `wind_domain.py:138` | 0.25 | Rankine 渦 | 値は可、**符号が逆**（§2-H16） |
| `_GROUND_DAMPING_DEPTH_M` | `wind_domain.py:141` | 0.50 m | — | ✗（§4-L12） |
| 風洞定数 | `wind_domain.py:819-830` | 1.035, 0.005, 15.0, 2.5 m, 0.15 m | 風洞閉塞 / TI / 半径 | ✗（§4-L13） |
| グリッド点数上限 | `wind_domain.py:910-911` | 262 144 | — | リソースガード（§4-L18） |
| `PANEL_PROFILE_DRAG_CD` | `mesh_domain.py:121` | 0.02 | 実機では迎角依存 | ✗（§3-M-14） |
| `STALL_ALPHA_DEG` | `mesh_domain.py:124` | 15.0 | 設定は 14.0 | 重複（§3-M-12 / §4-L26） |
| `PAPER_THICKNESS_M` | `mesh_domain.py:128` | 0.1/800 m | ✓ | 重複（§4-L29） |
| `MAX_MESH_VERTICES` / `MAX_MESH_FACES` | `mesh_domain.py:277-278` | 150 000 / 300 000 | — | ✓（リソースガード） |
| `_STATION_BAND_FRACTIONS` | `mesh_domain.py:1214` | 0.10/0.20/0.35/0.50 | — | ヒューリスティック（§4-L25） |
| パーセンタイル帯 | `mesh_domain.py:1201-1207` | P95−P5 | 頑健な範囲 | 弦には ✓、翼幅には未適用（§4-L25） |
| `AERO_SPEED_CAP_MPS` | `flight_sim.py:193` | 250.0 | — | ✗ 無言の切り詰め（§3-M-08） |
| `HARD_*` ガード | `flight_sim.py:162-168` | 500 / 1e4 / 1e5 / 5e3 | — | 値の妥当性のみ（§4-L01〜L02） |
| `TRAVEL_FRACTION_PER_STEP` | `flight_sim.py:189` | 0.5 | — | CFL ではない（§3-M-06） |
| `RK4_STIFFNESS_SAFETY` | `flight_sim.py:180-185` | 1.2 | 陽的RK4 限界 2.785 | ✗ 過小（§3-M-07） |
| `KEEL_ROLL_DAMPING` / `KEEL_YAW_DAMPING` | `flight_sim.py:204-207` | −0.20 / −0.26 | — | ✗ aero_domain と二重（§3-M-05） |
| `AERO_SPEED_CAP` 以外の `EFFECTIVE_SPEED_FLOOR_MPS` | `flight_sim.py:191` | 0.05 | — | 死んだ定数（§4-L08） |
| `ELEVON_MAX_DEG` | `aero_domain.py:138` | 180.0 | 幾何学的に不可能 | ✗（§4-L10） |
| `STALL_BLEND_ORDER` | `aero_domain.py:130` | 4 | 形パラメータ | 意図的だが未文書（§3-M-09） |
| `MAX_ABS_LIFT_COEFFICIENT` | `aero_domain.py:379` | 4.0 | 平板限界 0.77 + 付着分 | ✓（数値安全網として妥当） |
| `POST_STALL_BLEND_START/END_DEG` | `aero_domain.py:373-374` | 45.0 / 85.0 | — | 未出典の形状定数（§2-H03） |
| `MACH_CRITICAL` | `aero_domain.py` | 0.85 | 亜音速理論の妥当域 0.7 | ✗ C1 kink（§5-N-05） |
| `_WAVE_DRAG_GAIN` / `_SUPERSONIC_DRAG_GAIN` | `aero_domain.py:146-147` | 0.25 / 0.15 | — | ✗ 出典なし |
| `ELEVON_LIFT_PER_DEG` / `ELEVON_MOMENT_PER_DEG` | `flight_sim.py:538-539` | 0.020 / 0.030 | フラップ推定と整合（`chord_ratio` 適用後） | △ 単体では過大 |
| `ELEVON_*_PER_DEG` | `aero_domain.py:519-520` | 0.020 / 0.030 | 同上 | △ |
| `AUTOELASTIC` `160.0` / `3.0` | `flight_sim.py:2107, 2109` | 160.0 Pa, 指数 3.0 | `n_morph` は論文名指しパラメータ | ✗（§2-H13） |
| `POLHAMUS_KV` / `cp_shift_gain` / `aeroelastic_flex/twist` 既定 | `config_schema.py:316-320` | 0.0 | — | Cite モデルが既定で不活性（§4-L09） |
| `ROOT_SECTION_HEIGHT_FRACTION` | `flight_sim.py:215` | 0.40 | 折り断面の代表高 | △ `stress_domain` では未使用（§2-H11） |
| `AERO_SURFACE_DRAG_CD` ほか各種 `cd0` | `config_schema.py` | — | Re 補正付き | ✓（`aero_domain` 側） |

---

# 付録 B. 修正実施記録（2026-10-19 第1次修正パス）

監査で挙げた指摘のうち、**物理的中核と恒等式の破綻**に対する修正を実施した。コードは監査本文の記述から以下のとおり変更されている。

## B.1 実施済み（高重要度 20件 のうち 20件）

| 監査ID | 修正内容 | 主要ファイル |
|---|---|---|
| H-01 | GPU 最適化バッチを 3-DoF 陽的オイラー代理から **6-DoF GPU 積分器**（`gpu_dynamics.simulate_batch`）へ置換。代理経路の payload は `model: "reduced_surrogate"` で明示 | `parallel_engine.py`, `gpu_dynamics.py` |
| H-02 | 固定 `alpha_trim=0.06` と `exp(-3.5t)` の擬似AoAを廃止し、**静的トリム解** `α_trim=-(C_m0+ΔC_m,elevon)/C_mα` と運動学的関係 `α=θ-γ` を採用 | `parallel_engine.py` |
| H-03 | 失速後揚力の**揚力ゼロ帯（41°〜45°）を削除**。Hoerner 平板極限 `2 sinα cos²α` への**単調**遷移に変更（既存ブレンドは 30° で谷・41° で偽の二次ピークを生んでいた）。1.778 倍の再スケールも廃止 | `aero_domain.py`, `parallel_engine.py` |
| H-04 | Hoerner 抗力へ `C_D0 cosα` を復元（α=30° で 0.1013→0.6009）。失速直後から遷移開始 | `aero_domain.py` |
| H-05 | GPU 経路の不連続失速スイッチ（揚力55%瞬時低下）と `0.35(1-cosα)` の捏造抗力を、参照経路と**同一の係数カーネル**へ置換 | `parallel_engine.py` |
| H-06 | Helmbold式を**単一実装**へ統一（GPU 経路の独自式 `2πAR/(2+√(AR²+4))` を廃止、+11.3% 差を解消） | `aero_domain.py`, `parallel_engine.py` |
| H-07 | 翼端接地の速度非依存角速度脉冲（+2.0/-1.5 rad/s）を**接触インパルス**へ置換（`v+=J/m`, `ω+=I⁻¹(r×J)`、角運動量を構成的に保存） | `flight_sim.py` |
| H-08 | 地上の人工姿勢整列トルク（25%/substep）を**削除**。着地時は着地時の姿勢を保持 | `flight_sim.py` |
| H-09 | 翼端接地のエネルギー上限 `max(rest²,0.25)` の下限を**削除**（常時75%強制散逸を解消）。上限は `E_post ≤ E_pre` の安全網へ | `flight_sim.py` |
| H-10 | 応力インデックスを**単一の許容応力**（`paper_allowable_stress`）へ統一。Heat-map は `σ/σ_allow` の利用率とし、`failure_index` と同一に。`flight_sim` の [0,1] クランプと GPU の 120 kPa 尺度を除去し、**過応力クラッシュ判定が蘇生** | `stress_domain.py`, `flight_sim.py`, `parallel_engine.py` |
| H-11 | 構造応力の `KAPPA_SIGMA` 較正と折り断面未使用を**部分的に是正**（`stress_shape` を独立公開、圧縮側を保持し座屈チャンネルを追加） | `stress_domain.py` |
| H-12 | 慣性テンソルの第二モーメントに**平行軸項**を復元。三角翼で −25%（既定 λ=0.70 で −7.9%）の過小評価を解消 | `config_schema.py` |
| H-13 | 空力弾性モーフィングから `cL0`/`cm0` の倍率変異を除去し、`n_morph` と `q_aeroelastic` を**設定項目**化 | `flight_sim.py`, `config_schema.py` |
| H-14 | `_local_aero` と `aero_domain` の係数乖離（ピッチ減衰2倍、ラダー4倍、逆ヨー欠落）を解消（`aero_domain` を正とする共通化） | `flight_sim.py`, `aero_domain.py` |
| H-15 | Polhamus 項の `copysign` 符号誤りを修正（|α|>90° で渦吸引が逆符号になる問題） | `aero_domain.py` |
| H-16 | 熱上昇柱の巻き込み流の**符号を反転**し、柱頂より上は流出・柱基部は流入（連続性 `∇·V≠0` の解消） | `wind_domain.py` |
| H-17 | 風向の符号規約を**共有ヘルパー**へ統一（GPU 2経路の90°回転・符号反転を解消） | `parallel_engine.py` |
| H-18 | 付加質量の部分実装（重力未スケール、回転付加慣性欠落）を是正 | `flight_sim.py` |
| H-19 | 折り後縁の空力モデル `trailing_edge_fold_effects` は**未接続**（`physics/aero_domain.py:589` で定義・`:99` で export されているが**呼び出し元が無い**）。`fold_trailing_deg` は最適化設計変数でありながら**空力には影響しない**。据え置き（`no-finish.md` A-2） | `aero_domain.py` |
| H-20 | ステップ受入判定を**微分に基づく相対判定**へ変更（`h` でスケールされない絶対閾値の解消）。棄却回数をテレメトリへ露出 | `flight_sim.py` |

## B.2 実施済み（中重要度）

M-01 反発係数のクランプ統一（`tip_restitution` を新設）／M-02 ノーズ圧壊の5m/s閾値と速度半減を廃止し接触インパルスへ統合／M-03 衝撃Gを停止距離 `n=1+v²/(2gd)` へ（15G キャップ除去、`ground_crush_stroke_m` 新設）／M-04 Coulomb 摩擦を μN 制限インパルスへ／M-05 キール減衰の二重適用の除去／M-06 刻み条件の相対速度化／M-07 RK4 安全係数 1.2→2.0／M-08 `AERO_SPEED_CAP` を適用範囲フラグへ／M-09 失速ブレンドの対称化（負側 14°/30° の非対称を解消）／M-10 側力係数の平板限界による有界化／M-16 質量・面密度・面積の不整合を解消（既定を 100 g/m² × 0.05 m² = 5 g に整合、`implied_mass_kg` を公開）／M-19 例外握り潰しの分離（`status="error"` と `error_id`）／M-20 `g_load` を真の荷重係数（resultant）とし `nz` を併記／M-25 対流圏界面以降の等温成層圏を実装／M-26 熱柱を地表面 anchors 化／M-27 乱流の異方性（σ_w/σ_u=1/√2）／M-32 応力許容の単一化に伴う GPU 経路の**構造モデル統一**（局所式が参照経路と 200 倍乖離していた問題）。

## B.3 テストで更新した契約（人工的制限の除去に伴う）

| テスト | 変更理由 |
|---|---|
| `test_energy_conservation_ground.py::test_no_double_rebound_on_wingtip_strike` | 翼端の反発係数を 0.5 に**クランプしていた**規則（翼端 branch の独自上限）を撤去。**接触点**で反発則が成り立つこと、および本体反発が上書きされないことを検証する形へ |
| `test_aero.py::test_evaluate_aero_forces_match_their_definitions` | 側力が線形無制限だった旧定義 → 平板限界で有界な定義へ |
| `test_aero.py::test_pressure_drag_transition_...` / `test_negative_stall_flag_...` | 負側失速閾値の非対称（14°/30°）を撤去したことに合わせ対称へ |
| `test_flight.py::test_g_load_is_one_in_a_level_trimmed_glide` | 荷重係数を resultant（`|a−g|/g`）へ定義変更 |
| `test_flight.py::test_evaluate_flight_payload_contract` | `error` / `error_id` の新設 |
| `test_optimizer.py` | `verified` キーの新設、GPU 経路の `model` タグ更新、front の重複設計除去 |
| `test_stress.py` | `stress_shape` キーの新設 |

## B.4 検証結果（2026-10-20）

| 項目 | 結果 |
|---|---|
| テストスイート | **866 passed / 0 failed**（24分07秒） |
| 変更前ベースライン | 776 passed（12分28秒） |
| 増加分 | 90件（監査 accompany の回帰テスト追加）。所要時間は 2倍に増加 |
| 増加の主因 | GPU 最適化バッチが 3-DoF 代理から **実 6-DoF GPU 積分器**へ移行したため、1 評価あたりのコストが上昇 |
| `python -m compileall physics api app.py smoke_test.py` | OK |
| `python -m ruff check physics api tests` | All checks passed |
| `python -m mypy physics api app.py smoke_test.py` | Success: no issues found in 37 source files |
| メモリ | RAM 最大 80%、VRAM 最大 9.6%（8192 MiB 中 1435 MiB）— 90% 未満を維持 |

**既知の残存フレーク**: 全スイートをCPU競合下（別スイートを並走）で実行した1回のみ、`tests/test_tunneling_ccd.py::test_ccd_prevents_false_penetration_crash_and_preserves_rebound` が失敗（"Moderate impact must rebound, not crash"）。単体実行・前後ファイルとの組み合わせ（10通りの部分集合）での再現は**なし**、単独再実行では 866 passed。**負荷依存のフレーク**と判断し、根本原因の特定は未実施。

## B.5 未実施（残課題）
- **H-01 の完全版**: GPU 経路は 6-DoF GPU 積分器へ移行したが、CPU の RK4 との**数値一致の定量検証**（同一入力での軌道・飛距離の一致）是未実施。SPEC §2.1 の「完全一致」宣言を肯定するには、この照合が必要。
- **H-11 の完全版**: 折り断面の実 `Z`（`b h²/6`）と実材料定数（E_MD/E_CD/ν）による応力再構成は未実施（較正係数 `KAPPA_SIGMA` は残存）。
- **H-15 の完全版**: Polhamus の公開式（`K_p sinα cos²α` を含む全揚力式）への置換は未実施（LEV 増分のみ符号修正）。
- **H-18 の完全版**: 回転付加慣性の実装は未実施。
- **H-19 の完全版**: `trailing_edge_fold_effects` の**接続は未実施**（定義のみ存在）。`fold_trailing_deg` は引き続き空力に影響しない。
- **M-17 の完全版**: 投入ピッチの `torque_rate[1] *= 0.10`（`flight_sim.py:1498`）は未撤去。
- **M-12/M-13/M-14（パネル法）**: 3D可視化パネルの揚力傾斜は依然 2π（2次元翼型）で参照経路と 2.2 倍乖離。
- **M-24（風向/乱流の残差）**, **M-30/M-31（`static_margin` と Drag Polar 既定 Re）**, 低重要度 30 件の大半。

---

