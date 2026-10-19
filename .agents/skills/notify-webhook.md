# Skill: Webhook Notification (Discord Webhook 通知)
## Trigger (読み込み条件)
- 自律ループの開始時、サブエージェント委譲・実行時、タスク完了時、または致命的エラー・警告・ユーザー質問が発生した時。

## Procedure (最小手順)
1. 環境変数 `DISCORD_WEBHOOK_URL` または `.env` の存在を確認する（未設定時は警告を出力し送信スキップ）。
2. 用途に応じた状態タイプ・タイトル・要約（100文字程度）を引数に指定してスクリプトを実行する：
   ```powershell
   python tools/notify_discord.py <status_type> "<title>" "<summary>" ["extra_info"]
   ```
3. 状態カラー規約:
   - `start`: 開始（緑 `#2ECC71`）
   - `subagent`: サブエージェント連携（橙 `#E67E22`）
   - `end` / `finish`: 正常完了（青 `#3498DB`）
   - `fail` / `error`: 失敗・例外（赤 `#E74C3C`）
   - `warning`: 警告・注意（黄 `#F1C40F`）
   - `question`: ユーザー確認事項（桃 `#E91E63`）

## Verification Criteria (事前合否基準の型)
- 成功条件: スクリプト終了コードが 0 であること、または Webhook 未設定時に非破壊で正常スキップされること。
- 失敗条件: スクリプトが未捕捉例外でクラッシュし自律ループの進行を阻害すること。
