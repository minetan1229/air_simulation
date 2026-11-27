# AGENTS.md — air_simulation（v3.1: 3層並列ループ × Red-Teaming監査 × Freeze収束）
<!--
根拠: 肥大・概要記載は推論コスト+20%超 (arXiv:2602.11988) / 簡潔なコマンド・DoDは実行時間-28.6% (arXiv:2601.20404) /
タスク適合ルーティング＋中央検証がエラー伝播を抑止 (arXiv:2512.08296) / 最低探索バジェット付きFreeze宣言とRed-Teaming反転でP0収束。80行以内厳守。
-->

## 1. タスクルーティング & 3層エージェント体制
- **単体実行（逐次・小規模・強依存）**: 1〜2ファイル修正や逐次デバッグは Main 単体で最小変更・検証（過剰協調を回避）。
- **会社組織型並列（分解可能・マルチドメイン）**: 下表3層で実行し、全成果物を中央ゲート（Main + Watchdog）で検証。

| 階層・役職 | モデル / Effort | 主責務（DoD指向） | 禁止事項 |
|---|---|---|---|
| **Tier 1: Main（経営・PM）** | Antigravity `gemini-4-hydrogen` (`medium`) | 全体Plan・方針・排他境界指示・中央検証・単一コミッター・Freeze判定 | 分解済みタスクの微細実装介入 |
| **Tier 2: `team-lead`（現場）** | Subagent `gemini-4-hydrogen` (`low`) | WBS分解・タスク別Plan/方針/根本修正・単体セルフチェック | 境界外編集・共有状態ファイル直編集 |
| **Tier 2-QA: `qa-watchdog`** | Subagent `gemini-4-hydrogen` (`low`) | 必須1枠。差分監査・P0抜け穴/報酬ハッキング検知・`FREEZE_PASS`判定 | 実務コード実装・根拠なきP2掘り起こし |
| **Tier 3: `principal-advisor`** | `claude-opus-4-6-thinking` / `gemini-4-hydrogen` (`xhigh`) | 2回連続失敗・隔離機体の物理原因特定・横断設計判断のみ回答（読取専用） | 定型作業・直接編集への消費 |
| **Fallback** | Kilo Code CLI（無料モデル） | 429継続失敗・障害時の業務継続 | 監査スキップでの無条件採用 |

## 2. クローズド・ループ & Red-Teaming 監査規約
1. **Sync & Plan**: `git fetch origin` → 隔離 worktree（`tools/session_manager.py start` / `workspace: branch`）で `mission/<slug>[-<issue>]` 作成 → 仮説・DoD・**やらないこと（許容近似）**を明文化。
2. **Red-Teaming 反転監査（目視全件スキャン禁止）**: コード全件をLLM目視監査せず、3層ベンチマーク＋`milcy` をバグファインダーとして回し、**5観点サニティ（エネルギー生成・面積超過・CPU/GPU乖離・微擾乱崩壊・幾何破綻）**に引っかかった上位異常機体のみ隔離・解剖する。
3. **Evidence-First & 根本修正**: 全 P0/P1 指摘は必ず**最小失敗 `pytest` または悪役機体 JSON（`tests/adversarial_cases/`）**とセットで資産化。対症療法ペナルティ（怪しい形状の報酬削減）を禁止し、根本の物理式・制約を修正する。
4. **Escalate (2-Strike) & Freeze 収束**: 2回連続失敗・物理数理疑義は `tools/ask_advisor.py` で Tier 3 顧問へ相談。Watchdog は **「最低 Red-Teaming 探索バジェット消化＋既存 `tests/adversarial_cases/` 全 PASS＋新規 P0/P1 0件」** をすべて満たした場合のみ `VERDICT: FREEZE_PASS` を宣言してループ終了（早期 Freeze 禁止・P2 無限深掘り禁止）。
5. **Distill & Serial Integrate**: 進行中は `.agents/sessions/`・`.agents/knowledge.d/` へ分離出力（共有ファイル直編集禁止）。`master` マージは PR/CI 通過後、直列統合ゲートで `tools/distill_agents_state.py` 実行後に実施。

## 3. P0 vs P2 優先度定義・非標準ガードレール
- **P0（最優先封鎖）vs P2（凍結）**: **P0**＝最適化器が悪用できる抜け穴（エネルギー生成・面積超過・CPU/GPU乖離・微擾乱崩壊・幾何自己貫通・評価リラベル）。**P2**＝現行準定常モデルの許容近似範囲内の精度差（フルCFD/FSIとの微差等）は Non-Goal として現行ループで掘らない。
- **厳格完了・実装安全**: 事前仮説なき変更・現状挙動アサートのみのテスト完了・制約追記のみクローズ・未解決Issueへの重複クローズ・`TODO`/`...`/空関数・無許可削除・`master`直作業を禁止。
- **並列・環境・物理トラップ**: 横断コア（`core/`, `app.py`, `AGENTS.md`）同時1セッション制限。GPU は `tools/gpu_lock.py` 経由、テスト一時領域は `session_manager.py pytest`（OS自動適合）で分離、Flask は動的ポート（`PORT=0`）。CI は `shell: bash` 必須。風場(+z上)⇔NED(+z下)は $w_{NED}=-w_{wind}$・高度 `z_up=-z_ned` 統一。429 は共有冷却（`rate_limit_guard.py`）＋バックオフ継続。

## 4. 検証・運用コマンド（Windows / Linux 両対応）
```bash
python tools/session_manager.py start --slug <name> [--issue <num>]       # 隔離worktree開始
python tools/session_manager.py pytest -- tests -q                        # OS適合basetemp分離pytest
python tools/ask_advisor.py --model opus4.6 --topic "<slug>" --question "<質問>"
python tools/check_agents_hygiene.py && ruff check . && python -m compileall physics api tools app.py
python tools/redteam_sanity_screen.py --input-dir output/v3 --min-budget 20       # 5観点サニティ&Freeze判定
python tools/company_loop.py --goal "<目標>" --dry-run                            # 閉ループ状態機械(--liveで実稼働)
python tools/gpu_lock.py --timeout 600 -- python scripts/smoke_test.py
```

## 5. 段階的開示マップ（Progressive Disclosure — 必要時のみ読込）
| 必要なタイミング | 正本パス |
|---|---|
| 役職別サブエージェント | `.agents/agents/{team-lead,qa-watchdog,principal-advisor}.md` |
| Red-Teaming監査 / 委譲 / 顧問相談 | `.agents/skills/{redteam-audit-loop,subagent-delegation,escalation-advisor}/SKILL.md` |
| 検証 / 回収 / 引継 / 通知 | `.agents/skills/{verify-and-test,process-hygiene,loop-handover,notify-webhook,discord-bridge-routing}/SKILL.md` |
| 階層ドメイン規約（自動読込） | `physics/AGENTS.md` / `geometry/AGENTS.md` / `optimization/AGENTS.md` |
| 閉ループワークフロー / テレメトリ | `.agents/workflows/company_loop_workflow.py` / `.agents/telemetry/loop_history.jsonl` |
| 悪役資産 / 状態 / 知見 / 仕様 | `tests/adversarial_cases/` / `.agents/sessions/` / `.agents/Knowledge.md`(grepのみ) / `docs/SPECIFICATION.md` |
