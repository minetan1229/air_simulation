# Agent Core Protocol (Project-Agnostic OS)
<!-- 警告: 本ファイルへのプロジェクト固有仕様の記述を厳禁とする -->

## 1. Context Minimization (コンテキスト最小化)
トークン消費抑制と推論精度維持のため、遅延読み込み（Lazy Loading）を徹底せよ。
- **常駐**: セッション開始時は `AGENTS.md` と `.agents/Knowledge-important.md` のみ。
- **遅延参照**:
  - 現在地確認: `.agents/Project.md`（仕様詳細は `docs/SPECIFICATION.md`）。
  - 過去知見: `.agents/Knowledge.md` を全文読まず grep 抽出。
  - 定型手順: `.agents/skills/` 内の該当1ファイルのみ参照し完了後即破棄（#15）。
  - ログ/ソースの全文出力を禁止し、行指定抽出を徹底。

## 2. Anti-Fallacy Rule (テキサスの狙撃兵の誤謬排除)
事後正当化を厳禁とする。
- **事前基準固定**: 変更/実行前に「事前仮説」と「反証可能な合否基準」を明文化。
- **後付け変更禁止**: 結果を見て合否基準を緩めたり偶然の成功を「解決」とみなすことを禁止。
- **仮説棄却**: 予測不一致時は仮説を棄却し、因果特定まで次の仮説を再構築。

## 3. Autonomous Execution Loop (自律ループ)
タスクは微小ループとして再帰実行せよ。
1. **[Observe & Scope]**: Loop State を確認。残存タスクなしは **Phase 6** へ。ある場合は最小サブタスク1つを定義（#16: 初期要件・最上位目的にアンカリング）。
2. **[Hypothesize]**: 事前仮説と合否基準を宣言（#8: 調査目的の探索的スパイク特例あり）。
3. **[Execute]**: 最小変更/検証コマンドを1つ実行（#11: 不可逆操作前の先行状態WAL記録、#5: Settle/Syncバリア、#14: 親エージェント単一コミッター原則）。
4. **[Verify]**: 事前基準と照合。不一致時はロールバックし Phase 2 へ（リトライ上限3回、超過時はエスカレーション）。
   - #2: 収束メトリクス確認 & 3回連続同エラーで Livelock 中断。
   - #6: 不可逆検証のモック化、#7: Flaky検証の統計判定 (N>=3)、#9: 因果調査の深さ最大3階層制限。
5. **[Distill & Loop]**:
   - 確定事実は `.agents/Knowledge.md` に追記（#12: 前提条件・日付付き、#13: 定期GC/Pruning、#10: 偶発的発見は Project.md へ退避）。
   - 致命的制約は `.agents/Knowledge-important.md` へ昇格（50行以内）。
   - Loop State を更新。コンテキスト肥大時は `skills/loop-handover.md` 実行、余裕があれば Phase 1 へ。
6. **[Finalize & Terminate]**:
   - 全マイルストーン達成を確認し、ローカル検証（構文・静的解析・テスト全件）を通過させる。
   - トピックブランチから master へ統合後、リモートへ自動プッシュ (`git push`) を実行。
   - GitHub Actions CI/CD の実行結果を監視（`tools/check_runs.py`）し、全ジョブグリーンを確認。
   - GitHub Issue（`tools/check_issues.py` / `tools/read_issues.py`）を確認し、残存課題の修正・解決およびクローズ（`tools/close_resolved_issue.py`）を行って自律ループを正常終了（HALT）。

## 4. Meta-Control & Protocol Governance (プロトコル改定 #17)
- `AGENTS.md` 改変はユーザー明示要求または監査合意時のみ行い、無断での改変を禁止する。


## 5. Webhook & Progress Notification (進捗通知規約)
- 開始/委譲/完了/異常時は Discord Webhook 通知を実行（詳細は `.agents/skills/notify-webhook.md`）。
  - コマンド: `python tools/notify_discord.py <status_type> "<title>" "<summary>"`
  - カラー: `start`(緑), `subagent`(橙), `end`/`finish`(青), `fail`(赤), `warning`(黄), `question`(桃)
  - セキュリティ: Webhook URL は `DISCORD_WEBHOOK_URL` または `.env` 管理とし直書き厳禁。

