---
name: redteam-audit-loop
description: LLMの目視全件スキャンを廃止し、3層ベンチマーク＋milcy Red-Teamingループで5観点サニティ異常機体のみを隔離・解剖し、悪役ベンチマーク資産化と最低探索バジェット付きFREEZE_PASSで収束させる手順。
---

# Skill: Red-Teaming Audit Loop & Freeze Convergence

## 原則（5つの核心ルール）
1. **目視全件スキャン禁止**: コード全件を LLM に「問題点を深掘りしろ」と投げない（P2 の無限掘り起こしになる）。
2. **P0 vs P2 の分離**: 最適化器が悪用できる抜け穴（**P0: エネルギー生成・面積超過・CPU/GPU乖離・微擾乱崩壊・幾何破綻**）を最優先封鎖し、現行準定常モデルの許容近似差（P2）は Non-Goal として凍結する。
3. **Evidence-First**: 全 P0/P1 指摘は必ず最小失敗 `pytest` または悪役機体 JSON（`tests/adversarial_cases/`）とセットで資産化する。
4. **対症療法ペナルティ禁止**: サニティ検知器は「異常機体の隔離と原因特定」に使い、怪しい形状のスコアだけ下げるパッチではなく根本の物理式・幾何制約を修正する。
5. **早期 Freeze 禁止**: `VERDICT: FREEZE_PASS` は必ず最低探索バジェット消化＋全悪役回帰 PASS＋新規 P0/P1 0件の3条件すべてを満たした場合のみ許可する。

## Procedure（3層ベンチマーク＋Red-Teaming ループ）
1. **Layer 1–2（既存回帰・物理監査）**:
   ```bash
   python tools/session_manager.py pytest -- tests/test_adversarial_benchmark.py tests/test_physical_laws_audit.py tests/test_milcy_loopholes_audit.py -q
   ```
2. **Layer 3（`milcy` / GA2 Red-Teaming 探索）**:
   - `tools/gpu_lock.py` 経由で `milcy`（または GA2）を **最低探索バジェット（例: 20世代以上または200個体評価以上）** 回し、上位機体を以下 **5観点サニティ** で自動スクリーニングする：
     1. エネルギー生成（無風無推力で $dE/dt > 0$）
     2. 面積超過（A4 原紙面積超過・幾何自己貫通）
     3. CPU / GPU 評価乖離
     4. 微擾乱崩壊（微小摂動で非物理的発散・極端なスコア崩壊）
     5. 幾何破綻（2.5D 折り整合性・UV 展開破綻）
3. **隔離 & 根本原因解剖**:
   - 5観点サニティに引っかかった上位異常機体のみを隔離 JSON 化し、`team-lead`（難解時は `tools/ask_advisor.py` で Tier 3 顧問）が根本の物理式・制約原因を特定する。
4. **悪役ベンチマーク登録 & 根本修正**:
   - 隔離機体を `tests/adversarial_cases/<case_id>.json` に登録し、最小失敗 `pytest` を作成してから物理式・制約の根本修正を実装・全 PASS を確認する。
5. **Freeze 判定（収束）**:
   - (a) 最低探索バジェットを消化済み、(b) 既存 `tests/adversarial_cases/` 全 PASS、(c) 5観点サニティで新規 P0/P1 異常機体が **0件** の3条件を満たしたら、`qa-watchdog` が **`VERDICT: FREEZE_PASS`** を宣言して現行フェーズを凍結・終了する。

## 実行ハーネス（決定論的コマンド）
```bash
python tools/redteam_sanity_screen.py --input-dir output/v3 --min-budget 20 --json
python tools/company_loop.py --goal "P0抜け穴封鎖" --live --max-iterations 5
```
- Antigravity 内で並列起動する場合は `.agents/workflows/company_loop_workflow.py` を `/run-workflow` から実行する。
