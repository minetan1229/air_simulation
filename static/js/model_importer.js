/**
 * model_importer.js -- glTF / GLB / OBJ / STL ingestion.
 *
 * Three input paths, one pipeline:
 *   1. file picker      -> ``<input type=file>``  -> File
 *   2. drag & drop      -> dropzone               -> File
 *   3. URL              -> text field + button   -> {url, filename}
 *
 * ``importModel(source)`` is the single entry point.  A ``File`` is uploaded
 * as multipart/form-data; a URL descriptor is posted as JSON.  Either way the
 * server answers with a ``MeshMetrics`` payload, which is rendered into the
 * metrics table with a per-metric auto / manual-override toggle.
 *
 * Geometry for the 3D view is taken from the metrics payload when it carries
 * vertex data; otherwise the metrics are re-fetched with
 * ``GET /api/v1/mesh/metrics/<model_id>``, and finally, when the server only
 * publishes scalar quantities, the original file is parsed in the browser with
 * the matching three.js loader (loaded lazily so a CDN failure degrades to the
 * scalar reconstruction instead of breaking the page).
 */

/** Accepted extension list per format selector value (used for the hint text). */
export const STREAM_EXTENSIONS = Object.freeze({
  gltf: ".gltf / .glb",
  glb: ".glb",
  obj: ".obj",
  stl: ".stl",
});

/** Metrics surfaced in the table (mirrors MESH_METRIC_KEYS in config_schema). */
export const METRIC_ROWS = Object.freeze([
  { key: "surface_area_m2", label: "表面積 S", unit: "m2", digits: 6 },
  { key: "planform_area_m2", label: "翼面積 S_planform", unit: "m2", digits: 6 },
  { key: "wingspan_m", label: "翼幅 b", unit: "m", digits: 5 },
  { key: "mean_chord_m", label: "平均翼弦 c", unit: "m", digits: 5 },
  { key: "taper_ratio", label: "テーパ比 λ", unit: "-", digits: 4 },
  { key: "aspect_ratio", label: "アスペクト比 AR", unit: "-", digits: 4 },
  { key: "mass_kg", label: "質量 m", unit: "kg", digits: 6 },
  { key: "cg_x_m", label: "重心 x_cg", unit: "m", digits: 5 },
  { key: "cg_y_m", label: "重心 y_cg", unit: "m", digits: 5 },
  { key: "cg_z_m", label: "重心 z_cg", unit: "m", digits: 5 },
]);

/** Human labels for the model list entries. */
const FORMAT_LABELS = {
  gltf: "glTF",
  glb: "GLB",
  obj: "OBJ",
  stl: "STL",
  unknown: "?",
};

/** Normalise the incoming metrics into the flat table shape. */
function normaliseMetrics(metrics) {
  const flat = { ...metrics };
  const cg = metrics && metrics.cg_m;
  if (Array.isArray(cg) && cg.length >= 3) {
    flat.cg_x_m = Number(cg[0]);
    flat.cg_y_m = Number(cg[1]);
    flat.cg_z_m = Number(cg[2]);
  }
  // An inertia tensor arrives either flat (9 numbers) or nested (3x3).
  let diag = [NaN, NaN, NaN];
  const inertia = metrics && metrics.inertia_tensor;
  if (Array.isArray(inertia) && inertia.length >= 9) {
    diag = [Number(inertia[0]), Number(inertia[4]), Number(inertia[8])];
  } else if (inertia && Array.isArray(inertia[0]) && inertia[0].length >= 3) {
    diag = [Number(inertia[0][0]), Number(inertia[1][1]), Number(inertia[2][2])];
  }
  flat.inertia_diag = diag;
  return flat;
}

