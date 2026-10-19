# Knowledge Base (On-Demand Reference)
<!-- 警告: 全文読み込み禁止。grepやキーワード検索で必要な箇所のみ部分抽出すること。 -->
<!-- 記述形式: ## [Category] Topic / 検証済みの確定事実のみを簡潔に記述 -->

## [Architecture & Specs]
- ルート `AGENTS.md` と `.agents/AGENTS.md` は共通のプロジェクト非依存 Agent Core Protocol OS であり、詳細技術仕様は `docs/SPECIFICATION.md` に集約・遅延読み込み化されている。
- MCP実行基盤は `.kilo/mcp/`（`launch.mjs` が各サーバーのエントリポイントをcwd非依存に解決）、登録先は `.kilo/kilo.jsonc` の `mcp` キー。外部APIキーは不要。`fetch` のみ専用venv (`.kilo/mcp/venv`) で Python 実行。
- GPUバッチシミュレーション (`gpu_batch_evaluate_cases`) は非GPU環境（CI/Linux）で `parallel_evaluate` を安全なフォールバックとし、同一のスキーマ（`landed`, `crashed`, `status`, `horizontal_range_m`）を返却する。
- 飛行シミュレータの世界座標系はNED (+z下向き) であり、風場 (+z上向き) との鉛直風変換（$v_{rel} = v_{aircraft} - wind_{ned}$）を整合させている。
- 外部メッシュ取得の `validate_url_safe` は、プライベートIP/ループバック/クラウドメタデータ/非標準ポート/リダイレクトを遮断する。
- APIエラーハンドラはクライアントにスタックトレースを返さず、一意な `error_id` のみを返却する。
- 学術文献・理論的根拠レジストリは `/api/v1/meta/physics-references` エンドポイントで公開され、Helmbold/Polhamus/Viterna/Wieselsberger/Hoerner/Sutherland/AeroSandboxの典拠を網羅。

## [Verified Solutions & Troubleshooting]
- CI環境（GPU非搭載環境）でGPUバッチを呼び出すテストは、暗黙的例外送出ではなく安全なNumPy/CPUフォールバックとスキーマ互換性が必要。
- GitHub Actions の Windows runner では PowerShell が既定シェルのため、bash スクリプトを実行するワークフローには `defaults: run: shell: bash` の指定が必須。
- 全監査課題（#28〜#37）は対応完了し、新規監査テスト10件および既存700+件のテストが全パスして master (82d48ea) に反映済み。
- 自律ループプロトコルに Phase 6 [Finalize & Terminate]（正常終了）、リトライ上限3回、不一致時ロールバック規約を導入し、ループ制御の完全性を担保。
- MCP導入（2026-10-19）: context7 / sequential-thinking / fetch / memory / playwright の5サーバーを local stdio で登録し、`kilo mcp list` で全5件 `connected`、`tools/verify_mcp_servers.py` で initialize→tools/list→実ツールコールまで全件PASS。playwright は既存Chromium（`%LOCALAPPDATA%\ms-playwright`）を再利用し、成果物は `.kilo/mcp/playwright/` へ出力される。

## [Paper Registry Pipeline] (2026-10-20, commit a578f08 → master b2cbfc4)
- 学術論文2,000件（5カテゴリ×400件）を `search/` に構造化して収録。仕様は `docs/PAPER_REGISTRY_PIPELINE.md`、検証結果は `search/validation_report.json`（レジストリ再照合 2000/2000、URL解決 2000/2000、構造エラー 0）。
- 出典は Crossref（DOI・抄録・媒体）と DataCite（arXiv 接頭辞 10.48550、240件のプレプリントを含む）。**記憶情報での論文生成は禁止**し、全件をレジストリ API で実在確認した。
- 本環境では arXiv / OpenAlex / Semantic Scholar API が共有IP由来で HTTP 429 となる。代替として Crossref + DataCite + `doi.org` コンテンツネゴシエーション（CSL-JSON）で抄録を回復できる（回復率は低いため抄録未登録件は標題・媒体からの要約と明記）。
- 抄録カバレッジは Crossref 由来で約 25%、arXiv 経由を含めると 60%。抄録の無い論文は `抄録未登録（標題と掲載媒体から要約）` と明示し、支配方程式・手法はタイトルと分類情報からの推定である旨を併記する。
- 選定は「標題にコア語 + （コア語2件以上 or 補完語2件以上）」の標題基点ルール。標題ベースで選別するため、抄録のみに依存した緩い選別による雑多な論文を排除できる。
- 機械翻訳（Google 公開エンドポイント）で標題のみを日本語化し、原題は保持。翻訳は原文テキストの変換であり要約の生成ではない。
