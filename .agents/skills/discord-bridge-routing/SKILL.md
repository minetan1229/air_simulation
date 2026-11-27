---
name: discord-bridge-routing
description: Discordからエージェントを駆動する時、API 429レート制限・クォータ枯渇発生時の共有冷却・自動継続、およびKilo Codeへのフォールバックを処理する際に使用する。
---

# Skill: Discord × Agent Routing (Discord×Agent ルーティング)

## 1. エージェント体制
- 並列稼働枠・3層モデル体制の正本は `.agents/skills/subagent-delegation/SKILL.md` を参照（必須 Watchdog 1枠＋排他境界または隔離 worktree）。

## 2. バックエンド（正本: `tools/bridge_backends.py`）
| バックエンド | 実行体 | 識別子 | 推奨モデル |
|---|---|---|---|
| `antigravity`（主） | `agy -p ... --output-format stream-json` | 会話UUID | `gemini-4-hydrogen-medium`（Main）/ `gemini-4-hydrogen-low`（Sub） |
| `kilo`（副・フォールバック） | `kilo run --format json --auto -- <prompt>` | `ses_*` セッション | `kilo/kilo-auto/free`（無料） |

## 3. ルーティング & 429 協調規則（正本: `tools/bridge_router.py`, `tools/rate_limit_guard.py`）
1. **429 共有冷却 & 自動継続**: `TooManyRequests` / `RESOURCE_EXHAUSTED` / `quota exceeded` 検出時は失敗扱いせず、`tools/rate_limit_guard.py` へ共有クールダウンを記録した上でジッター付き指数バックオフ後に `continue` で再開する。
2. **フォールバック**: 主バックエンドが完了不能なら、処理を中断せず Kilo Code 無料モデルへ切り替えて完遂し、監視エージェントが通知する。
3. **クォータ・監視コマンド**: `!quota` / `/quota`（`tools/bridge_quota.py`）、`!monitor` / `/monitor`（`tools/bridge_monitor.py`）。
