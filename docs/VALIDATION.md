# 検証と妥当性確認 (Verification & Validation)

**Verification（検証）の定義**: 数学的・数値的に正しく実装しているか（方程式の解法が正しいか）。
**Validation（妥当性確認）の定義**: 現実世界（風洞試験値・実飛行データ）に対して物理的に妥当か。

本プロジェクトは、学術文献・閉形式解・独立な数値解を**正本・参照解**とし、以下の 3 層で V&V を実施しています。

---

## 1. 検証 (Verification) — 数値的正しさ

### 1.1 閉形式との照合（解析解がある領域）

| 物理量 | 参照解 | 許容誤差 | テスト |
|---|---|---|---|
| ISA 大気 $T(h), p(h), \rho$ | US Standard Atmosphere 1976 | 相対誤差 $10^{-6}$ | `tests/test_atmosphere_wind_conformance.py` / `test_atmo_wind.py` |
| Helmbold 揚力傾斜 $C_{L\alpha}$ | 閉形式 | 相対誤差 $10^{-12}$ | `tests/test_aero.py::test_helmbold_matches_closed_form` |
| 誘導抗力 $C_{Di} = C_L^2/(\pi e AR)$ | 閉形式 | 相対誤差 $10^{-3}$ | `tests/test_aero.py` |
| Sutherland 動粘性 $\mu(T)$ | Sutherland (1893) 式 | 相対誤差 $10^{-3}$ | `tests/test_atmosphere_wind_conformance.py` |
| メッシュ幾何特性（面積・重心・慣性） | 厳密積分 | 相対誤差 $10^{-6}$ | `tests/test_mesh.py` |
| RK4 収束性（時間刻み依存性） | 刻み半減の系統的収束 | 収束 1 次（Issue #31 検証済み） | `tests/test_issue12_timestep_convergence.py` |

### 1.2 経路間パリティ（GPU / CPU / スカラー / バッチ）

- スカラー経路（`aero_domain.py`）とバッチ経路（`gpu_dynamics.py`）は **同一の canonical 関数**（`aero_kernel.py`）を呼ぶため、数式上の不一致は発生しません（単一実装原則）。
- パリティ検査スクリプト:
  - `tools/check_aero_kernel_parity.py` — `low_re_enabled=False` 旧挙動ビット同一（差異 0.0）
  - `tools/check_stress_peak_parity.py` — 応力ピークの縮約の厳密性
  - `tests/test_gpu_kernel_parity.py` — GPU / NumPy カーネル同値

### 1.3 保存性・不変性

- 四元数ノルム保存: RK4 の 1 ステップあたりのドリフトを $5 \times 10^{-3}$ 以内で正規化
- エネルギー保存（重力のみの条件）: `tests/test_energy_conservation_ground.py`
- 境界接地・CCD: `tests/test_tunneling_ccd.py` / `test_ground_penetration.py`

---

## 2. 妥当性確認 (Validation) — 物理的妥当さ

### 2.1 低レイノルズ数（LSB）モデルの妥当性

- **適用範囲**: $Re \in [10^3, 10^5]$、$Ma \le 0.3$、薄板（厚翼の空力特性は対象外）
- **補正式**: Pelletier & Mueller 2000、Sunada et al. 2002、Westphal et al. 2003、O'Meara & Mueller 1987 を単一実装で再現
  - $f_{C_{L\alpha}} = 1 - 0.04x,\quad f_{\alpha_{stall}} = 1 - 0.10x,\quad f_{C_{Lmax}} = 1 - 0.12x,\quad f_{CD0} = 1 + 0.03x$
  - 座標 $x = \log_{10}(10^5 / Re)$、$x=0$ at $Re \ge 10^5$、$x=2$ at $Re \le 10^3$
- **検証**: `tests/test_low_re_aerodynamics.py` / `test_issue68_reynolds_audit.py`（26 passed）、`test_lowre_envelope.py`、`test_low_re_correction.py`

### 2.2 残りの物理乖離（既知の制限、第四期）

`PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §0.0 および `no-finish.md` が正本です:

- **P0–P3 は完了**（paper-conformance 監査台帳）
- **F-8 / A-5**: パネル法（`mesh_domain.py`）と Helmbold 式は 2.2 倍乖離 — **意図的維持**（2 つの空力モデルを 1 式に統合しない。パネル法は実飛行・GA 評価経路に接続されていない）
- **KAPPA_SIGMA / fold 係数**: 値の据え置き（出典未取得）。再オープン条件は `docs/PHYSICS_MODELS.md` §5.2

### 2.3 GA 最適化の検証条件

- 目的関数・探索ロジックの既定値は `physics/ga_config.py` に集約（ハードコード禁止）
- パレート前線への**失敗した評価の混入**（`timeout` / `crashed` へのリラベル）は報酬ハッキング経路として禁止
- 検証テスト: `tests/test_ga_physics_readiness.py` / `test_multifidelity_pipeline.py` / `test_model_fidelity_metadata_p1.py`

---

## 3. ベンチマーク（比較グラフの作り方）

### 3.1 抗力極線（Drag Polar）

- エンドポイント: `POST /api/v1/aerodynamics/polar`
- 例: `curl -X POST http://127.0.0.1:5000/api/v1/aerodynamics/polar -H 'Content-Type: application/json' -d '{"aircraft":{"wing_area_m2":0.015,"wingspan_m":0.2},"alpha_min_deg":-8,"alpha_max_deg":20,"points":57,"reynolds":10000}'`
- 理論解（平板理論・Helmbold 閉形式）との比較グラフは `docs/images/` に追加し、本ファイルに引用してください。

### 3.2 風場の等方性・統計

- テスト: `tests/test_turbulence_stats.py` / `test_wind_turbulence_statistics.py` / `test_wind_turbulence_p1.py`
- 実測風場（Dryden / MIL-F-8785C スペクトル）との比較は Issue #81 (`test_issue81_wind_quantify.py`) で定量化されています。

### 3.3 ベンチマーク実行レポート

```powershell
python -m pytest tests/test_paper_conformance_p0_p1.py -q
python -m pytest tests/test_issue32_aero_reference_data.py -q
python tools/check_aero_kernel_parity.py
```

---

## 4. V&V の完了条件（Issue クローズ基準）

1. Issue 本文に記載された「完了条件」「調査・対応」の全項目が物理的・工学的に満たされていること。
2. **独立参照解**（閉形式・風洞試験値）または上記テストとの比較が成立していること。
3. テスト追加のみ・ドキュメント明記のみでの完了としない（追加テストは現状挙動のアサートに留まらないこと）。

---

## 5. 関連ドキュメント

- [docs/SPECIFICATION.md](./SPECIFICATION.md) 技術仕様の正本
- [docs/PHYSICS_MODELS.md](./PHYSICS_MODELS.md) 係数・式の正本
- [PHYSICS_PAPER_CONFORMANCE_AUDIT.md](../PHYSICS_PAPER_CONFORMANCE_AUDIT.md) 論文一致度監査の記録
- [docs/development.md](./development.md) テスト実行手順
