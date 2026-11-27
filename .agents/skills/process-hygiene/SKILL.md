---
name: process-hygiene
description: 常駐プロセス（pytest・開発サーバ・kiloワーカー・孤児MCP/ブラウザ）の使用中判定と回収を行う際に使用する。他セッション保護とセッション限定回収を含む。
---

# Skill: Process Hygiene (常駐プロセスの使用中判定と回収)

## 1. 判定軸（正本: `tools/process_hygiene.py`）
- **K1 ホスト / K2 ホスト所有**: セッション基盤（`kilo serve` / IDE）と所有 MCP プール・ブラウザを保護。
- **K3 明示指定 & アクティブセッション保護（並列安全）**:
  - `--keep-pid` / `--keep-since` に加え、`~/.cache/air_simulation/active_sessions/*.json` に登録された**生存中セッションの PID・子孫プロセス・およびその `worktree_path` を含むプロセスを自動保護**する。
  - これにより、あるセッションで `--apply` を実行しても同時進行中の他セッションの `pytest` やサーバを誤殺しない。
- **S1–S5 停止対象**: 孤児ワーカー・オーケストレータ・孤児 MCP・所有セッション不在の残存実行物・孤児ブラウザ。

## 2. 推奨コマンド
```bash
# 全体一覧（ドライラン）
python tools/process_hygiene.py --list

# 自セッションの worktree 配下の孤児のみ安全に回収
python tools/process_hygiene.py --session <session_id> --apply

# 全体回収（他アクティブセッションは自動保護）
python tools/process_hygiene.py --apply --json
```
