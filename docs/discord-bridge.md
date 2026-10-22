# Discord Remote Bridge 仕様 (Discord Bridge Specification)

本ドキュメントは `tools/discord_bridge.py` と `tools/bridge_*.py` の実際の振る舞いを正本とする。コードと本ドキュメントが乖離した場合はコードを正とする。

## 1. 概要

2つのエージェントバックエンドを駆動し、Discord からローカルの `simulation/v3` ワークスペースを遠隔操作するブリッジ。

| バックエンド | 実行体 | ストリーム形式 | 会話ID | 既定モデル |
|---|---|---|---|---|
| `antigravity`（主） | `agy -p <prompt> --model <m> --output-format stream-json --dangerously-skip-permissions` | NDJSON (`event`) | 会話UUID | `gemini-3.8-flash-low` |
| `kilo`（副・フォールバック） | `kilo run --model <provider/model> --format json --auto -- <prompt>` | NDJSON (`type`) | `ses_*` セッション | `kilo/kilo-auto/free`（**無料**） |

## 2. ルーティング（`tools/bridge_router.py`）

1. **主実行**: 要求バックエンドを1回実行し、NDJSON を `StreamEvent` に正規化して Discord のライブ表示へ流す。
2. **429 自動継続**: `TooManyRequests` / `RESOURCE_EXHAUSTED` / `quota exceeded` / `out of quota` 等を検出しても**失敗に確定させない**。指数バックオフ（`base` 2s, `cap` 60s, 最大 `BRIDGE_RATE_LIMIT_RETRIES` 3回）後に `continue` で同一処理を再開する（Antigravity: `--conversation` / `--continue`、Kilo: `--session` / `--continue`）。
   - 強いマーカー（`too many requests` / `resource_exhausted` / `quota exceeded` 等）は全出力から検出。弱いマーカー（単独の `rate limit` / `429`）は stderr・エラー出力に限定し、通常の回答本文での誤検出を防ぐ。
3. **自動フォールバック**: 主バックエンドが APIエラー・タイムアウト・残りレート0・429継続失敗のいずれかで完了不能なら、**中断せず** Kilo Code の無料モデルへ切り替えて完遂する。切替時は Discord 応答チャンネルと Webhook（メインエージェント宛）へ通知する。

## 3. レート制限 / クォータ（`tools/bridge_quota.py`）

- 取得: `agy -p "/quota"` → `group⇥metric⇥N%⇥resetISO` 形式、`agy -p "/credits"` → 残りクレジット。
- 保持: `QuotaTracker` が既定120秒（`BRIDGE_QUOTA_TTL`）キャッシュする。
- 枯渇判定: 週次残量（`Weekly Limit Remaining`）が 1% 以下で `is_exhausted`。
- **推定利用可能回数**: 総量の想定値を持たず、実測した「送信回数 vs 消費%」の傾きから算出する。測定不足時は算出しない（`None`）。
- 表示: `!quota` / `/quota`（専用 embed）、`/status` にも要約を出力。

## 4. 監視エージェント（`tools/bridge_monitor.py`）

- 監視対象: ホスト稼働（CPU/RAM/GPU）、バックエンド実行ファイルの有無、API応答レイテンシ、連続エラー、レート制限発生数、フォールバック回数、クォータ残量。
- 監視間隔: `BRIDGE_MONITOR_INTERVAL`（既定60秒）。アラート種別: `backend_unavailable` / `consecutive_errors` / `quota_exhausted` / `fallback`。
- 通知先: Discord 応答チャンネル（embed）＋ Discord Webhook（メインエージェント宛、`tools/notify_discord.py` 経由）。
- 表示: `!monitor` / `/monitor`。

## 5. 認可・ガードレール

- **認可 (is_authorized)**: `ALLOWED_USER_IDS` が空の場合、**全ユーザーを拒否**（安全側デフォルト）。空リストは「全許可」ではない。
- **チャンネル制限**: `ALLOWED_CHANNEL_IDS` が設定済みの場合、該当チャネルのみ許可。
- **レート制限**: ユーザー単位のトークンバケット（既定: 10 コマンド / 60 秒）。`DISCORD_RATE_LIMIT_MAX` / `DISCORD_RATE_LIMIT_WINDOW` で変更可。
- **プロンプト長検証**: 既定 4000 文字を超えるプロンプトは拒否（`DISCORD_MAX_PROMPT_LEN`）。
- **!chrome / /chrome URL 検証**: http/https のみ許可。プライベート IP / ループバック / リンクローカル / クラウドメタデータをブロックリストで拒否。bare な `localhost` ホスト名は許可。
- **単一インスタンス制御**: ループバックポート（既定 49372、`DISCORD_BRIDGE_LOCK_PORT`）をロックし、二重起動を防止。
- **`--dangerously-skip-permissions` / `--auto`**: ブリッジ側の認可（`is_authorized()`）は常に適用される。
- **内部例外**: Discord には Error ID のみを送出。スタックトレースはサーバーログに記録。
- **タスク ID 一意化**、**モデル名ホワイトリスト**、**起動時永続ビュー再登録**。

