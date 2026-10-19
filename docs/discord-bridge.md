# Discord Remote Bridge（Antigravity リモート操作）

`tools/discord_bridge.py` は、ローカルの Antigravity (AGY) エージェントを Discord から操作するためのブリッジです。
作業ディレクトリは `simulation/v3` に固定されており、Discord 上のコマンドでエージェントの実行・監視・停止を行えます。

---

## 1. 起動

```powershell
python tools/discord_bridge.py     # もしくは start_discord_bridge.bat
```

起動時に以下が行われます。

1. スラッシュコマンド（10 個）を Discord へ同期
2. 直近のセッションに対応する「返信する」ボタンを永続ビューとして再登録
3. `on_ready` でシステム情報（CPU / RAM / GPU）を/channel へ通知

---

## 2. コマンド（スラッシュ / プレフィックスは同一実装）

| コマンド | 概要 |
|---|---|
| `/chat <prompt>` / `!chat <prompt>` | エージェントへプロンプトを送信（並列実行可） |
| `/chats` / `!chats` | 最近のセッション一覧と会話の切替 |
| `/resume <id>` / `!resume <id>` | セッション ID（8 文字の短縮 ID 可）から再開 |
| `/new` / `!new` | 会話コンテキストをリセット |
| `/abort` / `!abort` | 実行中タスクの停止（🛑 リアクションでも可） |
| `/model [name]` / `!model [name]` | モデルの切替・表示 |
| `/models` / `!models` | モデル一覧（`!models refresh` で CLI から再取得） |
| `/status` / `!status` | CPU・メモリ・GPU・実行中タスク |
| `/chrome [url]` / `!chrome [url]` | ローカル Chrome の起動（http/https のみ許可） |
| `/help` / `!help` | コマンド一覧 |

DM、許可チャンネル、-bot メンション付きのメッセージは、そのままエージェントへのプロンプトとして送信されます。

### 会話セッションの扱い

- `agy` は `init` イベントの `conversation_id`（UUID）を返します。ブリッジはこれをチャンネルに紐付け、次回以降の入力に `--conversation <id>` を付与します。
- `/chats` は `~/.gemini/antigravity-cli/brain` および `~/.gemini/antigravity/brain` を走査し、transcript の最初の `USER_INPUT` から初期プロンプトを抽出して一覧表示します。
- `/resume` は ID の完全一致 → 前方一致の順に解決します。前方一致が複数ある場合は候補を提示し、一致なしの場合は警告しつつ ID をそのまま使用します。

---

## 3. 環境変数（`.env`）

| 変数 | 既定値 | 用途 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | - | Bot トークン（必須） |
| `ALLOWED_USER_IDS` | 空 | 実行を許可する User ID（カンマ区切り）。空の場合は誰でも実行可能 |
| `ALLOWED_CHANNEL_IDS` | 空 | 許可チャンネル（カンマ区切り）。空の場合は DM 以外の全チャンネルが対象 |
| `DEFAULT_MODEL` | `gemini-3.8-flash-low` | 既定モデル |
| `AGY_CLI_PATH` | `agy` | Antigravity CLI のパス（相対指定時は PATH を参照） |
| `BRIDGE_MAX_PROMPT_CHARS` | `8000` | プロンプト長の上限 |
| `BRIDGE_MAX_TASKS_PER_CHANNEL` | `3` | チャンネル同時実行数の上限 |
| `BRIDGE_MAX_TASKS_GLOBAL` | `8` | 全体同時実行数の上限 |
| `BRIDGE_TASK_TIMEOUT_SECONDS` | `1800` | 1 ターンの時間制限（`--print-timeout` にも反映） |
| `BRIDGE_PERSISTENT_VIEW_SESSIONS` | `50` | 起動時に永続ビュー登録するセッション数 |

---

## 4. 品質ガードレール

| 観点 | 実装 |
|---|---|
| 同時実行の制限 | チャンネル単位・全体単位で実行数を制限し、ホストのリソースを保護 |
| タイムアウト | CLI の `--print-timeout` とプロセス監視の二重化。超過時はプロセスを停止し部分出力を報告 |
| デッドロック防止 | stderr を別タスクで常に読み取り（パイプ枯渇による子プロセス停止を防止） |
| プロセスツリー終了 | POSIX は `killpg`、Windows は `taskkill /T` で子孫まで停止 |
| 入力検証 | プロンプト長、URL スキーム（http/https のみ）、モデル ID、セッション ID を検証 |
| 情報の秘匿 | 内部例外は `Error ID` のみ Discord へ提示し、詳細 はサーバログに記録 |
| イベントループ | ディスク走査・メトリクス取得・ブラウザ起動はすべて `asyncio.to_thread` で実行 |
| タスク管理 | バックグラウンドタスクを保持し、GC による取りこぼしと未処理例外を防止 |
| 再起動耐性 | 起動時に永続ビューを再登録し、「返信する」ボタンが再起動後も動作 |
| 終了処理 | シャットダウン時に実行中の子プロセスを全て停止 |

---

## 5. テスト

```powershell
python -m pytest tests/test_discord_bridge.py -q
```

テストは次を網羅しています。

- 入力検証（プロンプト長 / URL スキーム / モデル ID / セッション ID）
- チャネル分割・埋め込み生成
- 同時実行上限・タスク登録／解除・中断
- 永続ビューの再登録
- 実際の子プロセスを起動する実行エンジンの結合テスト（スタブ CLI  使用）
- CLI タイムアウト時の検出とプロセス停止

---

## 6. 関連ツール

- `tools/clear_discord_commands.py` : 古いスラッシュコマンドの一括削除
- `tools/notify_discord.py` : Webhook 通知（ブリッジとは別系統）
