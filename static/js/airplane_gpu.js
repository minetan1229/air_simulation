/**
 * airplane_gpu.js -- procedural folded-paper airplane with a GPU stress heat map.
 *
 * Everything here runs on a GPU-accelerated custom {@link THREE.ShaderMaterial}:
 *   * the vertex shader re-folds the wing by the live dihedral angle and bends
 *     it by the aerodynamic load, so dragging the dihedral / stiffness sliders
 *     deforms the model without rebuilding any geometry;
 *   * the fragment shader maps the per-vertex ``aStress`` attribute through a
 *     blue -> green -> red ramp, i.e. the 0..1 stress index the
 *     ``/api/v1/stress/analysis`` endpoint returns.
 *
 * Frame convention.  The simulator works in a right-handed frame with
 * ``x`` forward, ``y`` right wing and ``z`` **down**; three.js is Y-up.  The
 * whole simulator frame is therefore parented under ``rig``, a group carrying a
 * +90 deg rotation about X (the map ``(x, y, z) -> (x, -z, y)``), and the
 * aircraft pose is applied to ``frame`` inside it.  The mesh itself is built in
 * three.js orientation (X forward, Y up, Z span) and counter-rotated by -90 deg
 * so that its local axes read as the simulator's axes.
 *
 * This module is loaded with a *dynamic* ``import()`` from main.js: if the
 * three.js CDN or WebGL is unavailable the import rejects and the rest of the
 * UI keeps working.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

/** Full span / chord grid resolution of the main wing (vertices are flat-shaded). */
const WING_SPAN_SEGMENTS = 10;
const WING_CHORD_SEGMENTS = 6;

/** Default airframe used when the caller has nothing better to offer. */
const DEFAULT_PLANFORM = {
  wingspan_m: 0.20,
  wing_area_m2: 0.015,
  taper_ratio: 0.70,
};

/** Design tokens read out of the stylesheet so JS and CSS stay in sync. */
function readTokens() {
  const fallback = {
    bg: "#F8F9FA",
    surface: "#FFFFFF",
    accent: "#1A73E8",
    ok: "#34A853",
    warn: "#FBBC04",
    fail: "#EA4335",
    divider: "#E8EAED",
    textSecondary: "#5F6368",
    heatLow: "#1A73E8",
    heatMid: "#34A853",
    heatHigh: "#EA4335",
  };
  if (typeof window === "undefined" || !window.getComputedStyle) {
    return fallback;
  }
  try {
    const style = window.getComputedStyle(document.documentElement);
    const pick = (name) => {
      const value = style.getPropertyValue(name);
      return value && value.trim() ? value.trim() : fallback[name];
    };
    return {
      bg: pick("--bg"),
      surface: pick("--surface"),
      accent: pick("--accent"),
      ok: pick("--ok"),
      warn: pick("--warn"),
      fail: pick("--fail"),
      divider: pick("--divider"),
      textSecondary: pick("--text-secondary"),
      heatLow: pick("--heat-low"),
      heatMid: pick("--heat-mid"),
      heatHigh: pick("--heat-high"),
    };
  } catch (err) {
    return fallback;
  }
}

/**
 * A vertex of the procedural model.
 * @typedef {{x:number,y:number,z:number,span:number,wing:number,chord:number}} PaperVertex
 */

/** Vertex accumulator for the non-indexed (flat shaded) builder. */
function createStore() {
  return {
    position: [],
    normal: [],
    aSpan: [],
    aWing: [],
    aChord: [],
    aStress: [],
  };
}

/**
 * Append one triangle.  Vertices are duplicated so that
 * `computeVertexNormals()` yields per-face normals -- folded paper reads far
 * better with crisp facet edges than with smooth interpolation.
 */
function emitTriangle(store, A, B, C) {
  const ux = B.x - A.x;
  const uy = B.y - A.y;
  const uz = B.z - A.z;
  const vx = C.x - A.x;
  const vy = C.y - A.y;
  const vz = C.z - A.z;
  let nx = uy * vz - uz * vy;
  let ny = uz * vx - ux * vz;
  let nz = ux * vy - uy * vx;
  const length = Math.hypot(nx, ny, nz);
  if (length > 1e-12) {
    nx /= length;
    ny /= length;
    nz /= length;
  } else {
    nx = 0;
    ny = 1;
    nz = 0;
  }
  const verts = [A, B, C];
  for (let i = 0; i < 3; i += 1) {
    const v = verts[i];
    store.position.push(v.x, v.y, v.z);
    store.normal.push(nx, ny, nz);
    store.aSpan.push(v.span);
    store.aWing.push(v.wing);
    store.aChord.push(v.chord);
    store.aStress.push(0);
  }
}

/** Append a quad as two triangles, flipping the diagonal for a nicer crease. */
function emitQuad(store, A, B, C, D, flip) {
  if (flip) {
    emitTriangle(store, A, C, D);
    emitTriangle(store, A, B, C);
  } else {
    emitTriangle(store, A, B, C);
    emitTriangle(store, A, C, D);
  }
}

