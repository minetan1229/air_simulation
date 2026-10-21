/*
 * airframe.js — 高忠実度プロシージャル折り紙飛行機 (High-Fidelity Procedural Paper Airplane).
 *
 * Live AircraftConfig から本物の折り紙飛行機の幾何学構造を忠実に生成します:
 * - 左右の主翼 (Wings) と可変翼型・キャンバー
 * - 左右に厚みと立体感を持つ折り紙胴体キール (3D Folded Fuselage Keel)
 * - 機首の立体的な折り返しロックバラスト (Folded Nose Lock & Overlap Flaps)
 * - エレボン (Elevons) および翼端ウイングレット (Winglets) のシャープな折り目
 * - 折り紙特有の折り目線 (Crease Lines) と輪郭線 (Silhouette Edges)
 * - 応力ヒートマップ用の空力パネル UV 座標
 */

import * as THREE from "three";

const SPAN_SEGMENTS = 16;
const CHORD_SEGMENTS = 12;

/*/** Leading-edge sweep per shape family (0 = dart, 1 = glider/suzanne, 2 = wide-body/sky-king). */
// Dart has extreme slender delta wing sweep (0.92 = ~75 deg sweep angle)
const SHAPE_SWEEP = [0.92, 0.44, 0.28];
const SHAPE_NAME = ["dart", "glider (suzanne)", "wide-body (sky king)"];


function shapeIndex(flag) {
  const value = Number(flag);
  if (!Number.isFinite(value)) return 1;
  return Math.min(2, Math.max(0, Math.round(value)));
}

function smoothstep(edge0, edge1, value) {
  const t = Math.min(Math.max((value - edge0) / (edge1 - edge0 || 1), 0), 1);
  return t * t * (3 - 2 * t);
}

export function airframeSummary(cfg) {
  const span = Math.max(Number(cfg.wingspan_m) || 0, 1e-4);
  const area = Math.max(Number(cfg.wing_area_m2) || 1e-5, 1e-6);
  const taper = Math.min(Math.max(Number(cfg.taper_ratio) || 0.7, 0.05), 1);
  const rootChord = (2 * area) / (span * (1 + taper));
  const aspect = (span * span) / area;
  return {
    span,
    area,
    taper,
    rootChord,
    tipChord: taper * rootChord,
    meanChord: area / span,
    aspect,
    shape: SHAPE_NAME[shapeIndex(cfg.shape_flag)],
  };
}

/**
 * Surface sampler for one wing side.
 * `s` runs 0 (keel root junction) .. 1 (tip), `t` runs 0 (leading edge) .. 1 (trailing edge).
 * `side`: 1 for right wing (+z), -1 for left wing (-z).
 */
