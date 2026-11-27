---
name: escalation-advisor
description: 不明点・物理数理の確信不足・横断設計迷い・2回連続の検証失敗が発生した際に、Tier 3技術顧問（Opus 4.6 または Hydrogen XHigh）へ構造化質問を行い、回答をもとに根本修正を進める手順。
---

# Skill: Escalation to Principal Advisor (技術顧問への相談プロトコル)

## Trigger（発火条件 — 1つでも満たしたら即発火）
1. **2-Strike Rule**: 同一のバグ修正・テスト合格・収束改善に 2回連続で失敗した。
2. **物理・数理の確信不足**: 6DoF・空力・保存則・2.5D折り紙幾何の数式や符号・境界条件に確信が持てない。
3. **横断設計・契約変更**: 複数モジュール（`core/`, `physics/`, `geometry/`, `optimization/`, `api/`）を跨ぐ判断が必要。
4. **Watchdog 差し戻し2回**: Phase 4 の差し戻しが2回に達し、根本方針の再検討が必要。
5. **高コスト仕様分岐**: フルCFD導入など計算コスト・近似範囲（P2）に関わる判断が必要。

※単純な構文エラー・タイポ・定型テスト作成に Tier 3 顧問を消費しないこと。

## Procedure
1. **重複質問の事前防止**: `.agents/Knowledge.md` と `.agents/consultations/` をキーワード検索し、既回答の論点でないか確認する。
2. **構造化質問票の送信**:
   ```bash
   python tools/ask_advisor.py \
     --model opus4.6 \
     --topic "<slug>" \
     --context "<対象ファイル:行番号と前提>" \
     --question "<判断してほしい問い>" \
     --attempts "<既に試したことと失敗結果>"
   ```
   （物理数理の深掘りや Opus 4.6 混雑時は `--model hydrogen-xhigh` を指定。失敗時は自動でもう一方へフォールバックし、`.agents/consultations/` にログ保存される。）
3. **根本修正への反映 & 資産化**: 顧問の `Decision`・`Acceptance Criteria` を現行 Plan へ反映して実装・検証し、確定知見を `.agents/knowledge.d/<YYYYMMDD>_<session>.md` へ 1〜2行で出力する。