/** Extract vertex positions + indices from a metrics payload, if present. */
function extractGeometry(metrics) {
  if (!metrics || typeof metrics !== "object") {
    return null;
  }
  const candidates = [metrics.geometry, metrics.mesh, metrics.vertices, metrics.positions, metrics.vertices_m];
  for (const candidate of candidates) {
    if (!candidate) {
      continue;
    }
    if (Array.isArray(candidate) && candidate.length) {
      const flat = candidate.every((v) => typeof v === "number");
      if (flat && candidate.length >= 9 && candidate.length % 3 === 0) {
        return { positions: new Float32Array(candidate), indices: null };
      }
      if (Array.isArray(candidate[0]) && candidate[0].length === 3) {
        const out = new Float32Array(candidate.length * 3);
        for (let i = 0; i < candidate.length; i += 1) {
          const v = candidate[i];
          out[i * 3] = Number(v[0]) || 0;
          out[i * 3 + 1] = Number(v[1]) || 0;
          out[i * 3 + 2] = Number(v[2]) || 0;
        }
        return { positions: out, indices: null };
      }
    }
    if (candidate && typeof candidate === "object") {
      const positions = candidate.positions || candidate.vertices;
      if (Array.isArray(positions) && positions.length) {
        const flat = positions.every((v) => typeof v === "number");
        const out = flat
          ? new Float32Array(positions)
          : new Float32Array(positions.length * 3);
        if (!flat) {
          for (let i = 0; i < positions.length; i += 1) {
            const v = positions[i];
            out[i * 3] = Number(v[0]) || 0;
            out[i * 3 + 1] = Number(v[1]) || 0;
            out[i * 3 + 2] = Number(v[2]) || 0;
          }
        }
        const indexList = candidate.indices || candidate.faces;
        let indices = null;
        if (Array.isArray(indexList) && indexList.length >= 3) {
          indices = indexList.length * 3 > 65535 ? new Uint32Array(indexList) : new Uint16Array(indexList);
        }
        return { positions: out, indices };
      }
    }
  }
  return null;
}

export class ModelImporter {
  /**
   * @param {object} options
   * @param {{post:Function, postForm:Function, get:Function}} options.api
   * @param {object} options.elements DOM lookups (see index.html ids)
   * @param {(metrics: object) => void} [options.onMetrics]
   * @param {(payload: object) => void} [options.onGeometry] receives {positions, indices, metrics}
   * @param {(message: string, kind?: string) => void} [options.notify]
   */
  constructor(options = {}) {
    if (!options.api) {
      throw new Error("ModelImporter requires an ApiClient");
    }
    this.api = options.api;
    this.el = options.elements || {};
    this.onMetrics = typeof options.onMetrics === "function" ? options.onMetrics : () => {};
    this.onGeometry = typeof options.onGeometry === "function" ? options.onGeometry : () => {};
    this.notify = typeof options.notify === "function" ? options.notify : () => {};

    this.metrics = null;
    this.selectedModelId = null;
    this.overrides = {};
    this._lastFile = null;
    this._lastSource = null;
    this._dragDepth = 0;
  }

  // ------------------------------------------------------------------ bind