## 6. Context-Aware Protocol Maintenance (コンテキスト意識維持)
- 本ファイルは常駐コンテキストのためトークン消費を抑え簡潔を保つ。物理仕様は `docs/SPECIFICATION.md`、手順は `.agents/skills/` を参照。

## 7. 利用可能なMCPツール一覧 (MCP Tool Inventory)
- 設定: `.kilo/kilo.jsonc` / 実行基盤: `.kilo/mcp/`（local stdio・外部APIキー不要）/ 導入手順: `.kilo/mcp/README.md`
- 健全性確認: `python tools/verify_mcp_servers.py`（initialize → tools/list → 実ツールコール）／全カタログは `--list-only`
- 表記: Kiloでは `{server}_{tool}` 形式（例 `context7_resolve-library-id`, `playwright_browser_navigate`）。以下はサーバー別の実ツール名。

| サーバー | ツール | 用途 |
|---|---|---|
| `context7` | `resolve-library-id`, `query-docs` | 対象ライブラリの最新仕様・API文書の取得 |
| `sequential-thinking` | `sequentialthinking` | 多段推論の構造化（仮説→反証→再仮説） |
| `fetch` | `fetch` | Webページ/論文のMarkdown取得（SSRF遮断・robots.txt準拠は有効） |
| `memory` | `create_entities`, `create_relations`, `add_observations`, `read_graph`, `search_nodes`, `open_nodes`, `delete_entities`, `delete_relations`, `delete_observations` | セッション跨ぎ知識グラフ永続化（保存先 `.kilo/mcp/memory.jsonl`） |
| `playwright` | `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_fill_form`, `browser_press_key`, `browser_hover`, `browser_select_option`, `browser_evaluate`, `browser_take_screenshot`, `browser_console_messages`, `browser_network_requests`, `browser_wait_for`, `browser_find`, `browser_drag`, `browser_handle_dialog`, `browser_resize`, `browser_tabs`, `browser_run_code_unsafe`, `browser_close`（他 計25ツール） | Three.js UIの実ブラウザ操作・描画/コンソール確認 |

## 8. MCP使い分け行動指針 (MCP Usage Policy)
1. **ライブラリの実装・API利用の前** → 必ず `context7` で最新仕様を引く（記憶ではなく一次情報を優先）。対象: NumPy/SciPy/CuPy/Flask/Three.js/Playwright/MCP SDK等。
2. **複雑なバグ調査・多段物理推論**（6DoF数値破綻、CCD、翼端接地、エネルギー保存則、姿勢発散等） → `sequential-thinking` を §2 Anti-Fallacy と併用し、仮説・反証基準・棄却理由まで構造化して進める。
3. **Web調査・文献収集**（`search/` コーパス追加、原典論文、規格/API仕様ページ） → `fetch` を使用。`--allow-private-ips` / `--ignore-robots-txt` は使用禁止（セキュリティ基線の低下）。
4. **セッション跨ぎの事実保持**（物理定数、仕様値、既知不具合と回避策） → `memory` にエンティティ/関係/観測として記録し、着手時に `read_graph` / `search_nodes` で想起する。`.agents/Knowledge.md` が確定事実の正本であり、`memory` は検索用索引（正本と重複する長文の二重保管は禁止）。
5. **UIの動作確認**（Three.js描画、コンソールエラー、DOM状態、ネットワーク要求） → `playwright` を使用。`tools/capture_ui.py` / `tools/verify_repair_ui.py` の補完として「実際に描画されるか」を検証する。
6. **MCP不要のタスク**（ファイル検索/編集、pytest・ruff・mypy実行等） → 既存ツールのみ使用し、MCP呼び出しでコンテキストを浪費させない。
7. **MCP呼び出し失敗時** → まず `python tools/verify_mcp_servers.py` で接続とツール取得を切り分け、再導入は `.kilo/mcp/README.md` の手順に従う。
8. **MCP追加・変更時** → `.kilo/kilo.jsonc` を編集 → `verify_mcp_servers.py` → `kilo mcp list` で `connected` を確認 → Kiloセッションを再読込 → 本節の表を同期する。
