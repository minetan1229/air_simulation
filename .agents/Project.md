# Project Specification & Loop State

## 1. Project Goal (プロジェクトの目的・最終到達点)
- `docs/SPECIFICATION.md` に規定された「3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine」の全仕様（AeroSandbox準拠6DoF、Helmbold/Polhamus/Viterna空力モデル、受動空力弾性、3D風場、CuPy GPU並列・CPU完全一致フォールバック、REST API、Google Clean UI、セキュリティ、学術文献レジストリ）を維持・稼働させ、CIおよびテストを完全通過させる。

## 2. Current Milestone (現在のマイルストーン)
- [x] 現状のコードベース・テスト状態・Gitブランチ・GitHub Issue/PRの把握
- [x] Issue #28〜#37 の全監査課題の解決・テスト完了
- [x] クロスプラットフォーム CI 修正 (`defaults: run: shell: bash` による Windows/Linux 統一)
- [x] ルート `AGENTS.md` の仕様を `docs/SPECIFICATION.md` へ移管・整理
- [x] ルート `AGENTS.md` をプロジェクト非依存の「Agent Core Protocol OS」に再構築し、`.agents/` 自律ループと完全同期
- [x] `.agents/Knowledge-important.md` に常駐制約・技術ガードレールを統合（50行以内）
- [x] 全監査テスト (10/10) および smoke_test (13/13 stages) の完全パスと master (`82d48ea`) への反映
- [ ] 次期開発タスク（ユーザーからの新要件）のヒアリングと着手

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 1 [Observe & Scope]
- **Active Sub-task**: 次期サブタスクのスコープ定義（ユーザーからの指示待ち）
- **Current Hypothesis**: なし
- **Verification Criteria**: なし
- **Next Action**: ユーザーから新規タスク・目標をヒアリングし、最初の検証可能なマイルストーンを設定する。
