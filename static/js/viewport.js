/*
 * viewport.js — Three.js scene: ground reference, procedural airframe,
 * stress heat map, trajectory/ghost tracks and the wind particle system.
 *
 * Frame convention.  The simulator works in x-forward / y-right / z-down.
 * three.js is y-up, so the whole scene is rotated by +90 deg about +x:
 *
 *     three = (x, -z, y)          R = Rx(+90deg), a proper rotation
 *
 * Airframe geometry is authored directly in three-space (nose +x, wings +z).
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  applyStressColors,
  buildAirframeGeometry,
  buildCreaseLines,
  buildPanelLines,
  createAirframeMaterial,
  createCreaseMaterial,
  createEdgeMaterial,
  createPanelMaterial,
} from "./airframe.js";
import { WindParticles } from "./flowfield.js";
import { heatColour } from "./lib.js";

const SIM_TO_THREE = new THREE.Quaternion().setFromAxisAngle(
  new THREE.Vector3(1, 0, 0),
  Math.PI / 2
);
const SIM_TO_THREE_INV = SIM_TO_THREE.clone().conjugate();

const GROUND_VERT = /* glsl */ `
precision highp float;
varying vec3 vWorld;
void main() {
  vec4 world = modelMatrix * vec4(position, 1.0);
  vWorld = world.xyz;
  gl_Position = projectionMatrix * viewMatrix * world;
}
`;

const GROUND_FRAG = /* glsl */ `
precision highp float;
varying vec3 vWorld;
uniform vec3 uMinor;
uniform vec3 uMajor;
uniform vec3 uAxis;
uniform float uFade;
uniform float uSpacing;

float lineMask(vec2 p, float spacing, float weight) {
  vec2 c = p / spacing;
  vec2 g = abs(fract(c - 0.5) - 0.5) / max(fwidth(c), vec2(1e-5));
  float d = min(g.x, g.y);
  return 1.0 - smoothstep(0.0, weight, d);
}

void main() {
  float minor = lineMask(vWorld.xz, uSpacing, 1.0);
  float major = lineMask(vWorld.xz, uSpacing * 5.0, 1.2);
  float axis = 1.0 - smoothstep(0.0, 1.6, min(abs(vWorld.z), abs(vWorld.x)));
  float dist = length(vWorld.xz);
  float fade = 1.0 - smoothstep(uFade * 0.25, uFade, dist);
  vec3 colour = mix(uMinor, uMajor, major);
  colour = mix(colour, uAxis, axis * 0.85);
  float alpha = clamp(max(minor * 0.35, max(major * 0.6, axis)), 0.0, 1.0) * fade;
  if (alpha < 0.004) discard;
  gl_FragColor = vec4(colour, alpha);
}
`;

const TRACK_CAPACITY = 6000;

function isFinite3(vector) {
  return Array.isArray(vector) && vector.length >= 3 && vector.every((value) => Number.isFinite(value));
}

