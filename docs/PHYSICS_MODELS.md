# 物理モデル正本 (Canonical Physics Models)

本書は **Issue #48 / #49 / #50 / #51 / #52** の解答であり、`docs/SPECIFICATION.md` §5（学術引用・文献根拠レジストリ）を補足する。
各物理量について「**式 → 出典 → 正本モジュール → 適用限界 → 検証テスト**」を 1 箇所に集約する。

**原則**（`AGENTS.md` §1.2 / §2）:

- 同じ物理式は **1 実装** にしかない。スカラー経路 (`physics/aero_domain.py`) とバッチ経路
  (`physics/gpu_dynamics.py`) は **同一の正本関数を呼ぶ**。経路が 2 つあるなら、その 2 つは数式で
  結ばれていなければ「単一実装原則の違反」であり、数値の一致は保証されない。
- 出典のない係数を **新たに作らない**。出典の制約で実装できない値は **値を据え置き**、本書に
  据え置きの理由と再オープン条件を記す。
- モデルの **適用限界** を明記しない実装は、記録された検証結果を範囲外まで適用していることになる。

---

## 1. 正本モジュール一覧 (Issue #51: 物性・係数の重複定義の解消)

| 物理量 | 正本 (canonical) | 呼び出し元 | 経路間の取り違え防止 |
|---|---|---|---|
| 揚力傾斜 `C_Lα` | `physics/aero_kernel.py::helmbold_cl_alpha` | `physics/aero_domain.py::helmbold_cl_alpha` (再エクスポート), `aero_kernel.py:1594` | スカラー / NumPy / CuPy の 3 パスが **同一関数** を呼ぶ |
| 揚力・抗力・モーメント | `physics/aero_kernel.py::lift_coefficient` / `drag_coefficient` / `moment_coefficients` | `physics/aero_domain.py` (スカラー), `physics/gpu_dynamics.py` (バッチ) | `aero_kernel` は `ScalarMath` / `numpy` / `cupy` の **1 実装** を抽象化 |
| 失速後揚力 | `physics/aero_kernel.py::post_stall_lift_coefficient` | `aero_kernel.py::lift_coefficient`, `aero_domain.py` | §3 参照 |
| Polhamus 前縁渦揚力 | `physics/aero_kernel.py::polhamus_lift_coefficient` | `aero_kernel.py::lift_coefficient`, `aero_domain.py::polhamus_lift_coefficient` | §5 参照 |
| 翼根曲げ応力（スカラー縮約） | `physics/stress_domain.py::peak_root_stress_pa` | `physics/gpu_dynamics.py`, `physics/ga2/stage1.py`, `tools/check_stress_peak_parity.py` | §4 参照（縮約は応力場の**厳密な縮約**でなければならない） |
| 翼根曲げ応力（場） | `physics/stress_domain.py::stress_field` | `physics/flight_sim.py` (6DoF 正規経路), `stress_domain.analyse` | 6DoF 経路は **場** のピークを報告する |
| 大気（密度・音速・動粘性） | `physics/atmosphere_domain.py` | `physics/flight_sim.py`, `physics/aero_domain.py` | ISA + Sutherland。局所大気は同一関数を呼ぶ |
| 風場（3D 乱流） | `physics/wind_domain.py` | `physics/flight_sim.py::wind_vector`, `physics/gpu_dynamics.py` | 世界 NED への変換は `wind_vector` 1 箇所のみ |
| 応力許容値 | `physics/stress_domain.py::paper_allowable_stress` | 全経路 | **判定尺度は 1 つだけ**（`no-finish.md` §10 R-3） |
| GA の既定値 | `physics/ga_config.py` | `physics/ga_domain.py`, `physics/ga2/*` | 探索ロジックにハードコードしない |

**風の高さ基準の正本**: 風場は **地表面からの高度** (`z_up = -z_ned` からの `ground_level_m` 差し引き) で
サンプルする。`physics/wind_domain.py` と `physics/flight_sim.py::wind_vector` は同一の基準を使い、
`physics/gpu_dynamics.py` のバッチ経路も `flight_sim` と同じ `wind_vector` を呼ぶ
（バッチ経路が独自の高度基準を持つことはない）。

