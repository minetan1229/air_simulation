import re
from pathlib import Path

def build_master_glossary():
    header = """# 高校生向け航空・計算科学大用語集（完全版：全281項目）

> **この用語集について**:
> 紙飛行機シミュレーターや最先端の航空宇宙工学・計算物理・最適化AIで使われる重要専門用語（全281項目）を、高校生向けに直感的な例えと厳密な物理・数学的定義（数式）の両方を交えて徹底解説した大辞典です。
> すべての数式は MathJax / KaTeX（$ ... $ または $$ ... $$）で正常に描画されます。

---

## 目次

1. [【流体力学・空気力学・微小航空（MAV）】（項目 1〜86）](#1-流体力学空気力学微小航空mav項目-186)
2. [【飛行力学・運動力学・姿勢制御（6DoF）】（項目 87〜143）](#2-飛行力学運動力学姿勢制御6dof項目-87143)
3. [【大気・風場・環境物理】（項目 144〜155）](#3-大気風場環境物理項目-144155)
4. [【数値計算・計算物理・シミュレーション数学】（項目 156〜193）](#4-数値計算計算物理シミュレーション数学項目-156193)
5. [【構造力学・材料工学・紙の物理】（項目 194〜222）](#5-構造力学材料工学紙の物理項目-194222)
6. [【最適化・進化計算・AI・探索アルゴリズム】（項目 223〜264）](#6-最適化進化計算ai探索アルゴリズム項目-223264)
7. [【計算幾何学・折り紙数理・3Dグラフィックス】（項目 265〜281）](#7-計算幾何学折り紙数理3dグラフィックス項目-265281)

---
"""

    part1 = Path("aerodynamics_glossary.md").read_text(encoding="utf-8").strip()
    part2 = Path("flight_dynamics_glossary.md").read_text(encoding="utf-8").strip()
    part3 = Path("physics_glossary.md").read_text(encoding="utf-8").strip()
    part4 = Path("optimization_geometry_guide.md").read_text(encoding="utf-8").strip()

    # Part 1: Clean up heading if needed
    p1_body = re.sub(r"^###\s*【.*?】\s*\n+", "", part1)

    # Part 2: Contains both 6DoF and Atmosphere
    p2_split = re.split(r"###\s*【大気・風場・環境物理】", part2)
    p2_6dof = re.sub(r"^###\s*【.*?】\s*\n+", "", p2_split[0]).strip()
    p2_atmo = p2_split[1].strip() if len(p2_split) > 1 else ""

    # Part 3: Contains both Numerical and Structure
    p3_split = re.split(r"###\s*【構造力学・材料工学・紙の物理】", part3)
    p3_num = re.sub(r"^###\s*【.*?】\s*\n+", "", p3_split[0]).strip()
    p3_struct = p3_split[1].strip() if len(p3_split) > 1 else ""

    # Part 4: Contains both Optimization and Geometry
    p4_split = re.split(r"###\s*【計算幾何学・折り紙数理・3Dグラフィックス】", part4)
    p4_opt = re.sub(r"^###\s*【.*?】\s*\n+", "", p4_split[0]).strip()
    p4_geom = p4_split[1].strip() if len(p4_split) > 1 else ""

    sections = [
        "## 1. 【流体力学・空気力学・微小航空（MAV）】（項目 1〜86）\n\n" + p1_body,
        "## 2. 【飛行力学・運動力学・姿勢制御（6DoF）】（項目 87〜143）\n\n" + p2_6dof,
        "## 3. 【大気・風場・環境物理】（項目 144〜155）\n\n" + p2_atmo,
        "## 4. 【数値計算・計算物理・シミュレーション数学】（項目 156〜193）\n\n" + p3_num,
        "## 5. 【構造力学・材料工学・紙の物理】（項目 194〜222）\n\n" + p3_struct,
        "## 6. 【最適化・進化計算・AI・探索アルゴリズム】（項目 223〜264）\n\n" + p4_opt,
        "## 7. 【計算幾何学・折り紙数理・3Dグラフィックス】（項目 265〜281）\n\n" + p4_geom,
    ]

    full_text = header + "\n\n---\n\n" + "\n\n---\n\n".join(sections) + "\n"

    target_path = Path("docs/HIGH_SCHOOL_GLOSSARY_MASTER.md")
    target_path.write_text(full_text, encoding="utf-8")
    print(f"Master glossary written to {target_path} ({len(full_text)} characters, {target_path.stat().st_size} bytes)")

if __name__ == "__main__":
    build_master_glossary()
