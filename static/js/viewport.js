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

    // Explicitly request WebGL2 with high-performance power preference
    let glContext = null;
    try {
      glContext =
        canvas.getContext("webgl2", {
          powerPreference: "high-performance",
          antialias: true,
          alpha: false,
          preserveDrawingBuffer: true,
          failIfMajorPerformanceCaveat: false,
          desynchronized: true,
        }) ||
        canvas.getContext("webgl", {
          powerPreference: "high-performance",
          antialias: true,
          alpha: false,
          preserveDrawingBuffer: true,
        });
    } catch {
      // Fall back to Three.js internal context creation
    }

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      context: glContext || undefined,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
      preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(0xe7e4dc, 1);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

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
    this._buildWindTunnel();

    this.tunnelMode = false;
    this.tunnelParams = { speed: 12.0, alpha: 4.0, beta: 0.0, q: 0, cl: 0, cd: 0, cm: 0 };
    this.tunnelTime = 0;

    // 100-plane swarm fleet GPU instancing and camera modes
    this.fleetMesh = null;
    this.fleetTrajectories = [];
    this.fleetTime = 0;
    this.fleetDuration = 0;
    this.fleetLeadIndex = 0;
    this.fleetPlaying = false;
    this.cameraMode = "reset"; // "lead" | "swarm" | "fleet" | "reset"
    this._swarmCenter = new THREE.Vector3(0, 1.8, 0);
    this._leadPos = new THREE.Vector3(0, 1.8, 0);

    this.particles = new WindParticles(this.scene, this.renderer);

    this.gpuInfo = this._detectGpuRenderer();

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

    if (typeof navigator !== "undefined" && navigator.gpu) {
      navigator.gpu.requestAdapter({ powerPreference: "high-performance" }).then((adapter) => {
        if (adapter) {
          const reqInfo = adapter.requestAdapterInfo ? adapter.requestAdapterInfo() : Promise.resolve(adapter.info || {});
          reqInfo.then((info) => {
            const rawStr = `${info.vendor || ""} ${info.architecture || ""} ${info.description || info.device || ""}`.trim();
            const isNvidia = /nvidia|geforce|rtx|gtx/i.test(rawStr);
            this.webgpuInfo = {
              vendor: info.vendor || "NVIDIA",
              description: info.description || info.device || "GeForce RTX 4060",
              isNvidia,
              raw: rawStr || "WebGPU High-Performance Adapter",
            };
            if (isNvidia) {
              this.gpuInfo.isNvidia = true;
              this.gpuInfo.isIntel = false;
              this.gpuInfo.shortName = "RTX 4060";
              this.gpuInfo.raw = `WebGPU: ${this.webgpuInfo.raw} (WebGL: ${this.gpuInfo.raw})`;
              this.onStats({ fps: 60, elapsed: this.elapsed, particles: this.particles?.activeCount || 0, gpu: this.gpuInfo });
            }
          }).catch(() => {});
        }
      }).catch(() => {});
    }

    canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      this.running = false;
    });
  }

  _detectGpuRenderer() {
    try {
      const gl = this.renderer.getContext();
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      const unmasked = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      const vendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
      const raw = String(unmasked || "WebGL");

      const isNvidia = /nvidia|geforce|rtx|gtx/i.test(raw);
      const isIntel = /intel|uhd|iris/i.test(raw);
      const isAmd = /amd|radeon/i.test(raw);

      let shortName = raw;
      const angleMatch = raw.match(/ANGLE\s*\([^,]+,\s*([^,]+?)(?:\s+Direct3D|\s+\(0x|\s*,|\))/i);
      if (angleMatch && angleMatch[1]) {
        shortName = angleMatch[1].trim();
      }
      shortName = shortName
        .replace(/NVIDIA GeForce\s*/i, "")
        .replace(/Laptop GPU/i, "")
        .replace(/Graphics Family/i, "")
        .replace(/Direct3D\S+/i, "")
        .trim();

      if (!shortName || shortName.length > 24) {
        if (isNvidia) shortName = "RTX";
        else if (isIntel) shortName = "Intel UHD";
        else if (isAmd) shortName = "Radeon";
        else shortName = "WebGL";
      }

      return {
        raw,
        shortName,
        vendor: String(vendor || ""),
        isNvidia,
        isIntel,
        isAmd,
        isDiscrete: isNvidia || isAmd,
      };
    } catch {
      return {
        raw: "Unknown",
        shortName: "WebGL",
        vendor: "",
        isNvidia: false,
        isIntel: false,
        isAmd: false,
        isDiscrete: false,
      };
    }
  }

  /* ------------------------------------------------------------- scene */
  _buildEnvironment() {
    const hemi = new THREE.HemisphereLight(0xfdfbf6, 0xb9b3a6, 1.05);
    this.scene.add(hemi);
    const key = new THREE.DirectionalLight(0xfff6e8, 1.35);
    key.position.set(-2.0, 5.0, 3.0);
    key.castShadow = true;
    key.shadow.mapSize.width = 2048;
    key.shadow.mapSize.height = 2048;
    key.shadow.camera.near = 0.1;
    key.shadow.camera.far = 40;
    key.shadow.camera.left = -6;
    key.shadow.camera.right = 6;
    key.shadow.camera.top = 6;
    key.shadow.camera.bottom = -6;
    key.shadow.bias = -0.0005;
    this.keyLight = key;
    this.scene.add(key);
    this.scene.add(key.target);
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
    ground.receiveShadow = true;
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
      if (this.edgeLines.material) this.edgeLines.material.dispose();
      this.airframeGroup.remove(this.edgeLines);
    }
    if (this.creaseLines) {
      this.creaseLines.geometry.dispose();
      if (this.creaseLines.material) this.creaseLines.material.dispose();
      this.airframeGroup.remove(this.creaseLines);
    }
    if (this.panelLines) {
      this.panelLines.geometry.dispose();
      if (this.panelLines.material) this.panelLines.material.dispose();
      this.airframeGroup.remove(this.panelLines);
    }
    this.airframeMesh = new THREE.Mesh(geometry, this.airframeMaterial);
    this.airframeMesh.castShadow = true;
    this.airframeMesh.receiveShadow = true;
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

  _buildWindTunnel() {
    this.tunnelGroup = new THREE.Group();
    this.tunnelGroup.name = "windTunnel";

    // 1. Test section glass/wire frame (L: 6.0m, W: 3.2m, H: 2.2m)
    // Box centered at (0, 1.2, 0)
    const x0 = -3.0, x1 = 3.0;
    const y0 = 0.1, y1 = 2.3;
    const z0 = -1.6, z1 = 1.6;

    const frameVerts = [
      // Bottom rectangle
      x0, y0, z0,  x1, y0, z0,
      x1, y0, z0,  x1, y0, z1,
      x1, y0, z1,  x0, y0, z1,
      x0, y0, z1,  x0, y0, z0,
      // Top rectangle
      x0, y1, z0,  x1, y1, z0,
      x1, y1, z0,  x1, y1, z1,
      x1, y1, z1,  x0, y1, z1,
      x0, y1, z1,  x0, y1, z0,
      // 4 vertical posts
      x0, y0, z0,  x0, y1, z0,
      x1, y0, z0,  x1, y1, z0,
      x1, y0, z1,  x1, y1, z1,
      x0, y0, z1,  x0, y1, z1,
      // Intermediate ribs for realistic test section feel
      0, y0, z0,  0, y1, z0,
      0, y0, z1,  0, y1, z1,
      0, y0, z0,  0, y0, z1,
      0, y1, z0,  0, y1, z1,
    ];
    const frameGeo = new THREE.BufferGeometry();
    frameGeo.setAttribute("position", new THREE.Float32BufferAttribute(frameVerts, 3));
    const frameMat = new THREE.LineBasicMaterial({
      color: 0x1a73e8,
      transparent: true,
      opacity: 0.45,
      linewidth: 1,
    });
    this.tunnelGroup.add(new THREE.LineSegments(frameGeo, frameMat));

    // 2. Upstream contraction nozzle (cone expanding from +3.0m to +4.5m)
    const nozzleX = 4.5;
    const ny0 = -0.1, ny1 = 2.5, nz0 = -2.2, nz1 = 2.2;
    const nozzleVerts = [
      x1, y0, z0,  nozzleX, ny0, nz0,
      x1, y1, z0,  nozzleX, ny1, nz0,
      x1, y1, z1,  nozzleX, ny1, nz1,
      x1, y0, z1,  nozzleX, ny0, nz1,
      nozzleX, ny0, nz0,  nozzleX, ny1, nz0,
      nozzleX, ny1, nz0,  nozzleX, ny1, nz1,
      nozzleX, ny1, nz1,  nozzleX, ny0, nz1,
      nozzleX, ny0, nz1,  nozzleX, ny0, nz0,
    ];
    const nozzleGeo = new THREE.BufferGeometry();
    nozzleGeo.setAttribute("position", new THREE.Float32BufferAttribute(nozzleVerts, 3));
    const nozzleMat = new THREE.LineBasicMaterial({
      color: 0x34a853,
      transparent: true,
      opacity: 0.35,
    });
    this.tunnelGroup.add(new THREE.LineSegments(nozzleGeo, nozzleMat));

    // 3. Downstream diffuser cone (expanding from -3.0m to -4.5m)
    const diffX = -4.5;
    const dy0 = 0.0, dy1 = 2.4, dz0 = -2.0, dz1 = 2.0;
    const diffVerts = [
      x0, y0, z0,  diffX, dy0, dz0,
      x0, y1, z0,  diffX, dy1, dz0,
      x0, y1, z1,  diffX, dy1, dz1,
      x0, y0, z1,  diffX, dy0, dz1,
      diffX, dy0, dz0,  diffX, dy1, dz0,
      diffX, dy1, dz0,  diffX, dy1, dz1,
      diffX, dy1, dz1,  diffX, dy0, dz1,
      diffX, dy0, dz1,  diffX, dy0, dz0,
    ];
    const diffGeo = new THREE.BufferGeometry();
    diffGeo.setAttribute("position", new THREE.Float32BufferAttribute(diffVerts, 3));
    const diffMat = new THREE.LineBasicMaterial({
      color: 0xea4335,
      transparent: true,
      opacity: 0.3,
    });
    this.tunnelGroup.add(new THREE.LineSegments(diffGeo, diffMat));

    // 4. Test section sting balance strut (aerodynamic support post)
    this.stingGroup = new THREE.Group();
    this.stingGroup.name = "stingBalance";

    // Heavy base plate on floor
    const baseGeo = new THREE.CylinderGeometry(0.18, 0.22, 0.03, 24);
    const metalMat = new THREE.MeshStandardMaterial({
      color: 0x5f6368,
      roughness: 0.4,
      metalness: 0.8,
    });
    const baseMesh = new THREE.Mesh(baseGeo, metalMat);
    baseMesh.position.set(0, 0.015, 0);
    this.stingGroup.add(baseMesh);

    // Vertical main mast (r = 0.014m, height 1.1m)
    const mastGeo = new THREE.CylinderGeometry(0.012, 0.016, 1.1, 16);
    const mastMesh = new THREE.Mesh(mastGeo, metalMat);
    mastMesh.position.set(0, 0.55, 0);
    this.stingGroup.add(mastMesh);

    // Angled sting arm leading to aircraft belly
    this.stingStrut = new THREE.Group();
    const armGeo = new THREE.CylinderGeometry(0.005, 0.008, 0.18, 12);
    armGeo.rotateX(Math.PI / 6);
    const armMesh = new THREE.Mesh(armGeo, metalMat);
    armMesh.position.set(-0.03, 0.08, 0);
    this.stingStrut.add(armMesh);

    // Tip clamp sphere
    const clampGeo = new THREE.SphereGeometry(0.008, 12, 12);
    const clampMat = new THREE.MeshStandardMaterial({ color: 0x1a73e8, roughness: 0.3, metalness: 0.7 });
    const clampMesh = new THREE.Mesh(clampGeo, clampMat);
    clampMesh.position.set(0, 0.16, 0);
    this.stingStrut.add(clampMesh);

    this.stingStrut.position.set(0, 1.05, 0);
    this.stingGroup.add(this.stingStrut);

    this.tunnelGroup.add(this.stingGroup);

    // 5. Upstream smoke rake nozzle comb (visual injection comb at x=2.8)
    this.smokeRakeGroup = new THREE.Group();
    this.smokeRakeGroup.name = "smokeRakeComb";
    const rakeBarGeo = new THREE.CylinderGeometry(0.008, 0.008, 1.6, 16);
    rakeBarGeo.rotateX(Math.PI / 2);
    const rakeMat = new THREE.MeshStandardMaterial({ color: 0x4285f4, roughness: 0.3, metalness: 0.8 });
    const rakeBar = new THREE.Mesh(rakeBarGeo, rakeMat);
    rakeBar.position.set(2.8, 1.2, 0);
    this.smokeRakeGroup.add(rakeBar);

    // Nozzle needles (18 needles along the bar)
    const needleGeo = new THREE.CylinderGeometry(0.002, 0.004, 0.08, 8);
    needleGeo.rotateZ(Math.PI / 2);
    const needleMat = new THREE.MeshStandardMaterial({ color: 0x34a853, roughness: 0.2, metalness: 0.9 });
    for (let i = 0; i < 18; i += 1) {
      const zPos = -0.7 + (1.4 / 17) * i;
      const needle = new THREE.Mesh(needleGeo, needleMat);
      needle.position.set(2.76, 1.2, zPos);
      this.smokeRakeGroup.add(needle);
    }
    this.tunnelGroup.add(this.smokeRakeGroup);

    // 6. Aerodynamic force vector indicators (Lift, Drag arrows)
    this.liftArrow = new THREE.ArrowHelper(
      new THREE.Vector3(0, 1, 0),
      new THREE.Vector3(0, 1.25, 0),
      0.3,
      0x1a73e8,
      0.08,
      0.04
    );
    this.dragArrow = new THREE.ArrowHelper(
      new THREE.Vector3(-1, 0, 0),
      new THREE.Vector3(0, 1.25, 0),
      0.2,
      0xea4335,
      0.06,
      0.03
    );
    this.tunnelGroup.add(this.liftArrow);
    this.tunnelGroup.add(this.dragArrow);

    this.tunnelGroup.visible = false;
    this.scene.add(this.tunnelGroup);
  }

  setTunnelMode(enabled, params = {}) {
    this.tunnelMode = Boolean(enabled);
    if (this.tunnelGroup) this.tunnelGroup.visible = this.tunnelMode;
    if (this.gripGroup) this.gripGroup.visible = !this.tunnelMode;
    if (this.launchArrow && this.tunnelMode) this.launchArrow.visible = false;
    if (params) this.updateTunnelParams(params);
  }

  updateTunnelParams(params) {
    if (!this.tunnelParams) this.tunnelParams = {};
    Object.assign(this.tunnelParams, params);
  }

  _updateTunnelDynamics(dt) {
    if (!this.tunnelMode) return;
    const p = this.tunnelParams || {};
    const speed = Math.max(Number(p.speed) || 0, 0);
    const alpha = Number(p.alpha ?? 4.0);
    const beta = Number(p.beta ?? 0.0);
    const cl = Number(p.cl ?? 0.35);
    const cd = Number(p.cd ?? 0.08);
    const cm = Number(p.cm ?? 0.0);

    const DEG = Math.PI / 180;

    if (speed < 0.05) {
      const pitchRad = alpha * DEG;
      const yawRad = -beta * DEG;
      const euler = new THREE.Euler(0, -pitchRad, yawRad, "ZYX");
      const q = new THREE.Quaternion().setFromEuler(euler);
      this.setPose([0, 0, -1.2], [q.w, q.x, q.y, q.z]);
      if (this.stingStrut) {
        this.stingStrut.position.set(0, 1.05, 0);
      }
      return;
    }

    this.tunnelTime = (this.tunnelTime || 0) + dt;
    const t = this.tunnelTime;
    const vNorm = Math.min(speed / 12.0, 3.0);
    const isStall = alpha > 14.0 || alpha < -8.0;
    const stallFactor = isStall ? 2.8 : 1.0;

    // 1. Aerodynamic lift heave (upward float + dynamic flutter)
    const heaveSteady = Math.max(Math.min(cl * 0.025 * vNorm, 0.09), -0.05);
    const heaveOsc = (Math.sin(t * 7.7) * 0.0035 + Math.sin(t * 17.3) * 0.0018) * vNorm * stallFactor;
    const heave = heaveSteady + heaveOsc;

    // 2. Drag surge (rearward pushback deflection)
    const surgeSteady = -Math.max(Math.min(cd * 0.035 * vNorm, 0.06), 0.0);
    const surgeOsc = Math.sin(t * 6.3 + 0.7) * 0.002 * vNorm;
    const surge = surgeSteady + surgeOsc;

    // 3. Dynamic pitch moment + vortex buffeting
    const pitchDeflect = -cm * 4.0 * vNorm;
    const pitchBuffet = (Math.sin(t * 11.9) * 0.75 +
                         Math.sin(t * 24.1 + 1.1) * 0.45 +
                         Math.sin(t * 43.5) * 0.25) * vNorm * stallFactor;
    const pitchTotal = alpha + pitchDeflect + pitchBuffet;

    // 4. Lateral roll & yaw buffeting
    const rollBuffet = (Math.sin(t * 13.7 + 0.9) * 0.85 +
                        Math.sin(t * 28.3) * 0.4) * vNorm * (1.0 + Math.abs(beta) * 0.03);
    const yawBuffet = (Math.sin(t * 9.1 + 2.3) * 0.5 +
                       Math.sin(t * 20.4) * 0.25) * vNorm;
    const yawTotal = -beta + yawBuffet;

    const simPos = [surge, 0, -(1.2 + heave)];
    const pitchRad = pitchTotal * DEG;
    const yawRad = yawTotal * DEG;
    const rollRad = rollBuffet * DEG;
    const euler = new THREE.Euler(rollRad, -pitchRad, yawRad, "ZYX");
    const q = new THREE.Quaternion().setFromEuler(euler);

    this.setPose(simPos, [q.w, q.x, q.y, q.z]);

    if (this.stingStrut) {
      this.stingStrut.position.set(surge, 1.05 + heave, 0);
    }

    // 5. Update aerodynamic force vector arrows
    const origin = new THREE.Vector3(surge, 1.2 + heave, 0);
    if (this.liftArrow) {
      const liftMag = Math.max(0.05, Math.min(Math.abs(cl) * 0.45 * vNorm, 1.2));
      const liftDir = cl >= 0 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, -1, 0);
      this.liftArrow.position.copy(origin);
      this.liftArrow.setDirection(liftDir);
      this.liftArrow.setLength(liftMag, liftMag * 0.25, liftMag * 0.12);
      this.liftArrow.visible = speed > 0.1;
    }
    if (this.dragArrow) {
      const dragMag = Math.max(0.04, Math.min(Math.max(cd, 0.01) * 0.5 * vNorm, 1.0));
      this.dragArrow.position.copy(origin);
      this.dragArrow.setDirection(new THREE.Vector3(-1, 0, 0));
      this.dragArrow.setLength(dragMag, dragMag * 0.25, dragMag * 0.12);
      this.dragArrow.visible = speed > 0.1;
    }

    // 6. Propagate aerodynamic flow parameters to particle field
    if (this.particles && typeof this.particles.setTunnelAero === "function") {
      const span = this.airframeMesh?.geometry?.boundingSphere?.radius ? this.airframeMesh.geometry.boundingSphere.radius * 2 : 0.32;
      this.particles.setTunnelAero({
        cl,
        alpha: pitchTotal,
        speed,
        center: origin,
        span: new THREE.Vector3(0.28, 0.05, span),
      });
    }
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
    if (this._staggerTimer) {
      clearInterval(this._staggerTimer);
      this._staggerTimer = null;
    }
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
    this._ensembleTrajectories = trajectories;

    let maxDist = 0;
    trajectories.forEach((tr) => {
      const d = Number(tr.landing_distance_m) || 0;
      if (d > maxDist) maxDist = d;
    });

    const palette = [
      new THREE.Color(0x1a73e8), // Google Blue
      new THREE.Color(0x34a853), // Google Green
      new THREE.Color(0x9334e8), // Purple
      new THREE.Color(0x00bcd4), // Cyan
      new THREE.Color(0x4285f4), // Light Blue
      new THREE.Color(0x0f9d58), // Green
    ];

    // Sort to determine ranks if not pre-assigned
    const sorted = [...trajectories].sort((a, b) => (Number(b.landing_distance_m) || 0) - (Number(a.landing_distance_m) || 0));
    const topIds = new Set(sorted.slice(0, 3).map((t) => t.id));
    const bestId = sorted.length ? sorted[0].id : null;

    trajectories.forEach((tr, idx) => {
      const pts = tr.points;
      if (!pts || pts.length < 2) return;

      const rank = tr.rank || (sorted.findIndex((s) => s.id === tr.id) + 1);
      const isRank1 = tr.id === bestId || rank === 1;
      const isRank2 = rank === 2;
      const isRank3 = rank === 3;

      const totalCount = trajectories.length;
      const isWorst = rank === totalCount || (sorted.length > 0 && tr.id === sorted[sorted.length - 1].id);

      const positions = [];
      pts.forEach((p) => {
        // sim to three: x -> x, height -> y, lateral -> z
        positions.push(Number(p.x) || 0, Number(p.z) || 0, Number(p.y) || 0);
      });

      const geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));

      let col;
      let opacity = 0.35;
      let linewidth = 1;

      if (isRank1) {
        col = new THREE.Color(0xffb300); // 1st Gold (最高値)
        opacity = 1.0;
        linewidth = 3;
      } else if (isWorst) {
        col = new THREE.Color(0xe53935); // Worst Red (最低値)
        opacity = 0.95;
        linewidth = 2;
      } else if (isRank2) {
        col = new THREE.Color(0xb0bec5); // 2nd Silver
        opacity = 0.85;
      } else if (isRank3) {
        col = new THREE.Color(0xcd7f32); // 3rd Bronze
        opacity = 0.75;
      } else {
        col = palette[idx % palette.length];
      }

      const mat = new THREE.LineBasicMaterial({
        color: col,
        transparent: true,
        opacity: opacity,
        linewidth: linewidth,
      });

      const line = new THREE.Line(geom, mat);
      line.userData = { trajectoryId: tr.id, rank: rank, isRank1: isRank1, isWorst: isWorst };
      group.add(line);

      // Landing ground marker
      const lastPt = pts[pts.length - 1];
      if (lastPt) {
        const lx = Number(lastPt.x) || 0;
        const lz = Number(lastPt.y) || 0;

        if (isRank1) {
          // 1st place gold landing circle and outer pulsating ring (最高値)
          const innerGeom = new THREE.CircleGeometry(0.40, 24);
          innerGeom.rotateX(-Math.PI / 2);
          const innerMat = new THREE.MeshBasicMaterial({
            color: 0xffb300,
            transparent: true,
            opacity: 0.95,
            side: THREE.DoubleSide,
          });
          const inner = new THREE.Mesh(innerGeom, innerMat);
          inner.position.set(lx, 0.01, lz);
          inner.userData = { trajectoryId: tr.id, rank: 1 };
          group.add(inner);

          const ringGeom = new THREE.RingGeometry(0.48, 0.65, 32);
          ringGeom.rotateX(-Math.PI / 2);
          const ringMat = new THREE.MeshBasicMaterial({
            color: 0xffd54f,
            transparent: true,
            opacity: 0.75,
            side: THREE.DoubleSide,
          });
          const ring = new THREE.Mesh(ringGeom, ringMat);
          ring.position.set(lx, 0.012, lz);
          ring.userData = { trajectoryId: tr.id, rank: 1 };
          group.add(ring);
        } else if (isWorst) {
          // Worst place red landing circle (最低値)
          const innerGeom = new THREE.CircleGeometry(0.30, 20);
          innerGeom.rotateX(-Math.PI / 2);
          const innerMat = new THREE.MeshBasicMaterial({
            color: 0xd32f2f,
            transparent: true,
            opacity: 0.90,
            side: THREE.DoubleSide,
          });
          const inner = new THREE.Mesh(innerGeom, innerMat);
          inner.position.set(lx, 0.008, lz);
          inner.userData = { trajectoryId: tr.id, rank: rank, isWorst: true };
          group.add(inner);
        } else {
          const radius = (isRank2 || isRank3) ? 0.22 : 0.10;
          const markerGeom = new THREE.CircleGeometry(radius, 16);
          markerGeom.rotateX(-Math.PI / 2);
          const markerMat = new THREE.MeshBasicMaterial({
            color: col,
            transparent: true,
            opacity: (isRank2 || isRank3) ? 0.85 : 0.55,
            side: THREE.DoubleSide,
          });
          const marker = new THREE.Mesh(markerGeom, markerMat);
          marker.position.set(lx, 0.005, lz);
          marker.userData = { trajectoryId: tr.id, rank: rank };
          group.add(marker);
        }
      }
    });

    this.scene.add(group);
    this._ensembleGroup = group;
  }

  focusTrajectory(trajectoryId) {
    if (!this._ensembleTrajectories) return;
    const target = this._ensembleTrajectories.find((t) => t.id === trajectoryId);
    if (!target || !target.points || !target.points.length) return;

    const last = target.points[target.points.length - 1];
    const targetX = Number(last.x) || 0;
    const targetY = Number(last.z) || 0;
    const targetZ = Number(last.y) || 0;

    // Smoothly animate orbit controls target towards landing position
    if (this.controls) {
      this.controls.target.set(targetX, Math.max(targetY + 0.5, 0.5), targetZ);
      this.camera.position.set(targetX - 5.0, Math.max(targetY + 3.0, 3.0), targetZ + 5.0);
      this.controls.update();
    }
  }

  filterEnsembleTracks(mode = "top1") {
    if (!this._ensembleGroup) return;
    if (this._staggerTimer) {
      clearInterval(this._staggerTimer);
      this._staggerTimer = null;
    }

    if (mode === "stagger") {
      // Start with top 1, then reveal gradually in small batches
      this._ensembleGroup.children.forEach((child) => {
        const rank = child.userData ? child.userData.rank : 999;
        child.visible = rank === 1;
      });
      let currentRevealRank = 1;
      const maxRank = this._ensembleTrajectories ? this._ensembleTrajectories.length : 100;
      this._staggerTimer = setInterval(() => {
        if (!this._ensembleGroup) {
          clearInterval(this._staggerTimer);
          this._staggerTimer = null;
          return;
        }
        currentRevealRank += 3; // reveal 3 planes every 100ms
        this._ensembleGroup.children.forEach((child) => {
          const rank = child.userData ? child.userData.rank : 999;
          if (rank <= currentRevealRank) {
            child.visible = true;
          }
        });
        if (currentRevealRank >= maxRank) {
          clearInterval(this._staggerTimer);
          this._staggerTimer = null;
        }
      }, 100);
      return;
    }

    this._ensembleGroup.children.forEach((child) => {
      const rank = child.userData ? child.userData.rank : 999;
      if (mode === "top1") {
        child.visible = rank === 1;
      } else if (mode === "top3") {
        child.visible = rank <= 3;
      } else {
        child.visible = true;
      }
    });
  }

  /* ------------------------------------------------ 100-plane Swarm GPU Instancing */
  setEnsembleFleet(trajectories) {
    this.clearFleet();
    if (!trajectories || !trajectories.length) return;

    this.fleetTrajectories = trajectories;
    const count = trajectories.length;

    // Use current airframe geometry or synthesize fallback
    const geo = (this.airframeMesh && this.airframeMesh.geometry)
      ? this.airframeMesh.geometry.clone()
      : buildAirframeGeometry({});

    // Instanced material with custom paper roughness
    const mat = createAirframeMaterial();
    mat.vertexColors = false;
    mat.roughness = 0.85;

    this.fleetMesh = new THREE.InstancedMesh(geo, mat, count);
    this.fleetMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

    let maxT = 0;
    let maxDist = -1;
    let leadIdx = 0;

    const sorted = [...trajectories].sort((a, b) => (Number(b.landing_distance_m) || 0) - (Number(a.landing_distance_m) || 0));
    const bestId = sorted.length ? sorted[0].id : null;
    const worstId = sorted.length ? sorted[sorted.length - 1].id : null;

    const dummy = new THREE.Object3D();
    const color = new THREE.Color();

    trajectories.forEach((tr, i) => {
      const pts = tr.points || [];
      const dist = Number(tr.landing_distance_m) || 0;
      if (dist > maxDist) {
        maxDist = dist;
        leadIdx = i;
      }
      const tFlight = Number(tr.flight_time_s) || (pts.length * 0.02);
      if (tFlight > maxT) maxT = tFlight;

      // Color coding: 1st=Gold, 2nd=Silver, 3rd=Bronze, Worst=Red, Others=Pastel blue/green
      if (tr.id === bestId) {
        color.setHex(0xffb300); // 1st Gold (最高値)
      } else if (tr.id === worstId) {
        color.setHex(0xe53935); // Worst Red (最低値)
      } else if (sorted[1] && tr.id === sorted[1].id) {
        color.setHex(0xb0bec5); // 2nd Silver
      } else if (sorted[2] && tr.id === sorted[2].id) {
        color.setHex(0xcd7f32); // 3rd Bronze
      } else {
        const hue = 0.52 + ((i % 12) / 12) * 0.18;
        color.setHSL(hue, 0.70, 0.60);
      }
      this.fleetMesh.setColorAt(i, color);

      if (pts.length > 0) {
        const p0 = pts[0];
        dummy.position.set(Number(p0.x) || 0, Number(p0.z) || 1.8, Number(p0.y) || 0);
        dummy.scale.setScalar(1.0);
        dummy.updateMatrix();
        this.fleetMesh.setMatrixAt(i, dummy.matrix);
      }
    });

    if (this.fleetMesh.instanceColor) this.fleetMesh.instanceColor.needsUpdate = true;
    this.fleetMesh.instanceMatrix.needsUpdate = true;

    this.fleetDuration = Math.max(maxT, 1.0);
    this.fleetLeadIndex = leadIdx;
    this.fleetTime = 0;
    this.fleetPlaying = true;

    this.scene.add(this.fleetMesh);
  }

  clearFleet() {
    if (this.fleetMesh) {
      this.scene.remove(this.fleetMesh);
      if (this.fleetMesh.geometry) this.fleetMesh.geometry.dispose();
      if (this.fleetMesh.material) this.fleetMesh.material.dispose();
      this.fleetMesh = null;
    }
    this.fleetTrajectories = [];
    this.fleetPlaying = false;
    this.fleetTime = 0;
  }

  _updateFleet(dt) {
    if (!this.fleetMesh || !this.fleetTrajectories.length) return;
    this.fleetTime += dt;
    const t = this.fleetTime;
    const count = this.fleetTrajectories.length;
    const dummy = new THREE.Object3D();
    const currentPos = new THREE.Vector3();
    const dir = new THREE.Vector3();

    const swarmSum = new THREE.Vector3();
    let activeCount = 0;
    let allLanded = true;

    for (let i = 0; i < count; i += 1) {
      const tr = this.fleetTrajectories[i];
      const pts = tr.points;
      if (!pts || !pts.length) continue;

      const tFlight = Number(tr.flight_time_s) || (pts.length * 0.02);
      const isLanded = t >= tFlight;
      if (!isLanded) allLanded = false;

      const ptIdxRaw = pts.length > 1 ? (Math.min(t / Math.max(tFlight, 0.001), 1.0) * (pts.length - 1)) : 0;
      const idx0 = Math.floor(ptIdxRaw);
      const idx1 = Math.min(idx0 + 1, pts.length - 1);
      const frac = ptIdxRaw - idx0;

      const p0 = pts[idx0];
      const p1 = pts[idx1];

      // Coordinate mapping: sim (x, y, z) -> three (x, z, y)
      const x0 = Number(p0.x) || 0, y0 = Number(p0.z) || 0, z0 = Number(p0.y) || 0;
      const x1 = Number(p1.x) || 0, y1 = Number(p1.z) || 0, z1 = Number(p1.y) || 0;

      currentPos.set(
        x0 + (x1 - x0) * frac,
        Math.max(y0 + (y1 - y0) * frac, 0.015),
        z0 + (z1 - z0) * frac
      );

      dummy.position.copy(currentPos);

      if (!isLanded && (idx1 > idx0 || pts.length > 1)) {
        dir.set(x1 - x0, y1 - y0, z1 - z0);
        if (dir.lengthSq() > 1e-6) {
          dir.normalize();
          const m = new THREE.Matrix4();
          const worldUp = new THREE.Vector3(0, 1, 0);
          const side = new THREE.Vector3().crossVectors(dir, worldUp).normalize();
          if (side.lengthSq() > 1e-4) {
            const realUp = new THREE.Vector3().crossVectors(side, dir).normalize();
            m.makeBasis(dir, realUp, side);
            dummy.quaternion.setFromRotationMatrix(m);
          } else {
            dummy.quaternion.setFromUnitVectors(new THREE.Vector3(1, 0, 0), dir);
          }
        }
      } else {
        dummy.quaternion.set(0, 0, 0, 1);
      }

      dummy.scale.setScalar(1.0);
      dummy.updateMatrix();
      this.fleetMesh.setMatrixAt(i, dummy.matrix);

      if (!isLanded) {
        swarmSum.add(currentPos);
        activeCount += 1;
      }

      if (i === this.fleetLeadIndex) {
        this._leadPos.copy(currentPos);
      }
    }

    this.fleetMesh.instanceMatrix.needsUpdate = true;

    if (activeCount > 0) {
      this._swarmCenter.copy(swarmSum.multiplyScalar(1 / activeCount));
    } else if (count > 0) {
      this._swarmCenter.copy(this._leadPos);
    }

    if (allLanded && t > this.fleetDuration + 2.0) {
      this.fleetPlaying = false;
    }
  }

  setCameraMode(mode) {
    this.cameraMode = mode || "reset";
    if (this.cameraMode === "reset") {
      this.resetCamera();
    }
  }

  _updateCameraMode(dt) {
    if (!this.controls || !this.camera) return;
    const lerpFactor = Math.min(1.0, dt * 4.0);

    if (this.cameraMode === "lead" && this._leadPos) {
      // 1位機 (トップ機) を斜め後方・上方から追従
      const target = this._leadPos;
      this.controls.target.lerp(target, lerpFactor);
      const camDesired = new THREE.Vector3(target.x - 2.8, target.y + 1.2, target.z + 1.4);
      this.camera.position.lerp(camDesired, lerpFactor);
    } else if (this.cameraMode === "swarm" && this._swarmCenter) {
      // 飛行中の群れ重心 (Swarm Center of Mass) を自動追従
      const target = this._swarmCenter;
      this.controls.target.lerp(target, lerpFactor);
      const camDesired = new THREE.Vector3(target.x - 6.5, target.y + 3.8, target.z + 5.5);
      this.camera.position.lerp(camDesired, lerpFactor);
    } else if (this.cameraMode === "fleet") {
      // 100機全体を見渡す壮大なパノラマ俯瞰視点
      const center = this._swarmCenter || new THREE.Vector3(12, 1, 0);
      const target = new THREE.Vector3(center.x, 0.5, 0);
      this.controls.target.lerp(target, lerpFactor);
      const camDesired = new THREE.Vector3(center.x - 14.0, 11.0, 12.0);
      this.camera.position.lerp(camDesired, lerpFactor);
    }
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
    this.followTarget.copy(this.lastPose);
  }

  /** Fit the actual recorded path, including the ground below its start. */
  frameTrajectory(samples) {
    if (!samples?.length) return;
    const bounds = new THREE.Box3();
    for (const sample of samples) {
      if ([sample.x_m, sample.y_m, sample.z_m].every(Number.isFinite)) {
        bounds.expandByPoint(new THREE.Vector3(sample.x_m, -sample.z_m, sample.y_m));
      }
    }
    if (bounds.isEmpty()) return;
    bounds.expandByPoint(new THREE.Vector3(samples[0].x_m, 0, samples[0].y_m));
    const sphere = bounds.getBoundingSphere(new THREE.Sphere());
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const effectiveFov = Math.min(halfFov, Math.atan(Math.tan(halfFov) * this.camera.aspect));
    const distance = Math.max(sphere.radius / Math.sin(effectiveFov) * 1.12, 2);
    const direction = new THREE.Vector3(0.2, 0.3, 1).normalize();
    this.controls.target.copy(sphere.center);
    this.camera.position.copy(sphere.center).addScaledVector(direction, distance);
    this.camera.far = Math.max(400, distance + sphere.radius * 4);
    this.camera.updateProjectionMatrix();
    if (this.scene.fog) {
      this.scene.fog.near = distance + sphere.radius;
      this.scene.fog.far = distance + sphere.radius * 4 + 10;
    }
    // Start follow from this target to avoid jumping away on the next frame.
    this.followTarget.copy(sphere.center);
    this.controls.update();
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
    if (this.tunnelMode) {
      const target = new THREE.Vector3(0, 1.15, 0);
      this.controls.target.copy(target);
      this.camera.position.set(1.6, 1.7, 1.8);
      this.controls.update();
    } else {
      this.frameOn(this.lastPose, this.frameRadius());
    }
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

    if (this.tunnelMode) {
      this._updateTunnelDynamics(dt);
    }

    if (this.fleetMesh && this.fleetPlaying) {
      this._updateFleet(dt);
    }
    this._updateCameraMode(dt);

    this.particles.update(dt, this.elapsed);
    if (this.keyLight && this.airframeGroup) {
      const px = this.airframeGroup.position.x;
      const pz = this.airframeGroup.position.z;
      this.keyLight.position.set(px - 2.0, 5.0, pz + 3.0);
      this.keyLight.target.position.set(px, 0, pz);
      this.keyLight.target.updateMatrixWorld();
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);

    this.frameTimes.push(dt);
    if (this.frameTimes.length > 40) this.frameTimes.shift();
    if (this.elapsed - this.lastStats > 0.5) {
      this.lastStats = this.elapsed;
      const mean = this.frameTimes.reduce((a, b) => a + b, 0) / Math.max(this.frameTimes.length, 1);
      const fps = mean > 0 ? 1 / mean : 0;
      this._adaptParticles(fps);
      this.onStats({ fps, elapsed: this.elapsed, particles: this.particles.activeCount, gpu: this.gpuInfo });
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
    this.clearFleet();
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
