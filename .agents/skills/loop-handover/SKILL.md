---
name: loop-handover
description: 会話コンテキストが長くなった時、セッションの切り替え時、または状態を圧縮して次のセッションへ引き継ぐ際に使用する。
---

# Skill: Loop Handover (コンテキスト圧縮・状態引き継ぎ)

## Trigger (読み込み条件)
- チャット履歴（コンテキスト）が長くなった時、またはユーザーからセッション引き継ぎ・コンテキスト圧縮の指示があった時。

## Procedure (最小手順)
1. 確定した知見は `.agents/Knowledge.md` へ1〜2行で簡潔に蒸留・追記する。
2. `.agents/Project.md` の `Loop State`（完了タスク、棄却された仮説、次のアクション）を更新する。
3. ユーザーに「蒸留完了。新セッションで『AGENTS.mdに従いループ再開せよ』と指示願います」と提示して終了する。

## Verification Criteria (事前合否基準)
- **成功**: 新セッションで `AGENTS.md`、`.agents/context.md`、`.agents/Project.md` を読むだけで文脈損失なく再開できること。
- **失敗**: 未記録の前提条件や検証中仮説がチャット履歴内に取り残されていること。