function makeWingSampler(cfg, side) {
  const geom = airframeSummary(cfg);
  const semispan = geom.span / 2;
  const sIdx = shapeIndex(cfg.shape_flag);
  const sweep = SHAPE_SWEEP[sIdx];
  const dihedral = ((Number(cfg.dihedral_deg) || 0) * Math.PI) / 180;
  const tipFold = ((Number(cfg.fold_tip_deg) || 0) * Math.PI) / 180;
  const leFold = ((Number(cfg.fold_leading_deg) || 0) * Math.PI) / 180;
  const teFold = ((Number(cfg.fold_trailing_deg) || 0) * Math.PI) / 180;
  
  // Base elevon deflection (symmetric pitch trim, positive = trailing edge bent up for reflex glide)
  const elevonBase = ((Number(cfg.elevon_deg) || 0) * Math.PI) / 180;
  // Aileron trim / lift asymmetry (asymmetric roll trim):
  // +aileron_deg means roll right (right wing trailing edge down / left wing trailing edge up)
  const aileronVal = Number(cfg.aileron_deg || 0);
  const asymClVal = Number(cfg.asymmetry_cl || 0);
  const aileronRad = ((aileronVal + asymClVal * 20.0) * Math.PI) / 180;
  // side: +1 is right wing, -1 is left wing.
  // Effective elevon on this wing side:
  const elevonEffective = elevonBase - side * aileronRad * 0.5;

  const ctrlRatio = Math.min(Math.max(Number(cfg.elevon_chord_ratio) || 0.22, 0.02), 0.6);
  // Shape-specific winglet start: Sky King has broader winglets, Dart has outer tips
  const tipStart = sIdx === 2 ? 0.78 : (sIdx === 0 ? 0.88 : 0.82);

  // Keel root half-width (real paper airplanes have a folded spine thickness)
  const keelHalfWidth = Math.min(0.005, geom.span * 0.015);

  const chordAt = (s) => geom.rootChord + (geom.tipChord - geom.rootChord) * s;
  const leX = (s) => geom.rootChord - sweep * s * semispan;

  // Station computation: calculates exact wing surface fold line in 3D
  const station = (i) => {
    const s = i / SPAN_SEGMENTS;
    const sInner = Math.min(s, tipStart);
    const sOuter = Math.max(0, s - tipStart);
    const outerRatio = (1 - tipStart) > 1e-4 ? sOuter / (1 - tipStart) : 0;
    
    // Upward vertical winglet crease fold (e.g. Suzanne & Sky King winglet guide)
    // Smooth fold transition around the tip crease
    const foldFactor = outerRatio * outerRatio * (3 - 2 * outerRatio);
    const currentTipAngle = tipFold * foldFactor;

    // Wing dihedral up to the winglet fold line
    const innerSpan = sInner * (semispan - keelHalfWidth);
    const yDihedral = Math.sin(dihedral) * innerSpan;
    const zInner = keelHalfWidth + Math.cos(dihedral) * innerSpan;

    // Outer winglet fold (standing vertically or angled upward from the crease)
    const wingletLength = sOuter * semispan;
    const yWinglet = wingletLength * Math.sin(currentTipAngle);
    const zWinglet = wingletLength * Math.cos(currentTipAngle);

    return {
      s,
      x: leX(s),
      y: yDihedral + yWinglet,
      z: side * (zInner + zWinglet),
      chord: chordAt(s),
    };
  };

  return (s, t) => {
    const f = Math.min(Math.max(s, 0), 1) * SPAN_SEGMENTS;
    const i = Math.min(SPAN_SEGMENTS - 1, Math.floor(f));
    const w = f - i;
    const a = station(i);
    const b = station(Math.min(SPAN_SEGMENTS, i + 1));
    const x0 = a.x + (b.x - a.x) * w;
    const y0 = a.y + (b.y - a.y) * w;
    const z0 = a.z + (b.z - a.z) * w;
    const chord = a.chord + (b.chord - a.chord) * w;

    const tc = Math.min(Math.max(t, 0), 1);
    // Trailing edge extends in the -X direction (from nose x0 toward tail)
    let x = x0 - tc * chord;
    let y = y0;
    const z = z0;

    // Aerodynamic camber: real folded paper wings develop slight convex curvature under airflow
    const camber = 0.020 * chord * Math.sin(Math.PI * tc);
    y += camber;

    // Leading-edge fold / reinforced crease (dart & suzanne nose taper)
    if (leFold !== 0 && tc < 0.22) {
      const k = smoothstep(0, 1, 1 - tc / 0.22);
      y -= Math.sin(leFold) * chord * k * 0.4;
    }

    // Trailing edge fold (upward trim) and elevon / aileron on control band
    if (tc > 1 - ctrlRatio) {
      const k = smoothstep(0, 1, (tc - (1 - ctrlRatio)) / ctrlRatio);
      // teFold: upward fold/trim (+Y), elevonEffective: aerodynamic standard (+elevon = flap down = -Y)
      y += Math.sin(teFold) * chord * k * 0.4;
      y -= Math.sin(elevonEffective) * chord * k;
    }

    return [x, y, z];
  };
}

/**
 * 3D Keel Sampler with realistic paper fold thickness & V-shape grip (Fuselage Keel).
 * Seamlessly connects to the wing root along x: [geom.rootChord -> 0], y: 0, z: side * keelHalfWidth.
 */
