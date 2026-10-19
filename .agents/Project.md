# Project Specification & Loop State

## 1. Project Goal (プロジェクトの目的・最終到達点)
- `AGENTS.md` (3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine v4) の仕様全項目（流体力学・大気・応力・メッシュ・6DoF・最適化・ETA・UI/GPU可視化・REST API・セキュリティ・文献根拠・テスト）を完全実装し、ユニットテスト/smoke_testの完全通過、GitHub上のPR/Issueの確認・残タスク解決・CIグリーン化・masterへのマージを完了させる。

## 2. Current Milestone (現在のマイルストーン)
- [x] 現状のコードベース・テスト状態・Gitブランチ・GitHub Issue/PRの把握
- [x] 未実装・未通過項目の特定とタスク分解
- [x] Issue #28 (GPUバッチduration_s反映), #29 (CFDと飛行力学の区別), #30 (座標系符号変換), #32 (学術根拠レジストリ), #34 (トレースバック非漏洩), #35 (CPU/GPU結果一致), #37 (SSRF対策) の解決と検証
- [x] Issue #31 (固定刻み数値収束性), #33 (3D風場相関・発散有界性), #36 (パネル法解像度整合性) の解決と監査テスト通過
- [x] クロスプラットフォーム CI 修正 (`defaults: run: shell: bash` による Windows/Linux 統一)
- [x] `AGENTS.md` の v4 仕様完全更新（二層プロトコル、学術文献レジストリ、セキュリティ規約、全エンドポイント、最新ディレクトリ、監査課題履歴の網羅）
- [x] 全監査テスト (10/10) および既存テストスイートのローカル完全パス

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 5 [Distill & Loop]
- **Active Sub-task**: `AGENTS.md` 更新コミット、master へのマージ、および Discord 完了通知
- **Current Hypothesis**: 更新された `AGENTS.md` および `.agents/` ファイル群をコミット・マージ・プッシュすることで、仕様書と実コード・テストの完全整合が達成される。
- **Verification Criteria**: `git status` がクリーンで、最新コミットが `master` に反映され、Discord 完了通知が正常送信されること。
- **Next Action**: 変更をコミットし、`master` にマージして push を行い、Discord 完了通知を送信する。
