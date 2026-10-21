# Agent Core Protocol（常駐コア + 参照索引）
<!--
正本: 本規約=常駐最小 / .agents/Knowledge.md=過去知見 / .agents/Project.md=現在地・LoopState / .agents/skills/*=定型手順 / docs/SPECIFICATION.md=詳細仕様
常駐は本ファイルのみ。詳細・手順・過去知見は .agents/ から遅延オンデマンド参照（全文読込禁止）。
-->

## 0. 参照マップ（.agents 連携ハブ）
| 目的 | 参照先（正本） | 読み方・連携トリガー |
|---|---|---|
| 現在地・ゴール・Loop State | `.agents/Project.md` | §1〜§3（毎ループPhase 1で確認・Phase 5で更新） |
| 過去知見・既知不具合・回避策 | `.agents/Knowledge.md` | キーワードgrep抽出（全文読込禁止・確定事実追記） |
| 段階的検証 & CI/CD 手順 | `.agents/skills/verify-and-test.md` | Phase 4, 6 実行時（テスト順序・Git・CI確認） |
| Discord Webhook 通知 | `.agents/skills/notify-webhook.md` | フェーズ開始/完了/警告時（カラー・レポート連携） |
| サブエージェント役割分担 | `.agents/skills/subagent-delegation.md` | 並列作業・委譲時（Tier分離・担当境界表） |
| 常駐プロセスの使用中判定と回収 | `.agents/skills/process-hygiene.md` | メモリ圧迫・残存タスク確認時（`tools/process_hygiene.py`） |
| コンテキスト圧縮・状態引き継ぎ | `.agents/skills/loop-handover.md` | コンテキスト肥大・セッション終了時 |
| 詳細物理・API・アーキテクチャ | `docs/SPECIFICATION.md` | 実装・設計変更時（該当章のみ抽出） |
| ツール・タスク別スキル / MCP | `skill/README.md` / `.kilo/kilo.jsonc` | 該当スキル利用時 / MCP利用時 |

---

## 1. Core Protocol（自律ループ連携）
- **1.1 コンテキスト最小化**: 常駐は本ファイルのみ。`.agents/` 配下は必要時のみ部分抽出。ログ/ソースの全文出力禁止。`memory` MCPは検索索引（長文二重保管禁止）。
- **1.2 テキサスの狙撃兵の誤謬排除**: 事前に仮説と反証可能な合否基準を明文化。後付け変更や偶然動作での完了認定を禁止。不一致時は仮説棄却・因果特定。
- **1.3 自律ループ連携規約**:
  1. *Observe*: `.agents/Project.md` の Loop State 確認。残タスク無ければPhase 6へ。あれば最小サブタスク定義。
  2. *Hypothesize*: 事前仮説・合否基準を宣言。
  3. *Execute*: 最小変更・検証を1つ実行（不可逆操作は先行記録、単一コミッター原則）。
  4. *Verify*: 合否照合（`.agents/skills/verify-and-test.md`）。不一致時はロールバックしPhase 2へ（上限3回）。
  5. *Distill & Loop*: 確定事実は `.agents/Knowledge.md`、現在地は `.agents/Project.md` へ記録。肥大時は `.agents/skills/loop-handover.md` 実行。
  6. *Finalize & Terminate*: ローカル全通過 → 対象ブランチへマージ&push（既定は master。`genetic-project` フェーズは §2 ブランチ隔離に従い master 宛の操作を禁止）→ CI完了確認 → Issue確認クローズ後HALT。
- **1.4 規約統治**: ユーザー明示指示・監査合意なき本ファイル改変禁止。仕様は `docs/SPECIFICATION.md`、知見は `.agents/Knowledge.md` に集約。
- **1.5**: 値は物理的な値を使用し、意図的に調整された値にならないようにする
---

## 2. Guardrails（常駐制約）
- **Strict Rules**: ユーザー明示許可なきファイル削除禁止。事前仮説・合否基準なしの変更/実行禁止。偶然動作での完了禁止。ファイル/ログの全文読込・出力禁止。
- **Governance**:
  - *Zero-Placeholder*: `TODO`・`...`・空関数等の省略完全禁止。実動コードのみ記述。
  - *Single Committer*: サブエージェント委譲時もリポジトリ変更・コミットは親に一元化（`.agents/skills/subagent-delegation.md`）。
  - *Branch Isolation*: `genetic-project` 開発フェーズ（自律進化研究システム構築中）では `master`/`main` へのコミット・マージ・チェリーピック・PR作成を**厳禁**。`tools/branch_guard.py` が保護ブランチ実行を停止し、テストスイート（`conftest.py`）/ `app.py` / `smoke_test.py` に組み込み済み（`PAPER_AIRCRAFT_BRANCH_GUARD=warn|off` で緩和可）。
  - *Git & Discord*: ブランチ運用、フェーズ毎通知（`.agents/skills/notify-webhook.md`）。
- **Critical Traps（詳細・回避策は `.agents/Knowledge.md` 参照）**:
  - *CI Runner*: GitHub Actions の run は `defaults: run: shell: bash` 必須。
  - *文字コード*: PowerShell (`Get/Set-Content`) でのUTF-8破壊禁止。Python/専用ツール編集徹底。
  - *座標系*: 風場(+z上向き)と世界NED(+z下向き)の符号変換（$w_{NED}=-w_{wind}$）遵守。
  - *風高度規約*: 風は**地表面からの高度**（`z_up = -z_ned`）でサンプルする。6DoF参照経路とGPUバッチ経路は同一規約（詳細: `.agents/Knowledge.md`「風高度規約」）。
  - *境界/接地*: 水平100km制限、地中即死(`min_alt<-0.05`)、CCD補間、翼端散逸上限適用。
  - *評価status*: 失敗した評価を`timeout`/`crashed`等へ**リラベル**しない（パレート前線・incumbentへの混入＝報酬ハッキング経路。詳細: `.agents/Knowledge.md`「Infeasible Status Forwarding」）。
  - *GAの既定値*: 試行回数・目的関数の既定値は`physics/ga_config.py`へ集約し、探索ロジックにハードコードしない（詳細: `.agents/Knowledge.md`「GA Research Engine」）。
  - *既知の物理乖離*: 監査の進捗表は`PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §0.0（P0〜P3は完了）。残る乖離は同§2〜§5と`no-finish.md`が正本。

---

## 3. コマンド早見（常駐）
| 目的 | コマンド / 手順正本 |
|---|---|
| 構文 / 静的解析・型 | `python -m compileall physics api tools app.py smoke_test.py` / `ruff check .` / `mypy physics api app.py smoke_test.py` |
| 単体・監査 / E2E | `python -m pytest tests -q` / `python smoke_test.py`（詳細: `.agents/skills/verify-and-test.md`） |
| Discord通知 | `python tools/notify_discord.py <status> "<title>" "<summary>"`（詳細: `.agents/skills/notify-webhook.md`） |
| MCP確認 / 呼出 | `python tools/verify_mcp_servers.py` / `python tools/mcp_call.py <server> <tool> --arg k=v` |
| CI点検 / Issue | `python tools/check_runs.py` / `python tools/check_issues.py` / `close_resolved_issue.py` |
| 常駐プロセス回収 | `python tools/process_hygiene.py --list` / `--apply`（判定基準: `.agents/skills/process-hygiene.md`） |
| UI検証 / 物理診断 | `python tools/capture_ui.py` (実ブラウザはplaywright MCP) / `tools/analyze_glide_trim.py` 他 |

---

## 4. 正本維持規約（.agents 同期ルール）
- **配置**: 過去確定事実→`.agents/Knowledge.md` / 現在地・進捗→`.agents/Project.md` / 定型手順→`.agents/skills/` / 詳細仕様→`docs/SPECIFICATION.md`。
- **行数上限**: 本ファイル 80行以内 / `Knowledge.md` 80行以内 / `Project.md` 40行以内。
- **重複排除原則**: `.agents/` や `docs/` に正本がある内容（手順・役割分担表・詳細知見・物理定数）は本ファイルに直書きせず、該当ファイルへの参照リンクに留める。

---

## 5. MCP 使い分け（基盤: `.kilo/kilo.jsonc`）
1. `context7`: ライブラリ/外部API実装前の最新一次仕様取得。
2. `sequential-thinking`: 6DoF数値破綻・接地散逸・姿勢発散等の複雑な物理バグ・多段推論。
3. `fetch`: 論文・外部技術Markdown取得（内部IP遮断/robots.txt準拠）。
4. `memory`: セッション跨ぎ知識の検索索引（正本は `.agents/Knowledge.md`、長文二重保管禁止）。
5. `playwright`: UI/Three.jsの実ブラウザ描画・コンソールエラー検証。
※定型処理（ファイル編集、pytest等）にMCPを浪費しないこと。失敗時は `tools/verify_mcp_servers.py`。
