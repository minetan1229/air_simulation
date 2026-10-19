# Project Specification & Loop State

## 1. Project Goal (プロジェクトの目的・最終到達点)
- `docs/SPECIFICATION.md` に規定された「3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine」の全仕様（AeroSandbox準拠6DoF、Helmbold/Polhamus/Viterna空力モデル、受動空力弾性、3D風場、CuPy GPU並列・CPU フォールバック〔返却スキーマは全バックエンドで一致〕、REST API、Google Clean UI、セキュリティ、学術文献レジストリ）を維持・稼働させ、CIおよびテストを完全通過させる。

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
- [x] Kilo Code 自己強化: MCPサーバー5種(context7 / sequential-thinking / fetch / memory / playwright)の導入・接続検証・`AGENTS.md` §7/§8 への反映
- [x] 規約ギャップ分析と是正: 全バックエンド共通ペイロード契約（`physics/payload_contract.py`）、Issue #30/#32/#35 の検証実体化、ETA の EMA 適用、エラーエンドポイントの秘匿化、SPEC §3 の REST 契約充足、`mypy` の全面適用（ignore 撤去）、CI ゲートの blocking 化、docs/SPECIFICATION.md の虚偽主張の是正

## 3. Loop State (現在のループ状態・引き継ぎメモリ)
- **Current Phase**: Phase 4 [Verify & Document]
- **Active Sub-task**: 規約ギャップ分析の是正結果の検証と正本（SPEC / Project / README）への反映
- **Current Hypothesis**: 仕様書に「検証済み」と記載されていた項目（#30 / #32 / #35 / #36）と実装乖離（ETA 非平滑化・エラー秘匿漏れ・REST パス欠落・mypy 無視）はいずれも実装側の欠落かテストの空源性であり、実装と検証の双方を直せば仕様書の記述が事実と一致して SPEC を「正解」として維持できる。
- **Verification Criteria**: `python -m compileall physics api tools app.py smoke_test.py` が exit 0、`ruff check .` が指摘 0、`mypy --config-file mypy.ini physics api app.py smoke_test.py` が 0 errors（ignore 撤去済み）、`python -m pytest tests -q` が全パス（799 テスト）、`python smoke_test.py` が全 stage パス。
- **Next Action**: 残存する物理モデル仕様差異（§5 Phase 6: Viterna-Corrigan 未実装 / Helmbold 式相違 / 構造応力への alpha 入力 / ABL power law / エネルギー上限の接地経路適用）の是正可否を判断する。

