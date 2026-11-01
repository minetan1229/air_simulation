---
name: process-hygiene
description: メモリ圧迫時、テスト・ワーカープロセスの残存確認時、および不要なバックグラウンドプロセスを停止・回収する際に使用する。
---

# Skill: Process Hygiene（常駐プロセスの使用中判定と回収）

## Trigger (読み込み条件)
- メモリ圧迫・応答遅延・コアダンプ増加の検知時、またはバックグラウンドタスク停止要求時。
- セッション終了時や不要ワーカーの回収時。

## Procedure (最小手順)
1. **判定**: `python tools/process_hygiene.py --list`（KEEP/STOP と理由・実測値の一括表示）
2. **事前記録**: 停止対象PIDと理由を記録（不可逆操作の事前記録）
3. **回収**: `python tools/process_hygiene.py --apply --json`
4. **検証**: `--list` 再実行で STOP=0、IDEや主要ホストプロセスが無傷であることを確認

## Verification Criteria (事前合否基準)
- **成功**: 停止対象 0 件、解放メモリが期待値と一致、IDE・ホストが生存。
- **失敗**: IDE またはホストが停止、STOP判定が解消しない。
- **禁止**: ユーザー実ブラウザ・IDE・自己祖先プロセスの停止。