function makeRealisticKeelSampler(cfg, side) {
  const geom = airframeSummary(cfg);
  const sIdx = shapeIndex(cfg.shape_flag);
  const centreFold = Math.max(0, Number(cfg.fold_center_deg) || 0);
  
  // Real origami keel height: Dart has prominent deep structural spine keel
  const baseDepthRatio = sIdx === 2 ? 0.26 : (sIdx === 1 ? 0.24 : 0.32);
  const height = geom.rootChord * (baseDepthRatio + 0.30 * Math.sin((centreFold * Math.PI) / 180));
  const leFold = ((Number(cfg.fold_leading_deg) || 0) * Math.PI) / 180;
  const keelHalfWidth = Math.min(0.005, geom.span * (sIdx === 0 ? 0.008 : 0.015));


  return (s, t) => {
    const tc = Math.min(Math.max(t, 0), 1);
    // x: nose apex at geom.rootChord, trailing edge at 0
    const x = geom.rootChord * (1 - tc);

    let top = 0;
    if (leFold !== 0 && tc < 0.2) {
      top -= Math.sin(leFold) * geom.rootChord * smoothstep(0, 1, 1 - tc / 0.2) * 0.4;
    }

    // Profile of authentic keel:
    // Shallow at nose tip, deepening rapidly around 25-45% chord (the ergonomic grip zone),
    // and tapering cleanly toward the tail
    const gripBell = Math.sin(Math.PI * Math.min(tc * 1.5, 1.0));
    const depthCurve = 0.35 + 0.65 * Math.pow(gripBell, 0.7);
    const drop = height * depthCurve;
    const y = top - drop * s;

    // V-shaped keel wall:
    // At s=0 (wing root), width is exactly keelHalfWidth.
    // At s=1 (creased bottom spine), pinches tight to centerline paper thickness.
    const paperThickness = 0.00035;
    const z = side * (keelHalfWidth * (1.0 - s * 0.92) + paperThickness * s);

    return [x, y, z];
  };
}

/**
 * 3D Folded Nose Lock & Ballast Sampler (Nose Lock / Ballast Layers).
 * Fits directly on top of the main wing surface at the nose.
 */
function makeNoseLockSampler(cfg, side) {
  const geom = airframeSummary(cfg);
  const sIdx = shapeIndex(cfg.shape_flag);
  // Nose lock extends ~25% to 35% of root chord depending on airframe type
  const lockLength = geom.rootChord * (sIdx === 2 ? 0.36 : (sIdx === 1 ? 0.30 : 0.24));
  const lockWidth = geom.span * (sIdx === 2 ? 0.13 : (sIdx === 1 ? 0.10 : 0.07));
  const keelHalfWidth = Math.min(0.005, geom.span * 0.015);

  return (s, t) => {
    const u = Math.min(Math.max(s, 0), 1); // 0 (center keel junction) to 1 (outer fold)
    const v = Math.min(Math.max(t, 0), 1); // 0 (nose apex) to 1 (rear of lock fold)

    // Nose apex is at x = geom.rootChord, tapering backward
    const x = geom.rootChord - v * lockLength;
    // Layer sits slightly proud above the main wing surface (y ~ 0) for tangible physical sheet relief
    const y = 0.0009 + 0.0005 * (1.0 - v * 0.5);
    const z = side * (keelHalfWidth + u * (1.0 - v * 0.45) * lockWidth);

    return [x, y, z];
  };
}

/**
 * Under-fuselage nose ballast reinforcement sampler (adds double-folded paper depth at the nose).
 */
function makeUnderNoseBallastSampler(cfg, side) {
  const geom = airframeSummary(cfg);
  const ballastLength = geom.rootChord * 0.22;
  const keelHalfWidth = Math.min(0.005, geom.span * 0.015);

  return (s, t) => {
    const u = Math.min(Math.max(s, 0), 1);
    const v = Math.min(Math.max(t, 0), 1);
    const x = geom.rootChord - v * ballastLength;
    const y = -0.0012 * (1.0 - v) * (1.0 - u * 0.5);
    const z = side * keelHalfWidth * (1.05 - u * 0.7);
    return [x, y, z];
  };
}

