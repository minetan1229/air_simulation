/*
 * meshpanel.js — glTF / GLB / OBJ / STL import.
 *
 * The server owns the measurement (area, span, CG, inertia, aero panels); this
 * panel drives the three upload routes (file / drop / URL), renders the same
 * geometry locally with the three.js loaders and exposes the per-metric
 * manual overrides the API supports.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

import { clear, fmt, isNum, make, readArrayBuffer, readTextFile, sig, heatColour } from "./lib.js";
import { applyStressColors, createAirframeMaterial } from "./airframe.js";

const METRIC_ROWS = [
  { key: "surface_area_m2", label: "表面積 S", unit: "m²", digits: 5 },
  { key: "planform_area_m2", label: "翼面積", unit: "m²", digits: 5 },
  { key: "wingspan_m", label: "翼幅 b", unit: "m", digits: 4 },
  { key: "mean_chord_m", label: "平均翼弦 c̄", unit: "m", digits: 4 },
  { key: "taper_ratio", label: "テーパ比 λ", unit: "", digits: 3 },
  { key: "aspect_ratio", label: "アスペクト比 AR", unit: "", digits: 3 },
  { key: "volume_m3", label: "体積", unit: "m³", digits: 8 },
  { key: "mass_kg", label: "質量 m", unit: "g", digits: 2, scale: 1000 },
  { key: "paper_gsm", label: "坪量", unit: "g/m²", digits: 1 },
  { key: "cg_m", label: "重心 (x, y, z)", unit: "mm", digits: 1, scale: 1000, kind: "vector" },
  { key: "scale_factor", label: "スケール係数", unit: "×", digits: 3 },
];

export class MeshPanel {
  constructor({ api, store, dom, viewport, notify }) {
    this.api = api;
    this.store = store;
    this.dom = dom;
    this.viewport = viewport;
    this.notify = notify || (() => {});
    this.metrics = null;
    this.overrides = {};
    this.object = null;
    this.gltfLoader = new GLTFLoader();
    this.objLoader = new OBJLoader();
    this.stlLoader = new STLLoader();
  }

  mount() {
    const { dom } = this;
    this._buildOptions(dom.options);

    dom.drop.addEventListener("click", () => dom.file.click());
    dom.drop.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        dom.file.click();
      }
    });
    dom.file.addEventListener("change", () => {
      if (dom.file.files && dom.file.files[0]) this.importFile(dom.file.files[0]);
    });
    for (const type of ["dragenter", "dragover"]) {
      dom.drop.addEventListener(type, (event) => {
        event.preventDefault();
        dom.drop.classList.add("is-over");
      });
    }
    for (const type of ["dragleave", "drop"]) {
      dom.drop.addEventListener(type, (event) => {
        event.preventDefault();
        dom.drop.classList.remove("is-over");
      });
    }
    dom.drop.addEventListener("drop", (event) => {
      const file = event.dataTransfer?.files?.[0];
      if (file) this.importFile(file);
    });

    dom.fetch.addEventListener("click", () => this.importUrl(dom.url.value.trim()));
    dom.list.addEventListener("click", (event) => {
      const row = event.target.closest(".model");
      if (row && row.dataset.id) this.select(row.dataset.id);
    });
    this.refresh();
  }

  _buildOptions(host) {
    clear(host);
    this.scaleHint = this._select(host, "単位補正 scale_hint", [
      ["m", "m"],
      ["cm", "cm"],
      ["mm", "mm"],
    ], "m");
    this.cgMode = this._select(host, "CG 算出", [
      ["volume", "体積"],
      ["surface", "面積"],
    ], "volume");
    this.gsm = this._number(host, "坪量 g/m²", 40, 400, 1, 100);
  }

  _select(host, label, options, value) {
    const select = make("select", { class: "select" });
    for (const [optionValue, optionText] of options) {
      select.append(make("option", { value: optionValue, text: optionText }));
    }
    select.value = value;
    host.append(
      make("div", { class: "field" }, [
        make("div", { class: "field-top" }, [make("span", { class: "field-key", text: label })]),
        select,
      ])
    );
    return select;
  }

  _number(host, label, min, max, step, value) {
    const input = make("input", { type: "number", class: "num-input", min, max, step, value, style: "width:100%" });
    host.append(
      make("div", { class: "field" }, [
        make("div", { class: "field-top" }, [make("span", { class: "field-key", text: label })]),
        input,
      ])
    );
    return input;
  }

  get options() {
    return {
      scale_hint: this.scaleHint.value,
      cg_mode: this.cgMode.value,
      paper_gsm: Number(this.gsm.value),
    };
  }

  /* --------------------------------------------------------------- import */

  async importFile(file) {
    const name = file.name.toLowerCase();
    let local = null;
    try {
      if (name.endsWith(".glb") || name.endsWith(".gltf")) {
        const buffer = await readArrayBuffer(file);
        local = await this._parseGLTF(buffer);
      } else if (name.endsWith(".obj")) {
        local = await this._parseOBJ(await readTextFile(file));
      } else if (name.endsWith(".stl")) {
        local = this._parseSTL(await readArrayBuffer(file));
      } else {
        this.notify("glTF / GLB / OBJ / STL に対応しています", "warn");
        return;
      }
    } catch (error) {
      this.notify(`ローカル表示に失敗しました: ${error.message}`, "error");
    }
    try {
      const metrics = await this.api.meshImportFile(file, this.options);
      this._apply(metrics, local);
      await this.refresh();
      this.notify(`取込: ${metrics.name || file.name} (${metrics.source_format})`, "ok");
    } catch (error) {
      this.notify(`取込に失敗しました: ${error.message}`, "error");
    }
  }

  async importUrl(url) {
    if (!url) {
      this.notify("URL を入力してください", "warn");
      return;
    }
    try {
      const metrics = await this.api.meshImportUrl(url, this.options);
      this._apply(metrics, null);
      await this.refresh();
      this.notify(`取込: ${metrics.name || url}`, "ok");
    } catch (error) {
      this.notify(`URL からの取込に失敗しました: ${error.message}`, "error");
    }
  }

  async select(modelId) {
    try {
      const metrics = await this.api.meshSelect(modelId);
      this._apply(metrics, null);
      await this.refresh();
      this.notify(`選択: ${metrics.name || modelId.slice(0, 8)}`, "ok");
    } catch (error) {
      this.notify(`選択に失敗しました: ${error.message}`, "error");
    }
  }

  async refresh() {
    try {
      const payload = await this.api.meshList();
      this.store.meshModels = payload.models || [];
      this.store.mesh = payload.selected || null;
    } catch (error) {
      return;
    }
    const host = this.dom.list;
    clear(host);
    if (!this.store.meshModels.length) {
      host.append(make("p", { class: "chart-empty", text: "未読込" }));
      return;
    }
    for (const entry of this.store.meshModels) {
      host.append(
        make("div", { class: "model", "data-id": entry.model_id, "aria-current": entry.model_id === this.store.mesh ? "true" : null }, [
          make("span", {}, [
            make("div", { text: entry.name || entry.model_id.slice(0, 8) }),
            make("div", { class: "model-meta", text: `${entry.source_format} · ${entry.vertex_count}v · ${entry.face_count}f` }),
          ]),
          make("span", { class: "model-meta", text: `b=${fmt(entry.wingspan_m, 3)}m` }),
        ])
      );
    }
  }

  _apply(metrics, localObject) {
    this.metrics = metrics;
    this.overrides = { ...(metrics.overrides || {}) };
    this.store.mesh = metrics.model_id;
    if (localObject) this._mountLocal(localObject, metrics);
    else this.viewport.setMesh(null);
    this._renderMetrics();
    // The server pushed mesh-derived geometry into the live airframe; the app
    // re-reads the snapshot so the rail and the viewport stay in step.
    this.store.emit("mesh", metrics);
  }

  /* -------------------------------------------------------------- render */

  _mountLocal(object, metrics) {
    const material = createAirframeMaterial();
    const box = new THREE.Box3().setFromObject(object);
    const size = new THREE.Vector3();
    const centre = new THREE.Vector3();
    box.getSize(size);
    box.getCenter(centre);

    // Orient the imported frame like the procedural airframe: the longest
    // source axis becomes forward (+x), the second longest becomes span (+z).
    const ranked = [0, 1, 2].sort((a, b) => size.getComponent(b) - size.getComponent(a));
    const forwardSource = ranked[0];
    const spanSource = ranked[1];
    const upSource = ranked[2];
    const permute = (x, y, z) => {
      const axis = [x, y, z];
      return [axis[forwardSource], axis[upSource], axis[spanSource]];
    };

    object.traverse((node) => {
      if (!node.isMesh) return;
      node.geometry.computeVertexNormals();
      const attribute = node.geometry.getAttribute("position");
      for (let i = 0; i < attribute.count; i += 1) {
        const p = permute(attribute.getX(i), attribute.getY(i), attribute.getZ(i));
        attribute.setXYZ(i, p[0], p[1], p[2]);
      }
      attribute.needsUpdate = true;
      if (!node.geometry.getAttribute("uv")) {
        const zeros = new Float32Array(attribute.count * 2);
        node.geometry.setAttribute("uv", new THREE.Float32BufferAttribute(zeros, 2));
      }
      node.geometry.computeBoundingBox();
      node.geometry.computeBoundingSphere();
      node.material = material;
    });

    const transformed = new THREE.Box3().setFromObject(object);
    const tsize = new THREE.Vector3();
    const tcentre = new THREE.Vector3();
    transformed.getSize(tsize);
    transformed.getCenter(tcentre);
    object.position.sub(tcentre);

    const span = isNum(metrics?.wingspan_m) && metrics.wingspan_m > 1e-4 ? metrics.wingspan_m : tsize.z;
    const scale = span / (tsize.z || 1);
    object.scale.setScalar(scale);

    applyStressColors(firstGeometry(object), null, heatColour);
    this.viewport.setMesh(object);
    this.object = object;
  }

  _renderMetrics() {
    const host = this.dom.metrics;
    clear(host);
    if (!this.metrics) {
      host.append(make("p", { class: "chart-empty", text: "モデル未選択" }));
      return;
    }
    const table = make("table");
    table.append(
      make("thead", {}, [
        make("tr", {}, [
          make("th", { text: "物理量" }),
          make("th", { text: "値" }),
          make("th", { text: "手動" }),
        ]),
      ])
    );
    const body = make("tbody");
    const values = this.metrics;
    for (const row of METRIC_ROWS) {
      const raw = row.kind === "vector" ? values.cg_m : Number(values[row.key]);
      const shown = isNum(raw) ? raw * (row.scale || 1) : NaN;
      const overrideKey = row.key;
      const active = overrideKey in this.overrides;
      const shownOverride = row.kind === "vector"
        ? (this.overrides[overrideKey] || []).map((v) => fmt(Number(v) * 1000, 1)).join(", ")
        : fmt(Number(this.overrides[overrideKey]), 6);
      const input = make("input", {
        type: row.kind === "vector" ? "text" : "number",
        class: "num-input",
        step: "any",
        value: active ? shownOverride : isNum(raw) ? fmt(shown, row.digits) : "",
        style: row.kind === "vector" ? "width:100%;min-width:96px" : "width:100%",
        disabled: active ? null : true,
      });
      const toggle = make("input", { type: "checkbox", checked: active ? true : null });
      const apply = () => {
        if (row.kind === "vector") {
          const parts = String(input.value)
            .split(",")
            .map((part) => Number(part.trim()) / 1000);
          if (parts.length !== 3 || parts.some((value) => !Number.isFinite(value))) {
            this.notify("重心は x, y, z の 3 値 (mm) で入力してください", "warn");
            return;
          }
          this.overrides[overrideKey] = parts;
        } else {
          const value = Number(input.value);
          if (!Number.isFinite(value)) return;
          this.overrides[overrideKey] = value;
        }
        this._pushOverrides();
      };
      toggle.addEventListener("change", () => {
        if (toggle.checked) {
          input.disabled = false;
          apply();
        } else {
          delete this.overrides[overrideKey];
          input.disabled = true;
          this._pushOverrides();
        }
      });
      input.addEventListener("change", () => {
        if (!toggle.checked) return;
        apply();
      });
      body.append(
        make("tr", {}, [
          make("td", { text: `${row.label} [${row.unit || "-"}]` }),
          make("td", { text: isNum(shown) ? fmt(shown, row.digits) : "—" }),
          make("td", {}, [make("label", { class: "switch" }, [toggle, make("span", { class: "switch-box" }), input])]),
        ])
      );
    }
    const inertia = (values.inertia_tensor || []).map((value) => sig(Number(value), 3));
    body.append(
      make("tr", {}, [
        make("td", { text: "慣性テンソル I [g·m²]" }),
        make("td", { colspan: "2", text: inertia.length ? inertia.join(" ") : "—" }),
      ])
    );
    body.append(
      make("tr", {}, [
        make("td", { text: `パネル ${values.panel_count} / 頂点 ${values.vertex_count}` }),
        make("td", { colspan: "2", text: `${values.cg_mode} · ×${sig(Number(values.scale_factor), 3)}` }),
      ])
    );
    table.append(body);
    host.append(table);
  }

  async _pushOverrides() {
    if (!this.metrics) return;
    try {
      const updated = await this.api.meshOverrides(this.metrics.model_id, this.overrides);
      this.metrics = updated;
      this._renderMetrics();
      this.notify("上書き値を反映しました", "ok");
    } catch (error) {
      this.notify(`上書きに失敗しました: ${error.message}`, "error");
    }
  }

  /* ------------------------------------------------------------- loaders */

  _parseGLTF(buffer) {
    return new Promise((resolve, reject) => {
      this.gltfLoader.parse(buffer, "", (gltf) => resolve(gltf.scene || gltf.scenes[0]), reject);
    });
  }

  _parseOBJ(text) {
    return new Promise((resolve, reject) => {
      try {
        resolve(this.objLoader.parse(text));
      } catch (error) {
        reject(error);
      }
    });
  }

  _parseSTL(buffer) {
    try {
      const geometry = this.stlLoader.parse(buffer);
      const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
      mesh.name = "stl";
      return mesh;
    } catch (error) {
      throw error;
    }
  }
}

function firstGeometry(object) {
  let found = null;
  object.traverse((node) => {
    if (!found && node.isMesh) found = node.geometry;
  });
  return found;
}