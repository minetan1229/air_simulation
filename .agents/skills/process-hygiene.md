# Skill: Process Hygiene（常駐プロセスの使用中判定と回収）
## Trigger (読み込み条件)
- メモリ圧迫・応答遅延・コアダンプ増加の検知時、またはユーザー指示「無駄なタスクを閉じてメモリーを開放」「使用しているタスクも停止”时。
- セッション交代/elier 終了時に残存ワーカー・孤児ツールプールを生前処理したい時。

## Criteria (使用中/不要の判定基準)
判定は4軸（所有・進行・冗長・接続）。**4軸は全て機械的に実測する**（`tools/process_hygiene.py` が実測値を理由文字列に付す）。
| 軸 | 観測方法（実装） | 判定 |
|---|---|---|
| 所有 | `Get-CimInstance Win32_Process` の ParentProcessId を所有セッションテーブル（`kilo serve` / `kilo run --auto` / `supervisor.ps1`）と照合 | 所有者が生存＝使用中候補、所有者不在＝孤児 |
| 進行 | CPU 時間の**2点サンプル差分**（既定窓 3 s。`--progress-window` で変更、0 で無効化） | 差分 > 0.05 s＝進行あり（使用中）、0＝停滞（停止候補） |
| 冗長 | 実行ファイルパスを正規化した**コマンド署名の出現数** | 同一署名が 2 件以上＝冗長（停止対象） |
| 接続 | `netstat -ano`（Windows）/ `/proc/net/tcp`（Linux）の LISTEN・ESTABLISHED を所有者PIDで集計 | LISTEN>0 かつ ESTABLISHED=0＝アイドル常駐（停止対象） |

**ポリシー**（使用中プロセスの扱い。`--policy`）:
- `in-use-stop`（既定）: 進行中（使用中）のプロセスも **STOP に含める**。ユーザー指示「使用しているタスクはやめてほしい」に対応。
- `keep`: 進行中の S4 残存実行物を **KEEP**（安全側の既定）。S1/S2 は停止対象のまま。

### 判定ルール（KEEP / STOP）
- **KEEP**
  - K1 セッション基盤: `kilo.exe serve`（IDE拡張のホスト）。
  - K2 ホスト所有のツールプール: 親が K1 の MCP プロセス（使用時に自動再生成される）。**MCP マーカーは区切り文字を正規化して照合**（Windows の `@playwright\mcp\cli.js` も MCP プールと判定）。配下の chromium も K2 継承＝保護。
  - K3 明示保護: `--keep-pid` / `--keep-since` 指定。
- **STOP**
  - S1 並列ワーカー: `kilo run --auto` のワーカープロセス。
  - S2 オーケストレータ: `supervisor.ps1` / `run-worker.ps1` / `pipeline-check.ps1`。
  - S3 孤児ツールプール: 所有ワーカーが停止対象になった MCP プロセス群。
  - S4 残存実行物: `pytest` / `run_ga_research.py` / 一時スクリプト / `app.py` 開発サーバ / 所有セッション終了済みMCP。
  - S5 孤児ブラウザ: `ms-playwright` 配下の chromium（ユーザー実ブラウザ `chrome.exe`/`msedge.exe` は対象外）。**MCP プール所有のブラウザは S5 ではなく K2**。
- **注**: `--policy in-use-stop`（既定）により S1/S2 だけでなく進行中の S4 も STOP に含める（成果物はディスク上に残るため中断可能）。

## Procedure (最小手順)
1. 判定: `python tools/process_hygiene.py --list`（KEEP/STOP と理由・4軸の実測値・WS一括表示）
2. 事前記録: 停止対象PIDと理由を `.agents/Knowledge.md` へ追記（不可逆操作の先行記録）
3. 回収: `python tools/process_hygiene.py --apply --json`
4. 検証: 同コマンドの `--list` 再実行で STOP=0、VSCode/IDE が無傷、MCP が再生成可能なことを確認

## Verification Criteria (事前合否基準)
- 成功: 停止対象 0 件 / 解放メモリが停止前の STOP 合計 WS と一致 / `kilo serve` と IDE が生存 / 必要 MCP は次回呼び出しで再生成。
- 失敗: IDE または `kilo serve` が停止、STOP 判定が解消しない、WS 解放量が期待値を下回る。
- 禁止: ユーザー実ブラウザ・IDE・`kilo serve` の停止。`git` 実行中のプロセスの停止。**自分自身の祖先プロセスの停止**（ツールが自動除外）。
- 回帰固定化: `tests/test_process_hygiene.py`（4軸の実測・ポリシー・所有継承・自己保護の固定化）。