---

## 2. 揚力傾斜: Helmbold (1942) (Issue #48)

| 項目 | 内容 |
|---|---|
| 式 | `C_Lα = 2π AR / (2 + √(AR²(1+λ) + 4AR/π))` |
| 出典 | Helmbold, H. B. (1942), *Jahrbuch 1942 der deutschen Luftfahrtforschung*, I, 111-113 |
| 正本 | `physics/aero_kernel.py::helmbold_cl_alpha` |
| 適用限界 | **低アスペクト比 (AR ≲ 4) の直線翼**。AR が大きい翼では 2 次元値へ漸近する。翼型の特性は記述しない（後述 §2.1） |
| 独立検証 | `tests/test_aero.py::test_helmbold_matches_closed_form`（閉形式と `rel=1e-12`）、`::test_helmbold_monotonic_in_aspect_ratio`、`::test_helmbold_decreases_with_taper_ratio`、`::test_helmbold_reduces_to_the_elliptic_result` |

### 2.1 適用限界の明示

- Helmbold 式は **三角翼・直線翼** の低 AR wings の **揚力傾斜** の経験式であり、**失速後の挙動**・
  **抗力**・**モーメント**・**空力弾性**を記述しない。CNN 経路（`physics/aero_domain.py`）では
  この式を **Attached 域の傾斜** としてのみ使い、失速後は §3 の別モデル接手する。
- `cfg.cLalpha_per_rad > 0` のとき Helmbold は **上書き** される（ユーザー明示値＝単一実装原則の
  例外として、API 契約で公開されている）。
- **パネル法**（`physics/mesh_domain.py`）は Helmbold の 2.2 倍乖離する。これは `no-finish.md` §A-5 /
  F-8 の **意図的維持** であり、2 つの空力モデルを 1 式に統合しない（統合は単一実装原則を壊す）。

### 2.2 検証コマンド

```
python -m pytest tests/test_aero.py -q -k helmbold
```

---

## 3. 失速後モデル: Hoerner 平板 (1965) と Viterna-Corrigan (1982) の採否 (Issue #49)

### 3.1 実装されているモデル

| 項目 | 内容 |
|---|---|
| 実装 | `physics/aero_kernel.py::post_stall_lift_coefficient`（正本）、`physics/aero_domain.py::_post_stall_lift_coefficient`（再エクスポート） |
| 式 | 失速時の翼の `C_L` を基準に、**Hoerner の完全分離平板** `C_L = 2 sin α cos²α` の **混合角における値** へ `[α_stall, 45°]` で滑らかに接続（`POST_STALL_PLATE_BLEND_DEG = 45.0`） |
| 出典 | Hoerner, S. F. (1965), *Fluid-Dynamic Drag*, §2.4（平板の法線力） |
| 検証 | `tests/test_aero.py` の失速後テスト群、`tools/check_stress_peak_parity.py` |

**Viterna-Corrigan (1982) は未実装**。`/api/v1/meta/physics-references` のレジストリは
Hoerner 平板（実装済）と Viterna-Corrigan（**代替案・未実装**）を区別して公開する（§3.3）。

### 3.2 モデル選定の根拠と適用限界

- **採否**: Viterna-Corrigan は **風力タービン翼型** の失速後外挿のために導出され、**翼型データから
  幾何係数** `$A_1, A_2, B_1, B_2$` を求める。紙航空機の機体にはその翼型データが存在せず、
  係数を **出典なく** 推定することになる（`AGENTS.md` §2 の出典なき係数禁止）。
  Hoerner 平板は **係数を持たない**（形状のみ依存）ため、根拠のない数値を 1 つも増やさずに
  失速後を記述できる。
- **Hoerner 平板の適用限界**: 完全分離を仮定する **平板** の法線力であり、実翼の
  「翼型残留揚力」「前縁渦の残存揚力」「 bodily による有効翼面積の減少」を含まない。
  失速後の **揚力低下は定性的に正しい** が、**絶対値は平板より大きい**。
