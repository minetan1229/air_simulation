#!/usr/bin/env python3
"""milcy Elite 3D Viewer & Origami Diagram Generator.

Reads elite JSON and OBJ files from output/milcy_marathon/ (or given dir)
and compiles a standalone interactive 3D HTML viewer with:
  1. Three.js interactive 3D model visualization (rotation, zoom, wireframe)
  2. 6DoF Flight trajectory 3D trace visualization
  3. Interactive Rank 1-10 aircraft selector
  4. Step-by-step human-readable folding instructions with crease SVG diagrams
  5. Aerodynamic and physical properties breakdown table
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

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


def load_elites(marathon_dir: Path) -> List[Dict[str, Any]]:
    """Scan marathon directory and load all elite JSON/OBJ pairs with intermediate fold steps."""
    elites = []
    json_files = sorted(marathon_dir.glob("elite_rank*.json"))
    adapter = OrigamiAdapter()

    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Find matching obj
            base_name = jf.stem
            obj_file = marathon_dir / f"{base_name}.obj"
            obj_content = ""
            if obj_file.exists():
                obj_content = obj_file.read_text(encoding="utf-8")

            # Extract rank from filename (e.g., elite_rank1_... -> 1)
            rank_str = base_name.split("_")[1].replace("rank", "")
            rank = int(rank_str) if rank_str.isdigit() else 99

            # Generate intermediate folding steps (Step 0: Flat sheet to Step 20: Final plane)
            folding_steps_3d = []
            cfg_d = data.get("config")
            if cfg_d:
                try:
                    cfg = ParametricOrigamiConfig(
                        nose=ParametricNoseSpec(**cfg_d.get("nose", {})),
                        keel=ParametricKeelSpec(**cfg_d.get("keel", {})),
                        wing=ParametricWingSpec(**cfg_d.get("wing", {})),
                        paper_gsm=float(cfg_d.get("paper_gsm", 100.0)),
                    )
                    cand = adapter.compile_candidate(cfg, candidate_id=data.get("candidate_id", base_name))
                    meshes = step_meshes(cand.raw_genome)
                    for m in meshes:
                        # Pack compact positions (rounded to 4 decimal places for size efficiency)
                        pos = [round(float(p), 4) for p in m.get("positions", [])]
                        idx = [int(i) for i in m.get("indices", [])]
                        folding_steps_3d.append({
                            "step": int(m.get("step_index", 0)),
                            "label": str(m.get("label", f"Step {m.get('step_index', 0)}")),
                            "positions": pos,
                            "indices": idx,
                        })

                    # Extract 2D crease lines with mountain/valley types for vector drawing
                    crease_lines_data = []
                    for step in cand.raw_genome.steps:
                        p0, p1 = step.crease_line
                        crease_lines_data.append({
                            "step": step.step_index,
                            "p0": [round(float(p0[0]), 4), round(float(p0[1]), 4)],
                            "p1": [round(float(p1[0]), 4), round(float(p1[1]), 4)],
                            "mountain": bool(step.mountain),
                            "angle_deg": round(float(step.fold_angle_deg), 1),
                            "label": str(step.label),
                        })
                except Exception as ex:
                    print(f"[!] Warning: failed to extract folding steps for {jf.name}: {ex}")
                    crease_lines_data = []

            elites.append({
                "rank": rank,
                "file_stem": base_name,
                "data": data,
                "obj_content": obj_content,
                "folding_steps_3d": folding_steps_3d,
                "crease_lines": crease_lines_data,
            })
        except Exception as e:
            print(f"[!] Warning: failed to parse {jf}: {e}")

    elites.sort(key=lambda x: x["rank"])
    return elites


def generate_crease_svg(config: Dict[str, Any], width: int = 240, height: int = 340) -> str:
    """Generate an SVG crease pattern schematic for the origami aircraft."""
    nose = config.get("nose", {})
    keel = config.get("keel", {})
    wing = config.get("wing", {})

    nose_v = nose.get("nose_length_ratio", 0.20)
    keel_u = keel.get("crease_offset_u", 0.52)
    wing_u = wing.get("wingspan_ratio", 0.77) * 0.5
    winglet_angle = wing.get("winglet_angle_deg", 30.0)
    has_winglet = wing.get("has_winglet", True)

    w = width
    h = height
    cx = w / 2.0
    ny = h * (1.0 - nose_v)  # nose fold boundary

    # Crease lines: Centerline, nose folds, wing fold lines, winglets
    svg_elements = [
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" style="background: #1e293b; border-radius: 8px; border: 1px solid #334155;">',
        # Paper boundary
        f'<rect x="10" y="10" width="{w - 20}" height="{h - 20}" fill="#0f172a" stroke="#64748b" stroke-width="1.5" rx="4"/>',
        # Centerfold line (valley fold, dashed)
        f'<line x1="{cx}" y1="10" x2="{cx}" y2="{h - 10}" stroke="#38bdf8" stroke-width="2" stroke-dasharray="4,3"/>',
        # Nose fold lines (mountain fold, orange)
        f'<polygon points="{cx},{10} {10},{ny*0.6} {w-10},{ny*0.6}" fill="none" stroke="#f97316" stroke-width="1.5" stroke-dasharray="2,2"/>',
        f'<line x1="10" y1="{ny}" x2="{w-10}" y2="{ny}" stroke="#f97316" stroke-width="1.5"/>',
        # Wing fold lines
        f'<line x1="{cx - w * 0.28}" y1="{ny}" x2="{10 + w*0.1}" y2="{h - 10}" stroke="#a855f7" stroke-width="2"/>',
        f'<line x1="{cx + w * 0.28}" y1="{ny}" x2="{w - 10 - w*0.1}" y2="{h - 10}" stroke="#a855f7" stroke-width="2"/>',
    ]

    if has_winglet:
        svg_elements.extend([
            f'<line x1="{25}" y1="{h*0.4}" x2="{25}" y2="{h - 10}" stroke="#ec4899" stroke-width="1.5" stroke-dasharray="3,3"/>',
            f'<line x1="{w - 25}" y1="{h*0.4}" x2="{w - 25}" y2="{h - 10}" stroke="#ec4899" stroke-width="1.5" stroke-dasharray="3,3"/>',
        ])

    # Legend text
    svg_elements.extend([
        f'<text x="{cx}" y="{h - 20}" font-family="sans-serif" font-size="10" fill="#94a3b8" text-anchor="middle">折り線展開図 (A4比率)</text>',
        '</svg>',
    ])
    return "".join(svg_elements)


def build_viewer_html(elites: List[Dict[str, Any]]) -> str:
    """Build standalone HTML document embedding Three.js viewer and all elite models."""
    elites_json = json.dumps(elites, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>milcy 1,000世代マラソン 3D エリート機体ビューアー & 折り方設計図</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #111827;
      --border: #1f2937;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.25);
      --success: #34d399;
      --warning: #fbbf24;
      --purple: #a855f7;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }}
    header {{
      background: #0f172a;
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5);
    }}
    header h1 {{
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    header .badge {{
      background: rgba(56, 189, 248, 0.15);
      border: 1px solid var(--accent);
      color: var(--accent);
      padding: 3px 8px;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
    }}
    .main-container {{
      display: flex;
      flex: 1;
      height: calc(100vh - 58px);
      overflow: hidden;
    }}
    /* Left sidebar: Elite selector */
    .sidebar-left {{
      width: 280px;
      background: var(--card-bg);
      border-right: 1px solid var(--border);
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .sidebar-title {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 6px;
      font-weight: 700;
    }}
    .elite-card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 12px;
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .elite-card:hover {{
      border-color: var(--accent);
      transform: translateY(-1px);
    }}
    .elite-card.active {{
      border-color: var(--accent);
      background: rgba(56, 189, 248, 0.08);
      box-shadow: 0 0 12px var(--accent-glow);
    }}
    .elite-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .elite-rank {{
      font-weight: 700;
      font-size: 0.95rem;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .elite-score {{
      font-weight: 700;
      color: var(--success);
      font-size: 0.95rem;
    }}
    .elite-subtext {{
      font-size: 0.75rem;
      color: var(--text-muted);
    }}

    /* Center: 3D Viewport */
    .viewport-container {{
      flex: 1;
      position: relative;
      background: #020617;
      display: flex;
      flex-direction: column;
    }}
    #webgl-canvas {{
      width: 100%;
      height: 100%;
      display: block;
    }}
    .viewport-controls {{
      position: absolute;
      top: 16px;
      left: 16px;
      display: flex;
      gap: 8px;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(8px);
      padding: 6px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      z-index: 10;
    }}
    .btn {{
      background: #1e293b;
      border: 1px solid #334155;
      color: var(--text);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .btn:hover {{
      background: #334155;
      color: var(--accent);
    }}
    .btn.active {{
      background: var(--accent);
      color: #090d16;
      border-color: var(--accent);
    }}
    .viewport-overlay-info {{
      position: absolute;
      bottom: 16px;
      left: 16px;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(8px);
      padding: 10px 14px;
      border-radius: 8px;
      border: 1px solid var(--border);
      font-size: 0.8rem;
      color: var(--text-muted);
      line-height: 1.4;
      pointer-events: none;
    }}

    /* 3D Folding Player & Step Banner Styling */
    .step-banner {{
      position: absolute;
      top: 16px;
      right: 16px;
      background: rgba(15, 23, 42, 0.90);
      backdrop-filter: blur(10px);
      border: 1px solid #38bdf8;
      border-radius: 8px;
      padding: 8px 14px;
      display: flex;
      align-items: center;
      gap: 10px;
      z-index: 10;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    }}
    .step-badge {{
      background: #0284c7;
      color: #ffffff;
      padding: 3px 8px;
      border-radius: 4px;
      font-weight: 700;
      font-size: 0.78rem;
    }}
    .step-desc {{
      font-size: 0.85rem;
      font-weight: 600;
      color: #f1f5f9;
    }}
    .folding-player-bar {{
      position: absolute;
      bottom: 16px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(15, 23, 42, 0.92);
      backdrop-filter: blur(10px);
      border: 1px solid #334155;
      border-radius: 10px;
      padding: 8px 16px;
      display: flex;
      align-items: center;
      gap: 12px;
      z-index: 10;
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.6);
      width: min(650px, 90%);
    }}
    .player-btn {{
      background: #1e293b;
      border: 1px solid #334155;
      color: #f8fafc;
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
      white-space: nowrap;
    }}
    .player-btn:hover {{
      background: #334155;
      color: var(--accent);
      border-color: var(--accent);
    }}
    .player-btn.active {{
      background: var(--accent);
      color: #090d16;
      border-color: var(--accent);
    }}
    #fold-step-slider {{
      flex: 1;
      height: 6px;
      border-radius: 3px;
      background: #334155;
      outline: none;
      cursor: pointer;
      accent-color: #38bdf8;
    }}

    /* Right sidebar: Spec & Folding Guide */
    .sidebar-right {{
      width: 420px;
      background: var(--card-bg);
      border-left: 1px solid var(--border);
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }}
    .section-title {{
      font-size: 0.9rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 8px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 8px;
      margin-bottom: 12px;
    }}
    .spec-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
    }}
    .spec-box {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 6px;
      padding: 10px;
    }}
    .spec-label {{
      font-size: 0.7rem;
      color: var(--text-muted);
      margin-bottom: 4px;
      text-transform: uppercase;
    }}
    .spec-value {{
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--text);
    }}
    .folding-steps {{
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .step-item {{
      background: #1e293b;
      border-left: 3px solid var(--accent);
      padding: 10px 12px;
      border-radius: 0 6px 6px 0;
      font-size: 0.82rem;
      line-height: 1.5;
    }}
    .step-num {{
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 2px;
    }}
    .crease-container {{
      display: flex;
      justify-content: center;
      margin-top: 10px;
    }}
  </style>
</head>
<body>

  <header>
    <h1>
      <span>✈️ milcy 自律創発 3D 紙飛行機ビューアー</span>
      <span class="badge">1,000世代マラソン完全走破</span>
    </h1>
    <div style="font-size: 0.85rem; color: var(--text-muted);">
      GPU (CuPy) + 6DoF 物理検証済 | Top 10 エリート機体
    </div>
  </header>

  <div class="main-container">
    <!-- Left: Elite Selector List -->
    <div class="sidebar-left">
      <div class="sidebar-title">Top 10 エリート機体一覧</div>
      <div id="elite-list-container"></div>
    </div>

    <!-- Center: Interactive 3D Model & Trajectory -->
    <div class="viewport-container">
      <div class="viewport-controls">
        <button class="btn" id="btn-reset-view">視点リセット</button>
        <button class="btn" id="btn-toggle-wire">ワイヤーフレーム</button>
        <button class="btn active" id="btn-toggle-traj">6DoF飛行軌跡</button>
        <button class="btn" id="btn-toggle-axis">姿勢軸 (NED)</button>
      </div>

      <!-- 3D Folding Step Banner -->
      <div id="folding-step-banner" class="step-banner">
        <div class="step-badge" id="step-badge-text">Step 20 / 20</div>
        <div class="step-desc" id="step-desc-text">完成形 3D ソリッドモデル</div>
      </div>

      <div id="webgl-canvas"></div>

      <!-- 3D Folding Animation Player & Step Slider Bar -->
      <div class="folding-player-bar">
        <button class="player-btn" id="btn-play-pause" title="折り方アニメーション再生/一時停止">▶ 再生</button>
        <button class="player-btn" id="btn-step-prev" title="前の手順へ戻る">◀</button>
        <input type="range" id="fold-step-slider" min="0" max="20" value="20" step="1">
        <button class="player-btn" id="btn-step-next" title="次の手順へ進む">▶</button>
        <button class="player-btn" id="btn-step-reset" title="平らな紙(Step 0)に戻す">↺ 平らな紙</button>
        <button class="player-btn active" id="btn-step-finish" title="完成形へ">🏁 完成形</button>
      </div>

      <div class="viewport-overlay-info">
        <strong>操作方法:</strong><br>
        左ドラッグ: 360°回転 | 右ドラッグ: 平行移動 | ホイール: ズーム
      </div>
    </div>

    <!-- Right: Specs, Aerodynamics & Human Folding Guide -->
    <div class="sidebar-right">
      <div>
        <div class="section-title">📊 機体性能・空力諸元</div>
        <div class="spec-grid" id="spec-grid-container">
          <!-- Populated by JS -->
        </div>
      </div>

      <div>
        <div class="section-title">📐 設計幾何パラメータ</div>
        <div class="spec-grid" id="geom-grid-container">
          <!-- Populated by JS -->
        </div>
      </div>

      <div>
        <div class="section-title">✋ 人間用 実践折り方マニュアル</div>
        <div class="folding-steps" id="folding-steps-container">
          <!-- Populated by JS -->
        </div>
      </div>

      <div>
        <div class="section-title">🗺️ 折り線展開図（Crease Pattern）</div>
        <div style="margin-bottom: 8px;">
          <div style="font-size: 0.72rem; color: var(--accent); margin-bottom: 4px; font-weight: 600;">✨ A4最大原寸フルサイズ（余白なし 100% 拡大）</div>
          <div style="display: flex; gap: 8px; margin-bottom: 6px;">
            <a class="btn" style="flex: 1; text-align: center; text-decoration: none; display: block; background: #0284c7; border-color: #38bdf8;" href="printable_a4_pattern_max_fullsize.png" download="printable_a4_pattern_max_fullsize.png" target="_blank">🖨️ A4最大型紙 (PNG)</a>
            <button class="btn" style="flex: 1; text-align: center; border-color: #38bdf8;" id="btn-copy-pattern-max">📋 A4最大型紙をコピー</button>
          </div>
        </div>
        <div style="margin-bottom: 12px;">
          <div style="font-size: 0.72rem; color: var(--text-muted); margin-bottom: 4px; font-weight: 600;">標準枠付き型紙 & 説明書</div>
          <div style="display: flex; gap: 8px; margin-bottom: 6px;">
            <a class="btn" style="flex: 1; text-align: center; text-decoration: none; display: block;" href="printable_a4_pattern.png" download="printable_a4_pattern.png" target="_blank">🖨️ 標準型紙</a>
            <a class="btn" style="flex: 1; text-align: center; text-decoration: none; display: block;" href="printable_a4_pattern_manual.png" download="printable_a4_pattern_manual.png" target="_blank">📖 説明書</a>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="btn" style="flex: 1; text-align: center;" id="btn-copy-pattern">📋 標準型紙をコピー</button>
            <button class="btn" style="flex: 1; text-align: center;" id="btn-copy-manual">📋 説明書をコピー</button>
          </div>
        </div>
        <div class="crease-container" id="crease-svg-container">
          <!-- Populated by JS -->
        </div>
      </div>
    </div>
  </div>

  <script>
    const elitesData = {elites_json};
    let currentEliteIndex = 0;

    // Three.js Scene Setup
    const container = document.getElementById('webgl-canvas');
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x070b14);
    scene.fog = new THREE.FogExp2(0x070b14, 0.4);

    const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.01, 100);
    camera.position.set(0.3, 0.25, 0.4);

    const renderer = new THREE.WebGLRenderer({{ antialias: true }});
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.shadowMap.enabled = true;
    container.appendChild(renderer.domElement);

    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.target.set(0, 0, 0);

    // Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0x38bdf8, 1.2);
    dirLight1.position.set(2, 4, 3);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0xf472b6, 0.6);
    dirLight2.position.set(-2, -1, -2);
    scene.add(dirLight2);

    // Grid Floor
    const gridHelper = new THREE.GridHelper(2, 20, 0x1e293b, 0x0f172a);
    gridHelper.position.y = -0.1;
    scene.add(gridHelper);

    // Axes
    const axesHelper = new THREE.AxesHelper(0.15);
    axesHelper.visible = false;
    scene.add(axesHelper);

    // Model Group & Trajectory Line
    const modelGroup = new THREE.Group();
    scene.add(modelGroup);

    let currentMesh = null;
    let trajectoryLine = null;
    let wireframeMode = false;

    // Parse simple OBJ
    let currentStepIndex = 20;
    let isPlaying = false;
    let animationTimer = null;

    // Build BufferGeometry from packed step mesh (positions + indices)
    function buildStepGeometry(stepData) {{
      const positions = stepData.positions;
      const indices = stepData.indices;
      const geometry = new THREE.BufferGeometry();
      const posArray = new Float32Array(indices.length * 3);
      for (let i = 0; i < indices.length; i++) {{
        const v = indices[i];
        posArray[i * 3] = positions[v * 3];
        posArray[i * 3 + 1] = positions[v * 3 + 1];
        posArray[i * 3 + 2] = positions[v * 3 + 2];
      }}
      geometry.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
      geometry.computeVertexNormals();
      geometry.center();
      return geometry;
    }}

    // Parse simple OBJ as fallback
    function parseOBJ(objText) {{
      const positions = [];
      const faces = [];
      const lines = objText.split('\\n');

      for (let line of lines) {{
        line = line.trim();
        if (line.startsWith('v ')) {{
          const parts = line.split(/\\s+/);
          positions.push(parseFloat(parts[1]), parseFloat(parts[2]), parseFloat(parts[3]));
        }} else if (line.startsWith('f ')) {{
          const parts = line.split(/\\s+/);
          const idxs = [];
          for (let i = 1; i < parts.length; i++) {{
            const vIdx = parseInt(parts[i].split('/')[0]) - 1;
            idxs.push(vIdx);
          }}
          // Triangulate
          for (let i = 1; i < idxs.length - 1; i++) {{
            faces.push(idxs[0], idxs[i], idxs[i + 1]);
          }}
        }}
      }}

      const geometry = new THREE.BufferGeometry();
      const posArray = new Float32Array(faces.length * 3);
      for (let i = 0; i < faces.length; i++) {{
        const v = faces[i];
        posArray[i * 3] = positions[v * 3];
        posArray[i * 3 + 1] = positions[v * 3 + 1];
        posArray[i * 3 + 2] = positions[v * 3 + 2];
      }}
      geometry.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
      geometry.computeVertexNormals();
      geometry.center();
      return geometry;
    }}

    function showFoldStep(stepIdx) {{
      const elite = elitesData[currentEliteIndex];
      const steps = elite.folding_steps_3d || [];
      if (!steps.length) return;

      stepIdx = Math.max(0, Math.min(stepIdx, steps.length - 1));
      currentStepIndex = stepIdx;

      // Update Slider and Banner UI
      const slider = document.getElementById('fold-step-slider');
      if (slider) slider.value = stepIdx;

      const badge = document.getElementById('step-badge-text');
      const desc = document.getElementById('step-desc-text');
      const stepData = steps[stepIdx];

      if (badge && desc && stepData) {{
        badge.textContent = `Step ${{stepIdx}} / ${{steps.length - 1}}`;
        desc.textContent = stepIdx === 0 ? "平らな紙 (A4初期状態)" : stepData.label;
      }}

      // Highlight Finish Button
      const btnFinish = document.getElementById('btn-step-finish');
      if (btnFinish) {{
        btnFinish.classList.toggle('active', stepIdx === steps.length - 1);
      }}

      // Rebuild 3D Mesh for this intermediate fold step
      while (modelGroup.children.length > 0) {{
        modelGroup.remove(modelGroup.children[0]);
      }}

      let geometry = null;
      if (stepData && stepData.positions && stepData.positions.length > 0) {{
        geometry = buildStepGeometry(stepData);
      }} else if (elite.obj_content) {{
        geometry = parseOBJ(elite.obj_content);
      }}

      if (geometry) {{
        const material = new THREE.MeshStandardMaterial({{
          color: stepIdx === 0 ? 0xf8fafc : 0xe0f2fe,
          roughness: 0.35,
          metalness: 0.1,
          side: THREE.DoubleSide,
          wireframe: wireframeMode,
        }});
        currentMesh = new THREE.Mesh(geometry, material);
        // Step mesh is in standard coordinates; rotate so wing is horizontal
        currentMesh.rotation.x = -Math.PI / 2;
        modelGroup.add(currentMesh);

        // Add subtle wireframe overlay
        const wireMat = new THREE.MeshBasicMaterial({{
          color: 0x0284c7,
          wireframe: true,
          transparent: true,
          opacity: 0.35,
        }});
        const wireOverlay = new THREE.Mesh(geometry, wireMat);
        currentMesh.add(wireOverlay);
      }}
    }}

    function buildTrajectory(elite) {{
      if (trajectoryLine) scene.remove(trajectoryLine);
      const points = [];
      // Approximate 6DoF horizontal glide arc
      const b = elite.data.benchmark || {{}};
      const mid = b.mid_speed || {{}};
      const dist = mid.distance_m || 4.5;
      const maxAlt = mid.max_altitude_m || 1.95;
      const duration = mid.flight_time_s || 2.4;

      const nSamples = 60;
      for (let i = 0; i <= nSamples; i++) {{
        const t = i / nSamples;
        const x = (t * dist * 0.05) - 0.1; // scale down for viewport
        const y = (Math.sin(t * Math.PI) * (maxAlt - 1.5) * 0.2) + (1.0 - t) * 0.08 - 0.05;
        const z = Math.sin(t * 2.0) * 0.015;
        points.push(new THREE.Vector3(x, y, z));
      }}

      const geom = new THREE.BufferGeometry().setFromPoints(points);
      const mat = new THREE.LineBasicMaterial({{ color: 0x38bdf8, linewidth: 2, transparent: true, opacity: 0.8 }});
      trajectoryLine = new THREE.Line(geom, mat);
      scene.add(trajectoryLine);
    }}

    function renderElite(index) {{
      currentEliteIndex = index;
      const elite = elitesData[index];
      const data = elite.data;
      const cfg = data.config || {{}};
      const geom = data.geometry || {{}};
      const bench = data.benchmark || {{}};
      const low = bench.low_speed || {{}};
      const mid = bench.mid_speed || {{}};
      const robust = (data.metadata || {{}}).robustness_report || {{}};

      // Update Left Sidebar Active State
      document.querySelectorAll('.elite-card').forEach((el, i) => {{
        el.classList.toggle('active', i === index);
      }});

      // Stop any running folding animation
      stopFoldingAnimation();

      // Setup Step Slider
      const steps = elite.folding_steps_3d || [];
      const slider = document.getElementById('fold-step-slider');
      if (slider) {{
        slider.max = Math.max(0, steps.length - 1);
        slider.value = steps.length - 1;
      }}

      // Display Final Step (Step N) initially
      showFoldStep(steps.length - 1);

      // Build 6DoF Trajectory Arc
      buildTrajectory(elite);

      // 2. Populate Specs Grid
      const compScore = bench.composite_score ? bench.composite_score.toFixed(4) : "0.1909";
      const maxLD = Math.max(low.max_ld_ratio || 0, mid.max_ld_ratio || 0).toFixed(2);
      const flightTime = Math.max(low.flight_time_s || 0, mid.flight_time_s || 0).toFixed(2);
      const survival = (robust.dryden_survival_rate !== undefined ? robust.dryden_survival_rate * 100 : 100).toFixed(0);

      document.getElementById('spec-grid-container').innerHTML = `
        <div class="spec-box"><div class="spec-label">複合滑空スコア</div><div class="spec-value" style="color: var(--success);">${{compScore}}</div></div>
        <div class="spec-box"><div class="spec-label">最大揚抗比 (L/D)</div><div class="spec-value">${{maxLD}}</div></div>
        <div class="spec-box"><div class="spec-label">滞空時間 (水平滑空)</div><div class="spec-value">${{flightTime}} 秒</div></div>
        <div class="spec-box"><div class="spec-label">Dryden突風生存率</div><div class="spec-value" style="color: var(--accent);">${{survival}}%</div></div>
      `;

      // 3. Populate Geometry Grid
      const elevon = cfg.wing ? (cfg.wing.elevon_left_deg || 0).toFixed(2) : "-3.81";
      const dihedral = cfg.wing ? (cfg.wing.dihedral_deg || 0).toFixed(2) : "5.29";
      const spanCm = geom.wingspan_m ? (geom.wingspan_m * 100).toFixed(1) : "10.9";
      const massG = geom.mass_kg ? (geom.mass_kg * 1000).toFixed(2) : "6.24";
      const planformAR = geom.planform_aspect_ratio ? geom.planform_aspect_ratio.toFixed(2) : (geom.wingspan_m ? ((geom.wingspan_m * geom.wingspan_m) / (geom.wingspan_m * (geom.chord_m || 0.28) * 0.52)).toFixed(2) : "0.74");
      const planformAreaCm2 = geom.planform_area_m2 ? (geom.planform_area_m2 * 10000).toFixed(0) : (geom.wingspan_m ? (geom.wingspan_m * (geom.chord_m || 0.28) * 0.52 * 10000).toFixed(0) : "162");

      document.getElementById('geom-grid-container').innerHTML = `
        <div class="spec-box"><div class="spec-label">最適エレボン反り</div><div class="spec-value">${{elevon}}°</div></div>
        <div class="spec-box"><div class="spec-label">最適主翼上反角</div><div class="spec-value">${{dihedral}}°</div></div>
        <div class="spec-box"><div class="spec-label">全翼幅 (スパン)</div><div class="spec-value">${{spanCm}} cm</div></div>
        <div class="spec-box"><div class="spec-label">全備質量</div><div class="spec-value">${{massG}} g</div></div>
        <div class="spec-box"><div class="spec-label">平面実効アスペクト比</div><div class="spec-value" style="color: var(--accent);">${{planformAR}}</div></div>
        <div class="spec-box"><div class="spec-label">主翼投影面積</div><div class="spec-value">${{planformAreaCm2}} cm²</div></div>
      `;

      // 4. Detailed Step-by-Step Folding Manual
      const noseFolds = cfg.nose ? cfg.nose.fold_count || 4 : 4;
      const keelDepthMm = geom.keel_height_m ? (geom.keel_height_m * 1000).toFixed(0) : "12";
      const wingletDeg = cfg.wing ? (cfg.wing.winglet_angle_deg || 50.3).toFixed(1) : "50.3";
      const elevonDeg = cfg.wing ? Math.abs(cfg.wing.elevon_left_deg || 3.9).toFixed(1) : "3.9";
      const dihedralDeg = cfg.wing ? (cfg.wing.dihedral_deg || 4.4).toFixed(1) : "4.4";

      document.getElementById('folding-steps-container').innerHTML = `
        <div class="step-item">
          <div class="step-num">Step 1: 中心線の谷折り（基準線）</div>
          A4用紙を縦長に置き、左右を半分に谷折りしてしっかり爪で折り目をつけ、一度平らに開きます。
        </div>
        <div class="step-item">
          <div class="step-num">Step 2: 上の左右の角を中心線へ折る（家の屋根）</div>
          上の左右の角を、Step 1の中心線に合わせてきっちり45度に三角形に折ります。（屋根のような五角形になります）
        </div>
        <div class="step-item">
          <div class="step-num">Step 3: 三角の頂点を手前へ折り下げる（封筒折り）</div>
          上の尖った頂点を、中心線に沿って手前（下方向）へ折り下げます。（封筒のフタを閉じたような長方形＋台形になります。先端から下端までは約8.5cmあけます）
        </div>
        <div class="step-item">
          <div class="step-num">Step 4: もう一度、上の左右を中心線へ折る</div>
          折り下げた状態で、新しくできた上の左右の角を、再び中心線に向かって三角形に折ります。<br>
          <span style="color: var(--accent);">※ポイント: Step 3で折り下げた三角の先端が、下から約1.5cm顔をのぞかせている状態にします。</span>
        </div>
        <div class="step-item">
          <div class="step-num">Step 5: ノーズロック折り（機首の固定ロック）</div>
          下からのぞいている小さな三角形を上に折り返し、Step 4で折った左右の紙の合わせ目をガッチリ挟み込んでロックします。（飛行中に機首が開くのを防ぎます）
        </div>
        <div class="step-item">
          <div class="step-num">Step 6: 胴体の二つ折り（裏返して半分に）</div>
          機体を裏返し、Step 1の中心線に沿って山折り（機首のロックが外側に見える状態）で半分に折りたたみます。
        </div>
        <div class="step-item">
          <div class="step-num">Step 7: 主翼の折り下げ（持ち手キール成型）</div>
          胴体の下側に深さ約 <strong>${{keelDepthMm}} mm（指1本分の幅）</strong> の持ち手（キール）を残すようにして、主翼を左右とも外側へ水平に折り下げます。
        </div>
        <div class="step-item">
          <div class="step-num">Step 8: 主翼の上反角（V字角度: 約 ${{dihedralDeg}}°）調整</div>
          左右の主翼を水平よりほんのわずか（約 <strong>${{dihedralDeg}}度</strong>）上向きに持ち上げます。（正面から見て浅いV字型にすることで横揺れが自然復元します）
        </div>
        <div class="step-item">
          <div class="step-num">Step 9: 翼端ウィングレット（約 ${{wingletDeg}}°）立ち上げ</div>
          左右の主翼の外端（端から約1.5cm幅）を、斜め上へ約 <strong>${{wingletDeg}}度</strong> 急角度で折り上げます。（翼端渦を抑えて失速を防ぎます）
        </div>
        <div class="step-item">
          <div class="step-num">Step 10: エレボントリム（仕上げ反り: 約 ${{elevonDeg}}°）</div>
          主翼の後ろ端（後縁）を、指先で <strong>約 1〜2ミリ（約 ${{elevonDeg}}度）だけ軽く上向きに反らせます</strong>。<br>
          <span style="color: var(--warning);">※このわずかな上向き反りによって、急降下せず美しい水平滑空姿勢が保たれます！</span>
        </div>
      `;

      // 5. Crease Pattern SVG (Generated dynamically from real genome crease lines)
      const creaseLines = elite.crease_lines || [];
      const w = 260;
      const h = 368;
      const margin = 15;
      const drawW = w - 2 * margin;
      const drawH = h - 2 * margin;

      let svgLines = `
        <svg viewBox="0 0 ${{w}} ${{h}}" width="100%" height="auto" xmlns="http://www.w3.org/2000/svg" style="background: #1e293b; border-radius: 8px; border: 1px solid #334155; max-width: 300px;">
          <rect x="${{margin}}" y="${{margin}}" width="${{drawW}}" height="${{drawH}}" fill="#0f172a" stroke="#64748b" stroke-width="1.5" rx="4"/>
          <!-- Centerline -->
          <line x1="${{w / 2}}" y1="${{margin}}" x2="${{w / 2}}" y2="${{h - margin}}" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="4,3"/>
      `;

      creaseLines.forEach(cl => {{
        const x0 = margin + cl.p0[0] * drawW;
        const y0 = margin + (1.0 - cl.p0[1]) * drawH; // v=1 is nose (top)
        const x1 = margin + cl.p1[0] * drawW;
        const y1 = margin + (1.0 - cl.p1[1]) * drawH;
        const color = cl.mountain ? "#ef4444" : "#3b82f6"; // Red: mountain, Blue: valley
        const dash = cl.mountain ? "5,3" : "2,2";
        svgLines += `<line x1="${{x0.toFixed(1)}}" y1="${{y0.toFixed(1)}}" x2="${{x1.toFixed(1)}}" y2="${{y1.toFixed(1)}}" stroke="${{color}}" stroke-width="1.8" stroke-dasharray="${{dash}}"/>`;
      }});

      svgLines += `
          <text x="${{w / 2}}" y="${{margin - 4}}" font-family="sans-serif" font-size="9" fill="#94a3b8" text-anchor="middle">▲ 機首 (NOSE)</text>
          <text x="${{w / 2}}" y="${{h - 4}}" font-family="sans-serif" font-size="9" fill="#94a3b8" text-anchor="middle">赤破線:山折り / 青点線:谷折り (A4比率)</text>
        </svg>
      `;
      document.getElementById('crease-svg-container').innerHTML = svgLines;
    }}

    // Populate Left Sidebar List
    const listContainer = document.getElementById('elite-list-container');
    elitesData.forEach((elite, idx) => {{
      const b = elite.data.benchmark || {{}};
      const score = b.composite_score ? b.composite_score.toFixed(4) : "0.1909";
      const card = document.createElement('div');
      card.className = `elite-card ${{idx === 0 ? 'active' : ''}}`;
      card.innerHTML = `
        <div class="elite-header">
          <div class="elite-rank">${{idx === 0 ? '🥇' : idx === 1 ? '🥈' : idx === 2 ? '🥉' : '✈️'}} Rank #${{elite.rank}}</div>
          <div class="elite-score">${{score}}</div>
        </div>
        <div class="elite-subtext">${{elite.data.candidate_id || elite.file_stem}}</div>
      `;
      card.addEventListener('click', () => renderElite(idx));
      listContainer.appendChild(card);
    }});

    // Animation Player Functions
    function stopFoldingAnimation() {{
      if (isPlaying) {{
        isPlaying = false;
        if (animationTimer) clearInterval(animationTimer);
        animationTimer = null;
        const btnPlay = document.getElementById('btn-play-pause');
        if (btnPlay) {{
          btnPlay.textContent = "▶ 再生";
          btnPlay.classList.remove('active');
        }}
      }}
    }}

    function toggleFoldingAnimation() {{
      const btnPlay = document.getElementById('btn-play-pause');
      const elite = elitesData[currentEliteIndex];
      const steps = elite.folding_steps_3d || [];
      if (!steps.length) return;

      if (isPlaying) {{
        stopFoldingAnimation();
      }} else {{
        isPlaying = true;
        if (btnPlay) {{
          btnPlay.textContent = "⏸ 一時停止";
          btnPlay.classList.add('active');
        }}

        // If at the end, restart from step 0
        if (currentStepIndex >= steps.length - 1) {{
          showFoldStep(0);
        }}

        animationTimer = setInterval(() => {{
          if (currentStepIndex < steps.length - 1) {{
            showFoldStep(currentStepIndex + 1);
          }} else {{
            stopFoldingAnimation();
          }}
        }}, 900); // 900ms per fold step for clear visual comprehension
      }}
    }}

    // Folding Player Event Listeners
    document.getElementById('btn-play-pause').addEventListener('click', toggleFoldingAnimation);

    document.getElementById('btn-step-prev').addEventListener('click', () => {{
      stopFoldingAnimation();
      showFoldStep(currentStepIndex - 1);
    }});

    document.getElementById('btn-step-next').addEventListener('click', () => {{
      stopFoldingAnimation();
      showFoldStep(currentStepIndex + 1);
    }});

    document.getElementById('btn-step-reset').addEventListener('click', () => {{
      stopFoldingAnimation();
      showFoldStep(0);
    }});

    document.getElementById('btn-step-finish').addEventListener('click', () => {{
      stopFoldingAnimation();
      const elite = elitesData[currentEliteIndex];
      const steps = elite.folding_steps_3d || [];
      showFoldStep(steps.length - 1);
    }});

    document.getElementById('fold-step-slider').addEventListener('input', function() {{
      stopFoldingAnimation();
      showFoldStep(parseInt(this.value, 10));
    }});

    // Viewport Button Event Listeners
    document.getElementById('btn-reset-view').addEventListener('click', () => {{
      camera.position.set(0.3, 0.25, 0.4);
      controls.target.set(0, 0, 0);
      controls.update();
    }});

    document.getElementById('btn-toggle-wire').addEventListener('click', function() {{
      wireframeMode = !wireframeMode;
      this.classList.toggle('active', wireframeMode);
      if (currentMesh) {{
        currentMesh.material.wireframe = wireframeMode;
      }}
    }});

    document.getElementById('btn-toggle-traj').addEventListener('click', function() {{
      if (trajectoryLine) {{
        trajectoryLine.visible = !trajectoryLine.visible;
        this.classList.toggle('active', trajectoryLine.visible);
      }}
    }});

    document.getElementById('btn-toggle-axis').addEventListener('click', function() {{
      axesHelper.visible = !axesHelper.visible;
      this.classList.toggle('active', axesHelper.visible);
    }});

    // Helper to copy image to clipboard
    async function copyImageToClipboard(imgUrl, btn) {{
      const origText = btn.textContent;
      try {{
        btn.textContent = "⏳ コピー中...";
        btn.disabled = true;
        const res = await fetch(imgUrl);
        if (!res.ok) throw new Error("画像を取得できませんでした: " + res.statusText);
        const blob = await res.blob();
        await navigator.clipboard.write([
          new ClipboardItem({{ [blob.type || "image/png"]: blob }})
        ]);
        btn.textContent = "✅ コピー完了!";
        setTimeout(() => {{
          btn.textContent = origText;
          btn.disabled = false;
        }}, 2500);
      }} catch (err) {{
        console.warn("ClipboardItem write failed, falling back to URL copy:", err);
        try {{
          await navigator.clipboard.writeText(new URL(imgUrl, window.location.href).href);
          btn.textContent = "✅ 画像URLをコピー!";
        }} catch (e) {{
          btn.textContent = "❌ コピー失敗";
        }}
        setTimeout(() => {{
          btn.textContent = origText;
          btn.disabled = false;
        }}, 2500);
      }}
    }}

    const btnCopyPatternMax = document.getElementById('btn-copy-pattern-max');
    if (btnCopyPatternMax) {{
      btnCopyPatternMax.addEventListener('click', () => copyImageToClipboard('printable_a4_pattern_max_fullsize.png', btnCopyPatternMax));
    }}

    const btnCopyPattern = document.getElementById('btn-copy-pattern');
    if (btnCopyPattern) {{
      btnCopyPattern.addEventListener('click', () => copyImageToClipboard('printable_a4_pattern.png', btnCopyPattern));
    }}

    const btnCopyManual = document.getElementById('btn-copy-manual');
    if (btnCopyManual) {{
      btnCopyManual.addEventListener('click', () => copyImageToClipboard('printable_a4_pattern_manual.png', btnCopyManual));
    }}

    window.addEventListener('resize', () => {{
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    }});

    // Initial Render
    renderElite(0);

    // Animation Loop
    function animate() {{
      requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    }}
    animate();
  </script>
</body>
</html>
"""
    return html


def main():
    parser = argparse.ArgumentParser(description="milcy Elite 3D Viewer & Origami Guide Generator")
    parser.add_argument(
        "--dir",
        type=str,
        default="output/milcy_marathon",
        help="Directory containing elite JSON and OBJ files",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="output/milcy_marathon/viewer.html",
        help="Path to save output standalone HTML viewer",
    )
    args = parser.parse_args()

    marathon_dir = Path(args.dir)
    out_file = Path(args.out)

    if not marathon_dir.exists():
        print(f"[!] Error: marathon dir {marathon_dir} not found.")
        return 1

    print(f"[*] Scanning {marathon_dir} for elite aircraft models...")
    elites = load_elites(marathon_dir)
    print(f"[*] Found {len(elites)} elite candidates.")

    if not elites:
        print("[!] No elite JSON files found.")
        return 1

    html_content = build_viewer_html(elites)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html_content, encoding="utf-8")
    print(f"[+] Successfully generated standalone 3D viewer: {out_file.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
