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
- [ ] 変更内容のローカル検証、master へのマージ、およびプッシュ

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 4 [Verify]
- **Active Sub-task**: ルート `AGENTS.md` 刷新と `.agents/` 整合化の検証
- **Current Hypothesis**: ルート `AGENTS.md` の軽量化と `docs/SPECIFICATION.md` への仕様分離により、コンテキスト肥大化が解消され、`.agents/` の 5 フェーズ自律ループOSが正常に動作する。
- **Verification Criteria**: pytest 全件通過、smoke_test 全 13 ステージ通過、Git作業ツリーが適切に管理されていること。
- **Next Action**: ローカル検証（pytest, smoke_test）を実行し、問題なければコミット・masterマージ・pushを行う。
