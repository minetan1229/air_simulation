---
name: loop-handover
description: 会話コンテキストが長くなった時、セッションの切り替え時、または状態を圧縮して次のセッションへ引き継ぐ際に使用する。
---

# Skill: Loop Handover (コンテキスト圧縮・状態引き継ぎ)

## Procedure
1. **知見の分離出力**: 確定した事実・回避策・顧問（Opus 4.6 / Hydrogen XHigh）の設計判断を `.agents/knowledge.d/<YYYYMMDD>_<session_id>.md` へ簡潔に出力する（`.agents/Knowledge.md` は直編集せず統合ゲートで `tools/distill_agents_state.py` により蒸留）。
2. **セッション状態の更新**: `.agents/sessions/<session_id>.md` に以下を記録する：
   - 現在の Phase・ブランチ・worktree パス
   - 完了済み／未完了 WBS タスクと Non-Goals（P2凍結範囲）
   - 棄却された仮説・顧問相談ログ（`.agents/consultations/<file>.md`）
   - 登録済み悪役ケース（`tests/adversarial_cases/`）と次のアクション
3. **引き継ぎ宣言**: ユーザーに「状態保存完了。新セッション（Hydrogen Medium）で『AGENTS.md に従いセッション `<session_id>` を再開せよ』と指示願います」と提示して終了する。
