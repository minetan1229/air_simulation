# Knowledge Base (On-Demand Reference)
<!-- 警告: 全文読み込み禁止。grepやキーワード検索で必要な箇所のみ部分抽出すること。 -->
<!-- 記述形式: ## [Category] Topic / 検証済みの確定事実のみを簡潔に記述 -->

## [Architecture & Specs]
- ルート `AGENTS.md` と `.agents/AGENTS.md` は共通のプロジェクト非依存 Agent Core Protocol OS であり、詳細技術仕様は `docs/SPECIFICATION.md` に集約・遅延読み込み化されている。
- GPUバッチシミュレーション (`gpu_batch_evaluate_cases`) は非GPU環境（CI/Linux）で `parallel_evaluate` を安全なフォールバックとし、同一のスキーマ（`landed`, `crashed`, `status`, `horizontal_range_m`）を返却する。
- 飛行シミュレータの世界座標系はNED (+z下向き) であり、風場 (+z上向き) との鉛直風変換（$v_{rel} = v_{aircraft} - wind_{ned}$）を整合させている。
- 外部メッシュ取得の `validate_url_safe` は、プライベートIP/ループバック/クラウドメタデータ/非標準ポート/リダイレクトを遮断する。
- APIエラーハンドラはクライアントにスタックトレースを返さず、一意な `error_id` のみを返却する。
- 学術文献・理論的根拠レジストリは `/api/v1/meta/physics-references` エンドポイントで公開され、Helmbold/Polhamus/Viterna/Wieselsberger/Hoerner/Sutherland/AeroSandboxの典拠を網羅。

## [Verified Solutions & Troubleshooting]
- CI環境（GPU非搭載環境）でGPUバッチを呼び出すテストは、暗黙的例外送出ではなく安全なNumPy/CPUフォールバックとスキーマ互換性が必要。
- GitHub Actions の Windows runner では PowerShell が既定シェルのため、bash スクリプトを実行するワークフローには `defaults: run: shell: bash` の指定が必須。
- 全監査課題（#28〜#37）は対応完了し、新規監査テスト10件および既存700+件のテストが全パスして master (82d48ea) に反映済み。
