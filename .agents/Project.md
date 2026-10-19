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
- [x] ループエンジニアリング監査で特定された全14項目（#2, #5〜#17）のプロトコル策定および反映完了
- [x] 現行フェーズ・マイルストーンの全完了（Finalized）
- [x] GitHub上の全オープンIssue (#28〜#45) の検証完了およびクローズ (`state=closed`, `state_reason=completed`)
- [x] 3D物理アノマリー自律修正サイクル: 水平境界(100km)、地中生成/めり込み防止、高速連続衝突判定(CCD)、翼端接地エネルギー保存則制約の実装・全720テストPASS
- [x] Kilo Code 自己強化: MCPサーバー5種(context7 / sequential-thinking / fetch / memory / playwright)の導入・接続検証・`agents.md` §7/§8 への反映

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 6 [Finalize & Terminate]
- **Active Sub-task**: Kilo Code 環境自己強化（MCPサーバー導入・接続検証・`agents.md` への運用規約反映）完了
- **Current Hypothesis**: ローカル実行可能な公式MCPサーバー（外部APIキー不要）を `local stdio` で登録し、専用ランチャーと検証スクリプトを同梱すれば、自己探査・外部API仕様取得・多段推論・Web調査・知識永続化・UI実ブラウザ操作の各タスクをIDE内で完結でき、エージェントの事実性と検証性が向上する。
- **Verification Criteria**: `kilo mcp list` で5サーバー全て `connected`、`python tools/verify_mcp_servers.py` が exit 0（全サーバーで initialize→tools/list→実ツールコール成功）、Ruff/Mypy 指摘 0 件、pytest 全パス、`agents.md` に §7/§8 存在。
- **Next Action**: 自律ループ正常終了（HALT）。
