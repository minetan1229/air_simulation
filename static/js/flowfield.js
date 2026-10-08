/*
 * flowfield.js — wind visualisation.
 *
 * The 3D velocity field is computed by the server (NumPy / CuPy / torch) and
 * uploaded as a half-float volume texture.  Particles are advected on the GPU
 * through a ping-pong position texture (GPGPU style); when float render
 * targets are unavailable the same advection runs on the CPU with the same
 * field, and the active path is reported in the HUD.
 */

import * as THREE from "three";

const GRID_X = 256;
const GRID_Y = 192;
export const DEFAULT_PARTICLE_COUNT = GRID_X * GRID_Y;

// NOTE ON SHADER CONVENTIONS
// three.js prepends `attribute vec3 position; attribute vec2 uv;` and, on
// WebGL2, compiles every ShaderMaterial as `#version 300 es` with
// `#define attribute in` / `#define varying in|out` / `#define texture2D texture`
// and its own `pc_fragColor`.  The shaders below therefore use the GLSL1
// keywords, never redeclare the built-in attributes, and may still call
// `texture()` on a sampler3D because the compiled dialect is ES 3.00.

const SIM_VERT = /* glsl */ `
precision highp float;
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

const SIM_FRAG = /* glsl */ `
precision highp float;
precision highp sampler3D;
uniform sampler2D uPositions;
uniform sampler3D uWind;
uniform vec3 uBoxMin;
uniform vec3 uBoxSize;
uniform float uDt;
uniform float uTime;
uniform float uSeed;
uniform float uSpeedScale;
uniform float uSwirl;
uniform float uTunnel;
varying vec2 vUv;

vec3 hash3(vec2 p, float seed) {
  vec3 h = vec3(dot(p, vec2(127.1, 311.7)),
                dot(p, vec2(269.5, 183.3)),
                dot(p, vec2(419.2, 371.9)));
  return fract(sin(h + seed) * 43758.5453);
}

void main() {
  vec4 state;
  if (uSeed > 0.5) {
    if (uTunnel > 0.5) {
      // Wind tunnel nozzle seeding: regular stream lanes with slight jitter
      vec2 grid = floor(vUv * vec2(36.0, 24.0)) / vec2(36.0, 24.0);
      vec3 j = (hash3(vUv * 19.3, 1.0) - 0.5) * 0.04;
      state = vec4(fract(vUv.x + j.x), fract(grid.y + j.y), fract(grid.x + j.z), hash3(vUv, 3.1).x);
    } else {
      state = vec4(hash3(vUv, uSeed), 0.0);
    }
  } else {
    state = texture2D(uPositions, vUv);
  }

  vec3 uvw = clamp(state.xyz, 0.0, 1.0);
  vec3 flow = texture(uWind, vec3(uvw.x, uvw.z, uvw.y)).xyz;

  vec3 jitter = (hash3(vUv * 7.3 + uTime * 0.05, uSeed) - 0.5);
  vec3 swirl = cross(flow, jitter) * uSwirl;

  vec3 velocity = flow * uSpeedScale + swirl;
  vec3 next = state.xyz + velocity * uDt / uBoxSize;

  float life = fract(state.w + uDt * (uTunnel > 0.5 ? 0.22 : 0.12));

  if (uTunnel > 0.5) {
    // Respawn along the upstream nozzle wall as organized smoke filament lines
    if (next.x < 0.005 || next.x > 0.995 || next.y < 0.005 || next.y > 0.995 || next.z < 0.005 || next.z > 0.995 || life < 0.02) {
      vec2 grid = floor(vUv * vec2(36.0, 24.0)) / vec2(36.0, 24.0);
      vec3 j = (hash3(vUv * 43.1 + life * 17.0, 2.0) - 0.5) * 0.03;
      next = vec3(0.99, clamp(grid.y + j.y, 0.05, 0.95), clamp(grid.x + j.z, 0.05, 0.95));
      life = fract(hash3(vUv, 7.3).x * 0.2);
    }
  } else {
    next = fract(next);
  }

  gl_FragColor = vec4(next, life);
}
`;

const POINT_VERT = /* glsl */ `
precision highp float;
precision highp sampler3D;
uniform sampler2D uPositions;
uniform sampler3D uWind;
uniform vec3 uBoxMin;
uniform vec3 uBoxSize;
uniform float uScale;
uniform float uFade;
uniform float uTunnel;
attribute vec2 ref;
varying float vSpeed;
varying float vFade;