/**
 * Planform quantities derived from the airframe configuration.
 * @param {object} planform
 * @returns {object}
 */
function resolvePlanform(planform) {
  const span = Math.max(0.02, Number(planform && planform.wingspan_m) || DEFAULT_PLANFORM.wingspan_m);
  const area = Math.max(1e-5, Number(planform && planform.wing_area_m2) || DEFAULT_PLANFORM.wing_area_m2);
  const taper = Math.min(1, Math.max(0.05, Number(planform && planform.taper_ratio) || DEFAULT_PLANFORM.taper_ratio));
  const meanChord = area / span;
  // For a linearly tapered wing the area-weighted mean chord is (cr + ct)/2.
  const rootChord = (2 * meanChord) / (1 + taper);
  const tipChord = taper * rootChord;
  return { span, area, taper, meanChord, rootChord, tipChord, halfSpan: span / 2 };
}

/**
 * Build the folded-paper-airplane geometry.
 *
 * Panels: keel (the centre fold hanging below the fuselage), a swept main wing
 * with a slightly curved leading edge, up-turned winglets, a horizontal elevon
 * surface and a small dorsal tail fin.
 *
 * @param {THREE} three
 * @param {object} options
 * @param {object} options.planform see {@link resolvePlanform}
 * @param {number} options.elevonDeg rear-edge deflection baked into the geometry
 * @returns {{geometry: THREE.BufferGeometry, planform: object}}
 */
