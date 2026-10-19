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

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 3-4 [Execute / Verify] — genetic-project 物理監査是正
- **Active Sub-task**: `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` の P0〜P3（モデルタグ/応力許容/ヘルンボルド式/風符号 → 失速後空力 → 接地インパルス → GPU 6DoF 一致 → 最適化器の 6DoF 再検証）を実装し、`tests/test_paper_conformance_p0_p1.py`（P0/P1）と `tests/test_ga_physics_readiness.py`（P2/P3, 21件）で固定化。_branch guard_ は `tools/branch_guard.py` に実装し、テストスイート・`app.py`・`smoke_test.py` に組み込み済み。
- **Current Hypothesis**: 「6DoF の破綻要因を全て塞げば、GA が探索できる物理的評価基盤になる」こと。合否基準は (a) 既存全テスト PASS、(b) 新規監査テスト P0〜P3 全 PASS、(c) smoke_test 13/13 PASS、(d) 保護ブランチへの書き込みゼロ。
- **Verification Criteria**: 全 pytest PASS（`python -m pytest tests -q`）、`python smoke_test.py` 13/13 stages、`ruff check .` / `mypy` 0 件、`git branch --show-current` が `genetic-project`。
- **Next Action**: 全テスト PASS 確認 → `.agents/Knowledge.md` と Loop State 更新 → `genetic-project` ブランチへコミット（`master` 宛の操作は禁止）。
- **既知の残課題（今回の変更外・既存）**: `ruff` の `physics/mesh_domain.py` F822 は既存事象。`PHYSICS_PAPER_CONFORMANCE_AUDIT.md` の M 級項目（`static_margin` 接続 M30 / `fold_trailing_deg` 接続 H19 / 実材料の断面モデル H11-M26）は P3 の残タスクとして次フェーズで着手する。