void main() {
  vec4 state = texture2D(uPositions, ref);
  vec3 uvw = clamp(state.xyz, 0.0, 1.0);
  vec3 world = uBoxMin + uvw * uBoxSize;
  vSpeed = length(texture(uWind, vec3(uvw.x, uvw.z, uvw.y)).xyz);
  vFade = smoothstep(0.0, 0.12, state.w) * (1.0 - smoothstep(0.82, 1.0, state.w));

  vec4 mv = modelViewMatrix * vec4(world, 1.0);
  gl_Position = projectionMatrix * mv;
  float minPt = (uTunnel > 0.5) ? 2.2 : 1.0;
  float maxPt = (uTunnel > 0.5) ? 6.0 : 5.0;
  gl_PointSize = clamp(uScale / max(-mv.z, 0.05), minPt, maxPt);
}
`;

const POINT_FRAG = /* glsl */ `
precision highp float;
uniform vec3 uSlow;
uniform vec3 uFast;
uniform float uMaxSpeed;
uniform float uFade;
uniform float uTunnel;
varying float vSpeed;
varying float vFade;

void main() {
  vec2 offset = gl_PointCoord - vec2(0.5);
  float radius = dot(offset, offset);
  if (radius > 0.25) discard;
  float alphaMul = (uTunnel > 0.5) ? 0.70 : 0.32;
  float alpha = smoothstep(0.25, 0.02, radius) * uFade * vFade * alphaMul;
  float t = clamp(vSpeed / max(uMaxSpeed, 0.05), 0.0, 1.0);
  vec3 slowCol = (uTunnel > 0.5) ? vec3(0.72, 0.88, 1.0) : uSlow;
  vec3 fastCol = (uTunnel > 0.5) ? vec3(0.12, 0.58, 0.98) : uFast;
  vec3 colour = mix(slowCol, fastCol, t);
  gl_FragColor = vec4(colour, alpha);
}
`;

export class FlowField {
  constructor() {
    this.payload = null;
    this.bounds = { min: [-2, -2, 0], max: [2, 2, 2] };
    this.shape = [1, 1, 1];
    this.time = 0;
    this.backend = "—";
    this.grid = null; // CPU mirror: {nx,ny,nz,u,v,w}
  }

  get ready() {
    return Boolean(this.grid);
  }

  get meanSpeed() {
    return this.payload ? Number(this.payload.mean_speed) || 0 : 0;
  }

  get maxSpeed() {
    return this.payload ? Number(this.payload.max_speed) || 0 : 0;
  }

  /**
   * Absorb a `/api/v1/wind/field` payload.  The server packs the grid x
   * slowest / z fastest and treats z as height above ground; `ingest` re-orders
   * it into the three-space layout used by the volume texture and by
   * `velocityAt` (which works in simulator coordinates).
   */
  ingest(payload) {
    this.payload = payload;
    this.tunnelMode = Boolean(payload.tunnelMode);
    const [nx, ny, nz] = payload.shape;
    const lo = payload.bounds.min;
    const hi = payload.bounds.max;
    this.bounds = { min: lo, max: hi };
    this.shape = [nx, ny, nz];
    this.time = Number(payload.time_s) || 0;
    this.backend = payload.backend || "numpy";

    const count = nx * ny * nz;
    const u = new Float32Array(count);
    const v = new Float32Array(count);
    const w = new Float32Array(count);
    const vectors = payload.vectors;
    for (let x = 0; x < nx; x += 1) {
      for (let y = 0; y < ny; y += 1) {
        for (let z = 0; z < nz; z += 1) {
          // server packs x slowest / z fastest: ((x * ny) + y) * nz + z
          const src = (x * ny + y) * nz + z;
          // Data3DTexture wants x + nx * (y + ny * z)
          const dst = x + nx * y + nx * ny * z;
          u[dst] = vectors[0][src];
          v[dst] = vectors[1][src];
          w[dst] = vectors[2][src];
        }
      }
    }
    this.grid = { nx, ny, nz, u, v, w };
    return this;
  }

  /** Trilinear sample in simulator coordinates. */
  velocityAt(x, y, z, out) {
    const grid = this.grid;
    if (!grid) {
      out[0] = out[1] = out[2] = 0;
      return out;
    }
    const { nx, ny, nz, u, v, w } = grid;
    const lo = this.bounds.min;
    const hi = this.bounds.max;
    const fx = ((x - lo[0]) / (hi[0] - lo[0] || 1)) * (nx - 1);
    const fy = ((y - lo[1]) / (hi[1] - lo[1] || 1)) * (ny - 1);
    // server z is height above ground
    const fz = ((-z - lo[2]) / (hi[2] - lo[2] || 1)) * (nz - 1);
    const cx = Math.min(Math.max(fx, 0), nx - 1);
    const cy = Math.min(Math.max(fy, 0), ny - 1);
    const cz = Math.min(Math.max(fz, 0), nz - 1);
    const x0 = Math.floor(cx);
    const y0 = Math.floor(cy);
    const z0 = Math.floor(cz);
    const x1 = Math.min(x0 + 1, nx - 1);
    const y1 = Math.min(y0 + 1, ny - 1);
    const z1 = Math.min(z0 + 1, nz - 1);
    const wx = cx - x0;
    const wy = cy - y0;
    const wz = cz - z0;
    const idx = (px, py, pz) => px + nx * py + nx * ny * pz;
    const i000 = idx(x0, y0, z0);
    const i100 = idx(x1, y0, z0);
    const i010 = idx(x0, y1, z0);
    const i110 = idx(x1, y1, z0);
    const i001 = idx(x0, y0, z1);
    const i101 = idx(x1, y0, z1);
    const i011 = idx(x0, y1, z1);
    const i111 = idx(x1, y1, z1);
    const w000 = (1 - wx) * (1 - wy) * (1 - wz);
    const w100 = wx * (1 - wy) * (1 - wz);
    const w010 = (1 - wx) * wy * (1 - wz);
    const w110 = wx * wy * (1 - wz);
    const w001 = (1 - wx) * (1 - wy) * wz;
    const w101 = wx * (1 - wy) * wz;
    const w011 = (1 - wx) * wy * wz;
    const w111 = wx * wy * wz;
    out[0] = u[i000] * w000 + u[i100] * w100 + u[i010] * w010 + u[i110] * w110 +
             u[i001] * w001 + u[i101] * w101 + u[i011] * w011 + u[i111] * w111;
    out[1] = v[i000] * w000 + v[i100] * w100 + v[i010] * w010 + v[i110] * w110 +
             v[i001] * w001 + v[i101] * w101 + v[i011] * w011 + v[i111] * w111;
    out[2] = w[i000] * w000 + w[i100] * w100 + w[i010] * w010 + w[i110] * w110 +
             w[i001] * w001 + w[i101] * w101 + w[i011] * w011 + w[i111] * w111;
    return out;
  }

  /**
   * Half-float volume texture in three-space (y up).
   * Server z is height above ground, so the stored vector is reordered from
   * server (u, v, w_up) to three-space (u, w_up, v) and the vertical axis of
   * the texture runs opposite to the three-space y axis (see the shaders).
   */
  buildTexture(THREE_REF) {
    const grid = this.grid;
    const size = [this.shape[0], this.shape[1], this.shape[2]];
    const data = new Uint16Array(size[0] * size[1] * size[2] * 4);
    const toHalf = THREE_REF.DataUtils.toHalfFloat;
    const uSign = this.tunnelMode ? -1 : 1;
    for (let i = 0; i < grid.u.length; i += 1) {
      data[i * 4] = toHalf(grid.u[i] * uSign);
      data[i * 4 + 1] = toHalf(grid.w[i]);
      data[i * 4 + 2] = toHalf(grid.v[i]);
      data[i * 4 + 3] = toHalf(Math.hypot(grid.u[i], grid.v[i], grid.w[i]));
    }
    const texture = new THREE_REF.Data3DTexture(data, size[0], size[1], size[2]);
    texture.format = THREE_REF.RGBAFormat;
    texture.type = THREE_REF.HalfFloatType;
    texture.minFilter = THREE_REF.LinearFilter;
    texture.magFilter = THREE_REF.LinearFilter;
    texture.wrapS = THREE_REF.ClampToEdgeWrapping;
    texture.wrapT = THREE_REF.ClampToEdgeWrapping;
    texture.wrapR = THREE_REF.ClampToEdgeWrapping;
    texture.unpackAlignment = 1;
    texture.needsUpdate = true;
    return texture;
  }

  /** three-space box corners (y up) of the sampled region. */
  boxCorners() {
    const lo = this.bounds.min;
    const hi = this.bounds.max;
    const min = new THREE.Vector3(lo[0], lo[2], lo[1]);
    const max = new THREE.Vector3(hi[0], hi[2], hi[1]);
    return { min, max };
  }
}

export class WindParticles {
  constructor(scene, renderer, count = DEFAULT_PARTICLE_COUNT) {
    this.scene = scene;
    this.renderer = renderer;
    this.count = count;
    this.mode = "idle";
    this.field = new FlowField();
    this.time = 0;
    this.speedScale = 1;
    this.swirl = 0.22;
    this.budget = 0.65;
    this.drawn = 0;
    this.group = new THREE.Group();
    this.group.name = "wind";
    scene.add(this.group);

    this.gpuSupported = Boolean(
      renderer.capabilities.isWebGL2 &&
        renderer.extensions.has("EXT_color_buffer_float")
    );
    if (this.gpuSupported) this._initGpu();
    else this._initCpu();
    this.setBudget(this.budget);
  }

  get backend() {
    const active = this.activeCount;
    const kind = this.gpuSupported ? "gpgpu" : "cpu";
    return `${kind} ${active >= this.count ? active : `${active}/${this.count}`}`;
  }

  /* ------------------------------------------------------------ GPU path */
  _initGpu() {
    const renderer = this.renderer;
    const options = {
      minFilter: THREE.NearestFilter,
      magFilter: THREE.NearestFilter,
      format: THREE.RGBAFormat,
      type: THREE.HalfFloatType,
      depthBuffer: false,
      stencilBuffer: false,
      generateMipmaps: false,
    };
    this.rtA = new THREE.WebGLRenderTarget(GRID_X, GRID_Y, options);
    this.rtB = new THREE.WebGLRenderTarget(GRID_X, GRID_Y, options);
    this.rtA.texture.wrapS = THREE.ClampToEdgeWrapping;
    this.rtA.texture.wrapT = THREE.ClampToEdgeWrapping;
    this.rtB.texture.wrapS = THREE.ClampToEdgeWrapping;
    this.rtB.texture.wrapT = THREE.ClampToEdgeWrapping;

    this.seedTexture = new THREE.DataTexture(
      new Float32Array(GRID_X * GRID_Y * 4).fill(0),
      GRID_X,
      GRID_Y,
      THREE.RGBAFormat,
      THREE.FloatType
    );
    this.seedTexture.needsUpdate = true;

    const blank = new THREE.Data3DTexture(
      new Uint16Array(4 * 4 * 4 * 4),
      4,
      4,
      4
    );
    blank.format = THREE.RGBAFormat;
    blank.type = THREE.HalfFloatType;
    blank.minFilter = THREE.LinearFilter;
    blank.magFilter = THREE.LinearFilter;
    blank.needsUpdate = true;
    this.volumeTexture = blank;
    this.blankVolume = blank;

    this.simUniforms = {
      uPositions: { value: this.seedTexture },
      uWind: { value: blank },
      uBoxMin: { value: new THREE.Vector3(-2, 0, -2) },
      uBoxSize: { value: new THREE.Vector3(4, 2, 4) },
      uDt: { value: 0.016 },
      uTime: { value: 0 },
      uSeed: { value: 1 },
      uSpeedScale: { value: 1 },
      uSwirl: { value: this.swirl },
      uTunnel: { value: 0 },
    };
    this.simMaterial = new THREE.ShaderMaterial({
      uniforms: this.simUniforms,
      vertexShader: SIM_VERT,
      fragmentShader: SIM_FRAG,
      depthTest: false,
      depthWrite: false,
    });
    this.simScene = new THREE.Scene();
    this.simCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.simMaterial);
    quad.frustumCulled = false;
    this.simScene.add(quad);

    const references = new Float32Array(this.count * 2);
    let index = 0;
    for (let row = 0; row < GRID_Y; row += 1) {
      for (let col = 0; col < GRID_X; col += 1) {
        references[index * 2] = (col + 0.5) / GRID_X;
        references[index * 2 + 1] = (row + 0.5) / GRID_Y;
        index += 1;
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(this.count * 3), 3));
    geometry.setAttribute("ref", new THREE.Float32BufferAttribute(references, 2));

    this.pointUniforms = {
      uPositions: { value: this.rtA.texture },
      uWind: { value: blank },
      uBoxMin: this.simUniforms.uBoxMin,
      uBoxSize: this.simUniforms.uBoxSize,
      uScale: { value: 110 },
      uFade: { value: 1 },
      uTunnel: { value: 0 },
      uSlow: { value: new THREE.Color(0.72, 0.78, 0.85) },
      uFast: { value: new THREE.Color(0.24, 0.45, 0.7) },
      uMaxSpeed: { value: 1 },
    };
    this.pointMaterial = new THREE.ShaderMaterial({
      uniforms: this.pointUniforms,
      vertexShader: POINT_VERT,
      fragmentShader: POINT_FRAG,
      transparent: true,
      depthWrite: false,
      blending: THREE.NormalBlending,
    });
    this.points = new THREE.Points(geometry, this.pointMaterial);
    this.points.frustumCulled = false;
    this.group.add(this.points);
    this.current = this.rtA;
    this.next = this.rtB;
    this.needsSeed = true;
  }

  /* ------------------------------------------------------------ CPU path */
  _initCpu() {
    const geometry = new THREE.BufferGeometry();
    this.positions = new Float32Array(this.count * 3);
    this.speeds = new Float32Array(this.count);
    this.seedValues = new Float32Array(this.count);
    for (let i = 0; i < this.count; i += 1) {
      this.positions[i * 3] = Math.random();
      this.positions[i * 3 + 1] = Math.random();
      this.positions[i * 3 + 2] = Math.random();
      this.seedValues[i] = Math.random();
    }
    geometry.setAttribute("position", new THREE.BufferAttribute(this.positions, 3));
    geometry.setAttribute("speed", new THREE.BufferAttribute(this.speeds, 1));
    this.cpuGeometry = geometry;
    this.pointMaterial = new THREE.PointsMaterial({
      size: 0.018,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.45,
      color: 0x3f6ea3,
      depthWrite: false,
    });
    this.points = new THREE.Points(geometry, this.pointMaterial);
    this.points.frustumCulled = false;
    this.group.add(this.points);
    this.box = { min: new THREE.Vector3(-2, 0, -2), max: new THREE.Vector3(2, 2, 2) };
    this.sample = [0, 0, 0];
  }

  setField(field) {
    this.field = field;
    const isTunnel = Boolean(field.tunnelMode);
    // Visual gain: real air speeds are metres per second, advection is scaled for readability
    const peak = Math.max(field.maxSpeed, 0.02);
    if (isTunnel) {
      this.speedScale = Math.min(25, Math.max(2, 4.5 / Math.sqrt(peak)));
    } else {
      this.speedScale = Math.min(40, Math.max(1, 0.85 / peak));
    }
    if (this.gpuSupported) {
      if (field.ready) {
        if (this.volumeTexture && this.volumeTexture !== this.blankVolume) {
          this.volumeTexture.dispose();
        }
        this.volumeTexture = field.buildTexture(THREE);
        this.simUniforms.uWind.value = this.volumeTexture;
        this.pointUniforms.uWind.value = this.volumeTexture;
      }
      const box = field.boxCorners();
      this.simUniforms.uBoxMin.value.copy(box.min);
      this.simUniforms.uBoxSize.value.subVectors(box.max, box.min);
      this.simUniforms.uTunnel.value = isTunnel ? 1 : 0;
      this.pointUniforms.uTunnel.value = isTunnel ? 1 : 0;
      this.pointUniforms.uMaxSpeed.value = Math.max(field.maxSpeed, 0.05);
      this.mode = "gpgpu";
    } else {
      const box = field.boxCorners();
      this.box = box;
      this.pointMaterial.opacity = isTunnel ? 0.75 : 0.45;
      this.mode = "cpu";
    }
  }

  update(dt, elapsed) {
    if (!this.mode || this.mode === "idle") return;
    const step = Math.min(Math.max(dt, 1 / 240), 1 / 20);
    this.time = elapsed;
    if (this.gpuSupported) {
      const uniforms = this.simUniforms;
      uniforms.uPositions.value = this.needsSeed ? this.seedTexture : this.current.texture;
      uniforms.uSeed.value = this.needsSeed ? 1 : 0;
      uniforms.uDt.value = step;
      uniforms.uTime.value = elapsed;
      uniforms.uSpeedScale.value = this.speedScale;
      uniforms.uSwirl.value = this.swirl;
      const target = this.renderer.getRenderTarget();
      this.renderer.setRenderTarget(this.next);
      this.renderer.render(this.simScene, this.simCamera);
      this.renderer.setRenderTarget(target);
      const swap = this.current;
      this.current = this.next;
      this.next = swap;
      this.needsSeed = false;
      this.pointUniforms.uPositions.value = this.current.texture;
    } else {
      const { min, max } = this.box;
      const sizeX = max.x - min.x;
      const sizeY = max.y - min.y;
      const sizeZ = max.z - min.z;
      const positions = this.positions;
      const speeds = this.speeds;
      const out = this.sample;
      for (let i = 0; i < this.count; i += 1) {
        const wx = min.x + positions[i * 3] * sizeX;
        const wy = min.y + positions[i * 3 + 1] * sizeY;
        const wz = min.z + positions[i * 3 + 2] * sizeZ;
        // field.velocityAt works in sim coordinates (+z down)
        this.field.velocityAt(wx, wz, -wy, out);
        const speed = Math.hypot(out[0], out[1], out[2]);
        speeds[i] = speed;
        const uSign = (this.field && this.field.tunnelMode) ? -1 : 1;
        positions[i * 3] += (out[0] * uSign * this.speedScale * step) / (sizeX || 1);
        // third server component is vertical and points up -> three-space +y
        positions[i * 3 + 1] += (out[2] * this.speedScale * step) / (sizeY || 1);
        positions[i * 3 + 2] += (out[1] * this.speedScale * step) / (sizeZ || 1);
        if (positions[i * 3] < 0) positions[i * 3] += 1;
        if (positions[i * 3] > 1) positions[i * 3] -= 1;
        if (positions[i * 3 + 1] < 0) positions[i * 3 + 1] += 1;
        if (positions[i * 3 + 1] > 1) positions[i * 3 + 1] -= 1;
        if (positions[i * 3 + 2] < 0) positions[i * 3 + 2] += 1;
        if (positions[i * 3 + 2] > 1) positions[i * 3 + 2] -= 1;
      }
      this.cpuGeometry.attributes.position.needsUpdate = true;
      this.cpuGeometry.attributes.speed.needsUpdate = true;
      this.pointMaterial.size = Math.max(sizeX, sizeZ) * 0.012;
    }
  }

  setVisible(visible) {
    this.group.visible = Boolean(visible);
  }

  /**
   * Draw a fraction of the particle set.  Lowering the draw range (instead of
   * rebuilding the buffers) keeps the simulation intact while the renderer
   * catches up on weak GPUs or software rasterisers.
   */
  setBudget(fraction) {
    const ratio = Math.min(Math.max(Number(fraction) || 1, 0.05), 1);
    this.budget = ratio;
    const total = this.points ? this.points.geometry.getAttribute("position").count : 0;
    const visible = Math.max(Math.floor(total * ratio), 1);
    this.points.geometry.setDrawRange(0, visible);
    this.drawn = visible;
    return visible;
  }

  get activeCount() {
    return this.drawn || this.count;
  }

  reseed() {
    this.needsSeed = true;
  }

  dispose() {
    if (this.rtA) this.rtA.dispose();
    if (this.rtB) this.rtB.dispose();
    if (this.seedTexture) this.seedTexture.dispose();
    if (this.volumeTexture && this.volumeTexture !== this.blankVolume) this.volumeTexture.dispose();
    if (this.blankVolume) this.blankVolume.dispose();
    if (this.points) {
      this.points.geometry.dispose();
      this.scene.remove(this.group);
    }
  }
}