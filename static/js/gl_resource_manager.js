import * as THREE from "three";

const TEXTURE_KEYS = [
  "map", "alphaMap", "bumpMap", "normalMap", "roughnessMap",
  "metalnessMap", "specularMap", "envMap", "lightMap", "aoMap",
  "displacementMap", "emissiveMap", "gradientMap",
];

export class WebGLResourceManager {
  constructor(renderer, canvas, { onContextLost, onContextRestored } = {}) {
    this.renderer = renderer;
    this.canvas = canvas;
    this._sharedTextures = new Set();
    this._tracked = new Set();
    this._abort = new AbortController();

    const { signal } = this._abort;
    canvas.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      onContextLost?.(e);
    }, { signal });

    canvas.addEventListener("webglcontextrestored", (e) => {
      for (const tex of this._sharedTextures) tex.needsUpdate = true;
      onContextRestored?.(e);
    }, { signal });

    window.addEventListener("pagehide", () => this.disposeAll(), { signal });
  }

  registerSharedTexture(texture) {
    if (texture) this._sharedTextures.add(texture);
    return texture;
  }

  track(resource) {
    if (resource) this._tracked.add(resource);
    return resource;
  }

  disposeMaterial(material, { includeShared = false } = {}) {
    if (!material) return;
    for (const key of TEXTURE_KEYS) {
      const tex = material[key];
      if (!tex || typeof tex.dispose !== "function") continue;
      if (!includeShared && this._sharedTextures.has(tex)) {
        material[key] = null;
        continue;
      }
      tex.dispose();
      material[key] = null;
    }
    material.dispose?.();
  }

  disposeObject(root, { includeShared = false } = {}) {
    if (!root) return;
    const disposedMaterials = new Set();
    const disposedGeometries = new Set();

    root.traverse((node) => {
      if (node.isInstancedMesh) node.dispose();
      if (node.geometry && !disposedGeometries.has(node.geometry)) {
        disposedGeometries.add(node.geometry);
        node.geometry.dispose();
      }
      if (node.material) {
        const list = Array.isArray(node.material) ? node.material : [node.material];
        for (const mat of list) {
          if (mat && !disposedMaterials.has(mat)) {
            disposedMaterials.add(mat);
            this.disposeMaterial(mat, { includeShared });
          }
        }
      }
    });
    root.removeFromParent();
  }

  disposeAll(scene = null) {
    this._abort.abort();
    if (scene) this.disposeObject(scene, { includeShared: true });
    for (const res of this._tracked) res.dispose?.();
    this._tracked.clear();
    for (const tex of this._sharedTextures) tex.dispose?.();
    this._sharedTextures.clear();
    this.renderer?.dispose();
  }
}