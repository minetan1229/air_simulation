# 物理ロードマップ — 低Re・パネル法・空力弾性

本ドキュメントは将来計画である。現行の正規実装は `docs/PHYSICS_MODELS.md` および `docs/SPECIFICATION.md` に存在する（数式の重複は行わない）。

## 優先度

| ID | 項目 | 優先度 |
|---|---|---|
| R-P1 | 低Re LSBテーブル補間（ロジスティック置換） | P1 直接対応 |
| P-P2 | 低次パネル法/VLM マルチフィデリティ最終確認 | P2 GA後 |
| A-P3 | 薄紙相当ねじり剛性ビーム + 発散 | P3 将来 |

## R-P1: レイノルズ数依存性

現状: `physics/aero_kernel.py::_log_Re` + 5係数（Re 1e3–1e5、≥1e5でゼロ）。
検証済み: `tools/check_aero_kernel_parity.py` 0.0、`test_low_re_aerodynamics.py` + `test_issue68_reynolds_audit.py` 26件合格。
次ステップ: 単調立方テーブル（Pelletier & Mueller Fig. 5 データ）を `cl_alpha, alpha_stall, cl_max, cd0` に適用。外挿クランプは `_log_Re` と同一（包絡外でフラット）。受入基準: テーブル vs ロジスティック最大差分 <2%、parity 0.0 維持。出典: Pelletier & Mueller 2000; Sunada et al. 2002.

## P-P2: 低次パネル法 (VLM)

コスト: `O(N^2)` 濃密影響行列解法。N=200パネル → 4e4要素、CPUで ~ms；N=2000 → 4e6要素、~0.1–1 s。GA（≥1e4評価）では禁止的。
提案: マルチフィデリティ — GAは現行6DoF/Helmboldカーネルで実行、パネル/VLMは**最終エリートのみ**（上位 K≤20）にクロスチェックとして実行。
GA適合度は変更しない。パネルコードは `aero_kernel` 境界条件を呼び出し、重複定義しない。受入基準: エリート `|ΔCL|<0.05`、`|ΔCD|<0.005`。

## A-P3: 空力弾性 — 相当ねじり剛性ビーム

現状: 準定常モーフィング + `aeroelastic_flex/twist/compliance` ゲイン（未検証の経験的値）。
次ステップ: 各翼片を片持ちビームとしてモデル化、`GJ = E·J`、`J ≈ (b·t^3)/6`（薄肉閉断面/キール断面近似）、モーダル打ち切り（第1曲げ + 第1ねじり）、準定常発散チェック `q_div ~ GJ / (e·S·c^2)`。実測 `E_MD/E_CD` および折り目断面 `J` の計測が必要 — 定数を捏造しない（AGENTS §2）。受入基準: フラッター発生は予測しない（準定常のみ）；発散マッハ数/Reは $M<0.05$ で適用不能として報告。出典: Bisplinghoff & Ashley 1962.
