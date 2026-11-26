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
- [x] **milcy 折り幾何学的破綻の完全根絶 & 2.5D逐次折りカーネル刷新**: (1) Sutherland-Hodgman半平面クリッピング＋剛体鏡映＋逆アフィン行列 $M_i^{-1}$ による自動UV展開図生成（2.5D逐次折りステートマシン `kernel_25d.py`）を導入 (2) 人間工学的大工程（MacroFoldStep: 6〜10工程）と物理計算用原子ステップ（Atomic FoldStep: 10〜16ステップ）のバイレベル分離 (3) 中心線突破X字交差・全幅エレボンスライス・水増し空中キャンバー・機首横腹貫通の4大破綻を幾何構造的（Correct-by-Construction）に完全根絶 (4) `tests/v3` (23/23 PASS), 空力テスト (397 PASS), 実機型紙PDF/SVG出力 (`tools/generate_printable_a4.py`) 完全整合。
- [x] **milcy 収束監査完了（Iterations 1–4 & 厳密反証テスト44件）**: 6DoF物理・GPUバッチ(`apparent_active`マスク・`--fmad=false` 1-ULP検証・剛性dt安全制限)・2.5D折り紙コンパイラ・MAP-Elites・A4型紙/ビューアの監査指摘を全是正。44件の反証テスト（`test_milcy_audit_falsification.py` 27件 / `iter2` 8件 / `iter3` 9件）および `docs/physics_assumptions.md` / `docs/wontfix.md` を整備完了。

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 6 [Finalize & Terminate]
- **Verification Status**: MILCY収束監査（Iterations 1–4）完了。反証テスト44件（`test_milcy_audit_falsification.py` / `iter2` / `iter3`）全PASS、厳密監査/抜け穴テスト（`test_milcy_rigorous_audit.py` 10件 / `test_milcy_loopholes_audit.py` 6件）整備、`docs/physics_assumptions.md` と `docs/wontfix.md` 作成完了。
- **Branch & Safety**: 現作業ツリーにて MILCY 収束監査是正・ドキュメント整備・`.agents/` 同期完了（`Project.md <= 40行`, `Knowledge.md <= 80行`）。
- **Next Action**: 最終検証（テスト・lint・型チェック）後に変更をコミットし HALT。