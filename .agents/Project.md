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
- [x] **v3（milcy）アルゴリズム強化 & 接地寸法連動**: MCTS折り紙探索パラメータ（ノーズ折り回数・テーパー・キールロック）を幾何折線（Creases）へ直接連動、3D MAP-Elitesタイブレーク導入、Nelder-Meadクランプ強化。接地ステーション高度計算に実機キール深さを動的連動。

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 6 [Finalize & Terminate]
- **Verification Status**: Munk連成起因のピッチ発散解消（中忠実度滑空比率1.91x達成・Issue #211/#209）、paper registry jsonl git追跡（15件テスト全通）、xdist opt-inによるCIクラッシュ防止（Issue #213）、FMAトレランス是正、全テスト・スモーク14/14完全PASS。
- **Branch & Safety**: `genetic-project` へ統合・プッシュ完了（コミット: `8e819c9`）。
- **Next Action**: ユーザーへの完了報告。

