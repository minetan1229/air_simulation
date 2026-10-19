# Knowledge Base (On-Demand Reference)
<!-- 警告: 全文読み込み禁止。grepやキーワード検索で必要な箇所のみ部分抽出すること。 -->
<!-- 記述形式: ## [Category] Topic / 検証済みの確定事実のみを簡潔に記述 -->

## [Architecture & Specs]
- ルート `AGENTS.md` はプロジェクト非依存の Agent Core Protocol OS（常駐規約）であり、`.agents/AGENTS.md` は `.agents/` ワークスペースの読み込み順序と正本マップを定義する。詳細技術仕様は `docs/SPECIFICATION.md` に集約・遅延読み込み化されている。
- MCP実行基盤は `.kilo/mcp/`（`launch.mjs` が各サーバーのエントリポイントをcwd非依存に解決）、登録先は `.kilo/kilo.jsonc` の `mcp` キー。外部APIキーは不要。`fetch` のみ専用venv (`.kilo/mcp/venv`) で Python 実行。
- **返却ペイロードのキー契約の正本は `physics/payload_contract.py`（`PAYLOAD_KEYS` = 33 キー）**。6DoF 参照経路・CPU バッチ・CuPy バッチ・解析サロゲートの全バックエンドが同一のキー集合を返す。CuPy 経路は `approximate=true` / `physics_verification_eligible=false` / `limitations` を伴い、`stability` など測定不能な値は `None`（欠落や 0 偽装ではない）で報告する。
- **CuPy バッチ経路の物理は 6DoF 参照経路と一致しない**（3DoF 平行移動アンサンブル・固定 `dt=0.01 s`・6DoF 参照は適応 RK4）。「CPU/GPU 物理一致」を前提にした最適化は成立しない。`max_stability` のような参照専用客観指標は CuPy 経路で選択できず、optimiser が開始前に拒否する。
- 飛行シミュレータの世界座標系はNED (+z下向き) であり、風場 (+z上向き) との鉛直風変換（$w_{NED} = -w_{wind}$）を整合させている。**風場ドメインは「原点からの絶対高度」を期待する**ため、受け渡しには AGL 高度ではなく `-z_ned` を渡す（`ground_level_m` を二重に引かない）。
- 外部メッシュ取得の `validate_url_safe` は、プライベートIP/ループバック/リンクローカル/クラウドメタデータ（ホスト名明示 deny）/予約・マルチキャスト・未指定アドレス/非標準ポートを遮断する。**リダイレクトは各ホップを再検証**したうえで最大 5 ホップまで追従する。
- APIエラーハンドラはクライアントにスタックトレースを返さず、一意な `error_id` のみを返却する。`/api/v1/errors` と `/api/v1/errors/stats` もスタックトレース・クライアント IP・サーバーログ絶対パスを再公開しない。
- 静的解析の設定の正本は `ruff.toml` / `mypy.ini` / `pytest.ini`。`pyproject.toml` に置いた設定は静かに無視されるため置かない。`physics.*` の `ignore_errors` は撤去済み（全 34 ファイル型検査）。
- 学術文献・理論的根拠レジストリは `/api/v1/meta/physics-references` エンドポイントで公開され、Helmbold/Polhamus/Viterna/Wieselsberger/Hoerner/Sutherland/AeroSandboxの典拠を網羅。

## [Verified Solutions & Troubleshooting]
- CI環境（GPU非搭載環境）でGPUバッチを呼び出すテストは、暗黙的例外送出ではなく安全なNumPy/CPUフォールバックとスキーマ互換性が必要。
- GitHub Actions の Windows runner では PowerShell が既定シェルのため、bash スクリプトを実行するワークフローには `defaults: run: shell: bash` の指定が必須。
- `pip-audit` は**宣言依存セット**（`pip-audit -r requirements.txt`）に対して実行する。環境全体を監査すると pip / setuptools 自身の advisory が混入し、実装とは無関係に失敗する。
- 使い捨て調査スクリプトは `tools/experiments/` へ隔離し、`ruff.toml` の exclude と `pytest.ini` の `norecursedirs` の両方で除外する（一部スクリプトは import 時に 35 秒のシミュレーションを実行する）。
- `GET /api/v1/records/guinness` は検証マトリクス全体を実行すると 60 秒超かかる。ベンチマーク仕様のみを返し、検証実行は `?verify=1` の明示指定に限定する。
- 全監査課題（#28〜#37）は対応完了し、新規監査テストおよび既存テストが全パスして master に反映済み。
- 自律ループプロトコルに Phase 6 [Finalize & Terminate]（正常終了）、リトライ上限3回、不一致時ロールバック規約を導入し、ループ制御の完全性を担保。
- MCP導入（2026-10-19）: context7 / sequential-thinking / fetch / memory / playwright の5サーバーを local stdio で登録し、`kilo mcp list` で全5件 `connected`、`tools/verify_mcp_servers.py` で initialize→tools/list→実ツールコールまで全件PASS。playwright は既存Chromium（`%LOCALAPPDATA%\ms-playwright`）を再利用し、成果物は `.kilo/mcp/playwright/` へ出力される。

## [Paper Registry Pipeline] (2026-10-20, commit a578f08 → master b2cbfc4)
- 学術論文2,000件（5カテゴリ×400件）を `search/` に構造化して収録。仕様は `docs/PAPER_REGISTRY_PIPELINE.md`、検証結果は `search/validation_report.json`（レジストリ再照合 2000/2000、URL解決 2000/2000、構造エラー 0）。
- 出典は Crossref（DOI・抄録・媒体）と DataCite（arXiv 接頭辞 10.48550、240件のプレプリントを含む）。**記憶情報での論文生成は禁止**し、全件をレジストリ API で実在確認した。
- 本環境では arXiv / OpenAlex / Semantic Scholar API が共有IP由来で HTTP 429 となる。代替として Crossref + DataCite + `doi.org` コンテンツネゴシエーション（CSL-JSON）で抄録を回復できる（回復率は低いため抄録未登録件は標題・媒体からの要約と明記）。
- 抄録カバレッジは Crossref 由来で約 25%、arXiv 経由を含めると 60%。抄録の無い論文は `抄録未登録（標題と掲載媒体から要約）` と明示し、支配方程式・手法はタイトルと分類情報からの推定である旨を併記する。
- 選定は「標題にコア語 + （コア語2件以上 or 補完語2件以上）」の標題基点ルール。標題ベースで選別するため、抄録のみに依存した緩い選別による雑多な論文を排除できる。
- 機械翻訳（Google 公開エンドポイント）で標題のみを日本語化し、原題は保持。翻訳は原文テキストの変換であり要約の生成ではない。