export function buildPaperAirplaneGeometry(three, options = {}) {
  const plan = resolvePlanform(options.planform);
  const elevonDeg = Number(options.elevonDeg) || 0;
  const store = createStore();

  const { rootChord: cR, tipChord: cT, halfSpan } = plan;
  const noseX = 0.42 * cR;
  const keelDepth = 0.26 * cR;
  const tailLength = 1.15 * cR;
  const sweepTip = 0.55 * cR;
  const wingletHeight = 0.85 * cT;
  const elevonChord = 0.42 * cR;
  const elevonRoot = rootChord * 1.15;
  const elevonSpan = 0.34 * halfSpan;
  const elevonRad = (elevonDeg * Math.PI) / 180;
  const elevonDrop = Math.sin(elevonRad) * elevonChord * 0.34;

  // Leading-edge sweep grows faster than linearly so the LE looks drawn, not
  // machined: sweep(u) = sweepTip * (0.35u + 0.65u^2).
  const sweepAt = (u) => sweepTip * (0.35 * u + 0.65 * u * u);
  const spanAt = (u) => halfSpan * u;

  /** Wing surface point at span fraction u and chord fraction v (right side). */
  const wingPoint = (u, v) => {
    const xLE = -sweepAt(u);
    const xTE = -cR + u * (-sweepTip - cT + cR);
    const x = xLE + (xTE - xLE) * v;
    return {
      x,
      y: 0,
      z: spanAt(u),
      span: u,
      wing: 1,
      chord: v,
    };
  };

  // ---------------------------------------------------------------- keel ---
  // The centre fold: a thin vertical plate under the fuselage line.
  const keelUpper = [
    { x: noseX, y: 0, z: 0 },
    { x: 0, y: 0, z: 0 },
    { x: -cR, y: 0, z: 0 },
    { x: -cR - tailLength, y: 0, z: 0 },
  ];
  const keelLower = [
    { x: noseX * 0.72, y: -keelDepth, z: 0 },
    { x: -cR * 0.45, y: -keelDepth, z: 0 },
    { x: -cR * 0.92, y: -keelDepth, z: 0 },
    { x: -cR - tailLength, y: -0.32 * keelDepth, z: 0 },
  ];
  const keelVertex = (p, chord) => ({ x: p.x, y: p.y, z: p.z, span: 0, wing: 0, chord });
  for (let i = 0; i < 3; i += 1) {
    const chordA = 0.5 * (i / 3);
    const chordB = 0.5 * ((i + 1) / 3);
    emitQuad(
      store,
      keelVertex(keelUpper[i], chordA),
      keelVertex(keelLower[i], chordA),
      keelVertex(keelLower[i + 1], chordB),
      keelVertex(keelUpper[i + 1], chordB),
      i % 2 === 1,
    );
  }

  // ------------------------------------------------------------ main wing ---
  const grid = [];
  for (let iu = 0; iu <= WING_SPAN_SEGMENTS; iu += 1) {
    const u = iu / WING_SPAN_SEGMENTS;
    const row = [];
    for (let iv = 0; iv <= WING_CHORD_SEGMENTS; iv += 1) {
      row.push(wingPoint(u, iv / WING_CHORD_SEGMENTS));
    }
    grid.push(row);
  }
  for (let side = 0; side < 2; side += 1) {
    const sign = side === 0 ? 1 : -1;
    const mirror = (p) => ({ x: p.x, y: p.y, z: p.z * sign, span: p.span, wing: p.wing, chord: p.chord });
    for (let iu = 0; iu < WING_SPAN_SEGMENTS; iu += 1) {
      for (let iv = 0; iv < WING_CHORD_SEGMENTS; iv += 1) {
        // Winding has to follow the sign of the span so both faces point out.
        const a = mirror(grid[iu][iv]);
        const b = mirror(grid[iu + 1][iv]);
        const c = mirror(grid[iu + 1][iv + 1]);
        const d = mirror(grid[iu][iv + 1]);
        if (sign > 0) {
          emitQuad(store, a, b, c, d, false);
        } else {
          emitQuad(store, a, d, c, b, false);
        }
      }
    }
  }

  // ------------------------------------------------------------- winglets ---
  const wingletBase = [
    { x: -sweepTip, y: 0, z: halfSpan },
    { x: -sweepTip - cT, y: 0, z: halfSpan },
  ];
  const wingletTop = [
    { x: -sweepTip - 0.30 * cT, y: wingletHeight, z: halfSpan - 0.10 * cT },
    { x: -sweepTip - 1.18 * cT, y: wingletHeight, z: halfSpan - 0.10 * cT },
  ];
  for (let side = 0; side < 2; side += 1) {
    const sign = side === 0 ? 1 : -1;
    const m = (p) => ({ x: p.x, y: p.y, z: p.z * sign, span: 1, wing: 1, chord: p.chord });
    const A = m({ ...wingletBase[0], chord: 0.0 });
    const B = m({ ...wingletBase[1], chord: 1.0 });
    const C = m({ ...wingletTop[1], chord: 1.0 });
    const D = m({ ...wingletTop[0], chord: 0.0 });
    if (sign > 0) {
      emitQuad(store, A, B, C, D, true);
    } else {
      emitQuad(store, A, D, C, B, true);
    }
  }

  // -------------------------------------------------- elevon / tail surface ---
  // The rear third of the panel is hinged at 62 % chord and rotated by the
  // elevon deflection, which is what changes the trim angle of attack.
  const hinge = 0.62;
  const hingeX = -elevonRoot - elevonChord * hinge;
  const elevonPoint = (u, v) => {
    const x = -elevonRoot - elevonChord * v;
    const deflected = v > hinge;
    const dx = deflected ? (x - hingeX) : 0;
    const y = deflected ? -dx * Math.tan(elevonRad) : 0;
    return {
      x: deflected ? hingeX + dx * Math.cos(elevonRad) : x,
      y: y - elevonDrop * 0,
      z: elevonSpan * u,
      span: 0.34 * u,
      wing: 0.45,
      chord: 0.55 * u + 0.45 * (v / hinge),
    };
  };
  const tailGrid = [];
  const TAIL_U = 3;
  const TAIL_V = 4;
  for (let iu = 0; iu <= TAIL_U; iu += 1) {
    const row = [];
    for (let iv = 0; iv <= TAIL_V; iv += 1) {
      row.push(elevonPoint(iu / TAIL_U, iv / TAIL_V));
    }
    tailGrid.push(row);
  }
  for (let side = 0; side < 2; side += 1) {
    const sign = side === 0 ? 1 : -1;
    const mirror = (p) => ({ x: p.x, y: p.y, z: p.z * sign, span: p.span, wing: p.wing, chord: p.chord });
    for (let iu = 0; iu < TAIL_U; iu += 1) {
      for (let iv = 0; iv < TAIL_V; iv += 1) {
        const a = mirror(tailGrid[iu][iv]);
        const b = mirror(tailGrid[iu + 1][iv]);
        const c = mirror(tailGrid[iu + 1][iv + 1]);
        const d = mirror(tailGrid[iu][iv + 1]);
        if (sign > 0) {
          emitQuad(store, a, b, c, d, false);
        } else {
          emitQuad(store, a, d, c, b, false);
        }
      }
    }
  }

  // ------------------------------------------------------------- tail fin ---
  const finRootX0 = -cR * 0.35;
  const finRootX1 = -cR - tailLength * 0.85;
  const finHeight = 0.62 * keelDepth;
  const finTopX0 = finRootX0 - 0.10 * cR;
  const fin = [
    { x: finRootX0, y: 0, z: 0 },
    { x: finRootX1, y: 0, z: 0 },
    { x: finRootX1 - 0.06 * cR, y: finHeight, z: 0 },
    { x: finTopX0, y: finHeight * 0.55, z: 0 },
  ].map((p, index) => ({ x: p.x, y: p.y, z: p.z, span: 0, wing: 0, chord: index < 2 ? 0.85 : 0.55 }));
  emitQuad(store, fin[0], fin[3], fin[2], fin[1], false);

  const geometry = new three.BufferGeometry();
  geometry.setAttribute("position", new three.Float32BufferAttribute(store.position, 3));
  geometry.setAttribute("normal", new three.Float32BufferAttribute(store.normal, 3));
  geometry.setAttribute("aSpan", new three.Float32BufferAttribute(store.aSpan, 1));
  geometry.setAttribute("aWing", new three.Float32BufferAttribute(store.aWing, 1));
  geometry.setAttribute("aChord", new three.Float32BufferAttribute(store.aChord, 1));
  geometry.setAttribute("aStress", new three.Float32BufferAttribute(store.aStress, 1));
  geometry.computeBoundingSphere();
  geometry.computeBoundingBox();
  return { geometry, planform: plan };
}

