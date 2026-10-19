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
- [x] ループエンジニアリング観点における `AGENTS.md` の監査・問題点特定および正常終了プロトコル（Phase 6）の導入
- [x] 現行フェーズ・マイルストーンの全完了（Finalized）

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 6 [Finalize & Terminate]
- **Active Sub-task**: 全フェーズ完了・正常終了処理
- **Current Hypothesis**: 全監査テスト通過およびプロトコル改修の完了により、全フェーズが健全に完了状態となる。
- **Verification Criteria**: 全テストパス、Gitクリーン状態、マイルストーン全完了。
- **Next Action**: 自律ループ正常終了（HALT）。新規要件発生時に Phase 1 へ復帰。
