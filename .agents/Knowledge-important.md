# Critical Constraints & Guardrails
<!-- 常駐ファイル: 常に50行以内を維持せよ。古い項目は統合・削除すること。 -->

## Strict Operational Rules
- 事前仮説と合否基準の宣言なしに、コード変更やコマンド実行を行ってはならない。
- 偶然動作した結果をもってタスク完了としてはならない（必ず因果を特定せよ）。
- ファイルやログの全文読み込み・全文出力を禁ずる（行指定・検索を徹底せよ）。

## Project Governance & Technical Guardrails
- **Zero-Placeholder Policy**: `TODO`、`...`、空関数等の記述省略は完全禁止。実動コードのみ記述する。
- **Git Branch Protocol**: 必ずブランチ作成 (`feat/`, `fix/`, `docs/` 等) → ローカル検証 → master マージの順序を遵守する。
- **Discord Notification Protocol**: 計画/開始/委譲/検証/GitHub連携/終了/重要警告のフェーズごとに `tools/notify_discord.py <status_type>` を実行する。完了系（`end`/`finish`/`github`/`commit`/`push`/`fail`）は自動で作業レポート（操作場所一覧・変更数・GitHub連携状況）を添付。
- **2-Tier Subagent Protocol**: 簡易・量産・後修正タスクは量産型 (kilocode: NVIDIA Nemotron 3.5 Super VL)、重要設計・統合はTier 1 (Main/Pro) に分離。単一コミッター（親）が統合検証する。
- **Safe CPU Fallback**: GPU (CuPy) バッチは非GPU環境で安全にNumPy/CPUフォールバックし、返却スキーマをGPU版と完全一致させる。
- **Security & Privacy**: 内部スタックトレースはクライアントに返さず `error_id` のみ返却。外部メッシュURL取込時はSSRFを遮断する。
- **MCP Baseline**: MCP `fetch` はプライベートIP遮断とrobots.txt準拠を維持する（`--allow-private-ips` / `--ignore-robots-txt` 禁止）。ライブラリAPI・仕様は記憶ではなく `context7` で一次情報から取得する。


## Critical Traps (昇格された重要知見・地雷リスト)
- CI環境（Linux/Windows）のRunnerシェル差分: GitHub Actionsのrunステップでは `defaults: run: shell: bash` の明示が必須。
- 座標系変換: 風場は+z上向き、世界NEDは+z下向き。受け渡し境界での符号変換（$w_{\text{NED}} = -w_{\text{wind}}$）を必ず維持せよ。
- 3D空間境界と接地: 水平100km制限(`HARD_HORIZONTAL_LIMIT_M`)、地中生成即時クラッシュ(`min_alt < -0.05`)、高速降下CCD補間、翼端接地時の運動エネルギー散逸上限(`E_post <= E_pre * max(rest^2, 0.25)`)を常時適用せよ。