- **適用範囲**: 失速後 (`α > α_stall`) の **揚力**。失速後の **抗力** は
  `physics/aero_domain.py::separated_pressure_drag_coefficient` が承担する（`POST_STALL_DRAG_BLEND_DEG = 15°`）。
- **連続性の保証**: 混合角における平板値を **固定目標** として使うため、合成された `C_L` は
  失速から混合角まで **厳密単調減少** し、混合角で平板曲線に **厳密に一致** する。
  `C_L == 0` の帯（揚力ゼロの深失速）が発生しない。
  検証: `tests/test_aero.py`（`C_L` の有限性と連続性を迎角全域で検査）。

### 3.3 レジストリとの整合 (Issue #49 の要件事項)

`api/routes/meta.py` の `physics-references` は各エントリに次を **必須** で持たせる:

- `implemented`: `true` / `false`
- `canonical_module`: 正本モジュールのパス
- `applicability`: 適用限界の 1 行

これにより「公開されている式が実装されている」という **虚偽の主張**（Viterna-Corrigan）が API 上で
OTHERS 燃焼しない。検証: `tests/test_physics_model_registry.py`。

---

## 4. レイノルズ数依存性 (Issue #50)

### 4.1 どの係数が Re に依存するか

| 係数 | 依存形 | 式 | 正本 |
|---|---|---|---|
| 抗力係数 `C_D0` | **依存** | `C_D0(Re) = cd0 · (1e5 / Re)^re_cd0_exponent` | `physics/aero_domain.py::parasite_drag_coefficient` |
| 揚力傾斜 `C_Lα` | **非依存** | `helmbold_cl_alpha(AR, λ)` | `physics/aero_kernel.py` |
| 失速後揚力 | **非依存** | `post_stall_lift_coefficient` | `physics/aero_kernel.py` |
| 誘導抗力 `C_L²/(π e AR)` | **非依存** | — | `physics/aero_domain.py` |
| 翼根曲げ応力 | **非依存** | 幾何と `nz` のみ | `physics/stress_domain.py` |

`re_cd0_exponent` の既定は **0**（`AircraftConfig`）。既定機体では `C_D0` は Re に依存しない。
**指数が 0 でない** ときのみ Re 依存が現れる。

### 4.2 範囲外 (Re ≪ 1e3 / Re ≫ 1e5) の扱い

- **クランプしない**。`C_D0(Re)` は範囲外でも式に従い **外挿** される（値を丸めない）。
- **警告ではなく**: テレメトリの `model_valid` フラグが外挿的事实を **機械可読に** 伝える。
  `physics/flight_sim.py`:
  - `MODEL_MACH_LIMIT = 0.3`
  - `MODEL_REYNOLDS_MIN = 1.0e3`
  - `MODEL_REYNOLDS_MAX = 1.0e5`
  - `model_valid = (mach <= MODEL_MACH_LIMIT) and (MODEL_REYNOLDS_MIN <= reynolds <= MODEL_REYNOLDS_MAX)`
- テレメトリは `model_valid` を **必ず** 出力する（`physics/flight_sim.py` の応答 `model_valid`）。
  UI は範囲外で **区別表示** する。
- 検証: `tests/test_aero.py`（`C_D0` の Re 依存形と単調性）、
  `tests/test_flight.py`（`model_valid` が範囲内で `True` / 範囲外で `False`）。

### 4.3 遷移の連続性

`C_D0(Re)` はべき乗則で **連続**（`Re` の不連続点を持たない）。Re 参照値 `1e5` は相関の基準点であり、
特異点ではない（`1e5/1e5 = 1` で `C_D0 = cd0`）。

---

## 5. Polhamus 前縁渦揚力 (1966, 1971) (Issue #52)

| 項目 | 内容 |
|---|---|
| 式 | `C_L = K_p sin α cos²α + K_v sin²α cos α` |
| 出典 | Polhamus, E. C. (1966), NASA TN D-3767 |
| 正本 | `physics/aero_kernel.py::polhamus_lift_coefficient` |
| 既定 | `polhamus_kp = 0.0`, `polhamus_kv = 0.0` (`physics/config_schema.py`) |

### 5.1 既定値 0.0 の妥当性