/**
 * 3D Under-wing origami tuck layer sampler.
 * In authentic folded paper airplanes (Dart, Suzanne, Sky King), folding the wings creates
 * under-wing triangular tuck flaps along the fuselage keel junction that stiffen the wing root.
 */
function makeUnderWingTuckSampler(cfg, side) {
  const geom = airframeSummary(cfg);
  const sIdx = shapeIndex(cfg.shape_flag);
  const keelHalfWidth = Math.min(0.005, geom.span * 0.015);
  // Tuck flap extends along the first 35% - 55% of chord and 22% - 32% of span
  const tuckChord = geom.rootChord * (sIdx === 0 ? 0.60 : (sIdx === 1 ? 0.45 : 0.38));
  const tuckSpan = (geom.span / 2) * (sIdx === 0 ? 0.32 : (sIdx === 1 ? 0.26 : 0.20));

  return (s, t) => {
    const u = Math.min(Math.max(s, 0), 1); // 0 (keel root junction) to 1 (outer triangular fold edge)
    const v = Math.min(Math.max(t, 0), 1); // 0 (front apex) to 1 (rear of tuck fold)

    // Sits just 0.5mm below the main wing underside for visible physical layer relief
    const x = geom.rootChord - v * tuckChord * (1.0 - u * 0.35);
    const y = -0.0006 - 0.0004 * (1.0 - v * 0.5);
    const z = side * (keelHalfWidth + u * (1.0 - v * 0.75) * tuckSpan);

    return [x, y, z];
  };
}

function pushSurface(target, sampler, spanSegments, chordSegments, flip = false, uvOffset = [0, 0], isLeftWing = false) {
  const base = target.positions.length / 3;
  for (let i = 0; i <= spanSegments; i += 1) {
    const s = i / spanSegments;
    const uSpan = isLeftWing ? (1.0 - s) * 0.5 : 0.5 + s * 0.5;
    for (let j = 0; j <= chordSegments; j += 1) {
      const t = j / chordSegments;
      const p = sampler(s, t);
      target.positions.push(p[0], p[1], p[2]);
      target.uvs.push(uSpan, t);
    }
  }
  const row = chordSegments + 1;
  for (let i = 0; i < spanSegments; i += 1) {
    for (let j = 0; j < chordSegments; j += 1) {
      const a = base + i * row + j;
      const b = a + 1;
      const c = a + row;
      const d = c + 1;
      if (flip) target.indices.push(a, c, b, b, c, d);
      else target.indices.push(a, b, c, b, d, c);
    }
  }
  return base;
}

