# Skill Catalog (スキル一覧・インデックス)

本ディレクトリ（`skill/`）は、紙飛行機 3D シミュレーターの開発・運用におけるコンテキスト効率とタスク実行精度を最大化するためのオンデマンド知識・手順モジュール群です。

## 1. 目的と運用方針
- **コンテキスト効率の最大化**: 毎回すべての設計書や手順をロードするのではなく、指示内容に応じたスキル（`SKILL.md`）のみを参照することで、LLMのトークン消費を抑え、高精度な推論を実現します。
- **再現性と安全性の担保**: 各タスクで守るべき「前提条件」「手順（ステップバイステップ）」「制約・禁止事項」を標準化します。

---

## 2. スキル共通フォーマット仕様
各スキルフォルダ直下の `SKILL.md` は以下の YAML Frontmatter および構造を持ちます：

```markdown
---
name: [スキル識別名 (kebab-case)]
description: [このスキルをいつ読み込むかのトリガー条件・概要]
prerequisites:
  - [前提となるファイル、ライブラリ、環境設定]
---

# [Skill Title]

## 1. Prerequisites (前提条件)
...
## 2. Trigger Conditions (発火条件)
...
## 3. Step-by-Step Workflow (実行手順)
...
## 4. Constraints & Prohibitions (制約・禁止事項)
...
```

---

## 3. スキル一覧 (Skill Index)

| スキル名 | 格納先 | 主な対象タスク・発火条件 | 状態 |
| :--- | :--- | :--- | :---: |
| **`verification-pipeline`** | [`skill/verification-pipeline/SKILL.md`](./verification-pipeline/SKILL.md) | 構文チェック、E2Eスモークテスト、Pytest実行、CI検証 | **実装済 (コア)** |
| **`flight-physics-tuning`** | [`skill/flight-physics-tuning/SKILL.md`](./flight-physics-tuning/SKILL.md) | 6DoF力学、空力モデル診断、モーフィング調整、プリセット評価 | **実装済 (コア)** |

---

## 4. スキル利用のクイックリファレンス
- **コード変更後の検証**: `skill/verification-pipeline/SKILL.md` をロードして Step 1〜Step 5 を実行。
- **物理・飛行の違和感調査**: `skill/flight-physics-tuning/SKILL.md` をロードして診断スクリプトを実行。