/** Build the custom heat-map material. */
function createAirplaneMaterial(three, tokens) {
  return new three.ShaderMaterial({
    side: three.DoubleSide,
    uniforms: {
      uDihedral: { value: 0.0872665 },
      uDeflect: { value: 0.012 },
      uSpan: { value: 0.20 },
      uStiffness: { value: 0.35 },
      uHeat: { value: 1 },
      uPaperColor: { value: new three.Color(0xf7f9fb) },
      uLowColor: { value: new three.Color(tokens.heatLow) },
      uMidColor: { value: new three.Color(tokens.heatMid) },
      uHighColor: { value: new three.Color(tokens.heatHigh) },
      uSkyColor: { value: new three.Color(0xffffff) },
      uGroundColor: { value: new three.Color(0xdfe3e8) },
      uLightDir: { value: new three.Vector3(0.45, 0.78, 0.44).normalize() },
      uLightColor: { value: new three.Color(0xfff7e8) },
      uAmbient: { value: 0.62 },
    },
    vertexShader: /* glsl */ `
      attribute float aStress;
      attribute float aSpan;
      attribute float aWing;
      attribute float aChord;

      uniform float uDihedral;
      uniform float uDeflect;
      uniform float uStiffness;

      varying float vStress;
      varying float vSpan;
      varying float vChord;
      varying vec3 vNormalW;
      varying vec3 vViewDir;
      varying vec3 vWorldPos;

      void main() {
        vec3 p = position;

        // --- dihedral -------------------------------------------------------
        // The spanwise offset lives in z; rotating (y, z) about the fuselage
        // line by uDihedral raises both wingtips.  Vertices with aWing < 1
        // (keel, tail fin) are blended by the same weight so they stay put.
        float weight = clamp(aWing, 0.0, 1.0);
        float zr = abs(p.z);
        float ca = cos(uDihedral);
        float sa = sin(uDihedral);
        float yRot = p.y * ca + zr * sa;
        float zRot = sign(p.z) * zr * ca;
        p.y = mix(p.y, yRot, weight);
        p.z = mix(p.z, zRot, weight);

        // --- load-driven bending --------------------------------------------
        // Cantilever approximation: a wing loaded at the tip deflects with the
        // square of the spanwise station, w(r) proportional to r^2, and a
        // stiffer sheet (paper_stiffness) shrinks the deflection.
        float r = clamp(aSpan, 0.0, 1.0);
        float softness = 1.0 - 0.65 * clamp(uStiffness, 0.0, 1.0);
        float bend = uDeflect * softness * r * r * (0.2 + 0.8 * clamp(aStress, 0.0, 1.0));
        p.y += bend * weight;
        // Elastic twist: the trailing edge lags the leading edge, so the
        // chordwise station modulates the upwash.
        p.y += uDeflect * 0.18 * softness * clamp(aStress, 0.0, 1.0) * aChord * r * weight;
        // A little inboard sweep as the wing loads up.
        p.z -= sign(p.z) * bend * 0.25 * weight;

        vStress = clamp(aStress, 0.0, 1.0);
        vSpan = clamp(aSpan, 0.0, 1.0);
        vChord = clamp(aChord, 0.0, 1.0);

        // The displacement is millimetre-scale on a 200 mm model, so the
        // undeformed normal is an accurate first-order approximation.
        vNormalW = normalize(normalMatrix * normal);

        vec4 world = modelMatrix * vec4(p, 1.0);
        vWorldPos = world.xyz;
        vec4 viewPos = viewMatrix * world;
        vViewDir = -viewPos.xyz;
        gl_Position = projectionMatrix * viewPos;
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;

      uniform vec3 uPaperColor;
      uniform vec3 uLowColor;
      uniform vec3 uMidColor;
      uniform vec3 uHighColor;
      uniform vec3 uSkyColor;
      uniform vec3 uGroundColor;
      uniform vec3 uLightDir;
      uniform vec3 uLightColor;
      uniform float uAmbient;
      uniform float uHeat;

      varying float vStress;
      varying float vSpan;
      varying float vChord;
      varying vec3 vNormalW;
      varying vec3 vViewDir;
      varying vec3 vWorldPos;

      // 0 = blue, 0.5..0.55 = green, 1 = red.  The mid-point transition is
      // deliberately narrow so the green band reads as "nominal".
      vec3 heatRamp(float t) {
        t = clamp(t, 0.0, 1.0);
        vec3 c = mix(uLowColor, uMidColor, smoothstep(0.0, 0.55, t));
        c = mix(c, uHighColor, smoothstep(0.55, 1.0, t));
        return c;
      }

      void main() {
        vec3 n = normalize(vNormalW);
        if (!gl_FrontFacing) {
          n = -n;
        }

        // Hemisphere ambient: sky colour from above, bounce colour from below.
        float hemi = 0.5 + 0.5 * dot(n, vec3(0.0, 1.0, 0.0));
        vec3 ambient = mix(uGroundColor, uSkyColor, hemi) * uAmbient;
        float lambert = max(dot(n, normalize(uLightDir)), 0.0);
        vec3 diffuse = uLightColor * lambert;

        vec3 base = mix(uPaperColor, heatRamp(vStress), uHeat);

        // Faint fibre banding along the chord so folds read as folds.
        float fibre = 0.95 + 0.05 * sin(vChord * 96.0 + vSpan * 3.0);
        vec3 color = base * (ambient + diffuse) * fibre;

        // Rim light keeps the silhouette readable on a light background.
        float rim = pow(1.0 - max(dot(n, normalize(vViewDir)), 0.0), 3.0);
        color += uLightColor * rim * 0.10 * (0.4 + 0.6 * uHeat);

        // Contact shading: a paper plane flying a few centimetres above the
        // grid should darken towards it, which is cheap stand-in ambient
        // occlusion without a shadow map.
        float ground = clamp(vWorldPos.y / 0.35, 0.0, 1.0);
        color *= mix(0.72, 1.0, ground * ground);

        gl_FragColor = vec4(color, 1.0);
      }
    `,
  });
}

