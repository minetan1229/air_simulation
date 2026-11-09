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
- **Current Phase**: Phase 5 [Distill & Loop]
- **Verification Status**: mission/pytest-full-fix-all(9体:Wave1=4,Wave2=4,残り=リトライ+I)でpytest全修正完了。A:`aero_domain.py` fixed-point発散を0.5緩和+40回+effective_stall_alpha+カーネル義CLで5.144deg収束へ修正。B/C/D全passing(GA289+/API177/CFD・issue116)。E:time_budgetはflake(単独5.1-6.7s pass)、lsb_invarianceは恒常pass。F:`test_investigate.py`全6件個別passing(13.6分)、`test_ga_engine.py`51passed/332.5s。G:49ファイル793passed/1failed(`test_scripts_paths`は`.venv`存在の環境依存のみ)。H:compileall/ruff/mypy全PASS、smoke14/14PASS(48.51s)。I:`flight_sim.py:989` wind規約違反(height=-z_ned-ground→-z_ned)修正。並走3本フルpytestは34%でデッドロック気味→全停止し1ファイルずつ逐次実行へ切替。**2026-11-09 P0**: 接地安全ネットの一律`v,w`スケール廃止→過剰エネルギーから法線インパルス再計算へ置換(`flight_sim.py:_apply_ground_contact`)。付加質量連成`compute_apparent_forces`は`P_app=2*m_z*w*(u*q-v*p)`符号不定(正の機械仕事)を確認済みのため無効化のまま受動`m_eff/J_eff`。検証: `test_energy_conservation_ground` 3 passed、`test_physical_laws_audit`/`test_physics_audit`/`test_added_mass_parity`/`test_paper_conformance_p0_p1` 102 passed、ground系 33 passed、`ruff`/`mypy`/`smoke 14/14` PASS。詳細はKnowledge.md[Ground Contact Energy Guard]参照。
- **Branch & Safety**: genetic-project 上で隔離維持。master/main への操作厳禁（AGENTS.md §2 Branch Isolation）。単一コミッター原則。
- **Next Action**: 次の未解決 `priority: medium` Issue を選び、仮説・合否基準を置いて調査する。

