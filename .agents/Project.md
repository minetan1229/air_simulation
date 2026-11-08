# Project Specification & Loop State

## 1. Project Goal (プロジェクトの目的・最終到達点)
- docs/SPECIFICATION.md に規定された「3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine」の全仕様を維持・稼働させ、多忠実度（Multi-Fidelity）現実的パイプラインを統合・完結させる。

## 2. Current Milestone (現在のマイルストーン)
- [x] **中忠実度現実的構成（コスト2.00x）**: 5ステーション・ストリップモデル空力積分、低Re LSB動的失速ヒステリシス、3DoF薄板付加質量回転項、非線形動的減衰を統合。実測実行時間比率 2.00x（要求2〜5倍内達成）。
- [x] **エネルギー非増加性 & 厳密検証**: 無風無推力滑空時の力学的エネルギー単調非増加（dE/dt <= 0）を検証。
- [x] **多忠実度パレート再評価パイプライン**: 最適化探索中は中忠実度（2.00x）で母集団を評価し、最終パレート候補のみ定常RANS / 実験校正サロゲート（RK4 fine dt=0.005s）で再評価。
- [x] **物理法則欠陥台帳（全14課題）の完全解決**: P-01(Kirchhoff付加質量/Munk), P-02(キール二重減衰排除), P-03(Ixzテンソルパリティ), P-05(失速後抗力ブレンド), P-06(横すべり側力連続性), P-07(ARゼロ除算防護), P-08(翼端渦散逸上限), P-14(接地クーロンdt), P-26(サーマル質量保存), P-27(紙直交異方性ヤング率), P-51(デルタ翼ロール慣性b^2/24), P-53/P-55(サロゲート地面効果/応力Pa), P-61/P-62(GA2静安定/座屈), P-63(エラスティカ大たわみ)。
- [x] **厳密反証テストスイート**: 13個の専用テストファイル（25件）が全PASS、smoke_test 14/14ステージ完全PASS。

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 6 [Finalize & Terminate]
- **Verification Status**: Issue #72 の CFD 現状記述をコード・テストと照合して同期。対象テスト 9 passed / `git diff --check` passed。作業ツリーには本件以外の既存変更あり。次は `priority: medium` の未解決 Issue を一件ずつ扱う。
- **Branch & Safety**: genetic-project 上で隔離維持。master/main への操作厳禁（AGENTS.md §2 Branch Isolation）。単一コミッター原則。
- **Next Action**: 次の未解決 `priority: medium` Issue を選び、仮説・合否基準を置いて調査する。

