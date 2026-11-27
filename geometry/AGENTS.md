# geometry/AGENTS.md — 折り紙幾何・2.5Dカーネル不変条件

## P0 不変条件（最優先封鎖）
- **Correct-by-Construction**: `geometry/v3_origami/kernel_25d.py` による2.5D逐次折りにおいて、中心線突破 X字交差・全幅エレボンスライス・空中キャンバー水増し・機首横腹貫通・A4原紙面積超過を幾何構造レベルで禁止。
- **バイレベル分離**: 人間工学的大工程（`MacroFoldStep`）と物理計算用原子ステップ（`Atomic FoldStep`）の整合性を維持し、展開図 UV 出力と 3D メッシュが乖離しないこと。
- **対症療法禁止**: 破綻形状をスコア減点だけで弾かず、折り演算・制約条件の根本を修正する。

## Non-Goals（P2 凍結・許容近似）
- 紙厚の3D有限要素大変形・微視的繊維異方性・湿気カール等のミクロ力学再現は現行ループの Non-Goal とする。

## 検証コマンド
```bash
python tools/session_manager.py pytest -- tests/v3/ tests/test_origami_genome.py tests/test_origami_geometry_audit.py -q
```