export class Viewport {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.onStats = options.onStats || (() => {});
    this.follow = false;
    this.heatVisible = true;
    this.trackPoints = [];
    this.ghostPoints = [];
    this.lastPose = new THREE.Vector3();
    this.followTarget = new THREE.Vector3();
    this._budget = 0.65;

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
      preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(0xe7e4dc, 1);

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0xe7e4dc, 5, 26);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.02, 400);
    this.camera.position.set(0.86, 0.42, 0.92);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.minDistance = 0.04;
    this.controls.maxDistance = 120;
    this.controls.maxPolarAngle = Math.PI * 0.85; // Allow viewing from underneath the aircraft/paper
    this.controls.screenSpacePanning = true;
    this.controls.target.set(0.12, 0.08, 0);

    this._buildEnvironment();
    this._buildAirframe();
    this._buildTracks();

    this.particles = new WindParticles(this.scene, this.renderer);

    this.clock = new THREE.Clock();
    this.frameTimes = [];
    this.lastStats = 0;
    this.elapsed = 0;
    this.running = true;
    this._loop = this._loop.bind(this);
    this.frameHandle = requestAnimationFrame(this._loop);

    this.resize();
    this._observer = new ResizeObserver(() => this.resize());
    this._observer.observe(canvas.parentElement || canvas);
    canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      this.running = false;
    });
  }

  /* ------------------------------------------------------------- scene */
  _buildEnvironment() {
    const hemi = new THREE.HemisphereLight(0xfdfbf6, 0xb9b3a6, 1.05);
    this.scene.add(hemi);
    const key = new THREE.DirectionalLight(0xfff6e8, 1.35);
    key.position.set(-1.2, 2.4, 1.6);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xdfe6f2, 0.45);
    fill.position.set(1.8, 0.9, -1.4);
    this.scene.add(fill);

    this.groundMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uMinor: { value: new THREE.Color(0.62, 0.6, 0.55) },
        uMajor: { value: new THREE.Color(0.42, 0.4, 0.36) },
        uAxis: { value: new THREE.Color(0.24, 0.26, 0.3) },
        uFade: { value: 30 },
        uSpacing: { value: 0.2 },
      },
      vertexShader: GROUND_VERT,
      fragmentShader: GROUND_FRAG,
      transparent: true,
      depthWrite: false,
    });
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), this.groundMaterial);
    ground.rotation.x = -Math.PI / 2;
    ground.renderOrder = -1;
    this.scene.add(ground);

    const axes = new THREE.BufferGeometry();
    axes.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(
        [
          -6, 0.0006, 0, 6, 0.0006, 0,
          0, 0.0006, -6, 0, 0.0006, 6,
        ],
        3
      )
    );
    this.scene.add(
      new THREE.LineSegments(
        axes,
        new THREE.LineBasicMaterial({ color: 0x2a2d33, transparent: true, opacity: 0.55 })
      )
    );

    // Launch marker: a 1 m reference square under the hand.
    const marker = new THREE.BufferGeometry();
    marker.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(
        [0, 0.001, -0.5, 0, 0.001, 0.5, 0, 0.001, 0.5, 0.6, 0.001, 0.5, 0.6, 0.001, 0.5],
        3
      )
    );
    this.scene.add(
      new THREE.Line(
        marker,
        new THREE.LineBasicMaterial({ color: 0x1a73e8, transparent: true, opacity: 0.5 })
      )
    );
  }

  _buildAirframe() {
    this.airframeGroup = new THREE.Group();
    this.airframeGroup.name = "airframe";
    this.scene.add(this.airframeGroup);

    this.airframeMaterial = createAirframeMaterial();
    this.airframeMesh = null;
    this.edgeLines = null;
    this.panelLines = null;

    this.meshHolder = new THREE.Group();
    this.meshHolder.name = "imported";
    this.airframeGroup.add(this.meshHolder);

    // Grip contact indicator
    this.gripGroup = new THREE.Group();
    this.gripGroup.name = "gripIndicator";
    const gripGeo = new THREE.SphereGeometry(0.007, 16, 16);
    const gripMat = new THREE.MeshBasicMaterial({ color: 0xe65100, depthTest: false, transparent: true, opacity: 0.95 });
    const gripSphere = new THREE.Mesh(gripGeo, gripMat);
    gripSphere.renderOrder = 999;
    this.gripGroup.add(gripSphere);

    // Contact ring around grip point
    const ringGeo = new THREE.RingGeometry(0.010, 0.014, 24);
    const ringMat = new THREE.MeshBasicMaterial({ color: 0xffb74d, side: THREE.DoubleSide, depthTest: false, transparent: true, opacity: 0.85 });
    const ringMesh = new THREE.Mesh(ringGeo, ringMat);
    ringMesh.rotation.x = Math.PI / 2;
    ringMesh.renderOrder = 998;
    this.gripGroup.add(ringMesh);

    // Vertical contact pin / stem
    const pinGeo = new THREE.CylinderGeometry(0.0015, 0.0015, 0.02, 8);
    const pinMat = new THREE.MeshBasicMaterial({ color: 0xff9800, depthTest: false });
    const pinMesh = new THREE.Mesh(pinGeo, pinMat);
    pinMesh.position.y = -0.01;
    pinMesh.renderOrder = 997;
    this.gripGroup.add(pinMesh);

    this.gripGroup.visible = true;
    this.airframeGroup.add(this.gripGroup);
  }

  _rebuildAirframe(cfg) {
    const geometry = buildAirframeGeometry(cfg);
    if (this.airframeMesh) {
      this.airframeMesh.geometry.dispose();
      this.airframeGroup.remove(this.airframeMesh);
    }
    if (this.edgeLines) {
      this.edgeLines.geometry.dispose();
      this.airframeGroup.remove(this.edgeLines);
    }
    if (this.creaseLines) {
      this.creaseLines.geometry.dispose();
      this.airframeGroup.remove(this.creaseLines);
    }
    if (this.panelLines) {
      this.panelLines.geometry.dispose();
      this.airframeGroup.remove(this.panelLines);
    }
    this.airframeMesh = new THREE.Mesh(geometry, this.airframeMaterial);
    this.airframeGroup.add(this.airframeMesh);

    const edges = new THREE.EdgesGeometry(geometry, 24);
    this.edgeLines = new THREE.LineSegments(edges, createEdgeMaterial());
    this.airframeGroup.add(this.edgeLines);

    this.creaseLines = new THREE.LineSegments(buildCreaseLines(cfg), createCreaseMaterial());
    this.airframeGroup.add(this.creaseLines);

    this.panelLines = new THREE.LineSegments(buildPanelLines(cfg), createPanelMaterial());
    this.panelLines.visible = Boolean(this.showPanels);
    this.airframeGroup.add(this.panelLines);
  }

  _buildTracks() {
    this.trackGeometry = new THREE.BufferGeometry();
    this.trackPositions = new Float32Array(TRACK_CAPACITY * 3);
    this.trackColors = new Float32Array(TRACK_CAPACITY * 3);
    this.trackGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(this.trackPositions, 3).setUsage(THREE.DynamicDrawUsage)
    );
    this.trackGeometry.setAttribute(
      "color",
      new THREE.BufferAttribute(this.trackColors, 3).setUsage(THREE.DynamicDrawUsage)
    );
    this.trackGeometry.setDrawRange(0, 0);
    this.trackLine = new THREE.Line(
      this.trackGeometry,
      new THREE.LineBasicMaterial({ vertexColors: true, linewidth: 2 })
    );
    this.trackLine.frustumCulled = false;
    this.scene.add(this.trackLine);

    // High-visibility turquoise/cyan ghost track
    this.ghostGeometry = new THREE.BufferGeometry();
    this.ghostGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array(TRACK_CAPACITY * 3), 3)
    );
    this.ghostGeometry.setDrawRange(0, 0);
    this.ghostLine = new THREE.Line(
      this.ghostGeometry,
      new THREE.LineBasicMaterial({
        color: 0x00bcd4,
        transparent: true,
        opacity: 0.85,
        linewidth: 2,
      })
    );
    this.ghostLine.frustumCulled = false;
    this.scene.add(this.ghostLine);

    // Ghost landing marker (target ring on the ground where the previous flight landed)
    this.ghostMarkerGroup = new THREE.Group();
    const ringGeo1 = new THREE.RingGeometry(0.08, 0.11, 24);
    ringGeo1.rotateX(-Math.PI / 2);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0x00bcd4,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.75,
    });
    this.ghostMarkerGroup.add(new THREE.Mesh(ringGeo1, ringMat));

    const ringGeo2 = new THREE.RingGeometry(0.20, 0.22, 24);
    ringGeo2.rotateX(-Math.PI / 2);
    this.ghostMarkerGroup.add(new THREE.Mesh(ringGeo2, ringMat));

    const markerPinGeo = new THREE.CylinderGeometry(0.003, 0.003, 0.12, 8);
    const markerPinMesh = new THREE.Mesh(markerPinGeo, ringMat);
    markerPinMesh.position.y = 0.06;
    this.ghostMarkerGroup.add(markerPinMesh);

    this.ghostMarkerGroup.visible = false;
    this.scene.add(this.ghostMarkerGroup);

    // Launch vector helper (indicates release speed & launch angle at release pose)
    this.launchArrow = new THREE.ArrowHelper(
      new THREE.Vector3(1, 0, 0),
      new THREE.Vector3(0, 0, 0),
      0.5,
      0xff5722,
      0.08,
      0.04
    );
    this.launchArrow.visible = false;
    this.scene.add(this.launchArrow);
  }

  /* ------------------------------------------------------------ public */
  setAirframe(cfg) {
    this._rebuildAirframe(cfg);
    if (this.airframeMesh) this.airframeMesh.visible = !this.meshObject;
  }

  setStress(grid) {
    const target = this.meshObject || this.airframeMesh;
    if (!target || !grid) return;
    applyStressColors(target.geometry, grid, heatColour);
    if (target.material) target.material.vertexColors = this.heatVisible;
  }

  setHeatVisible(visible) {
    this.heatVisible = Boolean(visible);
    if (!this.airframeMaterial) return;
    // With the heat map off the vertex colours are ignored entirely, so the
    // airframe renders as plain paper regardless of the last stress field.
    this.airframeMaterial.vertexColors = this.heatVisible;
    this.airframeMaterial.color.set(this.heatVisible ? 0xffffff : 0xf7f2e8);
    this.airframeMaterial.needsUpdate = true;
  }

  setPose(position, quaternion) {
    const p = position || [0, 0, 0];
    this.lastPose.set(p[0], -p[2], p[1]);
    this.airframeGroup.position.copy(this.lastPose);
    if (quaternion) {
      const q = new THREE.Quaternion(quaternion[1], quaternion[2], quaternion[3], quaternion[0]);
      // Airframe vertices are authored in three-space (v_af = Rx(+90) * v_body).
      // To correctly rotate them by the sim body orientation q (v_world = q * v_body):
      // v_three_world = Rx(+90) * q * Rx(-90) * v_af
      this.airframeGroup.quaternion.copy(SIM_TO_THREE).multiply(q).multiply(SIM_TO_THREE_INV);
    }
  }

  /**
   * Pose from a recorded history sample.  Ring-buffer samples carry Euler
   * angles rather than a quaternion, and the server attitude matrix is
   * R = Rz(yaw) Ry(pitch) Rx(roll), which is exactly three's 'ZYX' order.
   */
  setPoseFromSample(sample) {
    if (!sample) return;
    const position = [sample.x_m, sample.y_m, sample.z_m];
    const DEG = Math.PI / 180;
    const euler = new THREE.Euler(
      (Number(sample.roll_deg) || 0) * DEG,
      (Number(sample.pitch_deg) || 0) * DEG,
      (Number(sample.yaw_deg) || 0) * DEG,
      "ZYX"
    );
    const q = new THREE.Quaternion().setFromEuler(euler);
    this.setPose(position, [q.w, q.x, q.y, q.z]);
  }

  /**
   * Append one recorded point.  Accepts either a live /simulation/step payload
   * (`position_m`) or a ring-buffer sample (`x_m` / `y_m` / `z_m`).
   */
  appendTrack(sample) {
    if (!sample) return;
    const p = Array.isArray(sample.position_m)
      ? sample.position_m
      : [sample.x_m, sample.y_m, sample.z_m];
    if (!isFinite3(p)) return;
    const x = p[0];
    const y = -p[2];
    const z = p[1];
    if (this.trackPoints.length) {
      const last = this.trackPoints[this.trackPoints.length - 1];
      const moved = Math.hypot(x - last[0], y - last[1], z - last[2]);
      if (moved < 0.006) return;
    }
    this.trackPoints.push([x, y, z, Number(sample.max_stress_index) || 0]);
    if (this.trackPoints.length > TRACK_CAPACITY) this.trackPoints.shift();
    this._writeTrack();
  }

  _writeTrack() {
    const count = this.trackPoints.length;
    const positions = this.trackGeometry.attributes.position;
    const colors = this.trackGeometry.attributes.color;
    const start = Math.max(0, count - TRACK_CAPACITY);
    for (let i = 0; i < count; i += 1) {
      const p = this.trackPoints[i];
      positions.array[i * 3] = p[0];
      positions.array[i * 3 + 1] = p[1];
      positions.array[i * 3 + 2] = p[2];
      const index = Math.min(Math.max((p[3] ?? 0) || 0, 0), 1);
      const [r, g, b] = heatColour(index);
      colors.array[i * 3] = r / 255;
      colors.array[i * 3 + 1] = g / 255;
      colors.array[i * 3 + 2] = b / 255;
    }
    positions.needsUpdate = true;
    colors.needsUpdate = true;
    this.trackGeometry.setDrawRange(start, count - start);
    this.trackGeometry.computeBoundingSphere();
  }

  clearTrack() {
    this.trackPoints = [];
    this.trackGeometry.setDrawRange(0, 0);
  }

  /** Move the finished track into the ghost slot before a new flight. */
  archiveTrack() {
    if (this.trackPoints.length < 4) {
      this.clearTrack();
      return;
    }
    const attribute = this.ghostGeometry.attributes.position;
    const count = Math.min(this.trackPoints.length, TRACK_CAPACITY);
    const start = this.trackPoints.length - count;
    for (let i = 0; i < count; i += 1) {
      const p = this.trackPoints[start + i];
      attribute.array[i * 3] = p[0];
      attribute.array[i * 3 + 1] = p[1];
      attribute.array[i * 3 + 2] = p[2];
    }
    attribute.needsUpdate = true;
    this.ghostGeometry.setDrawRange(0, count);
    this.ghostGeometry.computeBoundingSphere();

    // Place the ghost landing marker at the last point of the previous trajectory
    const lastP = this.trackPoints[this.trackPoints.length - 1];
    if (lastP && this.ghostMarkerGroup) {
      this.ghostMarkerGroup.position.set(lastP[0], 0.005, lastP[2]);
      this.ghostMarkerGroup.visible = Boolean(this.ghostLine.visible);
    }
    this.hasGhost = true;
    this.clearTrack();
  }

  clearGhost() {
    this.hasGhost = false;
    this.ghostGeometry.setDrawRange(0, 0);
    if (this.ghostMarkerGroup) this.ghostMarkerGroup.visible = false;
  }

  setTrackVisible(visible) {
    this.trackLine.visible = Boolean(visible);
  }

  setGhostVisible(visible) {
    const show = Boolean(visible);
    this.ghostLine.visible = show;
    if (this.ghostMarkerGroup) {
      this.ghostMarkerGroup.visible = Boolean(show && this.hasGhost);
    }
  }

  /**
   * Update the 3D launch vector arrow at release position.
   * `speedMps`: magnitude (m/s)
   * `pitchDeg`: launch angle above horizontal (deg)
   */
  setLaunchVector(speedMps, pitchDeg, heightM = 1.8) {
    if (!this.launchArrow) return;
    const speed = Math.max(0, Number(speedMps) || 0);
    if (speed < 0.1) {
      this.launchArrow.visible = false;
      return;
    }
    const pitchRad = ((Number(pitchDeg) || 0) * Math.PI) / 180;
    // Direction in three.js coords: x=forward, y=up, z=right
    const dir = new THREE.Vector3(Math.cos(pitchRad), Math.sin(pitchRad), 0).normalize();
    const length = Math.min(Math.max(speed * 0.06, 0.4), 3.0);
    this.launchArrow.position.set(0, Number(heightM) || 1.8, 0);
    this.launchArrow.setDirection(dir);
    this.launchArrow.setLength(length, length * 0.18, length * 0.09);
    this.launchArrow.visible = true;
  }

  setLaunchVectorVisible(visible) {
    if (this.launchArrow) this.launchArrow.visible = Boolean(visible);
  }

  setPanelsVisible(visible) {
    this.showPanels = Boolean(visible);
    if (this.panelLines) this.panelLines.visible = this.showPanels;
  }

  setGripVisible(visible) {
    this.showGrip = Boolean(visible);
    if (this.gripGroup) this.gripGroup.visible = this.showGrip;
  }

  updateGripPoint(point) {
    if (!this.gripGroup || !point) return;
    // point is [x_airframe, y_airframe, z_airframe] in airframe space
    // x = forward (nose toward tail), y = up/down (keel drop is negative y), z = left/right
    const x = Number(point[0]) || 0;
    const y = Number(point[1]) || 0;
    const z = Number(point[2]) || 0;
    this.gripGroup.position.set(x, y, z);
  }

  setParticlesVisible(visible) {
    this.particles.setVisible(visible);
  }

  setWindField(field) {
    this.particles.setField(field);
  }

  /* ------------------------------------------------ Ensemble Tracks (GPU 100 trajectories) */
  clearEnsembleTracks() {
    if (this._ensembleGroup) {
      this.scene.remove(this._ensembleGroup);
      disposeTree(this._ensembleGroup);
      this._ensembleGroup = null;
    }
  }

  setEnsembleTracks(trajectories) {
    this.clearEnsembleTracks();
    if (!trajectories || !trajectories.length) return;

    const group = new THREE.Group();
    group.name = "ensemble-trajectories";

    let maxDist = 0;
    trajectories.forEach((tr) => {
      const d = Number(tr.landing_distance_m) || 0;
      if (d > maxDist) maxDist = d;
    });

    const palette = [
      new THREE.Color(0x1a73e8), // Google Blue
      new THREE.Color(0x34a853), // Google Green
      new THREE.Color(0xfbbc04), // Google Amber
      new THREE.Color(0x9334e8), // Purple
      new THREE.Color(0x00bcd4), // Cyan
      new THREE.Color(0xff7043), // Deep Orange
    ];

    trajectories.forEach((tr, idx) => {
      const pts = tr.points;
      if (!pts || pts.length < 2) return;

      const isBest = Math.abs((Number(tr.landing_distance_m) || 0) - maxDist) < 1e-4;
      const positions = [];
      pts.forEach((p) => {
        // sim to three: x -> x, height -> y, lateral -> z
        positions.push(Number(p.x) || 0, Number(p.z) || 0, Number(p.y) || 0);
      });

      const geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));

      const col = isBest ? new THREE.Color(0xff2200) : palette[idx % palette.length];
      const mat = new THREE.LineBasicMaterial({
        color: col,
        transparent: true,
        opacity: isBest ? 0.95 : 0.45,
        linewidth: isBest ? 3 : 1,
      });

      const line = new THREE.Line(geom, mat);
      group.add(line);

      // Landing ground marker
      const lastPt = pts[pts.length - 1];
      if (lastPt) {
        const radius = isBest ? 0.30 : 0.12;
        const markerGeom = new THREE.CircleGeometry(radius, 16);
        markerGeom.rotateX(-Math.PI / 2);
        const markerMat = new THREE.MeshBasicMaterial({
          color: col,
          transparent: true,
          opacity: isBest ? 0.95 : 0.65,
          side: THREE.DoubleSide,
        });
        const marker = new THREE.Mesh(markerGeom, markerMat);
        marker.position.set(Number(lastPt.x) || 0, 0.005, Number(lastPt.y) || 0);
        group.add(marker);
      }
    });

    this.scene.add(group);
    this._ensembleGroup = group;
  }

  /* ------------------------------------------------ Video Recording */
  isRecording() {
    return Boolean(this._recorder && this._recorder.state === "recording");
  }

  startRecording(onStateChange) {
    if (this.isRecording()) return false;
    try {
      const stream = this.canvas.captureStream(60);
      let mimeType = "video/webm;codecs=vp9";
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = "video/webm;codecs=vp8";
      }
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = "video/webm";
      }
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = "video/mp4";
      }

      const recorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported(mimeType) ? mimeType : undefined,
        videoBitsPerSecond: 8_000_000,
      });

      this._recordedChunks = [];
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          this._recordedChunks.push(e.data);
        }
      };

      recorder.onstop = () => {
        const mime = recorder.mimeType || "video/webm";
        const ext = mime.includes("mp4") ? "mp4" : "webm";
        const blob = new Blob(this._recordedChunks, { type: mime });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
        a.href = url;
        a.download = `paper_airplane_flight_${ts}.${ext}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 3000);
        this._recordedChunks = [];
        if (onStateChange) onStateChange(false);
      };

      this._recorder = recorder;
      recorder.start(100);
      if (onStateChange) onStateChange(true);
      return true;
    } catch (err) {
      console.error("録画開始に失敗しました:", err);
      return false;
    }
  }

  stopRecording() {
    if (this._recorder && this._recorder.state === "recording") {
      this._recorder.stop();
      return true;
    }
    return false;
  }

  particleBackend() {
    return this.particles.backend;
  }

  setFollow(enabled) {
    this.follow = Boolean(enabled);
  }

  /**
   * Point the camera at the airframe from a fixed three-quarter angle, far
   * enough back that the whole span fits.  Called on boot, on reset and by the
   * 視点 button, so the airframe is always framed instead of sitting off-screen
   * at the launch height.
   */
  frameOn(centre, radius = 0.35) {
    const target = new THREE.Vector3(centre.x, centre.y, centre.z);
    const distance = Math.max(radius * 3.0, 0.5);
    const direction = new THREE.Vector3(0.62, 0.66, 0.72).normalize();
    this.controls.target.copy(target);
    this.camera.position.copy(target).addScaledVector(direction, distance);
    this.controls.update();
  }

  /** Airframe half-span based framing radius. */
  frameRadius() {
    const span = this.airframeMesh ? (this.airframeMesh.geometry.boundingSphere?.radius || 0.2) : 0.2;
    return Math.max(span * 1.15, 0.18);
  }

  resetCamera() {
    this.frameOn(this.lastPose, this.frameRadius());
  }

  setMesh(object3D) {
    if (this.meshObject) {
      this.meshHolder.remove(this.meshObject);
      disposeTree(this.meshObject);
      this.meshObject = null;
    }
    if (object3D) {
      object3D.position.set(0, 0, 0);
      this.meshHolder.add(object3D);
      this.meshObject = object3D;
      if (this.airframeMesh) this.airframeMesh.visible = false;
      if (this.edgeLines) this.edgeLines.visible = false;
    } else {
      if (this.airframeMesh) this.airframeMesh.visible = true;
      if (this.edgeLines) this.edgeLines.visible = true;
    }
  }

  resize() {
    const host = this.canvas.parentElement || this.canvas;
    const width = Math.max(host.clientWidth, 1);
    const height = Math.max(host.clientHeight, 1);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  _loop() {
    if (!this.running) return;
    this.frameHandle = requestAnimationFrame(this._loop);
    const dt = Math.min(this.clock.getDelta(), 0.1);
    this.elapsed += dt;

    if (this.follow) {
      // Stiff enough that the airframe stays framed at 6 m/s, soft enough to
      // absorb the 30-60 Hz position updates without visible stair-stepping.
      this.followTarget.lerp(this.lastPose, Math.min(1, dt * 25));
      const offset = this._followOffset || (this._followOffset = new THREE.Vector3());
      offset.copy(this.camera.position).sub(this.controls.target);
      this.controls.target.copy(this.followTarget);
      this.camera.position.copy(this.followTarget).add(offset);
    }

    this.particles.update(dt, this.elapsed);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);

    this.frameTimes.push(dt);
    if (this.frameTimes.length > 40) this.frameTimes.shift();
    if (this.elapsed - this.lastStats > 0.5) {
      this.lastStats = this.elapsed;
      const mean = this.frameTimes.reduce((a, b) => a + b, 0) / Math.max(this.frameTimes.length, 1);
      const fps = mean > 0 ? 1 / mean : 0;
      this._adaptParticles(fps);
      this.onStats({ fps, elapsed: this.elapsed, particles: this.particles.activeCount });
    }
  }

  /**
   * Trade particle count for frame rate.  The GPGPU state is untouched: only
   * the draw range shrinks, so the flow keeps simulating at full resolution
   * while rasterisation stays inside the frame budget.
   */
  _adaptParticles(fps) {
    const particles = this.particles;
    if (!particles.points) return;
    if (fps > 0 && fps < 26 && particles.budget > 0.06) {
      this._budget = Math.max(0.06, particles.budget * 0.65);
      particles.setBudget(this._budget);
    } else if (fps > 52 && particles.budget < 0.99) {
      this._budget = Math.min(1, particles.budget * 1.2);
      particles.setBudget(this._budget);
    }
  }

  dispose() {
    this.running = false;
    this.stopRecording();
    this.clearEnsembleTracks();
    cancelAnimationFrame(this.frameHandle);
    this._observer.disconnect();
    this.controls.dispose();
    this.particles.dispose();
    disposeTree(this.scene);
    this.renderer.dispose();
  }
}

function disposeTree(root) {
  root.traverse((node) => {
    if (node.geometry) node.geometry.dispose();
    if (node.material) {
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      for (const material of materials) material.dispose();
    }
  });
}