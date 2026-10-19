# Project Specification & Loop State

## 1. Project Goal (プロジェクトの目的・最終到達点)
- `AGENTS.md` (3D Paper Airplane High-Performance CFD Simulator v4) の仕様全項目（流体力学・大気・応力・メッシュ・6DoF・最適化・ETA・UI/GPU可視化・REST API・テスト）を完全実装し、ユニットテスト/smoke_testの完全通過、GitHub上のPR/Issueの確認・残タスク解決・CIグリーン化・masterへのマージを完了させる。

## 2. Current Milestone (現在のマイルストーン)
- [x] 現状のコードベース・テスト状態・Gitブランチ・GitHub Issue/PRの把握
- [x] 未実装・未通過項目の特定とタスク分解
- [x] Issue #28 (GPUバッチduration_s反映), #29 (CFDと飛行力学の区別), #30 (座標系符号変換), #32 (学術根拠レジストリ), #34 (トレースバック非漏洩), #35 (CPU/GPU結果一致), #37 (SSRF対策) の解決と検証
- [ ] CI（GitHub Actions）の完了確認と残る Issue (#31, #33, #36) の対応

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 4 [Verify]
- **Active Sub-task**: 最新コミット（0327a95）の GitHub Actions CI 完了確認
- **Current Hypothesis**: CPUフォールバックのスキーマ統一およびリントエラー修正により、CI (Python Code Verification, CI Pipeline) が完全にグリーン化する。
- **Verification Criteria**: GitHub Actions の run 39198274479 が success で完了すること。
- **Next Action**: CIのステータスを確認し、成功を確認後、残る Issue (#31, #33, #36) の監査テストを完了させる。
