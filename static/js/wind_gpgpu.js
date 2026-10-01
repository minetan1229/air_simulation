/**
 * wind_gpgpu.js -- 100 000+ advected wind particles with a GPGPU ping-pong core.
 *
 * Fast path (WebGL 2 + EXT_color_buffer_float):
 *   * two RGBA32F render targets (position + velocity) ping-ponged each frame,
 *     integrated by fullscreen fragment shaders;
 *   * the velocity shader samples a wind-field texture fetched from
 *     ``POST /api/v1/wind/field`` (refreshed about once a second) with manual
 *     trilinear interpolation out of a 2-D atlas;
 *   * particles that leave the domain -- or whose lifetime expires -- are
 *     respawned from a hash of their texel index so the seeding is
 *     deterministic and needs no CPU round trip;
 *   * drawn as a single ``THREE.Points`` with additive blending and a soft
 *     round sprite painted onto a canvas at runtime (no texture file).
 *
 * Fallback path (no float render targets, or any throw during setup): a plain
 * ``THREE.Points`` whose ``THREE.BufferAttribute`` positions are integrated on
 * the CPU at {@link FALLBACK_PARTICLE_COUNT} particles using the same wind
 * field.  The class never throws and never leaves the scene half-built.
 *
 * The GPGPU state lives in the *simulator* frame (x forward, y right, z down);
 * the sim -> three.js mapping ``(x, y, z) -> (x, -z, y)`` is applied once, in
 * the render vertex shader (or once per particle on the CPU).
 */

import * as THREE from "three";

/** Nominal particle budget: AGENTS.md 2.4 asks for 100 000+. */
export const DEFAULT_PARTICLE_COUNT = 100000;

/** Budget used when float render targets (or WebGL 2) are unavailable. */
export const FALLBACK_PARTICLE_COUNT = 2000;

/** Hard ceiling accepted by {@link WindGPGPU#setParticleCount}. */
export const MAX_PARTICLE_COUNT = 400000;

/** Fallback domain in simulator coordinates (metres) used before the first fetch. */
const DEFAULT_DOMAIN = {
  min: [-3, -5, -6],
  max: [10, 5, 0.4],
};

/** Half-width / half-height of the procedural sprite in pixels. */
const SPRITE_SIZE = 64;

/**
 * Paint a soft round additive sprite onto a canvas -- no external asset.
 * @returns {THREE.CanvasTexture}
 */