export function buildAirframeGeometry(cfg) {
  const sink = { positions: [], uvs: [], indices: [] };

  // 1. Right & Left main wings (with seamless keel root fusion, dihedral, camber & elevons)
  pushSurface(sink, makeWingSampler(cfg, 1), SPAN_SEGMENTS, CHORD_SEGMENTS, false, [0.5, 0], false);
  pushSurface(sink, makeWingSampler(cfg, -1), SPAN_SEGMENTS, CHORD_SEGMENTS, true, [0.0, 0], true);

  // 2. 3D V-shaped Keel fuselage (Left & Right side walls with authentic paper spine thickness)
  pushSurface(sink, makeRealisticKeelSampler(cfg, 1), 4, CHORD_SEGMENTS, false, [0.5, 0], false);
  pushSurface(sink, makeRealisticKeelSampler(cfg, -1), 4, CHORD_SEGMENTS, true, [0.0, 0], true);

  // 3. Triangular nose lock ballast layers (Upper Left & Right, resting on main wing)
  pushSurface(sink, makeNoseLockSampler(cfg, 1), 3, 4, false, [0.5, 0], false);
  pushSurface(sink, makeNoseLockSampler(cfg, -1), 3, 4, true, [0.0, 0], true);

  // 4. Under-nose ballast fold layers
  pushSurface(sink, makeUnderNoseBallastSampler(cfg, 1), 2, 3, false, [0.5, 0], false);
  pushSurface(sink, makeUnderNoseBallastSampler(cfg, -1), 2, 3, true, [0.0, 0], true);

  // 5. Authentic under-wing structural tuck flaps (Left & Right)
  pushSurface(sink, makeUnderWingTuckSampler(cfg, 1), 3, 4, true, [0.5, 0], false);
  pushSurface(sink, makeUnderWingTuckSampler(cfg, -1), 3, 4, false, [0.0, 0], true);

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(sink.positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(sink.uvs, 2));
  geometry.setIndex(sink.indices);
  geometry.computeVertexNormals();

  const count = sink.positions.length / 3;
  // Natural high-grade origami paper tone (warm ivory white)
  const paper = new Float32Array(count * 3);
  for (let i = 0; i < count; i += 1) {
    paper[i * 3] = 0.980;
    paper[i * 3 + 1] = 0.974;
    paper[i * 3 + 2] = 0.960;
  }
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(paper, 3));
  geometry.computeBoundingSphere();
  geometry.computeBoundingBox();
  return geometry;
}

/**
 * Realistic origami crease lines & silhouette wire (Crease Lines).
 * Highlights the authentic fold lines of actual paper airplanes:
 * - Center keel spine fold
 * - Wing root junction creases
 * - Elevon hinge crease lines
 * - Winglet tip vertical fold lines
 * - Nose lock fold triangles & tuck edges
 * - Under-wing triangular tuck creases
 */
export function buildCreaseLines(cfg) {
  const positions = [];
  const rightWing = makeWingSampler(cfg, 1);
  const leftWing = makeWingSampler(cfg, -1);
  const rightKeel = makeRealisticKeelSampler(cfg, 1);
  const leftKeel = makeRealisticKeelSampler(cfg, -1);
  const rightLock = makeNoseLockSampler(cfg, 1);
  const leftLock = makeNoseLockSampler(cfg, -1);
  const rightTuck = makeUnderWingTuckSampler(cfg, 1);
  const leftTuck = makeUnderWingTuckSampler(cfg, -1);

  // Helper to add line segment
  const addLine = (p0, p1) => {
    positions.push(p0[0], p0[1], p0[2], p1[0], p1[1], p1[2]);
  };

  // 1. Center keel spine (bottom crease)
  for (let j = 0; j < CHORD_SEGMENTS; j += 1) {
    const t0 = j / CHORD_SEGMENTS;
    const t1 = (j + 1) / CHORD_SEGMENTS;
    addLine(rightKeel(1, t0), rightKeel(1, t1));
  }

  // 2. Wing root junction folds (along top of keel)
  for (let j = 0; j < CHORD_SEGMENTS; j += 1) {
    const t0 = j / CHORD_SEGMENTS;
    const t1 = (j + 1) / CHORD_SEGMENTS;
    addLine(rightWing(0, t0), rightWing(0, t1));
    addLine(leftWing(0, t0), leftWing(0, t1));
  }

  // 3. Leading edges
  for (let i = 0; i < SPAN_SEGMENTS; i += 1) {
    const s0 = i / SPAN_SEGMENTS;
    const s1 = (i + 1) / SPAN_SEGMENTS;
    addLine(rightWing(s0, 0), rightWing(s1, 0));
    addLine(leftWing(s0, 0), leftWing(s1, 0));
  }

  // 4. Trailing edges
  for (let i = 0; i < SPAN_SEGMENTS; i += 1) {
    const s0 = i / SPAN_SEGMENTS;
    const s1 = (i + 1) / SPAN_SEGMENTS;
    addLine(rightWing(s0, 1), rightWing(s1, 1));
    addLine(leftWing(s0, 1), leftWing(s1, 1));
  }

  // 5. Wing tips (outer edges)
  addLine(rightWing(1, 0), rightWing(1, 1));
  addLine(leftWing(1, 0), leftWing(1, 1));

  // 6. Elevon hinge line (control band fold)
  const ctrlRatio = Math.min(Math.max(Number(cfg.elevon_chord_ratio) || 0.22, 0.02), 0.6);
  const teFoldT = 1 - ctrlRatio;
  for (let i = 0; i < SPAN_SEGMENTS; i += 1) {
    const s0 = i / SPAN_SEGMENTS;
    const s1 = (i + 1) / SPAN_SEGMENTS;
    addLine(rightWing(s0, teFoldT), rightWing(s1, teFoldT));
    addLine(leftWing(s0, teFoldT), leftWing(s1, teFoldT));
  }

  // 7. Winglet tip fold line (standing vertical guide)
  const sIdx = shapeIndex(cfg.shape_flag);
  const tipStart = sIdx === 2 ? 0.78 : (sIdx === 0 ? 0.88 : 0.82);
  addLine(rightWing(tipStart, 0), rightWing(tipStart, 1));
  addLine(leftWing(tipStart, 0), leftWing(tipStart, 1));

  // 8. Nose lock triangular crease lines
  addLine(rightLock(0, 0), rightLock(1, 1));
  addLine(rightLock(1, 1), rightLock(0, 1));
  addLine(leftLock(0, 0), leftLock(1, 1));
  addLine(leftLock(1, 1), leftLock(0, 1));

  // 9. Under-wing structural tuck diagonal fold lines
  addLine(rightTuck(0, 0), rightTuck(1, 1));
  addLine(rightTuck(1, 1), rightTuck(0, 1));
  addLine(leftTuck(0, 0), leftTuck(1, 1));
  addLine(leftTuck(1, 1), leftTuck(0, 1));

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  return geometry;
}

