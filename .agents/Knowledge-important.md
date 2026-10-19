# Critical Constraints & Guardrails
<!-- 常駐ファイル: 常に50行以内を維持せよ。古い項目は統合・削除すること。 -->

## Strict Operational Rules
- 事前仮説と合否基準の宣言なしに、コード変更やコマンド実行を行ってはならない。
- 偶然動作した結果をもってタスク完了としてはならない（必ず因果を特定せよ）。
- ファイルやログの全文読み込み・全文出力を禁ずる（行指定・検索を徹底せよ）。

## Project Governance & Technical Guardrails
- **Zero-Placeholder Policy**: `TODO`、`...`、空関数等の記述省略は完全禁止。実動コードのみ記述する。
- **Git Branch Protocol**: 必ずブランチ作成 (`feat/`, `fix/`, `docs/` 等) → ローカル検証 → master マージの順序を遵守する。
- **Discord Notification Protocol**: タスク開始・終了・重要警告時に `tools/notify_discord.py` を実行する。
- **2-Tier Subagent Protocol**: 簡易・量産・後修正タスクは量産型 (kilocode: NVIDIA Nemotron 3.5 Super VL)、重要設計・統合はTier 1 (Main/Pro) に分離。単一コミッター（親）が統合検証する。
- **Safe CPU Fallback**: GPU (CuPy) バッチは非GPU環境で安全にNumPy/CPUフォールバックし、返却スキーマをGPU版と完全一致させる。
- **Security & Privacy**: 内部スタックトレースはクライアントに返さず `error_id` のみ返却。外部メッシュURL取込時はSSRFを遮断する。


## Critical Traps (昇格された重要知見・地雷リスト)
- CI環境（Linux/Windows）のRunnerシェル差分: GitHub Actionsのrunステップでは `defaults: run: shell: bash` の明示が必須。
- 座標系変換: 風場は+z上向き、世界NEDは+z下向き。受け渡し境界での符号変換（$w_{\text{NED}} = -w_{\text{wind}}$）を必ず維持せよ。
