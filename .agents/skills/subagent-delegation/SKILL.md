---
name: subagent-delegation
description: 会社組織型の3層エージェント体制（Main: Hydrogen Medium / Sub: Hydrogen Low / Advisor: Opus 4.6 or Hydrogen XHigh）と大量セッション同時進行の委譲・担当境界管理に使用する。
---

# Skill: Subagent Delegation (会社組織型・並列委譲と境界管理)

## 1. タスクルーティングゲート（arXiv:2512.08296 準拠）
- **単体実行**: 1〜2ファイル修正・逐次デバッグ・強依存タスクは Main 単体で完了させる（過剰協調による性能劣化を回避）。
- **並列委譲**: 独立した複数ドメイン・複数モジュール改修・大量監査のみサブエージェントへ委譲する。

## 2. 3層モデル×役職マトリクス（単一正本）
| 階層 | 役職 | モデル / Effort | 主責務 |
|---|---|---|---|
| **Tier 1** | `Main`（経営・PM） | `gemini-4-hydrogen` (`medium`) | 全体Plan・方針・中央検証・単一コミッター |
| **Tier 2** | `team-lead`（現場リーダー） | `gemini-4-hydrogen` (`low`) | WBS分解・タスク別Plan/方針/根本修正・セルフチェック |
| **Tier 2-QA** | `qa-watchdog`（監査・必須1枠） | `gemini-4-hydrogen` (`low`) | 差分監査・P0抜け穴検知・`FREEZE_PASS`判定 |
| **Tier 3** | `principal-advisor`（技術顧問） | `claude-opus-4-6-thinking` / `gemini-4-hydrogen` (`xhigh`) | 2回連続失敗・悪役機体解剖・横断設計相談（読取専用） |
| **Fallback** | Kilo Code CLI | `kilo/kilo-auto/free` 等（無料） | 429継続失敗・障害時の業務継続 |

## 3. 並列稼働 & 同時進行パターンの使い分け
- **必須常駐**: `qa-watchdog` 1枠。実務 `team-lead` は排他境界または隔離 worktree（`workspace: branch` / `tools/session_manager.py`）を条件に標準 4〜6枠、必要時は親判断で動的拡張可。
- **独立機能・別 Issue 開発**: `tools/session_manager.py start` で 1セッション＝1 worktree 物理隔離。
- **1機能内の多数ファイル処理・監査**: Antigravity `/run-workflow`（`parallel()` + `workspace: branch`）。
- **複数モジュール協調開発**: Antigravity `/swarm`（ワーカー＋`@verifier`）。
- **横断コア制限**: `core/`・`app.py`・`AGENTS.md` の変更は同時1セッションのみ許可。`master` マージは PR/CI 通過後の直列統合ゲートに一本化。

## 4. 業務指示書（Mission Ticket）必須5項目
1. **Mission Goal & Non-Goals（P2凍結範囲）**
2. **Owned Scope（排他ファイル境界 / worktree）**
3. **Read-Only Context（参照仕様・Knowledge 箇所）**
4. **Acceptance Criteria（定量 DoD・必須テスト）**
5. **Escalation Rule（2-Strike 時は `tools/ask_advisor.py` で顧問相談）**