## 6. コマンド一覧

| Slash | Prefix | 概要 |
|---|---|---|
| `/chat <prompt>` | `!chat <prompt>` | エージェント実行（並列・429自動継続・自動フォールバック） |
| `/status` | `!status` | CPU・メモリ・GPU・タスク・バックエンド・クォータ・監視状況 |
| `/quota` | `!quota` | 残りのレート制限（利用率・推定利用可能回数・クレジット） |
| `/monitor` | `!monitor` | 監視エージェントの稼働状況とアラート |
| `/backend [antigravity\|kilo]` | `!backend` | 実行バックエンドの切替（Kilo は無料モデル） |
| `/models` / `/model [id]` | `!models` / `!m` | モデルの一覧・切替（Antigravity / Kilo Code） |
| `/chats` / `/resume <id>` | `!chats` / `!resume` | 会話一覧与会話再開（両バックエンド対応） |
| `/new` | `!new` | 会話コンテキストのリセット |
| `/chrome [url]` | `!chrome` | ローカル Chrome 起動 |
| `/abort` | `!abort` | 実行中タスクの停止（🛑 リアクションでも可） |

## 7. 環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | (必須) | Bot トークン |
| `ALLOWED_USER_IDS` | (空＝全拒否) | 許可ユーザー ID のカンマ区切り |
| `ALLOWED_CHANNEL_IDS` | (空＝全チャネル) | 許可チャネル ID のカンマ区切り |
| `DISCORD_RATE_LIMIT_MAX` | `10` | レート制限の最大コマンド数 |
| `DISCORD_RATE_LIMIT_WINDOW` | `60.0` | レート制限ウィンドウ秒 |
| `DISCORD_MAX_PROMPT_LEN` | `4000` | プロンプト最大文字数 |
| `DISCORD_BRIDGE_LOCK_PORT` | `49372` | 単一インスタンス制御ポート |
| `DISCORD_DANGEROUS_SKIP_PERMISSIONS` | `0` | 未使用（常に無効） |
| `DEFAULT_MODEL` | `gemini-3.8-flash-low` | 既定モデル（Antigravity） |
| `DEFAULT_BACKEND` | `antigravity` | 既定バックエンド |
| `FALLBACK_MODEL` | `kilo/kilo-auto/free` | フォールバック先モデル（**無料**） |
| `BRIDGE_FALLBACK_ENABLED` | `1` | フォールバックの有効/無効 |
| `BRIDGE_RATE_LIMIT_RETRIES` | `3` | 429 自動継続の最大再試行回数 |
| `BRIDGE_BACKOFF_BASE` / `BRIDGE_BACKOFF_CAP` | `2.0` / `60.0` | 指数バックオフの基底秒・上限秒 |
| `BRIDGE_TASK_TIMEOUT` | `1800` | 1タスクのタイムアウト秒 |
| `BRIDGE_MONITOR_INTERVAL` | `60` | 監視エージェントの監視間隔秒 |
| `BRIDGE_MONITOR_ERROR_THRESHOLD` | `3` | 連続エラーでアラートする回数 |
| `BRIDGE_QUOTA_TTL` | `120` | クォータのキャッシュ秒 |
| `AGY_CLI_PATH` / `KILO_CLI_PATH` | (自動検出) | CLI 実行ファイルの明示指定 |

## 8. テスト

- `tests/test_discord_bridge.py`: 認可ロジック・レート制限・URL 検証等のユニットテスト。
- `tests/test_bridge_agents.py`: クォータ解析・レート判定・バックオフ・バックエンドのコマンド生成とパース・**429自動継続**・**フォールバック**・監視エージェントのユニットテスト。
- 外部ネットワーク（Discord 含む）への実送信はテストで行わない（モックのみ）。
