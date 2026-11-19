---
name: kilo-subagent
description: Kilo Code CLIをサブエージェント（ワーカー）として並列起動・委譲し、調査・コード生成・バッチタスク・フォールバックを実行する際に使用する。
---

# Skill: Kilo Code Subagent (Kilo Code CLI ワーカー連携)

Kilo Code CLI（`kilo`）を Antigravity のサブエージェント（Tier 2 ワーカー）として並列実行・委譲するための手順およびツールガイド。

---

## 1. 概要と役割分担 (Tier-2 Worker)

- **親（Antigravity / Main Agent）**:
  - タスクの策定（Plan & Todo）、プロンプトの設計、並列起動の管理、成果物の統合・コード監査・Git コミット（単一コミッター原則）。
- **子（Kilo Code CLI ワーカー）**:
  - 文献・学術調査、コード断片の生成、定型テストの量産、長文ドキュメントドラフトの執筆など。
  - 主なモデル: `kilo/nvidia/nemotron-3-ultra-550b-a55b:free`（規定無料モデル）、`kilo/minimax/minimax-m3.1-flash:free`、`kilo/kilo-auto/free`。

---

## 2. 実行ツール: `tools/kilo_subagent.py`

### 2.1 主なコマンド例

1. **環境と CLI パスの確認**:
   ```bash
   python tools/kilo_subagent.py --check
   ```

2. **利用可能モデルの一覧**:
   ```bash
   python tools/kilo_subagent.py --list-models
   ```

3. **直接プロンプトを投げて結果をファイル出力**:
   ```bash
   python tools/kilo_subagent.py "低レイノルズ数における空力ヒステリシスについて10行で要約して" \
     -m "kilo/nvidia/nemotron-3-ultra-550b-a55b:free" \
     -o "docs/research/sample_summary.md"
   ```

4. **プロンプトファイルから投入 (長文調査タスク)**:
   ```bash
   python tools/kilo_subagent.py -p prompts/survey_task.txt -o docs/research/survey_result.md -v
   ```

5. **JSON 出力（プログラムやスクリプト連携用）**:
   ```bash
   python tools/kilo_subagent.py "Hello" --json
   ```

6. **セッション継続（マルチターン・対話コンテキスト維持）**:
   ```bash
   python tools/kilo_subagent.py "前回の回答の2点目を詳しく解説して" -s <session_id>
   ```

---

## 3. Python スクリプトからの直接呼出

```python
from tools.kilo_subagent import execute_kilo_worker

result = execute_kilo_worker(
    prompt="薄翼理論における失速角の推算式を解説してください。",
    model="kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    verbose=True
)

if result["success"]:
    print("出力結果:")
    print(result["text"])
    session_id = result["session_id"]
else:
    print("エラー:", result["error"])
```

---

## 4. サブエージェント無制限並列起動の運用手順

1. **タスクの分解**:
   - 調査や生成タスクをファイル単位またはトピック単位（例: Domain 1, Domain 2, ...）に分割。
2. **バックグラウンド並列実行**:
   - Antigravity の `run_command`（`IsDaemon: false`, `WaitMsBeforeAsync: 1000`）を用いて、複数ワーカーを同時にバックグラウンド起動。
3. **成果物監査 & 親コミット**:
   - 各ワーカーが出力した Markdown やコードを親エージェントが `view_file` で確認・検証（テキサスの狙撃兵の誤謬排除、プレースホルダー不在確認）。
   - 親エージェントが一元的にリポジトリへ反映・コミット。

---

## 5. 認証（初回のみ）

Kilo Code CLI で無料モデルを含むタスクを実行する際、未認証の場合は:
```bash
kilo auth login
```
を実行し、プロバイダまたは Kilo アカウントのログイン（無料登録）を完了させてください。
ログイン状況は `kilo auth list` で確認可能です。
