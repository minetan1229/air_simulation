# Skill: Webhook Notification (Discord Webhook 通知)
## Trigger (読み込み条件)
- 自律ループ開始、サブエージェント委譲、タスク完了、エラー・警告発生時。

## Procedure (最小手順)
1. `DISCORD_WEBHOOK_URL` 確認（未設定時は警告しスキップ）。
2. 実行: `python tools/notify_discord.py <status_type> "<title>" "<summary>" ["extra"] [--report|--no-report]`
3. 状態カラー: `plan`紫(#9B59B6), `start`緑(#2ECC71), `subagent`橙(#E67E22), `verify`青緑(#1ABC9C), `github`/`commit`/`push`藍(#5865F2), `end`/`finish`青(#3498DB), `fail`/`error`赤(#E74C3C), `warn`黄(#F1C40F), `question`桃(#E91E63)。
4. 作業レポート: 完了系（`end`/`finish`/`github`/`commit`/`push`/`fail`）は `📍 操作場所一覧` / `📊 変更数` / `🔗 GitHub連携状況` を自動添付。

## Verification Criteria (事前合否基準)
- 成功: 終了コード0、またはWebhook未設定時の正常スキップ。完了系Embedに3レポートフィールドを含むこと。
- 失敗: 未捕捉例外によるクラッシュ。
