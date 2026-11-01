# Project Specification & Loop State

## 1. Project Goal (プロジェクトの目的・最終到達点)
- `docs/SPECIFICATION.md` に規定された「3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine」の全仕様（AeroSandbox準拠6DoF、Helmbold/Polhamus/Viterna空力モデル、受動空力弾性、3D風場、CuPy GPU並列・CPU完全一致フォールバック、REST API、Google Clean UI、セキュリティ、学術文献レジストリ）を維持・稼働させ、CIおよびテストを完全通過させる。

## 2. Current Milestone (現在のマイルストーン)
- [x] 現状のコードベース・テスト状態・Gitブランチ・GitHub Issue/PRの把握、クロスプラットフォーム CI 修正 (`defaults: run: shell: bash`)
- [x] ルート `AGENTS.md` の仕様を `docs/SPECIFICATION.md` へ移管・整理し、Agent Core Protocol OS に再構築して `.agents/` 自律ループと完全同期
- [x] 全監査テスト (10/10) および smoke_test (14/14 stages) の完全パスと master への反映。Issue #28–#45 の全監査課題解決・クローズ
- [x] 3D物理アノマリー自律修正サイクル: 水平境界(100km)、地中埋没防止、高速連続衝突判定(CCD)、翼端接地エネルギー保存則制約の実装・全720テストPASS
- [x] 3D物理 論文一致度監査（2026-10-19）: `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` 作成。P0〜P3 を完了（`9538f8b`）
- [x] プロシージャル折り紙3Dジオメトリ & 無作為折り方遺伝子エンジン: `origami_domain.py` / `origami_genome.py` / UI 展開図・手順スライダ統合
- [x] 風高度規約 GPU バッチ parity 修正（`z_up = -z_ned`）/ 失敗評価 status 無変換転送 / 自律研究 GA エンジン & 研究コンソール実装
- [x] 文字化け（mojibake）修復完了 / コンソール操作一本化（ボタン重複撤去・死んだボタン解消）/ GA v2 超大規模GA・マルチGPU DNS/FSI 実装
- [x] Discord×Agent 統合（429指数バックオフ＋continue、Kilo Code無料モデルへの自動フォールバック、クォータ監視、サブエージェント上限4）
- [x] 全体調査・全 Issue 解決・UI 改善: P0 応力場とスカラー縮約乖離解消（`dihedral_load_multiplier`）、telemetry.js ES module 重複宣言修正、UI 1280px 溢れ解消、WCAG AA 適合
- [x] **残存物理問題の修正 & 実機データ校正（2026-11-02）**: ①失速後モデルに残留揚力パラメータ（`residual_cl`）を追加し CL/CD 連続性確立 ②GPU バッチ経路（`gpu_dynamics.py`）に 3x3 慣性テンソル（Euler 方程式 $\dot{\omega} = I^{-1}(M - \omega \times I\omega)$）および RK4 各段風場サンプリングを導入し参照経路パリティ向上 ③準静的空力弾性に 1 次遅れ（`first_order_lag`）を追加 ④薄板付加質量（`compute_added_mass`）オプション & 質量比警告（`compute_mass_ratio < 5.0`）⑤Polhamus $K_v$ 平面形からの自動推定 ⑥単一実装原則で `aero_kernel.py` に集約し既存全テスト非破壊で `model_valid` を拡張 ⑦実機データ校正ツール `tools/calibrate_flight_data.py` 配備（Nelder-Mead 軌跡 RMSE 最小化・自己診断テスト通過・新規テスト `test_physics_enhancements.py` 8/8 PASS）。

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 5 [Distill & Loop] → Phase 6 [Finalize] — 残存物理問題7項目の修正および実機データ校正ツールの整備完了。
- **Status Summary**: `physics/aero_kernel.py`（残留揚力・1次遅れ・付加質量・質量比・Polhamus Kv推定・model_valid拡張）、`physics/gpu_dynamics.py`（3x3 慣性テンソル非対角項・RK4各段風場サンプリング）、`physics/flight_sim.py`（空力弾性1次遅れ・付加質量/質量比統合）、`tools/calibrate_flight_data.py`（実機データ校正CLI）、`tests/test_physics_enhancements.py`（新規8テスト）。単一実装原則を厳守し、既存テスト 126 件の破壊ゼロ。
- **Branch & Safety**: 変更は `genetic-project` 上。`master`/`main` への操作は厳禁（`AGENTS.md` §2 Branch Isolation）。単一コミッター原則（親コミット）。
- **Verification Criteria**: `ruff check .` = **All checks passed!** / `mypy` = **Success: no issues found in 62 source files** / `compileall` = **exit 0** / `pytest tests/test_aero.py tests/test_physics_enhancements.py tests/test_physics_model_registry.py -q` = **153 passed** / `smoke_test.py` = **14/14 stages passed** / `Knowledge.md` = **80 行以内** / `Project.md` = **40 行以内**。
- **Next Action**: 親エージェントによる一括コミット・プッシュ・CI完了確認。
