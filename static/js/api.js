/* api.js — thin REST client for the /api/v1 surface. */

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function parse(response) {
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (error) {
      data = { raw: text };
    }
  }
  if (!response.ok) {
    const message =
      (data && (data.error || data.message)) ||
      `${response.status} ${response.statusText || "request failed"}`;
    throw new ApiError(message, response.status, data);
  }
  return data;
}

const DEFAULT_TIMEOUT_MS = 15000;

function createSignal(options) {
  if (options && options.signal) return options.signal;
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return AbortSignal.timeout(DEFAULT_TIMEOUT_MS);
  }
  const controller = new AbortController();
  setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  return controller.signal;
}

async function get(path, options = {}) {
  const init = {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal: createSignal(options),
    ...options,
  };
  const response = await fetch(`${BASE}${path}`, init);
  return parse(response);
}

async function post(path, body, options = {}) {
  const init = {
    method: "POST",
    headers: { Accept: "application/json" },
    signal: createSignal(options),
  };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined && body !== null) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  Object.assign(init, options);
  const response = await fetch(`${BASE}${path}`, init);
  return parse(response);
}

/** GET returning the raw response (used for CSV downloads). */
async function getText(path, options = {}) {
  const init = {
    cache: "no-store",
    signal: createSignal(options),
    ...options,
  };
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) await parse(response);
  return { text: await response.text(), type: response.headers.get("Content-Type") || "text/csv" };
}

export const api = {
  ApiError,

  /* meta */
  schemas: () => get("/meta/schemas"),
  health: () => get("/meta/health"),
  gpuInfo: () => get("/system/gpu"),

  /* configuration */
  snapshot: () => post("/environment/config", {}),
  pushConfig: (patch) => post("/environment/config", patch),
  atmosphere: (patch) => post("/environment/atmosphere", patch),
  currentAir: () => get("/environment/current"),

  /* aerodynamics */
  aero: (payload) => post("/aerodynamics/calculate", payload),
  polar: (payload) => post("/aerodynamics/polar", payload),

  /* wind + stress */
  wind: (payload) => post("/wind/field", payload),
  stress: (payload) => post("/stress/analysis", payload),

  /* flight */
  step: (payload) => post("/simulation/step", payload),
  reset: (payload) => post("/simulation/reset", payload ?? {}),
  history: () => get("/simulation/history"),
  frame: (index) => get(`/simulation/history/${index}`),
  batch: (cases, processes = 0) => post("/simulation/batch", { cases, processes }),
  ensemble: (payload) => post("/simulation/ensemble", payload),

  /* mesh */
  meshList: () => get("/mesh/models"),
  meshImportFile: (file, options) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (options && options.scale_hint) form.append("scale_hint", options.scale_hint);
    if (options && options.cg_mode) form.append("cg_mode", options.cg_mode);
    if (options && options.paper_gsm != null) form.append("paper_gsm", String(options.paper_gsm));
    return post("/mesh/import", form);
  },
  meshImportUrl: (url, options) =>
    post("/mesh/import", {
      url,
      scale_hint: (options && options.scale_hint) || "m",
      cg_mode: (options && options.cg_mode) || "volume",
      paper_gsm: (options && options.paper_gsm) ?? 100,
    }),
  meshSelect: (modelId) => post("/mesh/select", { model_id: modelId }),
  meshMetrics: (modelId) => get(`/mesh/metrics/${encodeURIComponent(modelId)}`),
  meshOverrides: (modelId, overrides) => post(`/mesh/overrides/${encodeURIComponent(modelId)}`, { overrides }),

  /* optimizer */
  optimizerRun: (payload) => post("/optimizer/run", payload),
  optimizerStatus: (jobId) => get(`/optimizer/status/${encodeURIComponent(jobId)}`),
  optimizerCancel: (jobId) => post(`/optimizer/cancel/${encodeURIComponent(jobId)}`),
  optimizerResult: (jobId) => get(`/optimizer/result/${encodeURIComponent(jobId)}`),
  optimizerCsv: (jobId) => getText(`/optimizer/result/${encodeURIComponent(jobId)}?format=csv`),
  jobs: () => get("/jobs"),

  /* data */
  exportData: (payload) => post("/data/export", payload),
  importBundle: (bundle) => post("/data/import", { bundle }),

  /* guinness records & throws */
  recordPresets: (category) => get(`/records/presets${category ? `?category=${encodeURIComponent(category)}` : ""}`),
  recordPreset: (key) => get(`/records/presets/${encodeURIComponent(key)}`),
  recordApply: (payload) => post("/records/apply", payload),
  recordThrowTechniques: () => get("/records/throw_techniques"),
};