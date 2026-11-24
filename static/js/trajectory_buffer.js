import * as THREE from "three";

/**
 * Fixed-length Float32Array based 6DoF trajectory buffer.
 * - append() uses addUpdateRange() for single-vertex GPU upload (O(1))
 * - capacity overflow uses TypedArray.copyWithin (native memmove) for single shift
 * - Scratch Vector3/Quaternion/Euler reused to eliminate per-frame GC
 */
export class TrajectoryLineBuffer {
  constructor(capacity = 6000, heatColourFn) {
    this.capacity = capacity;
    this.heatColour = heatColourFn;
    this.count = 0;

    this.positions = new Float32Array(capacity * 3);
    this.colors = new Float32Array(capacity * 3);

    this.posAttr = new THREE.BufferAttribute(this.positions, 3).setUsage(THREE.DynamicDrawUsage);
    this.colAttr = new THREE.BufferAttribute(this.colors, 3).setUsage(THREE.DynamicDrawUsage);

    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute("position", this.posAttr);
    this.geometry.setAttribute("color", this.colAttr);
    this.geometry.setDrawRange(0, 0);

    this.material = new THREE.LineBasicMaterial({ vertexColors: true });
    this.line = new THREE.Line(this.geometry, this.material);
    this.line.frustumCulled = false;

    this._lastX = NaN;
    this._lastY = NaN;
    this._lastZ = NaN;
  }

  append(x, y, z, stress = 0) {
    if (this.count > 0) {
      const dx = x - this._lastX;
      const dy = y - this._lastY;
      const dz = z - this._lastZ;
      if (dx * dx + dy * dy + dz * dz < 3.6e-5) return;
    }
    this._lastX = x;
    this._lastY = y;
    this._lastZ = z;

    if (this.count >= this.capacity) {
      this.positions.copyWithin(0, 3);
      this.colors.copyWithin(0, 3);
      this.count = this.capacity - 1;
      this._writeVertex(this.count, x, y, z, stress);
      this.count = this.capacity;
      this.posAttr.clearUpdateRanges();
      this.colAttr.clearUpdateRanges();
      this.posAttr.needsUpdate = true;
      this.colAttr.needsUpdate = true;
      return;
    }

    const idx = this.count;
    this._writeVertex(idx, x, y, z, stress);
    this.count += 1;

    this.posAttr.addUpdateRange(idx * 3, 3);
    this.colAttr.addUpdateRange(idx * 3, 3);
    this.posAttr.needsUpdate = true;
    this.colAttr.needsUpdate = true;
    this.geometry.setDrawRange(0, this.count);
  }

  setFromHistory(samples, endIndex) {
    const n = Math.min(endIndex + 1, samples.length, this.capacity);
    const start = Math.max(0, endIndex + 1 - n);
    let out = 0;
    for (let i = start; i < start + n; i += 1) {
      const s = samples[i];
      const x = Number(s.x_m) || 0;
      const y = -(Number(s.z_m) || 0);
      const z = Number(s.y_m) || 0;
      this._writeVertex(out, x, y, z, Number(s.max_stress_index) || 0);
      out += 1;
    }
    this.count = out;
    if (out > 0) {
      this._lastX = this.positions[(out - 1) * 3];
      this._lastY = this.positions[(out - 1) * 3 + 1];
      this._lastZ = this.positions[(out - 1) * 3 + 2];
    }
    this.posAttr.clearUpdateRanges();
    this.colAttr.clearUpdateRanges();
    this.posAttr.addUpdateRange(0, out * 3);
    this.colAttr.addUpdateRange(0, out * 3);
    this.posAttr.needsUpdate = true;
    this.colAttr.needsUpdate = true;
    this.geometry.setDrawRange(0, out);
  }

  _writeVertex(idx, x, y, z, stress) {
    const o = idx * 3;
    this.positions[o] = x;
    this.positions[o + 1] = y;
    this.positions[o + 2] = z;
    const clamped = stress < 0 ? 0 : stress > 1 ? 1 : stress;
    const [r, g, b] = this.heatColour(clamped);
    this.colors[o] = r / 255;
    this.colors[o + 1] = g / 255;
    this.colors[o + 2] = b / 255;
  }

  clear() {
    this.count = 0;
    this._lastX = this._lastY = this._lastZ = NaN;
    this.geometry.setDrawRange(0, 0);
  }

  dispose() {
    this.geometry.dispose();
    this.material.dispose();
    this.line.removeFromParent();
  }
}