export class AirplaneView {
  /**
   * @param {object} options
   * @param {HTMLCanvasElement} options.canvas
   * @param {() => object} [options.getConfig] returns the live airframe config
   * @param {(info: object) => void} [options.onFrame] called once per rendered frame
   */
  constructor(options = {}) {
    if (!options.canvas) {
      throw new Error("AirplaneView requires a canvas element");
    }
    this.canvas = options.canvas;
    this.getConfig = typeof options.getConfig === "function" ? options.getConfig : () => ({});
    this.onFrame = typeof options.onFrame === "function" ? options.onFrame : () => {};
    this.tokens = readTokens();

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = false;
    this.renderer.setClearColor(new THREE.Color(this.tokens.bg), 1);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(this.tokens.bg);

    this.camera = new THREE.PerspectiveCamera(48, 1, 0.01, 250);
    this.camera.position.set(0.42, 0.24, 0.52);

    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.target.set(0.05, 0.08, 0);
    this.controls.minDistance = 0.05;
    this.controls.maxDistance = 25;
    this.controls.maxPolarAngle = Math.PI * 0.52;

    // ---------------------------------------------------------- lighting ---
    // Shadow-free hemisphere + directional rig (prompt.md: no shadow maps,
    // the paper is thin and a shadow plane would fight the heat map).
    this.hemiLight = new THREE.HemisphereLight(0xffffff, 0xdfe3e8, 0.62);
    this.scene.add(this.hemiLight);
    this.dirLight = new THREE.DirectionalLight(0xfff7e8, 0.85);
    this.dirLight.position.set(0.45, 0.78, 0.44).multiplyScalar(5);
    this.scene.add(this.dirLight);

    // ------------------------------------------------------- ground grid ---
    this.ground = new THREE.GridHelper(6, 60, this.tokens.divider, this.tokens.divider);
    this.ground.material.opacity = 0.55;
    this.ground.material.transparent = true;
    this.ground.position.y = 0;
    this.scene.add(this.ground);

    // ------------------------------------------------- scene graph / rig ---
    // rig      : simulator frame (x fwd, y right, z down) -> three.js (Y up)
    // frame    : aircraft position + attitude inside the simulator frame
    // mesh     : the procedural model, counter-rotated to three.js orientation
    this.rig = new THREE.Group();
    this.rig.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
    this.scene.add(this.rig);

    this.frame = new THREE.Group();
    this.rig.add(this.frame);

    this.material = createAirplaneMaterial(THREE, this.tokens);
    const build = buildPaperAirplaneGeometry(THREE, {
      planform: this.getConfig() || DEFAULT_PLANFORM,
      elevonDeg: Number((this.getConfig() || {}).elevon_deg) || 0,
    });
    this.mesh = new THREE.Mesh(build.geometry, this.material);
    this.mesh.frustumCulled = false;
    this.mesh.rotation.x = -Math.PI / 2;
    this.frame.add(this.mesh);
    this.planform = build.planform;

    // -------------------------------------------------- trajectory trails ---
    this.trailMaterial = new THREE.LineBasicMaterial({ color: new THREE.Color(this.tokens.accent) });
    this.trailGeometry = new THREE.BufferGeometry();
    this.trailGeometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(3 * 512), 3));
    this.trailGeometry.setDrawRange(0, 0);
    this.trail = new THREE.Line(this.trailGeometry, this.trailMaterial);
    this.trail.frustumCulled = false;
    this.rig.add(this.trail);
    this._trailPoints = [];

    this.ghostMaterial = new THREE.LineBasicMaterial({
      color: new THREE.Color(this.tokens.textSecondary),
      transparent: true,
      opacity: 0.45,
    });
    this.ghostGeometry = new THREE.BufferGeometry();
    this.ghostGeometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(3), 3));
    this.ghostGeometry.setDrawRange(0, 0);
    this.ghost = new THREE.Line(this.ghostGeometry, this.ghostMaterial);
    this.ghost.frustumCulled = false;
    this.rig.add(this.ghost);

    // ---------------------------------------------------------------- misc --
    this._clock = new THREE.Clock();
    this._running = true;
    this._fpsAccum = 0;
    this._fpsFrames = 0;
    this._lastPlanformKey = this._planformKey();
    this._disposed = false;
    this._resizeHandler = () => this.resize();
    window.addEventListener("resize", this._resizeHandler);
    this.resize();
  }

  // ------------------------------------------------------------- geometry

  /** @returns {string} cache key identifying the planform parameters. */
  _planformKey() {
    const cfg = this.getConfig() || {};
    return [
      Number(cfg.wingspan_m || 0).toFixed(4),
      Number(cfg.wing_area_m2 || 0).toFixed(5),
      Number(cfg.taper_ratio || 0).toFixed(3),
      Number(cfg.elevon_deg || 0).toFixed(2),
    ].join("|");
  }

  /**
   * Push the live airframe configuration into the scene.  Planform changes
   * rebuild the mesh; dihedral and stiffness only touch uniforms so dragging a
   * slider stays at 60 FPS.
   * @param {object} [config]
   */
  applyParameters(config) {
    const cfg = config || this.getConfig() || {};
    const span = Math.max(0.02, Number(cfg.wingspan_m) || this.planform.span);
    const stiffness = Math.min(1, Math.max(0, Number(cfg.paper_stiffness ?? 0.35)));
    this.material.uniforms.uDihedral.value = ((Number(cfg.dihedral_deg) || 0) * Math.PI) / 180;
    this.material.uniforms.uSpan.value = span;
    this.material.uniforms.uStiffness.value = stiffness;
    // 12 mm tip deflection at full load, scaled by the real span.
    this.material.uniforms.uDeflect.value = Math.min(0.05, 0.06 * span);

    const key = this._planformKey();
    if (key !== this._lastPlanformKey) {
      this._lastPlanformKey = key;
      this.rebuildGeometry(cfg);
    }
  }

  /**
   * Rebuild the procedural mesh from the current planform.
   * @param {object} cfg
   */
  rebuildGeometry(cfg) {
    const source = cfg || this.getConfig() || {};
    const build = buildPaperAirplaneGeometry(THREE, {
      planform: source,
      elevonDeg: Number(source.elevon_deg) || 0,
    });
    const previous = this.mesh.geometry;
    this.mesh.geometry = build.geometry;
    this.planform = build.planform;
    if (previous && typeof previous.dispose === "function") {
      previous.dispose();
    }
    // Keep the live stress field across the rebuild.
    if (this._stressField) {
      this.setStressField(this._stressField);
    }
  }

  /**
   * Replace the procedural mesh with imported geometry.
   * @param {Float32Array} positions flat xyz triple array
   * @param {Uint16Array|Uint32Array|null} indices optional index buffer
   * @param {object} [metrics] mesh metrics used to rescale into metres
   * @returns {object} geometry statistics
   */
  loadBufferGeometry(positions, indices, metrics) {
    if (!positions || positions.length < 9) {
      throw new Error("loadBufferGeometry requires at least 3 vertices");
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    if (indices && indices.length >= 3) {
      geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    } else {
      const triCount = Math.floor(positions.length / 9);
      const auto = triCount * 3;
      const autoIndices = auto > 65535 ? new Uint32Array(auto) : new Uint16Array(auto);
      for (let i = 0; i < triCount; i += 1) {
        autoIndices[i * 3] = i * 3;
        autoIndices[i * 3 + 1] = i * 3 + 1;
        autoIndices[i * 3 + 2] = i * 3 + 2;
      }
      geometry.setIndex(new THREE.BufferAttribute(autoIndices, 1));
    }

    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    const box = geometry.boundingBox;
    const sizeX = Math.max(1e-6, box.max.x - box.min.x);
    const sizeY = Math.max(1e-6, box.max.y - box.min.y);
    const sizeZ = Math.max(1e-6, box.max.z - box.min.z);
    const spanZ = Math.max(sizeY, sizeZ);
    const centreY = (box.min.y + box.max.y) * 0.5;

    // Derive the helper attributes from the bounding box so the same shader,
    // the same stress sampler and the same dihedral control keep working.
    const count = positions.length / 3;
    const aSpan = new Float32Array(count);
    const aChord = new Float32Array(count);
    const aWing = new Float32Array(count);
    const aStress = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      const x = positions[i * 3];
      const y = positions[i * 3 + 1];
      const z = positions[i * 3 + 2];
      aSpan[i] = Math.min(1, Math.max(0, Math.abs(z) / (spanZ * 0.5)));
      aChord[i] = Math.min(1, Math.max(0, (box.max.x - x) / sizeX));
      aWing[i] = Math.abs(y - centreY) < spanZ * 0.12 ? 1 : 0;
      aStress[i] = 0;
    }
    geometry.setAttribute("aSpan", new THREE.BufferAttribute(aSpan, 1));
    geometry.setAttribute("aChord", new THREE.BufferAttribute(aChord, 1));
    geometry.setAttribute("aWing", new THREE.BufferAttribute(aWing, 1));
    geometry.setAttribute("aStress", new THREE.BufferAttribute(aStress, 1));

    const previous = this.mesh.geometry;
    this.mesh.geometry = geometry;
    if (previous && typeof previous.dispose === "function") {
      previous.dispose();
    }

    // Rescale an imported mesh onto the paper plane's real size: the server
    // reports `scale_hint`-corrected metres, but a mesh authored in the wrong
    // orientation still needs the longest axis along the span.
    const longest = Math.max(sizeX, sizeY, sizeZ);
    const targetSpan = this.planform.span || 0.2;
    const scale = Number.isFinite(metrics && metrics.wingspan_m) && metrics.wingspan_m > 0
      ? 1
      : THREE.MathUtils.clamp(targetSpan / longest, 0.05, 20);
    if (Math.abs(scale - 1) > 1e-3) {
      this.mesh.scale.setScalar(scale);
    } else {
      this.mesh.scale.setScalar(1);
    }
    this.planform.span = spanZ * scale;
    this.material.uniforms.uSpan.value = this.planform.span;

    return {
      vertices: count,
      triangles: geometry.index ? geometry.index.count / 3 : 0,
      scale,
      boundingBox: { min: box.min.toArray(), max: box.max.toArray() },
    };
  }

  /**
   * Rebuild the procedural mesh purely from scalar metrics (used when the
   * server publishes planform quantities but no vertex data).
   * @param {object} metrics
   */
  applyMetricsGeometry(metrics) {
    const m = metrics || {};
    this.rebuildGeometry({
      wingspan_m: Number(m.wingspan_m) || this.planform.span,
      wing_area_m2: Number(m.planform_area_m2) || Number(m.surface_area_m2) || this.planform.area,
      taper_ratio: Number(m.taper_ratio) || this.planform.taper,
      elevon_deg: Number((this.getConfig() || {}).elevon_deg) || 0,
    });
  }

  // ------------------------------------------------------------- stress

  /**
   * Sample a stress field and upload it into the ``aStress`` attribute.
   *
   * Accepts either a 2-D grid (``[[spanwise][chordwise]]``) or a 1-D
   * spanwise profile, which is exactly what
   * ``POST /api/v1/stress/analysis`` returns.
   *
   * @param {Array} field
   */
  setStressField(field) {
    this._stressField = field;
    const geometry = this.mesh.geometry;
    const attribute = geometry.getAttribute("aStress");
    if (!attribute) {
      return;
    }
    const spanAttribute = geometry.getAttribute("aSpan");
    const chordAttribute = geometry.getAttribute("aChord");
    if (!spanAttribute || !chordAttribute || !Array.isArray(field) || field.length === 0) {
      return;
    }

    const grid2d = Array.isArray(field[0]) && typeof field[0].length === "number";
    const rows = grid2d ? field.length : 0;
    const cols = grid2d && field[0].length ? field[0].length : 0;
    const profile = grid2d ? null : field;

    const bilinear = (r, c) => {
      const i0 = Math.min(rows - 1, Math.max(0, Math.floor(r)));
      const j0 = Math.min(cols - 1, Math.max(0, Math.floor(c)));
      const i1 = Math.min(rows - 1, i0 + 1);
      const j1 = Math.min(cols - 1, j0 + 1);
      const fr = r - i0;
      const fc = c - j0;
      const at = (i, j) => {
        const value = Number(field[i] && field[i][j]);
        return Number.isFinite(value) ? value : 0;
      };
      const a = at(i0, j0) * (1 - fr) + at(i1, j0) * fr;
      const b = at(i0, j1) * (1 - fr) + at(i1, j1) * fr;
      return a * (1 - fc) + b * fc;
    };

    const profileAt = (r) => {
      const index = Math.min(profile.length - 1, Math.max(0, Math.round(r * (profile.length - 1))));
      const value = Number(profile[index]);
      return Number.isFinite(value) ? value : 0;
    };

    const count = attribute.count;
    for (let i = 0; i < count; i += 1) {
      const s = spanAttribute.getX(i);
      const c = chordAttribute.getX(i);
      const value = grid2d ? bilinear(s * (rows - 1), c * (cols - 1)) : profileAt(s);
      attribute.setX(i, Math.min(1, Math.max(0, value)));
    }
    attribute.needsUpdate = true;
  }

  /**
   * Turn the heat map on or off (paper colour when off).
   * @param {boolean} enabled
   */
  setHeatMap(enabled) {
    this.material.uniforms.uHeat.value = enabled ? 1 : 0;
  }

  // --------------------------------------------------------------- pose

  /**
   * Place the aircraft from a simulation state.
   * @param {{position_m?: number[], quaternion?: number[]}} state
   */
  setPose(state) {
    if (!state) {
      return;
    }
    const p = state.position_m || state.position;
    if (Array.isArray(p) && p.length >= 3) {
      this.frame.position.set(Number(p[0]) || 0, Number(p[1]) || 0, Number(p[2]) || 0);
    }
    const q = state.quaternion;
    if (Array.isArray(q) && q.length >= 4) {
      // The simulator stores (w, x, y, z); three.js stores (x, y, z, w).
      const w = Number(q[0]);
      const x = Number(q[1]);
      const y = Number(q[2]);
      const z = Number(q[3]);
      const norm = Math.hypot(w, x, y, z) || 1;
      this.frame.quaternion.set(x / norm, y / norm, z / norm, w / norm);
    }
  }

  /**
   * Append a point to the live trajectory trail (simulator frame).
   * @param {number[]} position
   */
  pushTrailPoint(position) {
    if (!Array.isArray(position) || position.length < 3) {
      return;
    }
    this._trailPoints.push([Number(position[0]) || 0, Number(position[1]) || 0, Number(position[2]) || 0]);
    const capacity = this.trailGeometry.getAttribute("position").count;
    if (this._trailPoints.length > capacity) {
      this._trailPoints.shift();
    }
    this._flushTrail();
  }

  /** Rewrite the trail vertex buffer from the recorded points. */
  _flushTrail() {
    const attribute = this.trailGeometry.getAttribute("position");
    const n = Math.min(attribute.count, this._trailPoints.length);
    for (let i = 0; i < n; i += 1) {
      const p = this._trailPoints[i];
      attribute.setXYZ(i, p[0], p[1], p[2]);
    }
    attribute.needsUpdate = true;
    this.trailGeometry.setDrawRange(0, n);
    this.trailGeometry.computeBoundingSphere();
  }

  /** Clear the live trail and promote it to the translucent "ghost" trail. */
  archiveTrail() {
    if (this._trailPoints.length > 1) {
      const ghostAttribute = this.ghostGeometry.getAttribute("position");
      const n = Math.min(ghostAttribute.count, this._trailPoints.length);
      if (n < 2) {
        this.ghostGeometry.setAttribute(
          "position",
          new THREE.Float32BufferAttribute(new Float32Array(this._trailPoints.length * 3), 3),
        );
      }
      const target = this.ghostGeometry.getAttribute("position");
      const limit = Math.min(target.count, this._trailPoints.length);
      for (let i = 0; i < limit; i += 1) {
        const p = this._trailPoints[i];
        target.setXYZ(i, p[0], p[1], p[2]);
      }
      target.needsUpdate = true;
      this.ghostGeometry.setDrawRange(0, limit);
      this.ghostGeometry.computeBoundingSphere();
    }
    this._trailPoints = [];
    this._flushTrail();
  }

  /** Hide the archived ghost trail. */
  clearGhost() {
    this.ghostGeometry.setDrawRange(0, 0);
    this._trailPoints = [];
    this._flushTrail();
  }

  // --------------------------------------------------------------- frame

  /** Match the drawing buffer to the CSS size of the canvas. */
  resize() {
    const width = Math.max(1, this.canvas.clientWidth || window.innerWidth);
    const height = Math.max(1, this.canvas.clientHeight || window.innerHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  /**
   * Render one frame.
   * @param {number} [elapsedSeconds]
   * @returns {number} the frame delta in seconds
   */
  update(elapsedSeconds) {
    if (this._disposed) {
      return 0;
    }
    const delta = typeof elapsedSeconds === "number" ? elapsedSeconds : Math.min(0.1, this._clock.getDelta());
    this.controls.update();
    this.renderer.render(this.scene, this.camera);

    this._fpsAccum += delta;
    this._fpsFrames += 1;
    if (this._fpsAccum >= 0.5) {
      this.onFrame({ fps: this._fpsFrames / this._fpsAccum, particles: null });
      this._fpsAccum = 0;
      this._fpsFrames = 0;
    }
    return delta;
  }

  /** Report the live FPS. */
  getFps() {
    return this._fpsAccum > 0 ? this._fpsFrames / this._fpsAccum : 0;
  }

  /** Teardown: remove listeners and dispose every GPU resource. */
  dispose() {
    if (this._disposed) {
      return;
    }
    this._disposed = true;
    window.removeEventListener("resize", this._resizeHandler);
    this.controls.dispose();
    const geometries = [
      this.mesh.geometry,
      this.trailGeometry,
      this.ghostGeometry,
      this.ground.geometry,
    ];
    for (const geometry of geometries) {
      if (geometry && typeof geometry.dispose === "function") {
        geometry.dispose();
      }
    }
    const materials = [this.material, this.trailMaterial, this.ghostMaterial, this.ground.material];
    for (const material of materials) {
      if (material && typeof material.dispose === "function") {
        material.dispose();
      }
    }
    this.renderer.dispose();
  }
}

export default AirplaneView;