- `K_p = 0` のとき、Polhamus の ** Attached 項** は消え、機体自身の **3 次元 Helmbold 傾斜** が
  そのまま使われる。低アスペクト比の紙機体では **Helmbold の 3 次元傾斜のほうが物理的に正しい**
  （Polhamus 式は本来 **2 次元**（`K_p` は 2 次元揚力傾斜）で、三角翼の 3 次元効果を陽に含む）。
- `K_v = 0` のとき、**前縁渦の吸い出し項**も消える。紙機体には **Sharp-Edge Delta** の前縁渦を
  成立させる锐い前縁・三角平面形が存在しない（`no-finish.md` §A-1 のpanel 法との線形も Issues #47 の
  3D CFD フェーズで扱うべき事項）。
- したがって **既定 0.0 は「物理的に妥当」** であり、数値を 0 以外へ **持ち込む** 才是例外である。
  非 0 を使う場合は **Polhamus 式の適用対象（鋭い前縁の三角翼）であることを確認** したうえで、
  `K_p` / `K_v` を API から設定し、**応答に `polhamus_active` 相当の情報** が出ることを確認する。

### 5.2 適用限界

- Polhamus 式は **三角翼・超音速域** で導出されている。極超音速（`Ma > 0.3`）は本シミュレーターの
  妥当性範囲外（`MODEL_MACH_LIMIT = 0.3`、§4.2）。
- `K_p` は 2 次元量であり、**3 次元機体にそのまま適用** すると過大評価になる。`K_p > 0` を設定する
  場合は「2 次元傾斜」「3 次元傾斜」の使い分けを記録すること。

---

## 6. 構造応力 (Issue #51 の副次項: 縮約と場の整合)

| 項目 | 内容 |
|---|---|
| 正本（場） | `physics/stress_domain.py::stress_field` — 6DoF 正規経路が報告するピーク |
| 正本（縮約） | `physics/stress_domain.py::peak_root_stress_pa` — GPU バッチ経路が使う **スカラー** |
| 整合 | 縮約は **場の厳密な縮約** でなければならない。上反角の射影 `1/√(1+tan²Λ)`（`dihedral_load_multiplier`）も場と縮約の両方に適用する |

上反角の射影 aplic忘记了場合、縮約は場の **1/cos(Λ)** 倍（既定 5° で 3.8e-3、45° で 1.4e-1）になり、
GPU 経路と 6DoF 経路のピーク応力が食い違う。検証:

```
python tools/check_stress_peak_parity.py   # 最大相対差 0.000e+00
python -m pytest tests/test_gpu_kernel_parity.py -q
```

---

## 7. 検証コマンド一覧

```
python -m pytest tests/test_aero.py tests/test_flight.py -q
python -m pytest tests/test_gpu_kernel_parity.py tests/test_stress.py -q
python -m pytest tests/test_physics_model_registry.py -q
python tools/check_stress_peak_parity.py
python tools/check_gpu_parity.py
```

---

## 8. 未実装・据え置きの再オープン条件

| 項目 | 状態 | 再オープン条件 |
|---|---|---|
| Viterna-Corrigan 失速後外挿 | **未実装**（§3.2 の根拠） | 紙機体相当の翼型データ（`A_1, A_2, B_1, B_2` の算出に必要な `c_Lmax`・`c_dmax` と形状）が入手できること。入手まで **係数を推定しない** |
| `KAPPA_SIGMA` の材料物理 | **据え置き**（`docs/SPECIFICATION.md` §5.2 G-17） | 実紙・実折面の引裂強度・残留断面形状の実測データ |
| 後縁折れの空力係数 | **据え置き**（同上） | 紙航空機の後縁抗力増加・後縁渦の揚力寄与に関する実測論文 |
| Polhamus の非 0 適用 | **既定 0.0**（§5.1） | 鋭い前縁の三角翼形状の紙機体の設計への適用根拠 |

---

*本書は Issue #47 の 3D CFD ロードマップ（`docs/CFD_ROADMAP.md`）とは別物である。
CFD ロードマップは **3 次元 Navier–Stokes ソルバ** の導入計画であり、本書が扱う
**6DoF パネル法 + 経験モデル** の正本とは層が異なる。*
