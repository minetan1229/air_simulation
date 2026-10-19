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
- [x] 3D物理アノマリー自律修正サイクル: 水平境界(100km)、地中埋没防止、高速連続衝突判定(CCD)、翼端接地エネルギー保存則制約の実装・全720テストPASS
- [x] **3D物理 論文一致度監査（2026-10-19）**: `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` 作成。高20/中34/低30/非論文系8 の指摘と修正案・ロードマップ（P0〜P3）を提示。**コード未変更**
- [x] `agents.md` を「常駐コア（規約・ガードレール）+ 参照索引」に再構成し、知識・技能・現在地の正本は `.agents/` 配下へ維持（重複長文の常駐回避）
- [x] **プロシージャル折り紙3Dジオメトリ（2026-10-20 新マイルストーン）**: `physics/origami_domain.py` に展開図コンパイラ→三次元ヒンジ列→多層ソルidd生成を実装。Classic Dart / Nakamura Lock / Sky King の3機体、折り工程 α、Z-Fighting 構造的排除、紙の厚み・折り目ロール・PBR紙材質を実装し、UI に折り工程スライダーと機体タイプ切替を統合。
- [x] **genetic-project フェーズ立上り（2026-10-20）**: `genetic-project` ブランチを新規作成し、GA（遺伝的アルゴリズム）実装前に物理的健全性（P0〜P3）を確立する専用フェーズへ移行。`master`/`main` とは完全に分離（コミット・マージ・チェリーピック・PR 禁止）。`tools/branch_guard.py` による保護ブランチ実行停止を `conftest.py` / `app.py` / `smoke_test.py` に実装。
- [x] **無作為折り方遺伝子エンジン（2026-10-20 新マイルストーン）**: `physics/origami_genome.py` に折り操作のシーケンスを遺伝子として表現する Procedural Origami Engine を実装。`FoldStep`（折り線・山/谷別角度 -180..+180°・対称性・層順序・対象パネル）、世代0の完全無作為生成、保持可能判定（持ち手/キール幾何フィルタ＋重心位置＋自己貫通検査）、折り手順レシピ（手順表・Markdown・展開図 SVG）と段階メッシュ生成を実装し、API 6 系統と UI の折り手順スライダを統合。

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 4 [Verify / Stage] — 無作為折り方遺伝子エンジン検証完了・ステージング
- **Status Summary**: `physics/origami_genome.py` および `tests/test_origami_genome.py`（22/22 テスト完全PASS）のコンパイル・監査・単体テスト完了。折り紙エンドポイント（`/origami/genome`, `/genome/recipe`, `/genome/steps`）検証完了。
- **Branch & Safety**: `genetic-project` ブランチ保護確認（`tools/branch_guard.py` PASS）。未追跡ファイルおよびループ状態のステージング完了。
- **Verification Criteria**: `test_origami_genome.py` (22 PASS), `branch_guard.py` (0 error), `compileall` (0 syntax error).
- **Next Action**: `genetic-project` ブランチでのコミット作成および次期GA最適化ループ（適合度評価・交叉・変異）への移行。
- **既知の残課題（既存）**: `physics/mesh_domain.py` F822（既存）、`PHYSICS_PAPER_CONFORMANCE_AUDIT.md` M級項目（次フェーズ）。
