# Skill: Loop Handover (コンテキスト圧縮・状態引き継ぎ)
## Trigger (読み込み条件)
- チャット履歴（コンテキスト）が長くなった時、またはユーザーが「ループを引き継いで」「コンテキストをリセットして」と指示した時。

## Procedure (最小手順)
1. 確定した知見は `.agents/Knowledge.md`（致命的制約は `AGENTS.md` §2）へ1〜2行で蒸留・追記。
2. `.agents/Project.md` の `3. Loop State` を更新（完了タスク、棄却仮説、次 `Next Action`）。
3. ユーザーに「蒸留完了。新セッションで『AGENTS.mdに従いループ再開せよ』と指示願います」と提示し停止。

## Verification Criteria (事前合否基準)
- 成功条件: 新セッションで `AGENTS.md` と `Project.md`（必要時 `Knowledge.md`）を読むだけで文脈損失なく Phase 1 から再開可能。
- 失敗条件: 未記録の前提条件や検証中仮説がチャット履歴内に取り残されていること。