/** Panel lattice used by the "パネル" toggle: chordwise + spanwise lines. */
export function buildPanelLines(cfg) {
  const positions = [];
  const samplerRight = makeWingSampler(cfg, 1);
  const samplerLeft = makeWingSampler(cfg, -1);
  for (let i = 0; i <= SPAN_SEGMENTS; i += 1) {
    const s = i / SPAN_SEGMENTS;
    for (let j = 0; j < CHORD_SEGMENTS; j += 1) {
      const t0 = j / CHORD_SEGMENTS;
      const t1 = (j + 1) / CHORD_SEGMENTS;
      for (const sampler of [samplerRight, samplerLeft]) {
        const p0 = sampler(s, t0);
        const p1 = sampler(s, t1);
        positions.push(p0[0], p0[1], p0[2], p1[0], p1[1], p1[2]);
      }
    }
  }
  for (let j = 0; j <= CHORD_SEGMENTS; j += 1) {
    const t = j / CHORD_SEGMENTS;
    for (const sampler of [samplerRight, samplerLeft]) {
      const p0 = sampler(0, t);
      const p1 = sampler(1, t);
      positions.push(p0[0], p0[1], p0[2], p1[0], p1[1], p1[2]);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  return geometry;
}

/**
 * Orientation of the server stress grid.
 *
 * `stress_index` is a row-major 2-D array and the payload publishes the
 * resolution of both axes (`spanwise` = stations across the full span,
 * `chordwise` = stations from the leading edge to the trailing edge), so the
 * two possible layouts are told apart by the array shape:
 *
 *   rows.length === spanwise   ->  grid[spanwise][chordwise]
 *   rows.length === chordwise  ->  grid[chordwise][spanwise]
 *
 * A square grid (or a payload without the metadata) is resolved from the
 * cantilever peak instead: the peak sits on the wing root line, so the axis
 * whose peak coordinate is closest to the middle of the grid is the span
 * axis.
 */
function rowsWalkSpanwise(grid, rows) {
  const rowCount = rows.length;
  const colCount = (rows[0] || []).length;
  const declaredSpan = Number(grid && grid.spanwise);
  const declaredChord = Number(grid && grid.chordwise);
  const hasSpan = Number.isFinite(declaredSpan) && declaredSpan > 0;
  const hasChord = Number.isFinite(declaredChord) && declaredChord > 0;
  if (hasSpan && hasChord && declaredSpan !== declaredChord) {
    if (rowCount === declaredSpan && colCount === declaredChord) return true;
    if (rowCount === declaredChord && colCount === declaredSpan) return false;
  }
  let peakRow = 0;
  let peakCol = 0;
  let peak = -Infinity;
  for (let r = 0; r < rowCount; r += 1) {
    const line = rows[r] || [];
    for (let c = 0; c < line.length; c += 1) {
      const value = Number(line[c]);
      if (Number.isFinite(value) && value > peak) {
        peak = value;
        peakRow = r;
        peakCol = c;
      }
    }
  }
  const rowMid = (rowCount - 1) / 2;
  const colMid = (colCount - 1) / 2;
  return Math.abs(peakRow - rowMid) < Math.abs(peakCol - colMid);
}

/**
 * Bilinear sample of the server stress grid.
 *
 * The mesh UV is `(uSpan, t)`: `u` runs left tip -> wing root -> right tip
 * (spanwise) and `v` runs leading edge -> trailing edge (chordwise).  Which
 * array axis carries the span is taken from the payload's axis contract
 * (`rowsWalkSpanwise`), so the wing-root leading-edge peak is always drawn
 * on the root line and always decays along the span.
 */
function sampleGrid(grid, u, v, axes) {
  const rows = grid && grid.stress_index;
  if (!rows || !rows.length) return 0;
  const rowCount = rows.length;
  const colCount = (rows[0] || []).length;
  if (!colCount) return 0;

  const spanFrac = Math.min(Math.max(u, 0), 1);
  const chordFrac = Math.min(Math.max(v, 0), 1);
  const rowsAreSpanwise = axes ? axes.rowsAreSpanwise : rowsWalkSpanwise(grid, rows);

  const rowExtent = rowCount - 1;
  const colExtent = colCount - 1;
  const rowPos = (rowsAreSpanwise ? spanFrac : chordFrac) * rowExtent;
  const colPos = (rowsAreSpanwise ? chordFrac : spanFrac) * colExtent;

  const y0 = Math.min(Math.floor(rowPos), rowExtent);
  const x0 = Math.min(Math.floor(colPos), colExtent);
  const wy = rowPos - y0;
  const wx = colPos - x0;
  const r0 = rows[y0] || [];
  const r1 = rows[Math.min(y0 + 1, rowExtent)] || [];
  const a = r0[x0] ?? 0;
  const b = r0[Math.min(x0 + 1, colExtent)] ?? a;
  const c = r1[x0] ?? a;
  const d = r1[Math.min(x0 + 1, colExtent)] ?? c;
  return (a * (1 - wx) + b * wx) * (1 - wy) + (c * (1 - wx) + d * wx) * wy;
}

export function applyStressColors(geometry, grid, heatColour) {
  if (!geometry) return;
  const positions = geometry.getAttribute("position");
  if (!positions) return;
  let count = positions.count;
  let colors = geometry.getAttribute("color");
  if (!colors || colors.count !== count) {
    colors = new THREE.Float32BufferAttribute(new Float32Array(count * 3), 3);
    geometry.setAttribute("color", colors);
  }
  const uv = geometry.getAttribute("uv");
  const box = new THREE.Box3();
  const size = new THREE.Vector3();
  let useBox = false;
  if (!uv) {
    geometry.computeBoundingBox();
    box.copy(geometry.boundingBox || new THREE.Box3());
    box.getSize(size);
    useBox = true;
  }
  // The grid orientation only depends on the payload, so resolve it once
  // instead of once per vertex.
  const gridRows = grid && grid.stress_index;
  const axes = gridRows && gridRows.length
    ? { rowsAreSpanwise: rowsWalkSpanwise(grid, gridRows) }
    : null;
  for (let i = 0; i < count; i += 1) {
    let u;
    let v;
    if (useBox) {
      const x = positions.getX(i);
      const z = positions.getZ(i);
      u = size.z > 1e-9 ? Math.min(Math.max((z - box.min.z) / size.z, 0), 1) : 0.5;
      v = size.x > 1e-9 ? Math.min(Math.max((box.max.x - x) / size.x, 0), 1) : 0.5;
    } else {
      u = uv.getX(i);
      v = uv.getY(i);
    }
    const value = sampleGrid(grid, u, v, axes);
    const [r, g, b] = heatColour(value);
    colors.setXYZ(i, r / 255, g / 255, b / 255);
  }
  colors.needsUpdate = true;
  return colors;
}

let sharedPaperTexture = null;

/**
 * Procedural paper texture generator.
 * Creates an authentic micro-fiber bump texture on an HTML canvas,
 * giving the procedural airframe the tactile realism of real origami bond paper.
 */
function getPaperTextures() {
  if (typeof document === "undefined") return { map: null };
  if (!sharedPaperTexture) {
    try {
      const size = 512;
      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.fillStyle = "#faf7f2";
        ctx.fillRect(0, 0, size, size);

        const imgData = ctx.getImageData(0, 0, size, size);
        const data = imgData.data;

        // Paper pulp micro-fiber grain noise
        for (let y = 0; y < size; y += 1) {
          for (let x = 0; x < size; x += 1) {
            const idx = (y * size + x) * 4;
            const n = (Math.random() - 0.5) * 14;
            const streak = Math.sin(x * 0.12 + Math.cos(y * 0.05) * 2.0) * 3.5;
            const v = Math.min(255, Math.max(0, 246 + n + streak));
            data[idx] = v;
            data[idx + 1] = Math.max(0, v - 2);
            data[idx + 2] = Math.max(0, v - 5);
            data[idx + 3] = 255;
          }
        }
        ctx.putImageData(imgData, 0, 0);

        // Dispersed fibrous strands
        ctx.strokeStyle = "rgba(160, 145, 130, 0.09)";
        ctx.lineWidth = 0.8;
        for (let i = 0; i < 280; i += 1) {
          const fx = Math.random() * size;
          const fy = Math.random() * size;
          const len = 3 + Math.random() * 8;
          const ang = Math.random() * Math.PI * 2;
          ctx.beginPath();
          ctx.moveTo(fx, fy);
          ctx.lineTo(fx + Math.cos(ang) * len, fy + Math.sin(ang) * len);
          ctx.stroke();
        }

        sharedPaperTexture = new THREE.CanvasTexture(canvas);
        sharedPaperTexture.wrapS = THREE.RepeatWrapping;
        sharedPaperTexture.wrapT = THREE.RepeatWrapping;
        sharedPaperTexture.repeat.set(8, 8);
      }
    } catch (e) {
      // Graceful fallback for non-browser/headless environments
      sharedPaperTexture = null;
    }
  }
  return { map: sharedPaperTexture };
}

export function createAirframeMaterial() {
  const { map } = getPaperTextures();
  return new THREE.MeshStandardMaterial({
    color: 0xffffff,
    vertexColors: true,
    side: THREE.DoubleSide,
    flatShading: false, // Smooth surface normals along wing panels
    roughness: 0.82, // Tactile paper diffuse
    metalness: 0.02,
    bumpMap: map,
    bumpScale: 0.0008,
  });
}

export function createCreaseMaterial() {
  return new THREE.LineBasicMaterial({
    color: 0x334155, // Slate 700 - sharp origami crease shadow
    transparent: true,
    opacity: 0.85,
    linewidth: 2.0,
  });
}

export function createEdgeMaterial() {
  return new THREE.LineBasicMaterial({
    color: 0x0f172a, // Slate 900 - crisp paper boundary edge
    transparent: true,
    opacity: 0.90,
    linewidth: 2.2,
  });
}


export function createPanelMaterial() {
  return new THREE.LineBasicMaterial({
    color: 0x1a73e8,
    transparent: true,
    opacity: 0.42,
    depthWrite: false,
  });
}