function createSoftSprite(three) {
  const canvas = document.createElement("canvas");
  canvas.width = SPRITE_SIZE;
  canvas.height = SPRITE_SIZE;
  const context = canvas.getContext("2d");
  if (!context) {
    // Extremely defensive: a 1x1 opaque texture still renders *something*.
    const fallback = document.createElement("canvas");
    fallback.width = 1;
    fallback.height = 1;
    const fctx = fallback.getContext("2d");
    if (fctx) {
      fctx.fillStyle = "#FFFFFF";
      fctx.fillRect(0, 0, 1, 1);
    }
    return new three.CanvasTexture(fallback);
  }
  const centre = SPRITE_SIZE / 2;
  const gradient = context.createRadialGradient(centre, centre, 0, centre, centre, centre);
  gradient.addColorStop(0.0, "rgba(255, 255, 255, 1.0)");
  gradient.addColorStop(0.25, "rgba(210, 232, 255, 0.85)");
  gradient.addColorStop(0.6, "rgba(120, 180, 255, 0.28)");
  gradient.addColorStop(1.0, "rgba(90, 150, 240, 0.0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
  const texture = new three.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

/**
 * Can this renderer allocate floating point render targets?
 * @param {THREE.WebGLRenderer} renderer
 * @returns {boolean}
 */
export function supportsFloatRenderTargets(renderer) {
  try {
    const gl = renderer.getContext();
    if (!gl) {
      return false;
    }
    const isWebGL2 = typeof WebGL2RenderingContext !== "undefined"
      && gl instanceof WebGL2RenderingContext;
    if (!isWebGL2) {
      return false;
    }
    return Boolean(gl.getExtension("EXT_color_buffer_float")
      || gl.getExtension("EXT_color_buffer_half_float"));
  } catch (err) {
    return false;
  }
}

/**
 * Simulator frame -> three.js frame.
 * @param {number} x
 * @param {number} y
 * @param {number} z
 * @returns {number[]} three.js [x, y, z]
 */
export function simToThree(x, y, z) {
  return [x, -z, y];
}

/** Shared GLSL: trilinear wind-field sampling out of a 2-D atlas. */
const WIND_SAMPLER_GLSL = /* glsl */ `
  uniform sampler2D uWind;
  uniform vec3 uWindTexSize;   // (nx, ny, nz)
  uniform vec3 uWindOrigin;    // simulator-frame minimum corner
  uniform vec3 uWindCell;      // cell size (m)
  uniform float uWindEnabled;

  vec4 windFetch(vec3 cell) {
    vec3 c = clamp(cell, vec3(0.0), uWindTexSize - vec3(1.0));
    float width = uWindTexSize.x * uWindTexSize.y;
    vec2 uv = vec2((c.x + c.y * uWindTexSize.x) / width, (c.z + 0.5) / uWindTexSize.z);
    return texture2D(uWind, uv);
  }

  vec3 windAt(vec3 p) {
    if (uWindEnabled < 0.5) {
      return vec3(0.0);
    }
    vec3 g = (p - uWindOrigin) / uWindCell - vec3(0.5);
    vec3 f = floor(g);
    vec3 t = clamp(g - f, vec3(0.0), vec3(1.0));
    vec3 acc = vec3(0.0);
    float wsum = 0.0;
    for (int k = 0; k < 2; k++) {
      for (int j = 0; j < 2; j++) {
        for (int i = 0; i < 2; i++) {
          vec3 off = vec3(float(i), float(j), float(k));
          vec3 weight = mix(vec3(1.0) - t, t, off);
          float w = weight.x * weight.y * weight.z;
          acc += windFetch(f + off).xyz * w;
          wsum += w;
        }
      }
    }
    return acc / max(wsum, 1.0e-6);
  }

  float windSpeedAt(vec3 p) {
    if (uWindEnabled < 0.5) {
      return 0.0;
    }
    vec3 g = (p - uWindOrigin) / uWindCell;
    vec3 f = floor(clamp(g, vec3(0.0), uWindTexSize - vec3(1.0)));
    return windFetch(f).w;
  }
`;

/** Deterministic pseudo random numbers seeded from a texel index. */
const HASH_GLSL = /* glsl */ `
  float hash11(float n) {
    return fract(sin(n * 127.1) * 43758.5453123);
  }
  vec3 hash31(float n) {
    return vec3(hash11(n * 1.7), hash11(n * 3.1 + 11.3), hash11(n * 5.9 + 27.7));
  }
`;

const QUAD_VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

export class WindGPGPU {
  /**
   * @param {object} options
   * @param {THREE.WebGLRenderer} options.renderer
   * @param {THREE.Scene} options.scene
   * @param {number} [options.particleCount]
   * @param {(payload: object) => object} [options.fetchField] async wind-field fetcher
   * @param {(message: string, kind?: string) => void} [options.notify]
   * @param {object} [options.domain]
   * @param {number[]} [options.camera] camera position in three.js coordinates
   */
  constructor(options = {}) {
    this.renderer = options.renderer;
    this.scene = options.scene;
    this.fetchField = typeof options.fetchField === "function" ? options.fetchField : null;
    this.notify = typeof options.notify === "function" ? options.notify : () => {};
    this.domain = options.domain ? { ...options.domain } : {
      min: DEFAULT_DOMAIN.min.slice(),
      max: DEFAULT_DOMAIN.max.slice(),
    };
    this.cameraPosition = Array.isArray(options.camera) ? options.camera.slice() : [0.4, 0.3, 0.6];

    this.requestedCount = Math.min(MAX_PARTICLE_COUNT, Math.max(1,
      Math.floor(options.particleCount || DEFAULT_PARTICLE_COUNT)));
    this.mode = "cpu";
    this.reason = "";
    this._time = 0;
    this._disposed = false;
    this._windData = null;
    this._windTexture = null;
    this._windShape = [1, 1, 1];
    this._fieldMeta = { backend: "", maxSpeed: 0, meanSpeed: 0, points: 0 };
    this._lastFetchMs = -Infinity;
    this._refreshIntervalMs = options.refreshIntervalMs || 1000;
    this._frameCount = 0;

    this.sprite = createSoftSprite(THREE);
    this.points = null;

    const canFloat = supportsFloatRenderTargets(this.renderer);
    this.particleCount = canFloat ? this.requestedCount : FALLBACK_PARTICLE_COUNT;
    if (!canFloat) {
      this.reason = "WebGL2 float render targets unavailable (EXT_color_buffer_float missing)";
      this.mode = "cpu";
    }

    try {
      if (canFloat) {
        this.#buildGPGPU();
        this.mode = "gpgpu";
      } else {
        this.#buildCPUCloud();
      }
    } catch (err) {
      // Any failure during the fast path degrades to the CPU cloud; the class
      // must never leave the scene in a broken state.
      this.reason = `GPGPU setup failed: ${err && err.message ? err.message : String(err)}`;
      this.#teardownGPGPU();
      try {
        this.#buildCPUCloud();
        this.mode = "cpu";
      } catch (cpuErr) {
        this.reason += ` / CPU fallback failed: ${cpuErr && cpuErr.message ? cpuErr.message : String(cpuErr)}`;
        this.mode = "none";
      }
    }
    if (this.mode === "cpu" && canFloat) {
      this.notify(`風粒子: ${this.particleCount} 点の CPU フォールバックで描画します (${this.reason})`, "warn");
    }
  }

  // -------------------------------------------------------------- geometry

  /** Texture edge length that holds at least the requested particle count. */
  #textureSize(count) {
    return Math.max(2, Math.ceil(Math.sqrt(count)));
  }

  /** Shader uniforms shared by the compute passes and the draw pass. */
  #sharedUniforms() {
    return {
      uDelta: { value: 0 },
      uTime: { value: 0 },
      uDomainMin: { value: new THREE.Vector3(...this.domain.min) },
      uDomainMax: { value: new THREE.Vector3(...this.domain.max) },
      uLifetime: { value: 14.0 },
      uAdvection: { value: 2.6 },
      uJitter: { value: 0.35 },
      uSettle: { value: 0.06 },
      uSize: { value: 1.9 },
      uOpacity: { value: 0.55 },
      uPixelRatio: { value: Math.min(window.devicePixelRatio || 1, 2) },
      uWind: { value: this._windTexture },
      uWindTexSize: { value: new THREE.Vector3(1, 1, 1) },
      uWindOrigin: { value: new THREE.Vector3(...this.domain.min) },
      uWindCell: { value: new THREE.Vector3(1, 1, 1) },
      uWindEnabled: { value: 0 },
      uSprite: { value: this.sprite },
    };
  }

  /** Build the ping-pong GPGPU cloud. */
  #buildGPGPU() {
    const size = this.#textureSize(this.particleCount);
    this.textureSize = size;

    const makeTarget = () => {
      const target = new THREE.WebGLRenderTarget(size, size, {
        minFilter: THREE.NearestFilter,
        magFilter: THREE.NearestFilter,
        format: THREE.RGBAFormat,
        type: THREE.FloatType,
        depthBuffer: false,
        stencilBuffer: false,
        generateMipmaps: false,
        wrapS: THREE.ClampToEdgeWrapping,
        wrapT: THREE.ClampToEdgeWrapping,
      });
      target.texture.colorSpace = THREE.NoColorSpace;
      return target;
    };

    this.positionTargets = [makeTarget(), makeTarget()];
    this.velocityTargets = [makeTarget(), makeTarget()];
    this.currentIndex = 0;

    this.quadCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.quadGeometry = new THREE.PlaneGeometry(2, 2);
    this.quadScene = new THREE.Scene();

    // ------------------------------------------------------------ shaders --
    this.velocityMaterial = new THREE.ShaderMaterial({
      uniforms: Object.assign(this.#sharedUniforms(), {
        texturePosition: { value: null },
        textureVelocity: { value: null },
      }),
      vertexShader: QUAD_VERTEX_SHADER,
      fragmentShader: /* glsl */ `
        precision highp float;
        varying vec2 vUv;
        uniform sampler2D texturePosition;
        uniform sampler2D textureVelocity;
        uniform float uDelta;
        uniform float uTime;
        uniform float uAdvection;
        uniform float uJitter;
        uniform float uSettle;
        ${WIND_SAMPLER_GLSL}
        ${HASH_GLSL}
        void main() {
          vec4 pos = texture2D(texturePosition, vUv);
          vec4 vel = texture2D(textureVelocity, vUv);
          vec3 flow = windAt(pos.xyz);
          // First-order relaxation toward the local air velocity: the inertia
          // term makes streaks lag the field instead of snapping to it.
          float k = 1.0 - exp(-uAdvection * max(uDelta, 0.0));
          vec3 v = mix(vel.xyz, flow, k);
          // Turbulent jitter keyed off time so the pattern never freezes.
          vec3 noise = hash31(vUv.x * 4096.0 + vUv.y * 65536.0 + floor(uTime * 8.0)) - 0.5;
          v += noise * uJitter * length(flow);
          // Slow settling keeps the near-ground flow from stacking up.
          v.z -= uSettle * uDelta;
          gl_FragColor = vec4(v, vel.w);
        }
      `,
      depthTest: false,
      depthWrite: false,
    });

    this.positionMaterial = new THREE.ShaderMaterial({
      uniforms: Object.assign(this.#sharedUniforms(), {
        texturePosition: { value: null },
        textureVelocity: { value: null },
      }),
      vertexShader: QUAD_VERTEX_SHADER,
      fragmentShader: /* glsl */ `
        precision highp float;
        varying vec2 vUv;
        uniform sampler2D texturePosition;
        uniform sampler2D textureVelocity;
        uniform float uDelta;
        uniform float uLifetime;
        uniform vec3 uDomainMin;
        uniform vec3 uDomainMax;
        ${HASH_GLSL}
        void main() {
          vec4 pos = texture2D(texturePosition, vUv);
          vec4 vel = texture2D(textureVelocity, vUv);
          float age = pos.w + uDelta;
          vec3 p = pos.xyz + vel.xyz * uDelta;

          // Recycle when the particle leaves the domain or times out.
          vec3 margin = vec3(0.25) * (uDomainMax - uDomainMin);
          bool outside = any(lessThan(p, uDomainMin - margin))
                      || any(greaterThan(p, uDomainMax + margin));
          bool dead = age > uLifetime;
          if (outside || dead || pos.w <= 0.0) {
            float seed = vUv.x * 8192.0 + vUv.y * 131072.0 + age;
            vec3 r = hash31(seed);
            // Seed across the whole box, biased slightly upwind so the streaks
            // start ahead of the flow rather than inside it.
            p = mix(uDomainMin, uDomainMax, r);
            p.x = mix(uDomainMin.x, mix(uDomainMin.x, uDomainMax.x, 0.35), r.x);
            age = r.y * uLifetime * 0.25;
          }
          gl_FragColor = vec4(p, max(age, 0.001));
        }
      `,
      depthTest: false,
      depthWrite: false,
    });

    this.copyMaterial = new THREE.ShaderMaterial({
      uniforms: { textureInput: { value: null } },
      vertexShader: QUAD_VERTEX_SHADER,
      fragmentShader: /* glsl */ `
        precision highp float;
        varying vec2 vUv;
        uniform sampler2D textureInput;
        void main() {
          gl_FragColor = texture2D(textureInput, vUv);
        }
      `,
      depthTest: false,
      depthWrite: false,
    });

    // Exactly one fullscreen quad lives in the compute scene; each pass swaps
    // `material` on it, otherwise all passes would draw into the same target.
    this.computeQuad = new THREE.Mesh(this.quadGeometry, this.velocityMaterial);
    this.computeQuad.frustumCulled = false;
    this.quadScene.add(this.computeQuad);

    // --------------------------------------------------------- draw pass --
    this.uniforms = this.#sharedUniforms();
    this.geometry = new THREE.BufferGeometry();
    const references = new Float32Array(size * size * 2);
    const placeholder = new Float32Array(size * size * 3);
    const seeds = new Float32Array(size * size);
    for (let i = 0; i < size * size; i += 1) {
      references[i * 2] = ((i % size) + 0.5) / size;
      references[i * 2 + 1] = (Math.floor(i / size) + 0.5) / size;
      seeds[i] = i;
    }
    this.geometry.setAttribute("position", new THREE.BufferAttribute(placeholder, 3));
    this.geometry.setAttribute("aReference", new THREE.BufferAttribute(references, 2));
    this.geometry.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
    this.geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 500);

    this.drawMaterial = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      transparent: true,
      depthWrite: false,
      depthTest: true,
      blending: THREE.AdditiveBlending,
      vertexShader: /* glsl */ `
        precision highp float;
        attribute vec2 aReference;
        attribute float aSeed;
        uniform sampler2D texturePosition;
        uniform float uSize;
        uniform float uPixelRatio;
        varying float vAlpha;
        void main() {
          vec4 pos = texture2D(texturePosition, aReference);
          // Simulator frame -> three.js frame: (x, y, z) -> (x, -z, y)
          vec3 world = vec3(pos.x, -pos.z, pos.y);
          vec4 viewPos = viewMatrix * modelMatrix * vec4(world, 1.0);
          // Perspective-correct point size, clamped so near particles do not
          // blow up the additive blend.  aSeed varies the sprite size per
          // particle so the cloud does not look like one uniform grain.
          float distanceScale = 1.0 / max(-viewPos.z, 0.02);
          float sizeJitter = 0.75 + 0.5 * fract(aSeed * 0.6180339887);
          gl_PointSize = clamp(uSize * uPixelRatio * distanceScale * 0.09 * sizeJitter, 1.0, 22.0);
          gl_Position = projectionMatrix * viewPos;
          // Fade in and out over the lifetime stored in pos.w.
          float life = clamp(pos.w / 14.0, 0.0, 1.0);
          vAlpha = smoothstep(0.0, 0.12, life) * (1.0 - smoothstep(0.72, 1.0, life));
        }
      `,
      fragmentShader: /* glsl */ `
        precision highp float;
        uniform sampler2D uSprite;
        uniform float uOpacity;
        uniform vec3 uLowColor;
        uniform vec3 uHighColor;
        varying float vAlpha;
        void main() {
          vec4 texel = texture2D(uSprite, gl_PointCoord);
          if (texel.a < 0.01) {
            discard;
          }
          // Radial position inside the sprite drives a slight blue -> red
          // tint so a dense core reads hotter than its halo.
          float t = clamp(gl_PointCoord.x, 0.0, 1.0);
          vec3 tint = mix(uLowColor, uHighColor, t * 0.35);
          gl_FragColor = vec4(tint, texel.a * uOpacity * vAlpha);
        }
      `,
    });
    this.drawMaterial.uniforms.texturePosition = { value: this.positionTargets[0].texture };
    this.drawMaterial.uniforms.textureVelocity = { value: this.velocityTargets[0].texture };
    this.drawMaterial.uniforms.uLowColor = { value: new THREE.Color("#1A73E8") };
    this.drawMaterial.uniforms.uHighColor = { value: new THREE.Color("#EA4335") };
    this.uniforms.texturePosition = this.drawMaterial.uniforms.texturePosition;
    this.uniforms.textureVelocity = this.drawMaterial.uniforms.textureVelocity;

    this.points = new THREE.Points(this.geometry, this.drawMaterial);
    this.points.frustumCulled = false;
    this.scene.add(this.points);

    this.#seedTextures();
  }

  /** Fill both ping-pong targets with the initial state. */
  #seedTextures() {
    const size = this.textureSize;
    const total = size * size;
    const positions = new Float32Array(total * 4);
    const velocities = new Float32Array(total * 4);
    const min = this.domain.min;
    const max = this.domain.max;
    let seed = 20260901;
    const random = () => {
      // Deterministic LCG so a reload reproduces the same streak pattern.
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };
    for (let i = 0; i < total; i += 1) {
      positions[i * 4] = min[0] + random() * (max[0] - min[0]);
      positions[i * 4 + 1] = min[1] + random() * (max[1] - min[1]);
      positions[i * 4 + 2] = min[2] + random() * (max[2] - min[2]);
      positions[i * 4 + 3] = random() * 8.0; // staggered lifetimes
      velocities[i * 4] = 0;
      velocities[i * 4 + 1] = 0;
      velocities[i * 4 + 2] = 0;
      velocities[i * 4 + 3] = 1;
    }
    const positionTexture = new THREE.DataTexture(positions, size, size, THREE.RGBAFormat, THREE.FloatType);
    const velocityTexture = new THREE.DataTexture(velocities, size, size, THREE.RGBAFormat, THREE.FloatType);
    positionTexture.needsUpdate = true;
    velocityTexture.needsUpdate = true;
    this.#renderTextureInto(positionTexture, this.positionTargets[0]);
    this.#renderTextureInto(positionTexture, this.positionTargets[1]);
    this.#renderTextureInto(velocityTexture, this.velocityTargets[0]);
    this.#renderTextureInto(velocityTexture, this.velocityTargets[1]);
    positionTexture.dispose();
    velocityTexture.dispose();
    this.drawMaterial.uniforms.texturePosition.value = this.positionTargets[0].texture;
    this.drawMaterial.uniforms.textureVelocity.value = this.velocityTargets[0].texture;
  }

  /**
   * Blit a data texture into a render target.
   * @param {THREE.DataTexture} texture
   * @param {THREE.WebGLRenderTarget} target
   */
  #renderTextureInto(texture, target) {
    this.copyMaterial.uniforms.textureInput.value = texture;
    this.computeQuad.material = this.copyMaterial;
    const previousTarget = this.renderer.getRenderTarget();
    this.renderer.setRenderTarget(target);
    this.renderer.render(this.quadScene, this.quadCamera);
    this.renderer.setRenderTarget(previousTarget);
  }

  /** Build the CPU-advected fallback cloud. */
  #buildCPUCloud() {
    const count = Math.min(FALLBACK_PARTICLE_COUNT, Math.max(1, this.requestedCount));
    this.particleCount = count;
    this.textureSize = Math.max(2, Math.ceil(Math.sqrt(count)));

    this.uniforms = this.#sharedUniforms();
    this.geometry = new THREE.BufferGeometry();
    this.cpuPositions = new Float32Array(count * 3);
    this.cpuVelocities = new Float32Array(count * 3);
    this.cpuAges = new Float32Array(count);
    const seeds = new Float32Array(count);
    let seed = 20260901;
    const random = () => {
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };
    const min = this.domain.min;
    const max = this.domain.max;
    for (let i = 0; i < count; i += 1) {
      this.cpuPositions[i * 3] = min[0] + random() * (max[0] - min[0]);
      this.cpuPositions[i * 3 + 1] = min[1] + random() * (max[1] - min[1]);
      this.cpuPositions[i * 3 + 2] = min[2] + random() * (max[2] - min[2]);
      this.cpuAges[i] = random() * 8;
      seeds[i] = random();
    }
    this.positionAttribute = new THREE.BufferAttribute(this.cpuPositions, 3);
    this.positionAttribute.setUsage(THREE.DynamicDrawUsage);
    this.geometry.setAttribute("position", this.positionAttribute);
    this.geometry.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
    this.geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 500);

    this.drawMaterial = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexShader: /* glsl */ `
        precision highp float;
        attribute float aSeed;
        uniform float uSize;
        uniform float uPixelRatio;
        varying float vAlpha;
        void main() {
          // The CPU path already stores three.js coordinates.
          vec4 viewPos = viewMatrix * modelMatrix * vec4(position, 1.0);
          float distanceScale = 1.0 / max(-viewPos.z, 0.02);
          gl_PointSize = clamp(uSize * uPixelRatio * distanceScale * 0.09, 1.0, 22.0);
          gl_Position = projectionMatrix * viewPos;
          vAlpha = 0.35 + 0.65 * aSeed;
        }
      `,
      fragmentShader: /* glsl */ `
        precision highp float;
        uniform sampler2D uSprite;
        uniform float uOpacity;
        varying float vAlpha;
        void main() {
          vec4 texel = texture2D(uSprite, gl_PointCoord);
          if (texel.a < 0.01) {
            discard;
          }
          gl_FragColor = vec4(texel.rgb, texel.a * uOpacity * vAlpha);
        }
      `,
    });
    this.points = new THREE.Points(this.geometry, this.drawMaterial);
    this.points.frustumCulled = false;
    this.scene.add(this.points);
  }

  /** Release every GPGPU resource (used when downgrading to the CPU path). */
  #teardownGPGPU() {
    if (this.points) {
      this.scene.remove(this.points);
      this.points = null;
    }
    const targets = [
      ...(this.positionTargets || []),
      ...(this.velocityTargets || []),
    ];
    for (const target of targets) {
      if (target && typeof target.dispose === "function") {
        target.dispose();
      }
    }
    this.positionTargets = null;
    this.velocityTargets = null;
    const materials = [this.velocityMaterial, this.positionMaterial, this.copyMaterial, this.drawMaterial];
    for (const material of materials) {
      if (material && typeof material.dispose === "function") {
        material.dispose();
      }
    }
    if (this.quadGeometry && typeof this.quadGeometry.dispose === "function") {
      this.quadGeometry.dispose();
    }
  }

  // ---------------------------------------------------------------- public

  /** @returns {boolean} true when the GPGPU ping-pong path is active. */
  get isGPGPU() {
    return this.mode === "gpgpu" && Boolean(this.points);
  }

  /**
   * Resize the particle budget.  Any value above {@link FALLBACK_PARTICLE_COUNT}
   * is clamped when float render targets are unavailable.
   * @param {number} count
   * @returns {{mode: string, particleCount: number, textureSize: number}}
   */
  setParticleCount(count) {
    const requested = Math.min(MAX_PARTICLE_COUNT, Math.max(1, Math.floor(Number(count) || 0)));
    this.requestedCount = requested;
    const canFloat = supportsFloatRenderTargets(this.renderer);
    const target = canFloat ? requested : Math.min(FALLBACK_PARTICLE_COUNT, requested);
    if (target === this.particleCount && this.points) {
      return { mode: this.mode, particleCount: this.particleCount, textureSize: this.textureSize };
    }
    this.particleCount = target;

    if (this.mode === "gpgpu") {
      this.#teardownGPGPU();
      try {
        this.#buildGPGPU();
        this.mode = "gpgpu";
        this.reason = "";
      } catch (err) {
        this.reason = `GPGPU rebuild failed: ${err && err.message ? err.message : String(err)}`;
        this.#teardownGPGPU();
        this.#buildCPUCloud();
        this.mode = "cpu";
        this.particleCount = Math.min(FALLBACK_PARTICLE_COUNT, requested);
      }
    } else {
      this.#teardownGPGPU();
      this.#buildCPUCloud();
      this.mode = "cpu";
      this.particleCount = Math.min(FALLBACK_PARTICLE_COUNT, requested);
    }
    return { mode: this.mode, particleCount: this.particleCount, textureSize: this.textureSize };
  }

  /**
   * Upload a wind field returned by ``POST /api/v1/wind/field``.
   *
   * The payload carries ``positions``/``vectors`` as three flat component
   * arrays with ``shape = [nx, ny, nz]``, i.e. cell index
   * ``i + j*nx + k*nx*ny``.  It is packed into an RGBA float atlas of width
   * ``nx*ny`` and height ``nz`` (rgb = velocity, a = speed) which both the
   * GLSL sampler and the CPU integrator read from.
   *
   * @param {object} payload
   * @returns {boolean} true when the field was accepted
   */
  setWindField(payload) {
    try {
      if (!payload || typeof payload !== "object") {
        return false;
      }
      const shape = Array.isArray(payload.shape) ? payload.shape.map((n) => Math.max(1, Math.floor(Number(n) || 1))) : null;
      const vectors = payload.vectors;
      if (!shape || !Array.isArray(vectors) || vectors.length < 3) {
        return false;
      }
      const [nx, ny, nz] = shape;
      const expected = nx * ny * nz;
      const u = vectors[0];
      const v = vectors[1];
      const w = vectors[2];
      if (!Array.isArray(u) || u.length < expected) {
        return false;
      }
      const speeds = Array.isArray(payload.speeds) ? payload.speeds : null;

      const data = new Float32Array(expected * 4);
      for (let i = 0; i < expected; i += 1) {
        data[i * 4] = Number(u[i]) || 0;
        data[i * 4 + 1] = Number(v[i]) || 0;
        data[i * 4 + 2] = Number(w[i]) || 0;
        const speed = speeds && Number.isFinite(Number(speeds[i]))
          ? Number(speeds[i])
          : Math.hypot(data[i * 4], data[i * 4 + 1], data[i * 4 + 2]);
        data[i * 4 + 3] = speed;
      }

      const texture = new THREE.DataTexture(data, nx * ny, nz, THREE.RGBAFormat, THREE.FloatType);
      texture.minFilter = THREE.NearestFilter;
      texture.magFilter = THREE.NearestFilter;
      texture.wrapS = THREE.ClampToEdgeWrapping;
      texture.wrapT = THREE.ClampToEdgeWrapping;
      texture.generateMipmaps = false;
      texture.needsUpdate = true;
      if (this._windTexture) {
        this._windTexture.dispose();
      }
      this._windTexture = texture;
      this._windData = { data, nx, ny, nz };
      this._windShape = [nx, ny, nz];

      const positions = payload.positions;
      const min = [Infinity, Infinity, Infinity];
      const max = [-Infinity, -Infinity, -Infinity];
      if (Array.isArray(positions) && positions.length >= 3
        && Array.isArray(positions[0]) && Array.isArray(positions[1]) && Array.isArray(positions[2])) {
        for (let axis = 0; axis < 3; axis += 1) {
          const component = positions[axis];
          for (let i = 0; i < component.length; i += 1) {
            const value = Number(component[i]);
            if (!Number.isFinite(value)) {
              continue;
            }
            if (value < min[axis]) {
              min[axis] = value;
            }
            if (value > max[axis]) {
              max[axis] = value;
            }
          }
        }
      }
      const valid = Number.isFinite(min[0]) && Number.isFinite(max[0]) && max[0] > min[0];
      const cellSize = [1, 1, 1];
      const origin = valid ? min.slice() : this.domain.min.slice();
      if (valid) {
        this.domain = {
          min: [min[0] - 1, min[1] - 1, min[2] - 1],
          max: [max[0] + 1, max[1] + 1, max[2] + 1],
        };
        cellSize[0] = Math.max(1e-6, (max[0] - min[0]) / Math.max(1, nx - 1));
        cellSize[1] = Math.max(1e-6, (max[1] - min[1]) / Math.max(1, ny - 1));
        cellSize[2] = Math.max(1e-6, (max[2] - min[2]) / Math.max(1, nz - 1));
      } else {
        cellSize[0] = (this.domain.max[0] - this.domain.min[0]) / Math.max(1, nx);
        cellSize[1] = (this.domain.max[1] - this.domain.min[1]) / Math.max(1, ny);
        cellSize[2] = (this.domain.max[2] - this.domain.min[2]) / Math.max(1, nz);
      }

      this._fieldMeta = {
        backend: payload.backend || "",
        maxSpeed: Number(payload.max_speed) || 0,
        meanSpeed: Number(payload.mean_speed) || 0,
        points: Number(payload.grid_point_count) || expected,
      };

      const uniforms = this.drawMaterial ? this.drawMaterial.uniforms : null;
      const patch = (target) => {
        if (!target) {
          return;
        }
        if (target.uWind) {
          target.uWind.value = texture;
          target.uWindTexSize.value.set(nx, ny, nz);
          target.uWindOrigin.value.set(origin[0], origin[1], origin[2]);
          target.uWindCell.value.set(cellSize[0], cellSize[1], cellSize[2]);
          target.uWindEnabled.value = 1;
        }
        if (target.uDomainMin) {
          target.uDomainMin.value.set(this.domain.min[0], this.domain.min[1], this.domain.min[2]);
          target.uDomainMax.value.set(this.domain.max[0], this.domain.max[1], this.domain.max[2]);
        }
      };
      patch(uniforms);
      patch(this.velocityMaterial && this.velocityMaterial.uniforms);
      patch(this.positionMaterial && this.positionMaterial.uniforms);
      return true;
    } catch (err) {
      this.notify(`風場データの適用に失敗しました: ${err.message}`, "warn");
      return false;
    }
  }

  /**
   * Fetch a fresh wind field if the refresh interval has elapsed.
   * @param {number} nowMs
   * @returns {Promise<object|null>}
   */
  async refreshWindField(nowMs) {
    if (!this.fetchField) {
      return null;
    }
    const now = Number.isFinite(nowMs) ? nowMs : performance.now();
    if (now - this._lastFetchMs < this._refreshIntervalMs) {
      return null;
    }
    this._lastFetchMs = now;
    try {
      const payload = await this.fetchField();
      if (payload) {
        this.setWindField(payload);
      }
      return payload;
    } catch (err) {
      this.notify(`風場取得に失敗しました: ${err.message}`, "warn");
      return null;
    }
  }

  /**
   * Advance the simulation.
   * @param {number} delta seconds
   * @returns {object} per-frame statistics
   */
  update(delta) {
    if (this._disposed || !this.points) {
      return { mode: this.mode, particleCount: this.particleCount, fieldBackend: this._fieldMeta.backend };
    }
    const dt = Math.min(0.05, Math.max(0, Number(delta) || 0));
    this._time += dt;
    this._frameCount += 1;

    if (this.mode === "gpgpu") {
      this.#stepGPGPU(dt);
    } else {
      this.#stepCPU(dt);
    }
    return {
      mode: this.mode,
      particleCount: this.particleCount,
      textureSize: this.textureSize,
      fieldBackend: this._fieldMeta.backend,
      maxSpeed: this._fieldMeta.maxSpeed,
      meanSpeed: this._fieldMeta.meanSpeed,
    };
  }

  /** One velocity pass followed by one position pass, ping-ponged. */
  #stepGPGPU(dt) {
    const source = this.currentIndex;
    const destination = 1 - source;
    const read = this.positionTargets[source].texture;
    const readVelocity = this.velocityTargets[source].texture;

    const previousTarget = this.renderer.getRenderTarget();
    const previousAutoClear = this.renderer.autoClear;
    this.renderer.autoClear = false;

    this.velocityMaterial.uniforms.texturePosition.value = read;
    this.velocityMaterial.uniforms.textureVelocity.value = readVelocity;
    this.velocityMaterial.uniforms.uDelta.value = dt;
    this.velocityMaterial.uniforms.uTime.value = this._time;
    this.computeQuad.material = this.velocityMaterial;
    this.renderer.setRenderTarget(this.velocityTargets[destination]);
    this.renderer.render(this.quadScene, this.quadCamera);

    this.positionMaterial.uniforms.texturePosition.value = read;
    this.positionMaterial.uniforms.textureVelocity.value = this.velocityTargets[destination].texture;
    this.positionMaterial.uniforms.uDelta.value = dt;
    this.computeQuad.material = this.positionMaterial;
    this.renderer.setRenderTarget(this.positionTargets[destination]);
    this.renderer.render(this.quadScene, this.quadCamera);

    this.renderer.setRenderTarget(previousTarget);
    this.renderer.autoClear = previousAutoClear;

    this.currentIndex = destination;
    this.drawMaterial.uniforms.texturePosition.value = this.positionTargets[destination].texture;
    this.drawMaterial.uniforms.textureVelocity.value = this.velocityTargets[destination].texture;
    this.drawMaterial.uniforms.uTime.value = this._time;
  }

  /** Trilinear wind lookup on the CPU against the packed field. */
  #sampleWind(x, y, z) {
    const field = this._windData;
    if (!field) {
      return [0, 0, 0, 0];
    }
    const { data, nx, ny, nz } = field;
    const origin = this.drawMaterial.uniforms.uWindOrigin.value;
    const cell = this.drawMaterial.uniforms.uWindCell.value;
    const gx = (x - origin.x) / cell.x - 0.5;
    const gy = (y - origin.y) / cell.y - 0.5;
    const gz = (z - origin.z) / cell.z - 0.5;
    const fx = Math.floor(gx);
    const fy = Math.floor(gy);
    const fz = Math.floor(gz);
    const tx = Math.min(1, Math.max(0, gx - fx));
    const ty = Math.min(1, Math.max(0, gy - fy));
    const tz = Math.min(1, Math.max(0, gz - fz));
    let ax = 0;
    let ay = 0;
    let az = 0;
    let aw = 0;
    let wsum = 0;
    for (let k = 0; k < 2; k += 1) {
      for (let j = 0; j < 2; j += 1) {
        for (let i = 0; i < 2; i += 1) {
          const ci = Math.min(nx - 1, Math.max(0, fx + i));
          const cj = Math.min(ny - 1, Math.max(0, fy + j));
          const ck = Math.min(nz - 1, Math.max(0, fz + k));
          const w = (i ? tx : 1 - tx) * (j ? ty : 1 - ty) * (k ? tz : 1 - tz);
          const index = ((ck * ny + cj) * nx + ci) * 4;
          ax += data[index] * w;
          ay += data[index + 1] * w;
          az += data[index + 2] * w;
          aw += data[index + 3] * w;
          wsum += w;
        }
      }
    }
    const inverse = wsum > 1e-6 ? 1 / wsum : 0;
    return [ax * inverse, ay * inverse, az * inverse, aw * inverse];
  }

  /** Integrate the CPU cloud for one frame. */
  #stepCPU(dt) {
    const positions = this.cpuPositions;
    const velocities = this.cpuVelocities;
    const ages = this.cpuAges;
    const lifetime = this.uniforms.uLifetime.value;
    const advection = this.uniforms.uAdvection.value;
    const settle = this.uniforms.uSettle.value;
    const k = 1 - Math.exp(-advection * dt);
    const min = this.domain.min;
    const max = this.domain.max;
    const margin = [0.25 * (max[0] - min[0]), 0.25 * (max[1] - min[1]), 0.25 * (max[2] - min[2])];

    let seed = (this._frameCount * 2654435761) % 4294967296;
    const random = () => {
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };

    for (let i = 0; i < ages.length; i += 1) {
      const i3 = i * 3;
      const [wu, wv, ww] = this.#sampleWind(positions[i3], positions[i3 + 1], positions[i3 + 2]);
      velocities[i3] += (wu - velocities[i3]) * k;
      velocities[i3 + 1] += (wv - velocities[i3 + 1]) * k;
      velocities[i3 + 2] += (ww - settle - velocities[i3 + 2]) * k;

      positions[i3] += velocities[i3] * dt;
      positions[i3 + 1] += velocities[i3 + 1] * dt;
      positions[i3 + 2] += velocities[i3 + 2] * dt;
      ages[i] += dt;

      const outside = positions[i3] < min[0] - margin[0] || positions[i3] > max[0] + margin[0]
        || positions[i3 + 1] < min[1] - margin[1] || positions[i3 + 1] > max[1] + margin[1]
        || positions[i3 + 2] < min[2] - margin[2] || positions[i3 + 2] > max[2] + margin[2];
      if (outside || ages[i] > lifetime) {
        positions[i3] = min[0] + random() * (max[0] - min[0]);
        positions[i3 + 1] = min[1] + random() * (max[1] - min[1]);
        positions[i3 + 2] = min[2] + random() * (max[2] - min[2]);
        velocities[i3] = 0;
        velocities[i3 + 1] = 0;
        velocities[i3 + 2] = 0;
        ages[i] = random() * lifetime * 0.25;
      }
    }
    this.positionAttribute.needsUpdate = true;
    this.uniforms.uTime.value = this._time;
  }

  /** @returns {object} diagnostics for the header overlay */
  getStats() {
    return {
      mode: this.mode,
      particleCount: this.particleCount,
      textureSize: this.textureSize,
      reason: this.reason,
      fieldBackend: this._fieldMeta.backend,
      gridPoints: this._fieldMeta.points,
      maxSpeed: this._fieldMeta.maxSpeed,
      meanSpeed: this._fieldMeta.meanSpeed,
    };
  }

  /** Hide or show the particle cloud. */
  setVisible(visible) {
    if (this.points) {
      this.points.visible = Boolean(visible);
    }
  }

  /** Teardown. */
  dispose() {
    if (this._disposed) {
      return;
    }
    this._disposed = true;
    this.#teardownGPGPU();
    if (this._windTexture) {
      this._windTexture.dispose();
      this._windTexture = null;
    }
    if (this.sprite && typeof this.sprite.dispose === "function") {
      this.sprite.dispose();
    }
    this.points = null;
  }
}

export default WindGPGPU;
