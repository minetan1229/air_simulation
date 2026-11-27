# optimization/AGENTS.md — 最適化・milcy・GA2 不変条件

## P0 不変条件（最優先封鎖）
- **評価リラベル禁止**: 失敗・非実行可能評価を `timeout`/`crashed` 等へリラベルしてパレート前線・incumbent へ混入させない。
- **極値解保護 & 設定集約**: パレート境界極値解（`math.inf`）を保護し、試行回数・目的関数既定値は `physics/ga_config.py` に集約（ハードコード禁止）。
- **Red-Teaming 原則**: サニティ検知器は「異常機体の隔離・原因特定」専用とし、怪しい形状の報酬だけ削る対症療法ペナルティを禁止。
- **GPU 排他**: GPU バッチ評価・GA2・milcy 実行は必ず `tools/gpu_lock.py` 経由で行う。

## Non-Goals（P2 凍結・許容近似）
- サロゲート探索中の微細なハイパーパラメータ最適化やフル CFD 結合最適化は、P0 抜け穴封鎖完了後の別タスクとする。

## 検証コマンド
```bash
python tools/session_manager.py pytest -- tests/test_optimizer.py tests/test_ga2_runner.py tests/test_milcy_loopholes_audit.py -q
```
