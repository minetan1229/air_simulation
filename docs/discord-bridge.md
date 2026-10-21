# Discord Remote Bridge 仕様 (Discord Bridge Specification)

本ドキュメントは `tools/discord_bridge.py` の実際の振る舞いを正本とする。コードと本ドキュメントが乖離した場合はコードを正とする。

## 1. 概要

Antigravity (`agy`) CLI を `--output-format stream-json` で駆動し、Discord からローカルの `simulation/v3` ワークスペースを遠隔操作するブリッジ。会話 ID は UUID で brain ディレクトリ名と一致し、`--print-timeout` 超過時は部分出力を返す（終了コード 0 の正常終了扱い）。

## 2. 認可・ガードレール (Security & Guardrails)

- **認可 (is_authorized)**: `ALLOWED_USER_IDS` が空の場合、**全ユーザーを拒否**（安全側デフォルト）。空リストは「全許可」ではない。
- **チャンネル制限**: `ALLOWED_CHANNEL_IDS` が設定済みの場合、該当チャネルのみ許可。
- **レート制限**: ユーザー単位のトークンバケット（既定: 10 コマンド / 60 秒）。`DISCORD_RATE_LIMIT_MAX` / `DISCORD_RATE_LIMIT_WINDOW` で変更可。
- **プロンプト長検証**: 既定 4000 文字を超えるプロンプトは拒否（`DISCORD_MAX_PROMPT_LEN`）。
- **!chrome / /chrome URL 検証**: http/https のみ許可。プライベート IP / ループバック / リンクローカル / クラウドメタデータをブロックリストで拒否。bare な `localhost` ホスト名は許可。
- **単一インスタンス制御**: ループバックポート（既定 49372、`DISCORD_BRIDGE_LOCK_PORT`）をロックし、二重起動を防止。
- **`--dangerously-skip-permissions`**: ブリッジ側では素通しせず、常に `is_authorized()` を適用。
- **内部例外**: Discord には Error ID のみを送出。スタックトレースはサーバーログに記録。
- **タスク ID 一意化**、**モデル名ホワイトリスト**、**起動時永続ビュー再登録**。

## 3. 環境変数

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

## 4. テスト

- `tests/test_discord_bridge.py`: 認可ロジック・レート制限・URL 検証等のユニットテスト（`discord.py` インストール時のみ実行）。
- 外部ネットワーク（Discord 含む）への実送信はテストで行わない（モックのみ）。
