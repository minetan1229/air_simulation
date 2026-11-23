# Project Specification & Loop State

## 1. Project Goal (プロジェクトの目的・最終到達点)
- docs/SPECIFICATION.md に規定された「3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine」の全仕様を維持・稼働させ、多忠実度（Multi-Fidelity）現実的パイプラインを統合・完結させる。

## 2. Current Milestone (現在のマイルストーン)
- [x] **中忠実度現実的構成（コスト2.00x）**: 5ステーション・ストリップモデル空力積分、低Re LSB動的失速ヒステリシス、3DoF薄板付加質量回転項、非線形動的減衰を統合。実測実行時間比率 2.00x（要求2〜5倍内達成）。
- [x] **エネルギー非増加性 & 厳密検証**: 無風無推力滑空時の力学的エネルギー単調非増加（dE/dt <= 0）を検証。
- [x] **多忠実度パレート再評価パイプライン**: 最適化探索中は中忠実度（2.00x）で母集団を評価し、最終パレート候補のみ定常RANS / 実験校正サロゲート（RK4 fine dt=0.005s）で再評価。
- [x] **物理法則欠陥台帳（全14課題）の完全解決**: P-01(Kirchhoff付加質量/Munk), P-02(キール二重減衰排除), P-03(Ixzテンソルパリティ), P-05(失速後抗力ブレンド), P-06(横すべり側力連続性), P-07(ARゼロ除算防護), P-08(翼端渦散逸上限), P-14(接地クーロンdt), P-26(サーマル質量保存), P-27(紙直交異方性ヤング率), P-51(デルタ翼ロール慣性b^2/24), P-53/P-55(サロゲート地面効果/応力Pa), P-61/P-62(GA2静安定/座屈), P-63(エラスティカ大たわみ)。
- [x] **厳密反証テストスイート**: 13個の専用テストファイル（25件）が全PASS、smoke_test 14/14ステージ完全PASS。
- [x] **milcy アルゴリズム（旧v3探索パイプライン）の正式体系化**: 20工程パラメトリック折り紙MCTS + 3D MAP-Elites + Nelder-Meadトリム + Stage 4ロバストネス検証から成る形態創発パイプラインを「milcyアルゴリズム」として定義。互換性エイリアスとCLI/設定（`config/milcy_map_elites.yaml`）を整備。
- [x] **システム責務分離 & 5層クリーンアーキテクチャ**: God Package 化していた `physics/` を (1) `core/`, (2) `physics/`, (3) `geometry/`, (4) `optimization/`, (5) `infrastructure/` に分離。`v3/` の完全統合、`search/` (2,000件文献) の `docs/research/` 移設、全互換スタブ整備、スモークテスト 14/14 完全合格。
- [x] **フロントエンド解体 & 物理コア細分化**: `index.html` を 7 つの Jinja2 コンポーネントに部品化、`app.js` から `app_modes.js`, `app_events.js`, `app_ensemble.js` を抽出。`flight_sim.py` から `throw_solver.py`, `ground_contact.py` を分離。全54単体テスト・E2Eスモーク14/14完全通過。
- [x] **GA2 最適化健全化 & パレート境界極値解保護**: `default_objective` の墜落機25%底上げ（報酬ハッキング要因）を排除し物理的ペナルティ関数へ是正。`archive.py` の `_trim` および `runner.py` の選択においてパレート境界極値解（`math.inf`）の優先保護ガードを実装。全29テストPASS。
- [x] **フロントエンド ライフサイクル健全化**: `static/js/viewport.js` にテクスチャ再帰解放・WebGL `forceContextLoss` を含む完全な `dispose()` を実装。`static/js/app.js` に `_booted` / `_eventsBound` による二重バインド抑止を導入。全8フロントエンドテストPASS。
- [x] **milcy パイプライン抜け穴・脆弱性の網羅的解決**: (1) 3D MAP-Elites 実機A4紙飛行機領域へのグリッド範囲修正（wing loading 0.10-0.45, stability 0.10-0.90, AR 0.30-2.20）および疎領域優先好奇心選択 (2) シミュレーション依存の循環参照排除（幾何学的静安定余裕の独立計算） (3) 鉛直パラシュート失速・微小前進距離への有効L/D・前進速度ペナルティ付与 (4) Tier 0 自己交差・貫通判定の完全連動（折線隣接face IDペア照合修正） (5) Dryden乱流評価の3シードアンサンブル化（平均滞空時間・生存率） (6) CI環境におけるmatplotlib遅延インポート防護。全単体テスト・E2Eスモーク14/14完全PASS。

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 6 [Finalize & Terminate]
- **Verification Status**: milcy 抜け穴監査の全修正が完了。`tests/test_milcy_loopholes_audit.py` (6/6), `tests/v3` (23/23), `tests/test_milcy_viewer_refactor.py` (1/1), `scripts/smoke_test.py` (14/14 stages) 全PASS。
- **Branch & Safety**: `master` へ統合・プッシュ完了（`75a1f24`）。
- **Next Action**: ユーザーへの完了報告。
