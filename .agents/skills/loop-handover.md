# Skill: Loop Handover (コンテキスト圧縮・状態引き継ぎ)
## Trigger (読み込み条件)
- チャット履歴（コンテキスト）が長くなった時、またはユーザーが「ループを引き継いで」「コンテキストをリセットして」と指示した時。

## Procedure (最小手順)
1. 現在のサイクルで確定した事実を `.agents/Knowledge.md`（致命的なものは `Knowledge-important.md`）へ1〜2行で蒸留・追記する。
2. `.agents/Project.md` の `3. Loop State` を最新の状態（完了したタスク、現在棄却された仮説、次サイクルの `Next Action`）に書き換える。
3. ユーザーに対し「状態の蒸留が完了しました。新しいセッションを開始し、『.agents/AGENTS.md に従ってループを再開せよ』と指示してください」と出力して停止する。

## Verification Criteria (事前合否基準の型)
- 成功条件: 新規セッションのAIが `AGENTS.md`、`Knowledge-important.md`、`Project.md` の3ファイルを読むだけで、一切の文脈ロスなく次の Phase 1 から再開できる状態になっていること。
- 失敗条件: 未記録の前提条件や、検証途中の仮説がチャット履歴内に取り残されていること。
