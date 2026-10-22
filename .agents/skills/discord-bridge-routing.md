# Skill: Discord × Agent Routing (Discord×Agent ルーティング)
## Trigger (読み込み条件)
- Discord からエージェントを起動・継続・中断する時、429/クォータ枯渇/バックエンド異常dealした時、フォールバックを報告する時。

## 1. エージェント体制（サブエージェント上限4）
- メインエージェント1つに対し**稼働するサブエージェントは最大4つ**。超過時の委譲は拒否する。
- **必須1枠 = 監視（Monitor）エージェント**: システム稼働状態・APIレスポンス・エラー状況・レートリミットを常時監視し、異常は**即座に**メインエージェントへ報告・アラート（Discord Webhook＋応答チャンネル）。実務を兼務しない。
- **残り最大3枠 = 実務エージェント**: 実装・検索・Discord応答を担当領域で排他的に分担（境界表: `subagent-delegation.md`）。

## 2. バックエンド（正本: `tools/bridge_backends.py`）
| バックエンド | 実行体 | 識別子 | 既定モデル |
|---|---|---|---|
| `antigravity`（主） | `agy -p ... --output-format stream-json` | 会話UUID | `gemini-3.8-flash-low` |
| `kilo`（副・フォールバック） | `kilo run --format json --auto -- <prompt>` | `ses_*` セッション | `kilo/kilo-auto/free`（**無料**） |

- Kilo Code の既定は**無料モデル**。`/model kilo/<provider>/<model>` で任意モデルへ切替可。
- 実行ファイル解決順: 環境変数（`AGY_CLI_PATH` / `KILO_CLI_PATH`）→ `PATH` → `~/.vscode/extensions/kilocode.kilo-code-*/bin/kilo` → `~/.local/bin/kilo`。

## 3. ルーティング規則（正本: `tools/bridge_router.py`）
1. **主実行**: 要求されたバックエンドを `--output-format stream-json` / `--format json` で駆動し、NDJSON を正規化して Discord のライブ表示へ流す。
2. **429 自動継続**: `TooManyRequests` / `RESOURCE_EXHAUSTED` / `quota exceeded` 等を検出したら**失敗扱いしない**。指数バックオフ（既定 2s→4s→8s、上限60s、最大3回）後に `continue`（Antigravity: `--conversation`/`--continue`、Kilo: `--session`/`--continue`）で**同じ処理を自動再開**する。
3. **フォールバック**: 主バックエンドが APIエラー・タイムアウト・残りレート0・429継続失敗のいずれかで完了不能なら、**処理を中断せず Kilo Code（既定無料モデル）へ切り替え**て完遂する。
4. **報告**: フォールバック発生時は監視エージェントが `fallback` アラートを出し、①Discord 応答チャンネル ②Discord Webhook（メインエージェント宛）の両方へ「Kilo Code へフォールバックした旨」を送る。

## 4. レート制限（クォータ）表示（正本: `tools/bridge_quota.py`）
- 取得コマンド: `agy -p "/quota"`（`group⇥metric⇥N%⇥resetISO`）、`agy -p "/credits"`（残りクレジット）。
- 残りクォータ%: `Weekly Limit Remaining` / `Five Hour Limit Remaining`。週次残量が 1% 以下で**枯渇**と判定し、フォールバックを推奨するアラートを出す。
- 推定利用可能回数: 総量の想定値は置かず、**実測した「送信回数 vs 消費%」の傾き**から算出する（測定が足りなければ `—`）。
- 表示コマンド: `!quota` / `/quota`（`/status` にも要約を出す）。

## 5. 監視エージェント（正本: `tools/bridge_monitor.py`）
- 監視対象: ホスト稼働（CPU/RAM/GPU）、バックエンド実行ファイルの有無、API応答レイテンシ、連続エラー、レート_LIMIT発生数、クォータ残量。
- アラート種別: `backend_unavailable` / `consecutive_errors` / `quota_exhausted` / `fallback`。
- 表示コマンド: `!monitor` / `/monitor`。アラートは Discord 応答チャンネルと Webhook の両方へ送る。

## Verification Criteria (事前合否基準)
- 成功: 429発生時に停止せず、バックオフ後に `continue` で同一処理が再開し、失敗時のみ Kilo Code へ切替して完了する。切替は監視エージェント経由で Discord とメインエージェントへ通知される。
- 失敗: 429 を失敗として確定・応答を破棄する、またはフォールバック時にユーザーへ通知せず中断する。
