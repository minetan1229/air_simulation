#!/usr/bin/env python3
"""
Restore Japanese markdown documentation from git history before commit 6fe9d03,
while preserving the English versions as *.en.md for mkdocs.en.yml.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DOC_FILES = [
    "docs/getting-started.md",
    "docs/usage.md",
    "docs/troubleshooting.md",
    "docs/architecture.md",
    "docs/development.md",
    "docs/api.md",
    "docs/VALIDATION.md",
    "docs/CFD_ROADMAP.md",
    "docs/discord-bridge.md",
]


def git_show(commit_ref: str, path: str) -> str:
    cmd = ["git", "show", f"{commit_ref}:{path}"]
    return subprocess.check_output(cmd, cwd=str(ROOT), text=True, encoding="utf-8")


def main() -> None:
    print("[*] Starting Japanese documentation restoration...")

    # 1. Standard doc files
    for doc_rel in DOC_FILES:
        doc_path = ROOT / doc_rel
        en_path = doc_path.with_name(doc_path.stem + ".en.md")

        if doc_path.exists():
            current_en_text = doc_path.read_text(encoding="utf-8")
            if not en_path.exists():
                en_path.write_text(current_en_text, encoding="utf-8")
                print(f"  [+] Saved English version to {en_path.relative_to(ROOT)}")

        # Restore Japanese version from 6fe9d03~1
        try:
            ja_text = git_show("6fe9d03~1", doc_rel)
            doc_path.write_text(ja_text, encoding="utf-8")
            print(f"  [✓] Restored Japanese {doc_rel} ({len(ja_text.splitlines())} lines)")
        except Exception as err:
            print(f"  [!] Failed to restore {doc_rel}: {err}")

    # 2. SPECIFICATION.md
    spec_path = ROOT / "docs/SPECIFICATION.md"
    spec_en_path = ROOT / "docs/SPECIFICATION.en.md"
    if spec_path.exists() and not spec_en_path.exists():
        spec_en_path.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
        print("  [+] Saved English version to docs/SPECIFICATION.en.md")

    # For SPECIFICATION.md, restore Japanese from 6fe9d03~1, and ensure Section 8.1 (milcy) is included
    ja_spec = git_show("6fe9d03~1", "docs/SPECIFICATION.md")
    milcy_section = """
## 8.1 milcy アルゴリズム (milcy Morphogenetic Quality-Diversity Search Architecture)

旧 v3 パイプラインとして開発された形態創発アルゴリズムの正式体系。遺伝的アルゴリズム（GA）の世代淘汰型アプローチとは異なり、高品質かつ多様な機体群を同時発見する **MAP-Elites（多次元品質多様性探索）** を基軸とする。

### 主要構成要素
1. **20工程パラメトリック折り紙文法 (MCTS Origami)**:
   - `geometry.milcy_origami`（互換 `geometry.v3_origami`）
   - モンテカルロ木探索（MCTS）を用いて有効な折り紙構造（折り線、角度、重なり順、持ち手・把持条件）を自律生成。
2. **3D MAP-Elites 特徴空間アーカイブ (Quality-Diversity)**:
   - `optimization.map_elites`
   - 翼面荷重（Wing Loading）、静安定余裕（Static Stability Margin）、アスペクト比（Aspect Ratio）の 3 軸から成る 1,000 ニッチ（10×10×10）グリッド上にエリート機体を保持・更新。
3. **連続滑空トリム最適化 (Nelder-Mead Local Trim)**:
   - `optimization.trim`
   - エリート個体ごとにエレボン角（-4°〜+8°）および上反角（0°〜8°）を局所シンプレックス法で最適化し、最大揚抗比と滑空平衡角を自動チューニング。
4. **多次元頑健性ストレステスト (Robustness Grid)**:
   - `optimization.eval`
   - Dryden 乱流（MIL-F-8785C）および投下擾乱下でのシミュレーションを行い、頑健性スコアを算出して最終エクスポート（.obj, .json, .jsonl）。

---
"""
    if "## 8.1 milcy" not in ja_spec:
        # Insert before Section 9
        if "## 9. 検証コマンドと CI/CD ガイド" in ja_spec:
            parts = ja_spec.split("## 9. 検証コマンドと CI/CD ガイド", 1)
            ja_spec = parts[0] + milcy_section + "\n## 9. 検証コマンドと CI/CD ガイド" + parts[1]
        elif "## 9." in ja_spec:
            parts = ja_spec.split("## 9.", 1)
            ja_spec = parts[0] + milcy_section + "\n## 9." + parts[1]
        else:
            ja_spec += "\n" + milcy_section

    spec_path.write_text(ja_spec, encoding="utf-8")
    print(f"  [✓] Restored Japanese docs/SPECIFICATION.md with milcy Section 8.1 ({len(ja_spec.splitlines())} lines)")

    # 3. README.md
    readme_path = ROOT / "README.md"
    ja_readme = git_show("6fe9d03~1", "README.md")
    readme_path.write_text(ja_readme, encoding="utf-8")
    print(f"  [✓] Restored Japanese README.md ({len(ja_readme.splitlines())} lines)")

    print("[✓] Restoration finished successfully.")


if __name__ == "__main__":
    main()
