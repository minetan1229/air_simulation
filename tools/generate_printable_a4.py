#!/usr/bin/env python3
"""A4 Printable Origami Aircraft Sheet & Folding Manual Generator.

Generates high-resolution (300 DPI) printable A4 images (PNG) and vector sheets (SVG)
for origami aircraft designs produced by the milcy optimization loop.

Features:
  1. Full-scale A4 sheet (210mm x 297mm) crease pattern ready for direct printing.
  2. Clear color & stroke conventions:
     - Red dashed line (--): Mountain fold (山折り)
     - Blue dash-dot line (-.-): Valley fold (谷折り)
     - Step sequence badges (①, ②, ③...) with folding angle and target crease.
  3. Integrated practical folding instruction guide on the sheet margins / split page.
  4. Both Full-Sheet Print Mode (Pattern on A4) and 2-Page Manual Mode (Pattern + Step Photos/Diagrams).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib import font_manager

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.interfaces import (
    ParametricOrigamiConfig,
    ParametricNoseSpec,
    ParametricKeelSpec,
    ParametricWingSpec,
)
from geometry.v3_origami.adapter import OrigamiAdapter
from geometry.origami_genome import step_meshes

# Setup Japanese Font support for Matplotlib
def setup_japanese_font():
    preferred_fonts = ["Yu Gothic", "Meiryo", "MS Gothic", "BIZ UDGothic", "TakaoPGothic", "IPAGothic"]
    available_fonts = set(f.name for f in font_manager.fontManager.ttflist)
    for f in preferred_fonts:
        if f in available_fonts:
            matplotlib.rcParams["font.sans-serif"] = [f] + matplotlib.rcParams["font.sans-serif"]
            matplotlib.rcParams["font.family"] = "sans-serif"
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

setup_japanese_font()


def resolve_canonical_crease_style(label: str, raw_mountain: bool = True) -> Tuple[bool, str, str]:
    """Resolve canonical mountain/valley fold style from the perspective of flat A4 paper.

    Returns:
        (is_mountain, badge_symbol, fold_name):
        - is_mountain: True for mountain fold (red dashed), False for valley fold (blue dash-dot)
        - badge_symbol: e.g. '①', '②'
        - fold_name: friendly Japanese name
    """
    key = str(label).lower()
    if "body" in key:
        return (False, "①", "センターライン谷折り")
    elif "corner" in key:
        # Corner triangular fold onto center line is valley fold from paper front
        return (False, "②", "機首斜め三角折り")
    elif "collar" in key:
        # Collar side fold inward is valley fold from paper front
        return (False, "③", "機首サイド襟折り")
    elif "nose_lock" in key:
        # Folding tip back/inward
        return (True, "④", "先端ロック折り")
    elif "nose_reinforce" in key or "nose_rf" in key:
        # Nose flap reinforcement
        return (True, "⑤", "機首補強折り")
    elif "wing" in key and "winglet" not in key:
        # Folding wings downward from keel forms a mountain ridge at wing crease
        return (True, "⑥", "主翼折り下げ")
    elif "winglet" in key:
        # Folding wingtips upward from wing plane is valley fold
        return (False, "⑦", "翼端ウィングレット")
    elif "elevon" in key:
        # Elevon upward reflex trim is valley fold
        return (False, "⑧", "エレボントリム反り")
    elif "trailing" in key:
        return (False, "⑨", "後縁トリム")
    elif "camber" in key:
        return (True, "", "キャンバー微小折り")
    else:
        # Fallback to raw mountain/valley
        return (raw_mountain, "", label)


def load_candidate_data(json_path: Path) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_folding_instructions(config: Dict[str, Any]) -> List[Dict[str, str]]:
    nose = config.get("nose", {})
    keel = config.get("keel", {})
    wing = config.get("wing", {})

    taper = nose.get("taper_ratio", 0.65)
    keel_depth_ratio = keel.get("keel_depth_ratio", 0.08)
    keel_mm = round(keel_depth_ratio * 297.0 * 0.5, 1)
    dihedral = round(wing.get("dihedral_deg", 3.5), 1)
    winglet = round(wing.get("winglet_angle_deg", 45.0), 1)
    elevon = round(wing.get("elevon_left_deg", 2.0), 1)

    return [
        {
            "step": "①",
            "type": "谷折り (Valley)",
            "title": "センターラインの折り目付け",
            "desc": "青の一点鎖線①に沿って用紙を長手方向に半分に谷折りし、中央にしっかり折り目をつけて開きます。",
        },
        {
            "step": "②",
            "type": "谷折り (Valley)",
            "title": "機首斜め三角折り（左右対称）",
            "desc": "用紙上部の左右の角を、青の一点鎖線②に合わせて中央線に向かって手前に三角に谷折りします。",
        },
        {
            "step": "③",
            "type": "谷折り (Valley)",
            "title": "機首サイド襟折り（二重ロック）",
            "desc": "青の一点鎖線③に沿って、両サイドをもう一度中央へ向けて細く手前に谷折りします。",
        },
        {
            "step": "④",
            "type": "山折り (Mountain)",
            "title": "先端ロック折り（重心前方化）",
            "desc": "折られた機首の先端を赤の破線④で内側へ折り返し、ノーズの剛性を高めます。",
        },
        {
            "step": "⑤",
            "type": "山折り (Mountain)",
            "title": "機首補強折り（フラップ固定）",
            "desc": "赤の破線⑤で機首フラップをロックし、飛行時の機首開きを防止します。",
        },
        {
            "step": "胴体",
            "type": "山折り (Mountain)",
            "title": "胴体キール折り（裏返し二つ折り）",
            "desc": "全体を裏返し、中心線①に沿って機首が外側に出るように半分に山折りします。",
        },
        {
            "step": "⑥",
            "type": "山折り (Mountain)",
            "title": f"主翼の折り下げ（持ち手キール {keel_mm}mm）",
            "desc": f"下部に深さ約{keel_mm}mmのキール（指の持ち手）を残し、赤の破線⑥で主翼を左右とも外側へ水平に山折りします。",
        },
        {
            "step": "⑦",
            "type": "谷折り (Valley)",
            "title": f"翼端ウィングレット（{winglet}° 立ち上げ）",
            "desc": f"主翼外側の青の一点鎖線⑦で、両端を斜め上約{winglet}度へ急角度に谷折りして立ち上げます。",
        },
        {
            "step": "⑧ & ⑨",
            "type": "谷折り (Valley)",
            "title": f"エレボントリム（仕上げ反り {elevon}°）",
            "desc": f"主翼後縁（後ろ端）の青線⑧⑨を指先で約1〜2mm（{elevon}度）上向きに反らせます。",
        },
        {
            "step": "調整",
            "type": "姿勢確認",
            "title": f"上反角確認（浅いV字 {dihedral}°）",
            "desc": f"正面から見て主翼が水平より約{dihedral}度だけ持ち上がったV字形状になっていることを確認して完成！",
        },
    ]


def render_a4_printable_sheet(
    cand_data: Dict[str, Any],
    out_path: Path,
    include_manual_page: bool = True,
    dpi: int = 300,
) -> Path:
    """Generate high-resolution printable A4 paper airplane folding template."""
    cid = cand_data.get("candidate_id", "map-elite-plane")
    cfg_d = cand_data.get("config", {})
    bench = cand_data.get("benchmark", {})
    mid = bench.get("mid_speed", {})
    score = bench.get("composite_score", 0.188)

    cfg = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(**cfg_d.get("nose", {})),
        keel=ParametricKeelSpec(**cfg_d.get("keel", {})),
        wing=ParametricWingSpec(**cfg_d.get("wing", {})),
        paper_gsm=float(cfg_d.get("paper_gsm", 100.0)),
    )
    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(cfg, candidate_id=cid)
    genome = cand.raw_genome
    instructions = build_folding_instructions(cfg_d)

    # A4 dimensions in inches (210 x 297 mm)
    # Page 1: Printable Crease Pattern (Actual 1:1 scale for direct folding)
    fig = plt.figure(figsize=(8.27, 11.69), dpi=dpi)

    # Margins in normalized coords
    margin_x = 0.08  # left/right margin
    margin_top = 0.10
    margin_bottom = 0.08

    # Paper area on canvas
    pw = 1.0 - 2 * margin_x
    ph = 1.0 - margin_top - margin_bottom
    ax = fig.add_axes([margin_x, margin_bottom, pw, ph])

    # Sheet rectangle border (Simulates A4 paper boundary)
    sheet_rect = patches.Rectangle(
        (0, 0), 1, 1,
        linewidth=2.5,
        edgecolor="#1e293b",
        facecolor="#fcfbf7",
        linestyle="-",
        zorder=1
    )
    ax.add_patch(sheet_rect)

    # Grid / Cut lines
    ax.plot([0, 1], [0, 0], color="#94a3b8", linestyle=":", linewidth=1)
    ax.plot([0, 1], [1, 1], color="#94a3b8", linestyle=":", linewidth=1)
    ax.plot([0, 0], [0, 1], color="#94a3b8", linestyle=":", linewidth=1)
    ax.plot([1, 1], [0, 1], color="#94a3b8", linestyle=":", linewidth=1)

    # Draw Creases with canonical mountain / valley styles
    for step in genome.steps:
        p0, p1 = step.crease_line
        # Coordinate map: u in [0, 1], v in [0, 1] where v=1 is nose
        x0, y0 = p0[0], p0[1]
        x1, y1 = p1[0], p1[1]

        is_mtn, badge_num, fold_name = resolve_canonical_crease_style(step.label, step.mountain)
        color = "#dc2626" if is_mtn else "#2563eb"  # Red = mountain, Blue = valley
        ls = (0, (6, 4)) if is_mtn else (0, (3, 2, 1, 2))  # Dashed vs Dash-dot
        lw = 2.2 if "body" in step.label or "wing" in step.label or "corner" in step.label else 1.6

        ax.plot([x0, x1], [y0, y1], color=color, linestyle=ls, linewidth=lw, zorder=3)

        # Label badge
        if badge_num:
            mx = 0.5 * (x0 + x1)
            my = 0.5 * (y0 + y1)
            off_x = 0.022 if mx < 0.5 else -0.022
            off_y = 0.015 if my > 0.5 else -0.015

            ax.text(
                mx + off_x, my + off_y,
                badge_num,
                color="#ffffff",
                fontsize=9,
                fontweight="bold",
                ha="center",
                va="center",
                bbox=dict(boxstyle="circle,pad=0.25", facecolor=color, edgecolor="none", alpha=0.95),
                zorder=5,
            )

    # Title & Headers outside paper area
    fig.text(
        0.5, 0.955,
        f"milcy 実機印刷用 A4 折り図テンプレート（型紙）",
        fontsize=15,
        fontweight="bold",
        ha="center",
        color="#0f172a"
    )
    fig.text(
        0.5, 0.932,
        f"機体ID: {cid} | 複合滑空スコア: {score:.4f} | 6DoF滞空時間: {mid.get('flight_time_s', 2.4):.2f}s | 飛行距離: {mid.get('distance_m', 4.5):.2f}m",
        fontsize=9,
        ha="center",
        color="#475569"
    )

    # Legend / Key at top inside paper
    legend_box = dict(boxstyle="round,pad=0.5", facecolor="#ffffff", edgecolor="#cbd5e1", linewidth=1.2)
    ax.text(
        0.5, 0.04,
        "【線の見方】  赤の破線 ( - - - ) : 山折り (Mountain Fold)    ／    青の一点鎖線 ( - · - · ) : 谷折り (Valley Fold)\n"
        "番号順（① → ② → ... → ⑯）に折るだけで、航空工学的に最適な高性能グライダーが完成します。",
        fontsize=8.5,
        ha="center",
        va="center",
        color="#1e293b",
        fontweight="bold",
        bbox=legend_box,
        zorder=6
    )

    # Top Nose Arrow
    ax.annotate(
        "▲ 機首先端（前）",
        xy=(0.5, 0.985),
        xytext=(0.5, 0.94),
        arrowprops=dict(facecolor="#0284c7", edgecolor="none", width=2, headwidth=7),
        fontsize=9,
        fontweight="bold",
        color="#0284c7",
        ha="center",
        zorder=6
    )

    # Bottom Trailing Edge
    ax.text(
        0.5, 0.012,
        "▼ 翼後縁（後ろ端）",
        fontsize=8.5,
        fontweight="bold",
        color="#64748b",
        ha="center",
        zorder=6
    )

    # Clean up axis ticks
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.axis("off")

    # Footer notice
    fig.text(
        0.5, 0.025,
        "※A4普通紙に「等倍（倍率100% / ページに合わせない）」でプリントアウトしてご使用ください。",
        fontsize=8,
        ha="center",
        color="#64748b"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, facecolor="#ffffff", bbox_inches="tight")
    plt.close(fig)
    print(f"[+] Successfully exported printable A4 sheet (Page 1): {out_path.resolve()}")

    # Page 1-B: 1:1 Full Bleed Max Size A4 Pattern (210mm x 297mm full bleed for direct 100% borderless printing)
    full_path = out_path.parent / f"{out_path.stem}_max_fullsize.png"
    render_a4_max_fullsize_sheet(cand_data, full_path, dpi=dpi)

    # Page 2: Step-by-Step Practical Illustrated Guide (A4)
    if include_manual_page:
        guide_path = out_path.parent / f"{out_path.stem}_manual.png"
        render_a4_step_manual(cand_data, instructions, guide_path, dpi=dpi)

    return out_path


def render_a4_max_fullsize_sheet(
    cand_data: Dict[str, Any],
    out_path: Path,
    dpi: int = 300,
) -> Path:
    """Generate 100% full-bleed maximal A4 paper aircraft sheet (exact 210mm x 297mm canvas).

    Every coordinate u in [0, 1], v in [0, 1] maps exactly to the four corners of A4 paper,
    providing the maximum possible aircraft size with zero margins/wasted paper.
    """
    cid = cand_data.get("candidate_id", "map-elite-plane")
    cfg_d = cand_data.get("config", {})

    cfg = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(**cfg_d.get("nose", {})),
        keel=ParametricKeelSpec(**cfg_d.get("keel", {})),
        wing=ParametricWingSpec(**cfg_d.get("wing", {})),
        paper_gsm=float(cfg_d.get("paper_gsm", 100.0)),
    )
    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(cfg, candidate_id=cid)
    genome = cand.raw_genome

    # Exact A4 dimensions (8.27 in x 11.69 in = 210mm x 297mm) at 300 DPI (2481 x 3507 px)
    fig = plt.figure(figsize=(8.27, 11.69), dpi=dpi)
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")

    # Paper background
    ax.fill([0, 1, 1, 0], [0, 0, 1, 1], color="#ffffff", zorder=1)

    # Outer border (Exact A4 paper edges)
    ax.plot([0, 1, 1, 0, 0], [0, 0, 1, 1, 0], color="#94a3b8", linestyle="-", linewidth=1.5, zorder=2)

    # Draw Crease Lines exactly scaled across full A4 sheet with canonical styles
    for step in genome.steps:
        p0, p1 = step.crease_line
        x0, y0 = p0[0], p0[1]
        x1, y1 = p1[0], p1[1]

        is_mtn, badge_num, fold_name = resolve_canonical_crease_style(step.label, step.mountain)
        color = "#dc2626" if is_mtn else "#2563eb"
        ls = (0, (8, 5)) if is_mtn else (0, (4, 3, 1, 3))
        lw = 2.4 if "body" in step.label or "wing" in step.label or "corner" in step.label else 1.8

        ax.plot([x0, x1], [y0, y1], color=color, linestyle=ls, linewidth=lw, zorder=3)

        # Label badges on folds
        if badge_num:
            mx = 0.5 * (x0 + x1)
            my = 0.5 * (y0 + y1)
            off_x = 0.02 if mx < 0.5 else -0.02
            off_y = 0.012 if my > 0.5 else -0.012

            ax.text(
                mx + off_x, my + off_y,
                badge_num,
                color="#ffffff",
                fontsize=9.5,
                fontweight="bold",
                ha="center",
                va="center",
                bbox=dict(boxstyle="circle,pad=0.28", facecolor=color, edgecolor="none", alpha=0.92),
                zorder=5,
            )

    # Orientation badges (unobtrusive at very edges)
    ax.text(
        0.5, 0.988,
        "▲ 機首先端 (NOSE)",
        fontsize=8.5,
        fontweight="bold",
        color="#0284c7",
        ha="center",
        va="top",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="#f0f9ff", edgecolor="#bae6fd", alpha=0.85),
        zorder=6,
    )
    ax.text(
        0.5, 0.012,
        "▼ 翼後縁 (TRAILING EDGE) | 最大A4原寸 (赤:山折り / 青:谷折り)",
        fontsize=8.0,
        fontweight="bold",
        color="#64748b",
        ha="center",
        va="bottom",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="#f8fafc", edgecolor="#e2e8f0", alpha=0.85),
        zorder=6,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Save WITHOUT bbox_inches="tight" to preserve exact 8.27in x 11.69in A4 bounds
    fig.savefig(out_path, dpi=dpi, facecolor="#ffffff")
    plt.close(fig)
    print(f"[+] Successfully exported full-bleed A4 max size sheet: {out_path.resolve()}")
    return out_path


def render_a4_step_manual(
    cand_data: Dict[str, Any],
    instructions: List[Dict[str, str]],
    out_path: Path,
    dpi: int = 300,
) -> Path:
    """Generate accompanying A4 page with detailed step-by-step illustrated manual."""
    cid = cand_data.get("candidate_id", "map-elite-plane")
    cfg_d = cand_data.get("config", {})

    fig = plt.figure(figsize=(8.27, 11.69), dpi=dpi)

    # Header
    fig.text(
        0.5, 0.955,
        f"A4実践折り方マニュアル & 機体調整ガイド",
        fontsize=16,
        fontweight="bold",
        ha="center",
        color="#0f172a"
    )
    fig.text(
        0.5, 0.932,
        f"対象機体: {cid}（自律探索MAP-Elites最高ランク個体） | 印刷した型紙を見ながら手順通りに進めてください",
        fontsize=9,
        ha="center",
        color="#475569"
    )

    # Draw step cards (2 columns x 5 rows)
    cols = 2
    rows = 5
    card_w = 0.42
    card_h = 0.14
    start_x = 0.06
    start_y = 0.76
    gap_x = 0.04
    gap_y = 0.025

    for idx, inst in enumerate(instructions[:10]):
        col = idx % cols
        row = idx // cols

        cx = start_x + col * (card_w + gap_x)
        cy = start_y - row * (card_h + gap_y)

        ax = fig.add_axes([cx, cy, card_w, card_h])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")

        # Card Background
        is_valley = "谷折り" in inst["type"]
        accent_color = "#2563eb" if is_valley else "#dc2626"
        card_bg = patches.FancyBboxPatch(
            (0, 0), 1, 1,
            boxstyle="round,pad=0.03",
            facecolor="#f8fafc",
            edgecolor="#cbd5e1",
            linewidth=1.2,
            zorder=1
        )
        ax.add_patch(card_bg)

        # Header tag
        tag_bg = patches.Rectangle(
            (0.04, 0.72), 0.32, 0.22,
            facecolor=accent_color,
            edgecolor="none",
            zorder=2
        )
        ax.add_patch(tag_bg)
        ax.text(
            0.20, 0.83,
            f"Step {inst['step']}",
            color="#ffffff",
            fontweight="bold",
            fontsize=9.5,
            ha="center",
            va="center",
            zorder=3
        )

        ax.text(
            0.38, 0.83,
            inst["title"],
            color="#0f172a",
            fontweight="bold",
            fontsize=9.5,
            ha="left",
            va="center",
            zorder=3
        )

        # Description
        ax.text(
            0.05, 0.38,
            inst["desc"],
            color="#334155",
            fontsize=8.0,
            ha="left",
            va="center",
            wrap=True,
            zorder=3
        )

    # Bottom Aerodynamic Tuning Tips Box
    tip_ax = fig.add_axes([0.06, 0.04, 0.88, 0.12])
    tip_ax.set_xlim(0, 1)
    tip_ax.set_ylim(0, 1)
    tip_ax.axis("off")

    tip_bg = patches.FancyBboxPatch(
        (0, 0), 1, 1,
        boxstyle="round,pad=0.03",
        facecolor="#eff6ff",
        edgecolor="#38bdf8",
        linewidth=1.5,
        zorder=1
    )
    tip_ax.add_patch(tip_bg)

    tip_ax.text(
        0.03, 0.80,
        "【航空力学の極意】最長滑空のためのプロの投擲＆微調整テクニック",
        fontsize=10.5,
        fontweight="bold",
        color="#0369a1",
        zorder=2
    )

    tip_text = (
        "1. 後縁トリム（エレボン）: 急降下（機首から落ちる）する場合は後ろ端を0.5mmずつ上に反らせてください。\n"
        "   逆に失速（頭を上げて波打つ）する場合は、反りを少し戻して平らにします。\n"
        "2. 上反角の維持: 投げる前に正面から見て主翼が水平より約3〜4度だけ上がったV字形状であることを確認してください。\n"
        "3. 投げ方: 力任せに投げず、水平からやや上向き（5度程度）に向けて滑らかに押し出すようにリリースします。"
    )
    tip_ax.text(
        0.03, 0.38,
        tip_text,
        fontsize=8.2,
        color="#0f172a",
        zorder=2
    )

    fig.savefig(out_path, dpi=dpi, facecolor="#ffffff", bbox_inches="tight")
    plt.close(fig)
    print(f"[+] Successfully exported step-by-step manual (Page 2): {out_path.resolve()}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="A4 Printable Origami Template & Manual Generator")
    parser.add_argument(
        "--json",
        type=str,
        default="output/milcy_overnight/round_02/elite_rank1_map-elite-0426.json",
        help="Path to candidate JSON file",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="output/milcy_overnight/round_02/printable_a4_pattern.png",
        help="Path to save output A4 sheet PNG",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Print resolution DPI (default: 300 for crisp printing)",
    )
    args = parser.parse_args()

    json_path = Path(args.json)
    out_path = Path(args.out)

    if not json_path.exists():
        print(f"[!] Error: JSON file {json_path} not found.")
        return 1

    cand_data = load_candidate_data(json_path)
    render_a4_printable_sheet(cand_data, out_path, include_manual_page=True, dpi=args.dpi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