  /** Wire the picker, dropzone, URL field, list and options. */
  bind() {
    const el = this.el;

    if (el.fileInput && el.dropzone) {
      el.dropzone.addEventListener("click", () => el.fileInput.click());
      el.dropzone.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          el.fileInput.click();
        }
      });
      el.fileInput.addEventListener("change", () => {
        const file = el.fileInput.files && el.fileInput.files[0];
        if (file) {
          this.importModel(file).catch((err) => this.#fail(err, "ファイル読込"));
        }
        el.fileInput.value = "";
      });
    }

    if (el.dropzone) {
      const target = el.dropzone;
      target.addEventListener("dragenter", (event) => {
        event.preventDefault();
        this._dragDepth += 1;
        target.classList.add("is-dragover");
      });
      target.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = "copy";
        }
        target.classList.add("is-dragover");
      });
      target.addEventListener("dragleave", (event) => {
        event.preventDefault();
        this._dragDepth = Math.max(0, this._dragDepth - 1);
        if (this._dragDepth === 0) {
          target.classList.remove("is-dragover");
        }
      });
      target.addEventListener("drop", (event) => {
        event.preventDefault();
        this._dragDepth = 0;
        target.classList.remove("is-dragover");
        const dt = event.dataTransfer;
        if (!dt) {
          return;
        }
        const file = dt.files && dt.files[0];
        if (file) {
          this.importModel(file).catch((err) => this.#fail(err, "ドロップ読込"));
          return;
        }
        const uri = dt.getData("text/uri-list") || dt.getData("text/plain");
        if (uri && /^https?:\/\//i.test(uri.trim())) {
          this.importModel({ url: uri.trim(), filename: this.#filenameFromUrl(uri) })
            .catch((err) => this.#fail(err, "ドロップ URL 読込"));
        } else if (uri) {
          this.#status("ドロップされた URL は http:// または https:// で始まる必要があります", "error");
        }
      });
    }

    // Stop the browser from navigating away when a model is dropped outside
    // the dropzone.
    const swallow = (event) => {
      event.preventDefault();
    };
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);

    if (el.urlImportButton) {
      el.urlImportButton.addEventListener("click", () => {
        const url = el.urlInput ? el.urlInput.value.trim() : "";
        if (!url) {
          this.#status("URL を入力してください", "error");
          return;
        }
        // `/api/v1/mesh/import` refuses anything that is not http(s).
        if (!/^https?:\/\//i.test(url)) {
          this.#status("URL は http:// または https:// で始まる必要があります", "error");
          return;
        }
        this.importModel({ url, filename: this.#filenameFromUrl(url) })
          .catch((err) => this.#fail(err, "URL 読込"));
      });
    }
    if (el.urlInput) {
      el.urlInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          if (el.urlImportButton) {
            el.urlImportButton.click();
          }
        }
      });
    }
    if (el.refreshButton) {
      el.refreshButton.addEventListener("click", () => {
        this.refreshModelList().catch((err) => this.#fail(err, "モデル一覧"));
      });
    }
    if (el.applyButton) {
      el.applyButton.addEventListener("click", () => {
        if (!this.metrics) {
          this.#status("先にモデルを読み込んでください", "warn");
          return;
        }
        this.onMetrics(this.metrics);
        this.#status("算出物理量を機体設定に反映しました", "ok");
      });
    }
    if (el.formatSelect) {
      el.formatSelect.addEventListener("change", () => {
        const value = el.formatSelect.value;
        const list = STREAM_EXTENSIONS[value] || "";
        if (el.dropzoneHint) {
          el.dropzoneHint.textContent = list ? `許可: ${list}` : "任意のファイル";
        }
      });
    }

    this.refreshModelList().catch(() => {
      // A server that is not up yet must not break the panel.
    });
    return this;
  }

  // --------------------------------------------------------------- import

  /**
   * The single ingestion entry point for all three input paths.
   * @param {File|{url: string, filename?: string}} source
   * @returns {Promise<object>} the normalised metrics
   */
  async importModel(source) {
    if (!source) {
      throw new Error("取込ソースが指定されていません");
    }
    this._lastSource = source;
    this.#status("モデルを読み込み中…", "info");

    const options = this.#collectOptions();
    let metrics;
    if (typeof File !== "undefined" && source instanceof File) {
      this._lastFile = source;
      const form = new FormData();
      form.append("file", source, source.name);
      this.#appendOptions(form, options);
      metrics = await this.api.postForm("/api/v1/mesh/import", form);
    } else if (source instanceof Blob) {
      this._lastFile = source;
      const form = new FormData();
      form.append("file", source, options.filename || "model.bin");
      this.#appendOptions(form, options);
      metrics = await this.api.postForm("/api/v1/mesh/import", form);
    } else if (typeof source === "string") {
      metrics = await this.importModel({ url: source, filename: this.#filenameFromUrl(source) });
    } else if (source && typeof source.url === "string") {
      metrics = await this.api.post("/api/v1/mesh/import", {
        url: source.url,
        filename: source.filename || this.#filenameFromUrl(source.url),
        ...options,
      });
    } else {
      throw new Error("取込ソースの形式が不明です (File / Blob / {url})");
    }

    if (!metrics || typeof metrics !== "object") {
      throw new Error("サーバーがメトリクスを返しませんでした");
    }
    return this.#acceptMetrics(metrics);
  }

  /**
   * Store, render and publish a metrics payload.
   * @param {object} raw
   * @returns {Promise<object>}
   */
  async #acceptMetrics(raw) {
    const metrics = normaliseMetrics(raw);
    this.metrics = metrics;
    this.selectedModelId = metrics.model_id || this.selectedModelId;
    this.overrides = (metrics.overrides && typeof metrics.overrides === "object")
      ? { ...metrics.overrides }
      : {};

    this.#renderMetrics(metrics);
    this.#status(
      `${metrics.name || metrics.model_id || "model"} / ${FORMAT_LABELS[metrics.source_format] || metrics.source_format || "?"}`
      + ` / backend=${metrics.backend || "?"} / panels=${metrics.panel_count || 0}`
      + ` / 頂点=${metrics.vertex_count || 0}`,
      "ok",
    );
    this.onMetrics(metrics);
    await this.#resolveGeometry(metrics);

    if (this.selectedModelId) {
      this.#markSelected(this.selectedModelId);
    }
    this.refreshModelList().catch(() => {});
    return metrics;
  }

  /**
   * Make sure the 3D view receives geometry.
   *
   * Order of preference:
   *   1. vertex data already inside the import response
   *   2. ``GET /api/v1/mesh/metrics/<model_id>`` (in case only the summary
   *      endpoint carries the mesh)
   *   3. a client-side parse of the original file with the three.js loaders
   *   4. a scalar reconstruction built from the planform metrics
   *
   * @param {object} metrics
   * @returns {Promise<object|null>}
   */
  async #resolveGeometry(metrics) {
    let geometry = extractGeometry(metrics);
    if (!geometry && metrics.model_id) {
      try {
        const full = await this.api.get(`/api/v1/mesh/metrics/${encodeURIComponent(metrics.model_id)}`);
        geometry = extractGeometry(full);
        if (geometry) {
          this.#status(`${metrics.model_id}: メトリクス API からジオメトリを取得しました`, "ok");
        }
      } catch (err) {
        this.notify(`メトリクスの再取得に失敗しました: ${err.message}`, "warn");
      }
    }
    if (!geometry && this._lastFile) {
      geometry = await this.#parseLocally(this._lastFile);
      if (geometry) {
        this.#status(`${metrics.name || this._lastFile.name}: ブラウザ側でジオメトリを解析しました`, "ok");
      }
    }
    if (!geometry) {
      // Scalar-only payload: the viewport rebuilds a planform-consistent mesh.
      this.onGeometry({ positions: null, indices: null, metrics });
      return null;
    }
    geometry.metrics = metrics;
    this.onGeometry(geometry);
    return geometry;
  }

  /**
   * Parse a file in the browser with the matching three.js loader.  Returns
   * ``null`` when three.js is unreachable, so callers fall through to the
   * scalar reconstruction.
   * @param {Blob} file
   * @returns {Promise<{positions: Float32Array, indices: Uint16Array|Uint32Array|null}|null>}
   */
  async #parseLocally(file) {
    let THREE = null;
    try {
      THREE = await import("three");
    } catch (err) {
      return null;
    }
    const name = (file && file.name) || "";
    const lower = name.toLowerCase();
    const buffer = await file.arrayBuffer();

    const traverse = (root) => {
      let found = null;
      root.traverse((child) => {
        if (!found && child.isMesh && child.geometry && child.geometry.attributes.position) {
          found = child.geometry;
        }
      });
      return found;
    };

    const collect = (geometry) => {
      const position = geometry.attributes.position;
      const array = position.array instanceof Float32Array ? position.array : new Float32Array(position.array);
      let indices = null;
      if (geometry.index) {
        const src = geometry.index.array;
        indices = src.length > 65535 ? new Uint32Array(src) : new Uint16Array(src);
      }
      return { positions: array, indices };
    };

    try {
      let loader = null;
      if (lower.endsWith(".glb") || lower.endsWith(".gltf")) {
        const mod = await import("three/addons/loaders/GLTFLoader.js");
        loader = new mod.GLTFLoader();
        const gltf = await loader.parseAsync(buffer, "");
        const geometry = gltf.scene ? traverse(gltf.scene) : null;
        return geometry ? collect(geometry) : null;
      }
      if (lower.endsWith(".obj")) {
        const mod = await import("three/addons/loaders/OBJLoader.js");
        loader = new mod.OBJLoader();
        const group = loader.parse(new TextDecoder().decode(buffer));
        const geometry = traverse(group);
        return geometry ? collect(geometry) : null;
      }
      if (lower.endsWith(".stl")) {
        const mod = await import("three/addons/loaders/STLLoader.js");
        loader = new mod.STLLoader();
        const geometry = loader.parse(buffer);
        return collect(geometry);
      }
    } catch (err) {
      this.notify(`ブラウザ側のモデル解析に失敗しました: ${err.message}`, "warn");
      return null;
    }
    return null;
  }

  // --------------------------------------------------------------- options

  /** Read the import option fields. */
  #collectOptions() {
    const el = this.el;
    const gsm = el.gsmInput ? Number(el.gsmInput.value) : NaN;
    const options = {
      scale_hint: el.scaleHint ? el.scaleHint.value : "m",
      cg_mode: el.cgMode ? el.cgMode.value : "volume",
      paper_gsm: Number.isFinite(gsm) ? gsm : 100,
    };
    if (Object.keys(this.overrides).length) {
      options.overrides = { ...this.overrides };
    }
    if (el.formatSelect && el.formatSelect.value) {
      options.format = el.formatSelect.value;
    }
    return options;
  }

  /**
   * Append the option fields to a multipart body.
   *
   * Only `scale_hint`, `cg_mode` and `paper_gsm` are read from the form by
   * `POST /api/v1/mesh/import`; overrides travel on the dedicated
   * `POST /api/v1/mesh/overrides/<model_id>` route once a model exists, so
   * they are deliberately not attached here.
   *
   * @param {FormData} form
   * @param {object} options
   */
  #appendOptions(form, options) {
    form.append("scale_hint", options.scale_hint);
    form.append("cg_mode", options.cg_mode);
    form.append("paper_gsm", String(options.paper_gsm));
    if (options.format) {
      form.append("format", options.format);
    }
    if (options.filename) {
      form.append("filename", options.filename);
    }
  }

  // --------------------------------------------------------- model listing

  /** Refresh the cached model list from the server. */
  async refreshModelList() {
    const response = await this.api.get("/api/v1/mesh/models");
    const models = (response && Array.isArray(response.models)) ? response.models : [];
    this.#renderModelList(models, response && response.selected);
    return models;
  }

  /**
   * Render the model list.
   * @param {Array<object>} models
   * @param {string|null} selectedId
   */
  #renderModelList(models, selectedId) {
    const list = this.el.modelList;
    if (!list) {
      return;
    }
    list.textContent = "";
    const active = selectedId || this.selectedModelId;
    if (!models.length) {
      const item = document.createElement("li");
      item.className = "label-sm";
      item.textContent = "キャッシュ済みモデルはありません";
      list.appendChild(item);
      return;
    }
    for (const model of models) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "model-list-item";
      button.dataset.modelId = model.model_id || "";
      if (model.model_id && model.model_id === active) {
        button.classList.add("is-selected");
      }
      const name = document.createElement("span");
      name.textContent = model.name || model.model_id || "(unnamed)";
      const meta = document.createElement("span");
      meta.className = "model-id";
      const format = FORMAT_LABELS[model.source_format] || model.source_format || "?";
      meta.textContent = `${format} · ${(model.created_at || "").slice(0, 19)}`;
      button.appendChild(name);
      button.appendChild(meta);
      button.addEventListener("click", () => {
        this.selectModel(model.model_id).catch((err) => this.#fail(err, "モデル選択"));
      });
      item.appendChild(button);
      list.appendChild(item);
    }
  }

  /**
   * Select a cached model server-side and adopt its metrics.
   * @param {string} modelId
   * @returns {Promise<object>}
   */
  async selectModel(modelId) {
    if (!modelId) {
      throw new Error("model_id が指定されていません");
    }
    const metrics = await this.api.post("/api/v1/mesh/select", { model_id: modelId });
    const normalised = normaliseMetrics(metrics || {});
    normalised.model_id = normalised.model_id || modelId;
    return this.#acceptMetrics(normalised);
  }

  // --------------------------------------------------------- metrics table

  /** Draw the metrics table with per-metric override toggles. */
  #renderMetrics(metrics) {
    const body = this.el.metricBody;
    if (!body) {
      return;
    }
    body.textContent = "";
    const serverOverrides = (metrics.overrides && typeof metrics.overrides === "object") ? metrics.overrides : {};

    for (const row of METRIC_ROWS) {
      const autoValue = Number(metrics[row.key]);
      const overrideKey = row.key;
      const isOverridden = Object.prototype.hasOwnProperty.call(serverOverrides, overrideKey)
        || Object.prototype.hasOwnProperty.call(this.overrides, overrideKey);
      const display = isOverridden ? Number(this.overrides[overrideKey] ?? serverOverrides[overrideKey]) : autoValue;

      const tr = document.createElement("tr");

      const nameCell = document.createElement("td");
      nameCell.textContent = `${row.label} [${row.unit}]`;

      const valueCell = document.createElement("td");
      valueCell.className = "num";
      valueCell.textContent = Number.isFinite(display) ? display.toFixed(row.digits) : "—";

      const controlCell = document.createElement("td");

      const input = document.createElement("input");
      input.type = "number";
      input.className = "metric-input";
      input.step = String(Number(1e-6).toFixed(Math.max(0, row.digits)));
      input.min = String(Math.min(0, Number.isFinite(display) ? display : 0) - (row.key === "taper_ratio" ? 0 : 1));
      input.value = Number.isFinite(display) ? String(Number(display.toFixed(row.digits + 2))) : "";
      input.disabled = true;
      input.setAttribute("aria-label", `${row.label} 上書き値`);

      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = isOverridden;
      toggle.setAttribute("aria-label", `${row.label} 手動上書き`);
      toggle.style.marginRight = "4px";

      input.addEventListener("change", () => {
        const n = Number(input.value);
        if (!Number.isFinite(n)) {
          return;
        }
        this.overrides[overrideKey] = n;
        valueCell.textContent = n.toFixed(row.digits);
        this.#pushOverrides().catch((err) => this.#fail(err, "上書き保存"));
      });

      toggle.addEventListener("change", () => {
        input.disabled = !toggle.checked;
        if (toggle.checked) {
          const n = Number(input.value);
          this.overrides[overrideKey] = Number.isFinite(n) ? n : (Number.isFinite(autoValue) ? autoValue : 0);
          valueCell.textContent = this.overrides[overrideKey].toFixed(row.digits);
        } else {
          delete this.overrides[overrideKey];
          valueCell.textContent = Number.isFinite(autoValue) ? autoValue.toFixed(row.digits) : "—";
        }
        this.#pushOverrides().catch((err) => this.#fail(err, "上書き保存"));
      });

      controlCell.appendChild(toggle);
      controlCell.appendChild(input);

      tr.appendChild(nameCell);
      tr.appendChild(valueCell);
      tr.appendChild(controlCell);
      body.appendChild(tr);
    }

    // Inertia diagonal, read-only (it is derived from mass + geometry).
    const diag = metrics.inertia_diag || [];
    if (diag.some((v) => Number.isFinite(v))) {
      const tr = document.createElement("tr");
      const nameCell = document.createElement("td");
      nameCell.textContent = "慣性テンソル対角 [kg·m²]";
      const valueCell = document.createElement("td");
      valueCell.className = "num";
      valueCell.textContent = diag.map((v) => (Number.isFinite(v) ? v.toExponential(2) : "-")).join(" / ");
      const empty = document.createElement("td");
      tr.appendChild(nameCell);
      tr.appendChild(valueCell);
      tr.appendChild(empty);
      body.appendChild(tr);
    }
  }

  /**
   * Send the current override set to the server.
   *
   * `POST /api/v1/mesh/overrides/<model_id>` accepts either a bare mapping or
   * an `overrides` wrapper; the explicit wrapper is sent so the intent is
   * unambiguous.  The response is the recomputed `MeshMetrics`, which is fed
   * back through `#acceptMetrics` so the table, the airframe form and the 3D
   * view all reflect the corrected numbers.
   */
  async #pushOverrides() {
    if (!this.selectedModelId) {
      return null;
    }
    const metrics = await this.api.post(
      `/api/v1/mesh/overrides/${encodeURIComponent(this.selectedModelId)}`,
      { overrides: { ...this.overrides } },
    );
    const normalised = normaliseMetrics(metrics || {});
    normalised.model_id = normalised.model_id || this.selectedModelId;
    normalised.overrides = { ...this.overrides };
    await this.#acceptMetrics(normalised);
    this.#status(
      `${Object.keys(this.overrides).length} 件の上書きを保存しました`,
      "ok",
    );
    return metrics;
  }

  /** Highlight the active entry in the model list. */
  #markSelected(modelId) {
    const list = this.el.modelList;
    if (!list) {
      return;
    }
    for (const button of list.querySelectorAll(".model-list-item")) {
      button.classList.toggle("is-selected", button.dataset.modelId === modelId);
    }
  }

  // --------------------------------------------------------------- helpers

  /**
   * Derive a file name from a URL.
   * @param {string} url
   * @returns {string}
   */
  #filenameFromUrl(url) {
    const text = String(url || "");
    const clean = text.split(/[?#]/)[0];
    const parts = clean.split("/");
    const last = parts[parts.length - 1] || "model";
    return decodeURIComponent(last) || "model";
  }

  /**
   * Update the status line.
   * @param {string} text
   * @param {string} kind info|ok|warn|error
   */
  #status(text, kind) {
    const node = this.el.status;
    if (!node) {
      return;
    }
    node.textContent = text;
    node.classList.toggle("is-error", kind === "error");
    node.classList.toggle("is-ok", kind === "ok");
  }

  /**
   * Shared error path.
   * @param {Error} err
   * @param {string} context
   */
  #fail(err, context) {
    const message = err && err.message ? err.message : String(err);
    this.#status(`${context} 失敗: ${message}`, "error");
    this.notify(`${context}: ${message}`, "error");
  }
}

export default ModelImporter;
