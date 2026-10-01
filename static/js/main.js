/**
 * main.js -- application bootstrap for the 3D Paper Airplane CFD Simulator.
 *
 * Responsibilities
 *   - {@link ApiClient}: one `fetch` wrapper with JSON handling, timeouts and
 *     `{error: "..."}` extraction so every failure surfaces in the UI;
 *   - airframe / environment / launch form bindings (numeric input + range
 *     slider pairs, dual-unit temperature and pressure);
 *   - flight-status readouts and four Chart.js time-series;
 *   - the playback clock: while playing, `/api/v1/simulation/step` is polled
 *     (rate-limited to 60 requests/second by `TimeController`) and never while
 *     paused;
 *   - keyboard shortcuts: space = play/pause, arrows = step, R = reset.
 *
 * Robustness.  Three.js and Chart.js come from CDNs.  Chart.js degrades to a
 * readable placeholder and Three.js is loaded with a dynamic `import()` inside
 * a try/catch, so a machine without network access or WebGL still gets every
 * panel, every chart fallback and every API call working.  `window.__vizState`
 * is published early so the app stays inspectable without a GPU.
 */

/* ==========================================================================
 * API client
 * ========================================================================== */

/** Error carrying the HTTP status and the decoded payload. */
export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {number} status
   * @param {*} payload
   */
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export class ApiClient {
  /**
   * @param {string} [baseUrl] prefix for every endpoint (empty = same origin)
   * @param {number} [timeoutMs]
   */
  constructor(baseUrl = "", timeoutMs = 30000) {
    this.baseUrl = String(baseUrl).replace(/\/+$/, "");
    this.timeoutMs = timeoutMs;
    this.requestCount = 0;
  }

  /**
   * Build a fully qualified URL.
   * @param {string} path
   * @param {object} [query]
   * @returns {string}
   */
  url(path, query) {
    const suffix = String(path || "");
    const full = suffix.startsWith("http") ? suffix : `${this.baseUrl}${suffix}`;
    if (!query) {
      return full;
    }
    const parts = [];
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) {
        continue;
      }
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
    }
    return parts.length ? `${full}?${parts.join("&")}` : full;
  }

  /**
   * Issue a request and decode the JSON body.
   * @param {string} method
   * @param {string} path
   * @param {object} [options]
   * @param {*} [options.json] JSON request body
   * @param {FormData} [options.form] multipart request body
   * @param {object} [options.query]
   * @returns {Promise<*>}
   */
  async request(method, path, options = {}) {
    const url = this.url(path, options.query);
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timer = controller ? window.setTimeout(() => controller.abort(), this.timeoutMs) : null;
    const init = { method, headers: { Accept: "application/json" } };
    if (options.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.json);
    } else if (options.form) {
      // Let the browser set the multipart boundary.
      init.body = options.form;
    }
    if (controller) {
      init.signal = controller.signal;
    }
    this.requestCount += 1;
    let response = null;
    try {
      response = await fetch(url, init);
    } catch (err) {
      const aborted = err && (err.name === "AbortError" || err.code === 20);
      throw new ApiError(
        aborted ? `${method} ${path} が ${this.timeoutMs} ms でタイムアウトしました` : `${method} ${path} への接続に失敗しました: ${err && err.message ? err.message : String(err)}`,
        0,
        null,
      );
    } finally {
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    }

    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (err) {
        payload = text;
      }
    }
    if (!response.ok) {
      const message = (payload && typeof payload === "object" && (payload.error || payload.message || payload.detail))
        ? String(payload.error || payload.message || payload.detail)
        : `${method} ${path} が HTTP ${response.status} ${response.statusText || ""}`.trim();
      throw new ApiError(message, response.status, payload);
    }
    return payload;
  }

  /** @param {string} path @param {*} body @returns {Promise<*>} */
  post(path, body) {
    return this.request("POST", path, { json: body === undefined ? {} : body });
  }

  /** @param {string} path @param {object} [query] @returns {Promise<*>} */
  get(path, query) {
    return this.request("GET", path, { query });
  }

  /** @param {string} path @param {FormData} form @returns {Promise<*>} */
  postForm(path, form) {
    return this.request("POST", path, { form });
  }
}

/* ==========================================================================
 * Small DOM helpers
 * ========================================================================== */

/** @param {string} id @returns {HTMLElement|null} */
function el(id) {
  return document.getElementById(id);
}

/** Clamp a number into a range, tolerating NaN. */
function clampNumber(value, low, high, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return fallback;
  }
  if (low !== undefined && low !== null && n < low) {
    return low;
  }
  if (high !== undefined && high !== null && n > high) {
    return high;
  }
  return n;
}

/** Fixed-decimal formatter that never prints NaN. */
function fmt(value, digits) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

/* ==========================================================================
 * Form field catalogues
 * ========================================================================== */

/** Airframe controls: id stem `af`, bound to `aircraft.<key>`. */
const AIRFRAME_FIELDS = [
  { key: "mass_kg", digits: 4 },
  { key: "paper_gsm", digits: 0 },
  { key: "paper_stiffness", digits: 2 },
  { key: "wingspan_m", digits: 3 },
  { key: "wing_area_m2", digits: 4 },
  { key: "taper_ratio", digits: 2 },
  { key: "dihedral_deg", digits: 1 },
  { key: "fold_leading_deg", digits: 1 },
  { key: "fold_trailing_deg", digits: 1 },
  { key: "fold_tip_deg", digits: 1 },
  { key: "fold_center_deg", digits: 1 },
  { key: "elevon_deg", digits: 1 },
];

/** Launch controls: id stem `launch`. */
const LAUNCH_FIELDS = [
  { key: "launch_speed", digits: 1 },
  { key: "launch_angle_deg", digits: 1 },
  { key: "launch_roll_deg", digits: 1 },
];

/**
 * Environment controls: id stem `env`, bound to `environment.<key>`.
 * `scale` converts between the displayed unit and the config unit.
 */
const ENVIRONMENT_FIELDS = [
  { id: "humidity_pct", key: "humidity_rel", scale: 100, digits: 0 },
  { id: "wind_speed", key: "wind_speed", digits: 1 },
  { id: "wind_direction_deg", key: "wind_direction_deg", digits: 0 },
  { id: "gust_intensity", key: "gust_intensity", digits: 2 },
  { id: "thermal_strength", key: "thermal_strength", digits: 1 },
  { id: "gravity", key: "gravity", digits: 2 },
  { id: "ground_friction", key: "ground_friction", digits: 2 },
  { id: "drag_scale", key: "drag_scale", digits: 2 },
  { id: "lift_scale", key: "lift_scale", digits: 2 },
  { id: "wind_tunnel_speed", key: "wind_tunnel_speed", digits: 1 },
];

/** Toggle controls: checkbox id -> `environment.<key>`. */
const ENVIRONMENT_TOGGLES = [
  { id: "env-toggle-ground_effect_enabled", key: "ground_effect_enabled" },
  { id: "env-toggle-compressibility_enabled", key: "compressibility_enabled" },
  { id: "env-toggle-wind_tunnel_enabled", key: "wind_tunnel_enabled" },
];

/** Defaults matching `config_schema.AircraftConfig` / `LaunchConfig`. */
const DEFAULT_AIRCRAFT = {
  name: "Paper Glider",
  shape_flag: 1,
  mass_kg: 0.005,
  paper_gsm: 100,
  paper_stiffness: 0.35,
  sheet_area_m2: 0.02,
  wing_area_m2: 0.015,
  wingspan_m: 0.2,
  taper_ratio: 0.7,
  dihedral_deg: 5,
  fold_leading_deg: 0,
  fold_trailing_deg: 15,
  fold_tip_deg: 0,
  fold_center_deg: 0,
  elevon_deg: 8,
  elevon_chord_ratio: 0.22,
  cL0: 0.1,
  cd0: 0.02,
  oswald_e: 0.75,
  cl_max: 0.9,
  stall_alpha_deg: 14,
  stall_sharpness: 1.4,
  post_stall_slope: -0.35,
  re_cd0_exponent: 0.15,
  cm0: -0.06,
  cm_alpha: -0.9,
  cm_q: -12,
  cy_beta: -0.25,
  cl_beta: -0.03,
  n_beta: 0.08,
  cl_roll_rate: 0.1,
  cm_roll_rate: -0.02,
  n_roll_rate: -0.08,
  cl_pitch_rate: -0.06,
  cm_pitch_rate: 0.05,
  n_pitch_rate: -0.12,
  cl_yaw_rate: 0.08,
  cm_yaw_rate: 0,
  n_yaw_rate: 0.15,
  static_margin: 0.12,
  cg_offset_x: 0,
};

/** Defaults matching `config_schema.LaunchConfig`. */
const DEFAULT_LAUNCH = {
  launch_speed: 12,
  launch_angle_deg: 12,
  launch_roll_deg: 0,
  launch_yaw_deg: 0,
  launch_height_m: 1.2,
};

/** Defaults matching `config_schema.EnvironmentConfig`. */
const DEFAULT_ENVIRONMENT = {
  temperature_K: 288.15,
  pressure_Pa: 101325,
  humidity_rel: 0.5,
  isa_mode: true,
  wind_speed: 0,
  wind_direction_deg: 0,
  gust_intensity: 0.2,
  thermal_strength: 0.5,
  gravity: 9.80665,
  ground_friction: 0.4,
  drag_scale: 1,
  lift_scale: 1,
  ground_effect_enabled: true,
  compressibility_enabled: true,
  wind_tunnel_enabled: false,
  wind_tunnel_speed: 12,
};

/** Client-side frame history cap; older frames force the replay path. */
const MAX_CLIENT_FRAMES = 3000;

/** Replay of an evicted frame is capped so a long scrub cannot hang the page. */
const MAX_REPLAY_STEPS = 1500;

/** Chart history length (scrolling window). */
const CHART_WINDOW = 400;

/** Stress heat-map refresh cadence while playing. */
const STRESS_INTERVAL_MS = 200;

/** Wind-field fetch interval (also enforced inside `WindGPGPU`). */
const WIND_INTERVAL_MS = 1000;

/** Debounce applied to environment slider drags (number inputs are immediate). */
const ENV_DEBOUNCE_MS = 120;

/* ==========================================================================
 * Chart wrapper
 * ========================================================================== */

/** Is Chart.js usable? */
function chartsAvailable() {
  return typeof window !== "undefined" && typeof window.Chart === "function";
}

/**
 * One scrolling line chart with a CDN-failure fallback.
 */
class TimeSeriesChart {
  /**
   * @param {string} canvasId
   * @param {string} fallbackId
   * @param {{color: string, unit: string, digits: number}} options
   */
  constructor(canvasId, fallbackId, options) {
    this.canvas = el(canvasId);
    this.fallback = el(fallbackId);
    this.options = options;
    this.chart = null;
    this.maxPoints = CHART_WINDOW;
    this.points = [];
    this.available = chartsAvailable();

    if (!this.available) {
      if (this.fallback) {
        this.fallback.hidden = false;
        this.fallback.textContent = `Chart.js CDN に接続できないため ${options.label} のグラフを描画できません。`;
      }
      if (this.canvas) {
        this.canvas.hidden = true;
      }
      return;
    }
    if (this.fallback) {
      this.fallback.hidden = true;
    }
    if (!this.canvas) {
      this.available = false;
      return;
    }
    const context = this.canvas.getContext("2d");
    if (!context) {
      this.available = false;
      if (this.fallback) {
        this.fallback.hidden = false;
      }
      return;
    }
    this.chart = new window.Chart(context, {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: options.label,
          data: [],
          borderColor: options.color,
          backgroundColor: options.color,
          borderWidth: 1.5,
          fill: false,
          pointRadius: 0,
          tension: 0.15,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        normalized: true,
        parsing: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => `${options.label}: ${Number(item.parsed.y).toFixed(options.digits)} ${options.unit}`,
            },
          },
        },
        scales: {
          x: {
            type: "linear",
            ticks: { display: false, maxTicksLimit: 4 },
            grid: { color: "rgba(95, 99, 104, 0.14)" },
          },
          y: {
            type: "linear",
            ticks: { maxTicksLimit: 4, font: { size: 9 } },
            grid: { color: "rgba(95, 99, 104, 0.14)" },
          },
        },
        elements: { line: { borderJoinStyle: "round" } },
      },
    });
  }

  /**
   * Append a sample and drop the oldest once the window is full.
   * @param {number} t seconds
   * @param {number} value
   */
  push(t, value) {
    const v = Number(value);
    this.points.push([Number(t) || 0, Number.isFinite(v) ? v : 0]);
    if (this.points.length > this.maxPoints) {
      this.points.splice(0, this.points.length - this.maxPoints);
    }
    if (!this.available || !this.chart) {
      return;
    }
    const labels = this.points.map((p) => p[0]);
    this.chart.data.labels = labels;
    this.chart.data.datasets[0].data = this.points.map((p) => ({ x: p[0], y: p[1] }));
    this.chart.update("none");
  }

  /** Drop every sample. */
  clear() {
    this.points = [];
    if (this.available && this.chart) {
      this.chart.data.labels = [];
      this.chart.data.datasets[0].data = [];
      this.chart.update("none");
    }
  }

  /** @returns {number} */
  get sampleCount() {
    return this.points.length;
  }
}

/* ==========================================================================
 * Application
 * ========================================================================== */

class SimulatorApp {
  constructor() {
    this.api = new ApiClient("");
    this.state = {
      aircraft: { ...DEFAULT_AIRCRAFT },
      environment: { ...DEFAULT_ENVIRONMENT },
      launch: { ...DEFAULT_LAUNCH },
    };
    this.frames = [];
    this.frameBase = 0;
    this.lastStep = null;
    this.stepBusy = false;
    this.activeRun = null;
    this.stopRequested = false;
    this.replayTarget = null;
    this.stress = null;
    this.lastStressMs = -Infinity;
    this.lastStepMs = -Infinity;
    this.environmentDirty = false;
    this.viz = null;
    this.wind = null;
    this.derived = {};
    this.simulationBackend = "";
    this.fatal = null;
  }

  // ---------------------------------------------------------------- toasts

  /**
   * Push a transient message onto the toast stack.
   * @param {string} message
   * @param {"info"|"ok"|"warn"|"error"} [kind]
   * @param {number} [timeoutMs]
   */
  notify(message, kind = "info", timeoutMs = 5200) {
    const stack = el("toast-stack");
    if (!stack) {
      return;
    }
    const toast = document.createElement("div");
    toast.className = `toast toast-${kind}`;
    const text = document.createElement("span");
    text.textContent = String(message);
    const close = document.createElement("button");
    close.className = "toast-close";
    close.type = "button";
    close.setAttribute("aria-label", "閉じる");
    close.textContent = "×";
    close.addEventListener("click", () => toast.remove());
    toast.appendChild(text);
    toast.appendChild(close);
    stack.appendChild(toast);
    window.setTimeout(() => {
      if (toast.parentNode) {
        toast.remove();
      }
    }, timeoutMs);
  }

  /** @param {Error} err @param {string} context */
  reportError(err, context) {
    const message = err && err.message ? err.message : String(err);
    this.notify(`${context}: ${message}`, "error", 7000);
    // eslint-disable-next-line no-console
    console.error(`[${context}]`, err);
  }

  // ----------------------------------------------------------- chrome bits

  /** Section collapse + responsive drawer toggles. */
  bindChrome() {
    for (const button of document.querySelectorAll("[data-section-toggle]")) {
      button.addEventListener("click", () => {
        const section = button.closest(".section");
        if (!section) {
          return;
        }
        const collapsed = section.classList.toggle("is-collapsed");
        button.setAttribute("aria-expanded", collapsed ? "false" : "true");
      });
    }
    const left = el("left-panel");
    const right = el("right-panel");
    const toggleLeft = el("btn-toggle-left");
    const toggleRight = el("btn-toggle-right");
    if (toggleLeft && left) {
      toggleLeft.addEventListener("click", () => left.classList.toggle("is-open"));
    }
    if (toggleRight && right) {
      toggleRight.addEventListener("click", () => right.classList.toggle("is-open"));
    }
  }

  // ------------------------------------------------------- airframe form

  /**
   * Bind a numeric-input + range-slider pair.
   * @param {object} spec
   * @param {string} spec.stem `af` | `launch` | `env`
   * @param {string} spec.id field id used in the HTML (`<stem>-<id>`)
   * @param {(value:number, immediate:boolean) => void} spec.onChange
   * @param {number} [spec.scale] multiply the displayed value to get the config value
   * @param {number} [spec.digits]
   * @returns {{read:()=>number, write:(v:number)=>void}|null}
   */
  bindPair(spec) {
    const range = el(`${spec.stem}-range-${spec.id}`);
    const num = el(`${spec.stem}-num-${spec.id}`);
    const val = el(`${spec.stem}-val-${spec.id}`);
    if (!range || !num) {
      this.notify(`フォーム要素が見つかりません: ${spec.stem}-${spec.id}`, "error");
      return null;
    }
    const low = Number(range.min);
    const high = Number(range.max);
    const write = (raw) => {
      const value = clampNumber(raw, low, high, Number(range.value) || 0);
      range.value = String(value);
      num.value = String(Number(value.toFixed(6)));
      if (val) {
        val.textContent = spec.digits === undefined
          ? String(value)
          : `${value.toFixed(spec.digits)}${spec.unit ? ` ${spec.unit}` : ""}`;
      }
      return value;
    };
    // Slider drags are debounced by the caller; number entry is immediate.
    range.addEventListener("input", () => {
      const value = write(range.value);
      spec.onChange(value, false);
    });
    num.addEventListener("change", () => {
      const value = write(num.value);
      spec.onChange(value, true);
    });
    num.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        num.blur();
      }
    });
    return { read: () => Number(num.value), write };
  }

  /** Wire every airframe and launch control. */
  bindAirframeForm() {
    this.airframeControls = {};
    for (const field of AIRFRAME_FIELDS) {
      const control = this.bindPair({
        stem: "af",
        id: field.key,
        digits: field.digits,
        onChange: (value) => {
          this.state.aircraft[field.key] = value;
          if (this.viz) {
            this.viz.applyParameters(this.state.aircraft);
          }
          this.environmentDirty = true;
          this.publishState();
        },
      });
      if (control) {
        this.airframeControls[field.key] = control;
      }
    }
    this.launchControls = {};
    for (const field of LAUNCH_FIELDS) {
      const control = this.bindPair({
        stem: "launch",
        id: field.key,
        digits: field.digits,
        onChange: (value) => {
          this.state.launch[field.key] = value;
          this.environmentDirty = true;
          this.publishState();
        },
      });
      if (control) {
        this.launchControls[field.key] = control;
      }
    }

    const apply = el("btn-apply-airframe");
    if (apply) {
      apply.addEventListener("click", () => {
        this.resetSimulation("airframe applied").catch((err) => this.reportError(err, "機体設定の適用"));
      });
    }
    this.writeAirframeForm();
    this.writeLaunchForm();
  }

  /** Push `state.aircraft` into the form. */
  writeAirframeForm() {
    for (const field of AIRFRAME_FIELDS) {
      const control = this.airframeControls && this.airframeControls[field.key];
      if (!control) {
        continue;
      }
      const value = Number(this.state.aircraft[field.key]);
      if (Number.isFinite(value)) {
        control.write(value);
      }
    }
    if (this.viz) {
      this.viz.applyParameters(this.state.aircraft);
    }
  }

  /** Push `state.launch` into the form. */
  writeLaunchForm() {
    for (const field of LAUNCH_FIELDS) {
      const control = this.launchControls && this.launchControls[field.key];
      if (!control) {
        continue;
      }
      const value = Number(this.state.launch[field.key]);
      if (Number.isFinite(value)) {
        control.write(value);
      }
    }
  }

  // ------------------------------------------------------ environment form

  /**
   * Wire the environment panel.
   *
   * Every change is pushed to ``POST /api/v1/environment/atmosphere``
   * immediately (slider drags debounced by {@link ENV_DEBOUNCE_MS}, number
   * entries and toggles sent at once) and the derived density / viscosity /
   * speed-of-sound / Reynolds / Mach plus the server `backend` are echoed back
   * into the panel -- this is requirement A of prompt.md section 2.1.
   */
  bindEnvironmentForm() {
    this.environmentControls = {};
    this.pendingEnvironment = {};
    this.environmentTimer = null;

    const queue = (key, value, immediate) => {
      this.pendingEnvironment[key] = value;
      this.state.environment[key] = value;
      if (immediate) {
        this.flushEnvironment();
        return;
      }
      if (this.environmentTimer !== null) {
        window.clearTimeout(this.environmentTimer);
      }
      this.environmentTimer = window.setTimeout(() => this.flushEnvironment(), ENV_DEBOUNCE_MS);
    };

    // --- temperature: degC <-> K (both directions stay in sync) ------------
    const tempC = this.bindPair({
      stem: "env", id: "temp_c", digits: 1, unit: "°C",
      onChange: (value, immediate) => {
        const kelvin = value + 273.15;
        const control = this.environmentControls.temp_k;
        if (control) {
          control.write(kelvin);
        }
        queue("temperature_K", kelvin, immediate);
      },
    });
    const tempK = this.bindPair({
      stem: "env", id: "temp_k", digits: 2, unit: "K",
      onChange: (value, immediate) => {
        const celsius = value - 273.15;
        const control = this.environmentControls.temp_c;
        if (control) {
          control.write(celsius);
        }
        queue("temperature_K", value, immediate);
      },
    });
    if (tempC) {
      this.environmentControls.temp_c = tempC;
    }
    if (tempK) {
      this.environmentControls.temp_k = tempK;
    }

    // --- pressure: hPa <-> Pa ---------------------------------------------
    const pressHpa = this.bindPair({
      stem: "env", id: "press_hpa", digits: 1, unit: "hPa",
      onChange: (value, immediate) => {
        const pascals = value * 100;
        const control = this.environmentControls.press_pa;
        if (control) {
          control.write(pascals);
        }
        queue("pressure_Pa", pascals, immediate);
      },
    });
    const pressPa = this.bindPair({
      stem: "env", id: "press_pa", digits: 0, unit: "Pa",
      onChange: (value, immediate) => {
        const hpa = value / 100;
        const control = this.environmentControls.press_hpa;
        if (control) {
          control.write(hpa);
        }
        queue("pressure_Pa", value, immediate);
      },
    });
    if (pressHpa) {
      this.environmentControls.press_hpa = pressHpa;
    }
    if (pressPa) {
      this.environmentControls.press_pa = pressPa;
    }

    for (const field of ENVIRONMENT_FIELDS) {
      const control = this.bindPair({
        stem: "env",
        id: field.id,
        digits: field.digits,
        onChange: (value, immediate) => {
          queue(field.key, field.scale ? value / field.scale : value, immediate);
        },
      });
      if (control) {
        this.environmentControls[field.id] = control;
      }
    }

    for (const toggle of ENVIRONMENT_TOGGLES) {
      const node = el(toggle.id);
      if (!node) {
        this.notify(`トグルが見つかりません: ${toggle.id}`, "error");
        continue;
      }
      node.addEventListener("change", () => {
        queue(toggle.key, node.checked, true);
      });
    }

    const headerToggle = el("wind-tunnel-toggle");
    if (headerToggle) {
      headerToggle.addEventListener("change", () => {
        const panelToggle = el("env-toggle-wind_tunnel_enabled");
        if (panelToggle && panelToggle.checked !== headerToggle.checked) {
          panelToggle.checked = headerToggle.checked;
        }
        queue("wind_tunnel_enabled", headerToggle.checked, true);
      });
    }

    const refresh = el("btn-refresh-environment");
    if (refresh) {
      refresh.addEventListener("click", () => {
        this.loadEnvironment().catch((err) => this.reportError(err, "環境取得"));
      });
    }

    this.writeEnvironmentForm();
  }

  /** Push `state.environment` into the form. */
  writeEnvironmentForm() {
    const env = this.state.environment;
    const set = (id, value) => {
      const control = this.environmentControls && this.environmentControls[id];
      if (control && Number.isFinite(Number(value))) {
        control.write(Number(value));
      }
    };
    set("temp_c", Number(env.temperature_K) - 273.15);
    set("temp_k", Number(env.temperature_K));
    set("press_hpa", Number(env.pressure_Pa) / 100);
    set("press_pa", Number(env.pressure_Pa));
    for (const field of ENVIRONMENT_FIELDS) {
      const value = Number(env[field.key]);
      if (field.scale) {
        set(field.id, value * field.scale);
      } else {
        set(field.id, value);
      }
    }
    for (const toggle of ENVIRONMENT_TOGGLES) {
      const node = el(toggle.id);
      if (node) {
        node.checked = Boolean(env[toggle.key]);
      }
    }
    const headerToggle = el("wind-tunnel-toggle");
    if (headerToggle) {
      headerToggle.checked = Boolean(env.wind_tunnel_enabled);
    }
  }

  /** Send the queued partial environment update. */
  flushEnvironment() {
    if (this.environmentTimer !== null) {
      window.clearTimeout(this.environmentTimer);
      this.environmentTimer = null;
    }
    const patch = this.pendingEnvironment;
    if (!patch || Object.keys(patch).length === 0) {
      return;
    }
    this.pendingEnvironment = {};
    this.api.post("/api/v1/environment/atmosphere", patch)
      .then((response) => this.applyEnvironmentResponse(response, false))
      .catch((err) => this.reportError(err, "環境パラメータ更新"));
  }

  /**
   * Merge an environment response into the local state and echo the derived
   * values back into the panel.
   *
   * `/api/v1/environment/atmosphere` returns the atmospheric state flattened
   * at the top level *and* the fully merged environment under `environment`;
   * the nested object is authoritative (it carries every field, including the
   * ones the atmosphere evaluator does not echo), so it is merged last.
   *
   * @param {object} response
   * @param {boolean} authoritative true when the server is the source of truth
   */
  applyEnvironmentResponse(response, authoritative) {
    if (!response || typeof response !== "object") {
      return;
    }
    const envKeys = Object.keys(DEFAULT_ENVIRONMENT);
    const flatten = (source) => {
      for (const key of envKeys) {
        if (Number.isFinite(Number(source[key]))) {
          this.state.environment[key] = source[key];
        }
      }
    };
    flatten(response);
    if (response.environment && typeof response.environment === "object") {
      flatten(response.environment);
    }
    this.derived = {
      density: response.density_kgm3,
      viscosity: response.dynamic_viscosity_Pa_s,
      kinematic: response.kinematic_viscosity_m2s,
      sound: response.sound_speed_mps,
      reynolds: response.reynolds,
      mach: response.mach_reference,
      backend: response.backend,
      isaMode: response.isa_mode !== undefined
        ? response.isa_mode
        : this.state.environment.isa_mode,
    };
    if (authoritative) {
      this.writeEnvironmentForm();
    }
    this.renderDerived();
    this.publishState();
  }

  /** Render the derived atmosphere readouts. */
  renderDerived() {
    const set = (id, value) => {
      const node = el(id);
      if (node) {
        node.textContent = value;
      }
    };
    const d = this.derived;
    set("derived-density", fmt(d.density, 5) + " kg/m³");
    set("derived-viscosity", d.viscosity ? `${Number(d.viscosity).toExponential(4)} Pa·s` : "—");
    set("derived-kinematic", d.kinematic ? `${Number(d.kinematic).toExponential(4)} m²/s` : "—");
    set("derived-sound", fmt(d.sound, 2) + " m/s");
    set("derived-reynolds", d.reynolds ? Number(d.reynolds).toExponential(3) : "—");
    set("derived-mach", d.mach !== undefined && d.mach !== null ? Number(d.mach).toFixed(5) : "—");
    const chip = el("env-backend-chip");
    if (chip) {
      chip.textContent = `backend: ${d.backend || "—"}`;
    }
  }

  /** `GET /api/v1/environment/current` and repopulate the form. */
  async loadEnvironment() {
    const response = await this.api.get("/api/v1/environment/current");
    this.applyEnvironmentResponse(response, true);
    return response;
  }

  // ------------------------------------------------------------- readouts

  /** Update the flight-status overlay from a step response. */
  renderStatus(step) {
    if (!step) {
      return;
    }
    const set = (id, value, unit) => {
      const node = el(id);
      if (!node) {
        return;
      }
      node.textContent = "";
      const text = document.createElement("span");
      text.textContent = value;
      node.appendChild(text);
      if (unit) {
        const small = document.createElement("small");
        small.textContent = unit;
        node.appendChild(small);
      }
    };
    set("stat-altitude", fmt(step.altitude_m, 2), "m");
    set("stat-speed", fmt(step.speed_mps, 2), "m/s");
    set("stat-alpha", fmt(step.alpha_deg, 2), "°");
    set("stat-beta", fmt(step.beta_deg, 2), "°");
    set("stat-roll", fmt(step.roll_deg, 1), "°");
    set("stat-pitch", fmt(step.pitch_deg, 1), "°");
    set("stat-yaw", fmt(step.yaw_deg, 1), "°");
    set("stat-ld", fmt(step.ld_ratio, 2));
    set("stat-gload", fmt(step.g_load, 2), "g");
    set("stat-stress", step.max_stress_pa ? Number(step.max_stress_pa).toFixed(2) : "—", "Pa");
    set("stat-stress-index", fmt(step.max_stress_index, 3));
    set("stat-dynq", fmt(step.dynamic_pressure_pa, 2), "Pa");

    const position = el("stat-position");
    if (position) {
      const p = step.position_m || [];
      position.textContent = Array.isArray(p) && p.length >= 3
        ? `${Number(p[0]).toFixed(2)} / ${Number(p[1]).toFixed(2)} / ${Number(p[2]).toFixed(2)}`
        : "—";
    }
    const reMach = el("stat-re-mach");
    if (reMach) {
      reMach.textContent = `Re ${step.reynolds ? Number(step.reynolds).toExponential(2) : "—"} / M ${step.mach !== undefined ? Number(step.mach).toFixed(4) : "—"}`;
    }
    const backendChip = el("sim-backend-chip");
    if (backendChip) {
      this.simulationBackend = step.backend || this.simulationBackend;
      backendChip.textContent = `backend: ${this.simulationBackend || "—"}`;
    }
    const substepsChip = el("sim-substeps-chip");
    if (substepsChip) {
      substepsChip.textContent = `substeps: ${step.substeps ?? "—"} · dt ${Number(step.dt || 0).toExponential(2)} s`;
    }
    this.renderStatusPill(step);
    this.renderDock(step);
  }

  /**
   * Colour the status pill from the stress `status` field
   * (ok -> green, high_load -> amber, failed -> red).
   * @param {object} step
   */
  renderStatusPill(step) {
    const pill = el("status-pill");
    if (!pill) {
      return;
    }
    let status = "ok";
    let text = "正常";
    if (this.stress && typeof this.stress.status === "string") {
      status = this.stress.status;
    }
    if (step.crashed || step.diverged) {
      status = "failed";
      text = step.diverged ? "発散" : "墜落";
    } else if (step.landed) {
      text = "着地";
    } else if (step.stalled) {
      status = status === "failed" ? "failed" : "high_load";
      text = "失速";
    } else if (status === "high_load") {
      text = "高負荷";
    } else if (status === "failed") {
      text = "破断";
    }
    if (status !== "ok" && status !== "high_load" && status !== "failed") {
      status = "ok";
    }
    pill.className = `status-pill status-${status}`;
    pill.textContent = text;
  }

  /**
   * Update the bottom dock labels.
   * @param {object} step
   */
  renderDock(step) {
    const time = el("dock-time");
    if (time) {
      time.textContent = `t = ${Number(step.time_s || 0).toFixed(3)} s`;
    }
    this.renderTimelineLabels();
  }

  /** Update the frame counter and slider bounds. */
  renderTimelineLabels() {
    const controller = this.timeController;
    if (!controller) {
      return;
    }
    const total = controller.frameCount;
    const current = controller.currentFrame;
    const slider = el("timeline-range");
    if (slider) {
      slider.max = String(Math.max(0, total - 1));
      slider.value = String(current);
      slider.disabled = total <= 1;
    }
    const frame = el("dock-frame");
    if (frame) {
      frame.textContent = `frame ${current} / ${Math.max(0, total - 1)}`
        + (controller.retainedFrom > 0 ? ` (≥${controller.retainedFrom})` : "");
    }
  }

  // --------------------------------------------------------------- charts

  /** Create the four line charts (or their fallbacks). */
  bindCharts() {
    this.charts = {
      altitude: new TimeSeriesChart("chart-altitude", "chart-altitude-fallback", {
        label: "altitude", unit: "m", digits: 2, color: "#1A73E8",
      }),
      speed: new TimeSeriesChart("chart-speed", "chart-speed-fallback", {
        label: "speed", unit: "m/s", digits: 2, color: "#34A853",
      }),
      alpha: new TimeSeriesChart("chart-alpha", "chart-alpha-fallback", {
        label: "alpha", unit: "deg", digits: 2, color: "#FBBC04",
      }),
      stress: new TimeSeriesChart("chart-stress", "chart-stress-fallback", {
        label: "max stress", unit: "Pa", digits: 1, color: "#EA4335",
      }),
    };
    const clear = el("btn-clear-charts");
    if (clear) {
      clear.addEventListener("click", () => {
        for (const chart of Object.values(this.charts)) {
          chart.clear();
        }
        this.updateSampleCount();
      });
    }
    if (!chartsAvailable()) {
      this.notify("Chart.js CDN に到達できないため、グラフ領域にフォールバックメッセージを表示します", "warn");
    }
  }

  /** Append one step to every chart. */
  pushChartSample(step) {
    const t = Number(step.time_s) || 0;
    this.charts.altitude.push(t, step.altitude_m);
    this.charts.speed.push(t, step.speed_mps);
    this.charts.alpha.push(t, step.alpha_deg);
    this.charts.stress.push(t, step.max_stress_pa !== undefined ? step.max_stress_pa : step.max_stress_index);
    this.updateSampleCount();
  }

  /** Refresh the "N samples" label. */
  updateSampleCount() {
    const node = el("chart-sample-count");
    if (node && this.charts) {
      node.textContent = `${this.charts.altitude.sampleCount} samples`;
    }
  }

  // ------------------------------------------------------------ playback

  /** Wire the time-control dock and the keyboard shortcuts. */
  bindTimeDock() {
    const { TimeController } = this.modules;
    this.timeController = new TimeController({ frameCount: 0, speed: 1 });
    this.timeController.onFrame((directive) => this.handleDirective(directive));

    const play = el("btn-play");
    const pause = el("btn-pause");
    if (play) {
      play.addEventListener("click", () => this.timeController.play());
    }
    if (pause) {
      pause.addEventListener("click", () => this.timeController.pause());
    }
    const back = el("btn-step-back");
    if (back) {
      back.addEventListener("click", () => this.timeController.stepBackward(1));
    }
    const forward = el("btn-step-fwd");
    if (forward) {
      forward.addEventListener("click", () => this.timeController.stepForward(1));
    }
    const reset = el("btn-reset");
    if (reset) {
      reset.addEventListener("click", () => {
        this.resetSimulation("reset button").catch((err) => this.reportError(err, "リセット"));
      });
    }

    for (const button of document.querySelectorAll("#speed-group .speed-btn")) {
      button.addEventListener("click", () => {
        const value = Number(button.dataset.speed);
        const snapped = this.timeController.setSpeed(value);
        this.syncSpeedButtons(snapped);
      });
    }

    const timeline = el("timeline-range");
    if (timeline) {
      timeline.addEventListener("input", () => {
        this.timeController.seek(Number(timeline.value));
      });
    }

    this.bindKeyboard();
    this.syncSpeedButtons(1);
  }

  /** Highlight the active speed button. */
  syncSpeedButtons(speed) {
    for (const button of document.querySelectorAll("#speed-group .speed-btn")) {
      button.classList.toggle("is-active", Number(button.dataset.speed) === speed);
    }
    const chip = el("play-state-chip");
    if (chip) {
      chip.textContent = this.timeController && this.timeController.isPlaying
        ? `PLAYING ${speed}x`
        : "PAUSED";
    }
  }

  /** space = play/pause, arrows = step, R = reset. */
  bindKeyboard() {
    document.addEventListener("keydown", (event) => {
      const target = event.target;
      if (target && (
        target.tagName === "INPUT"
        || target.tagName === "SELECT"
        || target.tagName === "TEXTAREA"
        || target.isContentEditable
      )) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      switch (event.key) {
        case " ":
        case "Spacebar":
          event.preventDefault();
          this.timeController.toggle();
          this.syncSpeedButtons(this.timeController.speed);
          break;
        case "ArrowRight":
          event.preventDefault();
          this.timeController.stepForward(event.shiftKey ? 10 : 1);
          this.syncSpeedButtons(this.timeController.speed);
          break;
        case "ArrowLeft":
          event.preventDefault();
          this.timeController.stepBackward(event.shiftKey ? 10 : 1);
          this.syncSpeedButtons(this.timeController.speed);
          break;
        case "r":
        case "R":
          event.preventDefault();
          this.resetSimulation("keyboard R").catch((err) => this.reportError(err, "リセット"));
          break;
        default:
          break;
      }
    });
  }

  /**
   * Handle a playback directive coming out of the `TimeController`.
   * @param {{kind: string, frame?: number, steps?: number}} directive
   */
  handleDirective(directive) {
    switch (directive.kind) {
      case "play":
        this.stopRequested = false;
        this.syncSpeedButtons(this.timeController.speed);
        break;
      case "pause":
        this.stopRequested = true;
        this.syncSpeedButtons(this.timeController.speed);
        break;
      case "speed":
        this.syncSpeedButtons(directive.speed);
        break;
      case "frames":
      case "cursor":
        this.renderTimelineLabels();
        break;
      case "seek":
        this.serveRecordedFrame(directive.frame);
        break;
      case "advance":
        this.stopRequested = false;
        this.runSteps(directive.steps).catch((err) => this.reportError(err, "シミュレーションステップ"));
        break;
      case "replay":
        // The server ring buffer no longer holds the requested frame: reset
        // and re-run the flight from the launch condition.
        this.replayTo(directive.frame).catch((err) => this.reportError(err, "フレーム再生"));
        break;
      default:
        break;
    }
  }

  /**
   * Render a frame that is already in the client-side history buffer.
   * @param {number} frame
   */
  serveRecordedFrame(frame) {
    const index = frame - this.frameBase;
    const step = index >= 0 ? this.frames[index] : null;
    if (!step) {
      this.notify(`フレーム ${frame} はクライアントに保持されていません`, "warn");
      return;
    }
    this.lastStep = step;
    this.renderStatus(step);
    if (this.viz) {
      this.viz.setPose(step);
      this.viz.pushTrailPoint(step.position_m);
    }
    this.renderTimelineLabels();
  }

  /**
   * Issue ``n`` sequential `/simulation/step` calls.
   *
   * Concurrent batches are serialised through {@link #activeRun} so a manual
   * reset can wait for an in-flight playback batch to finish instead of
   * interleaving two step sequences against the same server-side state.
   *
   * @param {number} n
   * @returns {Promise<number>} number of steps actually issued
   */
  async runSteps(n) {
    if (this.activeRun) {
      await this.activeRun.catch(() => {});
    }
    const count = Math.max(1, Math.floor(Number(n) || 1));
    const task = this.#runStepBatch(count);
    this.activeRun = task;
    try {
      return await task;
    } finally {
      if (this.activeRun === task) {
        this.activeRun = null;
      }
    }
  }

  /**
   * @param {number} count
   * @returns {Promise<number>} steps issued
   */
  async #runStepBatch(count) {
    this.stepBusy = true;
    let issued = 0;
    try {
      for (let i = 0; i < count; i += 1) {
        if (this.stopRequested) {
          break;
        }
        const step = await this.api.post("/api/v1/simulation/step", {});
        this.timeController.settle(1);
        issued += 1;
        this.recordStep(step);
        if (step.landed || step.crashed || step.diverged) {
          this.stopRequested = true;
          this.timeController.pause();
          this.notify(
            step.diverged ? "数値発散を検出しました。リセットしてください"
              : (step.crashed ? "墜落しました" : "着地しました"),
            step.crashed || step.diverged ? "warn" : "ok",
          );
          break;
        }
      }
    } finally {
      this.stepBusy = false;
    }
    return issued;
  }

  /**
   * Reset the flight and replay forward to ``target`` frames.
   * @param {number} target
   */
  async replayTo(target) {
    const wanted = Math.max(0, Math.floor(target));
    const steps = Math.min(wanted, MAX_REPLAY_STEPS);
    if (wanted > MAX_REPLAY_STEPS) {
      this.notify(
        `再生上限 ${MAX_REPLAY_STEPS} フレームのため frame ${steps} で停止します (要求: ${wanted})`,
        "warn",
      );
    }
    await this.resetSimulation("replay");
    await this.runSteps(steps);
    this.timeController.setCursor(steps);
    this.renderTimelineLabels();
  }

  /**
   * Store a step response in the client history and refresh everything.
   * @param {object} step
   */
  recordStep(step) {
    if (!step || typeof step !== "object") {
      return;
    }
    this.lastStep = step;
    this.frames.push(step);
    if (this.frames.length > MAX_CLIENT_FRAMES) {
      this.frames.splice(0, this.frames.length - MAX_CLIENT_FRAMES);
      this.frameBase += 1;
    }
    const total = this.frameBase + this.frames.length;
    this.timeController.setFrameCount(total);
    this.timeController.setRetainedFrom(this.frameBase);
    this.renderStatus(step);
    this.pushChartSample(step);
    if (this.viz) {
      this.viz.setPose(step);
      this.viz.pushTrailPoint(step.position_m);
    }
    const now = performance.now();
    this.lastStepMs = now;
    if (now - this.lastStressMs > STRESS_INTERVAL_MS) {
      this.lastStressMs = now;
      this.requestStress(step).catch(() => {
        // Heat-map refresh is best effort; a failure must not stop playback.
      });
    }
  }

  // --------------------------------------------------------------- stress

  /**
   * `POST /api/v1/stress/analysis` and hand the field to the shader.
   * @param {object} step
   */
  async requestStress(step) {
    const source = step || this.lastStep;
    const speed = Number(source && source.speed_mps) || 0;
    const loadFactor = Number(source && source.g_load) || 1;
    const density = this.derived.density !== undefined && this.derived.density !== null
      ? Number(this.derived.density)
      : 1.225;
    const response = await this.api.post("/api/v1/stress/analysis", {
      speed_mps: speed,
      density_kgm3: density,
      load_factor: loadFactor,
      spanwise: 8,
      chordwise: 6,
    });
    this.stress = response;
    if (this.viz) {
      this.viz.setStressField(response && response.stress_index);
    }
    this.renderStatusPill(source || this.lastStep);
    return response;
  }

  // ---------------------------------------------------------------- wind

  /**
   * Build the fetcher handed to `WindGPGPU`: `POST /api/v1/wind/field`.
   * @returns {() => Promise<object>}
   */
  makeWindFetcher() {
    return async () => {
      const position = (this.lastStep && this.lastStep.position_m) || [0, 0, 0];
      const centre = [Number(position[0]) || 0, Number(position[1]) || 0, Number(position[2]) || 0];
      const half = [8, 6, 6];
      return this.api.post("/api/v1/wind/field", {
        bounds_min: [centre[0] - half[0], centre[1] - half[1], centre[2] - half[2]],
        bounds_max: [centre[0] + half[0], centre[1] + half[1], centre[2] + half[2]],
        resolution: [14, 10, 8],
        time_s: Number(this.lastStep && this.lastStep.time_s) || 0,
        backend: "auto",
      });
    };
  }

  // -------------------------------------------------------------- modules

  /** Load the local modules (time controller, storage, panels). */
  async loadModules() {
    const [timeModule, storageModule, modelModule, etaModule, optimizerModule] = await Promise.all([
      import("./time_controller.js"),
      import("./storage.js"),
      import("./model_importer.js"),
      import("./eta_display.js"),
      import("./optimizer_panel.js"),
    ]);
    this.modules = {
      TimeController: timeModule.TimeController,
      StorageManager: storageModule.StorageManager,
      ModelImporter: modelModule.ModelImporter,
      EtaDisplay: etaModule.EtaDisplay,
      OptimizerPanel: optimizerModule.OptimizerPanel,
    };
    // Objective catalogue shared by the panel and the weight sliders.
    this.objectiveInfo = optimizerModule.OBJECTIVE_INFO;
  }

  /** Instantiate the storage manager and wire the header buttons. */
  bindStorage() {
    const { StorageManager } = this.modules;
    this.storage = new StorageManager({
      api: this.api,
      getConfigBundle: () => this.getConfigBundle(),
      onApply: (bundle) => this.applyBundle(bundle),
      notify: (message, kind) => this.notify(message, kind),
    });

    const save = el("btn-save-json");
    if (save) {
      save.addEventListener("click", () => {
        save.disabled = true;
        this.storage.saveJson()
          .catch((err) => this.reportError(err, "JSON 保存"))
          .finally(() => {
            save.disabled = false;
          });
      });
    }
    const load = el("btn-load-json");
    const fileInput = el("json-file-input");
    if (load && fileInput) {
      load.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        fileInput.value = "";
        if (!file) {
          return;
        }
        this.storage.loadJsonFile(file)
          .then(() => this.afterConfigChange("設定読込"))
          .catch((err) => this.reportError(err, "JSON 読込"));
      });
    }
    const csv = el("btn-export-csv");
    if (csv) {
      csv.addEventListener("click", () => {
        csv.disabled = true;
        this.storage.exportFlightCsv()
          .catch((err) => this.reportError(err, "CSV 出力"))
          .finally(() => {
            csv.disabled = false;
          });
      });
    }

    // Mirror the boot configuration so a reload restores it without a round trip.
    const mirrored = this.storage.restoreLocal();
    if (mirrored) {
      this.applyBundle(mirrored, true);
    }
  }

  /** Wire the 3D-model panel. */
  bindModelImporter() {
    const { ModelImporter } = this.modules;
    const elements = {
      fileInput: el("mesh-file-input"),
      dropzone: el("mesh-dropzone"),
      dropzoneHint: el("mesh-dropzone-hint"),
      urlInput: el("mesh-url-input"),
      urlImportButton: el("mesh-url-import"),
      refreshButton: el("mesh-refresh-list"),
      applyButton: el("mesh-apply-to-airframe"),
      formatSelect: el("mesh-format"),
      scaleHint: el("mesh-scale-hint"),
      cgMode: el("mesh-cg-mode"),
      gsmInput: el("mesh-gsm"),
      modelList: el("mesh-model-list"),
      status: el("mesh-status"),
      metricBody: el("mesh-metric-body"),
    };
    this.modelImporter = new ModelImporter({
      api: this.api,
      elements,
      onMetrics: (metrics) => this.applyMeshMetrics(metrics),
      onGeometry: (payload) => this.applyMeshGeometry(payload),
      notify: (message, kind) => this.notify(message, kind),
    });
    this.modelImporter.bind();

    const loadModel = el("btn-load-model");
    if (loadModel) {
      loadModel.addEventListener("click", () => {
        const section = el("section-model");
        if (section) {
          section.classList.remove("is-collapsed");
          const header = section.querySelector(".section-header");
          if (header) {
            header.setAttribute("aria-expanded", "true");
          }
          if (window.innerWidth <= 1100 && el("left-panel")) {
            el("left-panel").classList.add("is-open");
          }
        }
        const input = el("mesh-file-input");
        if (input) {
          input.click();
        }
      });
    }
  }

  /**
   * Adopt the planform numbers computed by the mesh domain.
   * @param {object} metrics
   */
  applyMeshMetrics(metrics) {
    if (!metrics) {
      return;
    }
    const wingspan = Number(metrics.wingspan_m);
    const area = Number(metrics.planform_area_m2) || Number(metrics.surface_area_m2);
    const taper = Number(metrics.taper_ratio);
    const mass = Number(metrics.mass_kg);
    const overrides = (metrics.overrides && typeof metrics.overrides === "object") ? metrics.overrides : {};
    const pick = (key, auto) => (Number.isFinite(Number(overrides[key])) ? Number(overrides[key]) : auto);

    if (Number.isFinite(wingspan) && wingspan > 0) {
      this.state.aircraft.wingspan_m = pick("wingspan_m", wingspan);
    }
    if (Number.isFinite(area) && area > 0) {
      this.state.aircraft.wing_area_m2 = pick("planform_area_m2", area);
    }
    if (Number.isFinite(taper) && taper > 0) {
      this.state.aircraft.taper_ratio = pick("taper_ratio", taper);
    }
    if (Number.isFinite(mass) && mass > 0) {
      this.state.aircraft.mass_kg = pick("mass_kg", mass);
    }
    if (Number.isFinite(Number(metrics.aspect_ratio)) && Number(metrics.aspect_ratio) > 0) {
      this.state.aircraft.aspect_ratio = Number(metrics.aspect_ratio);
    }
    this.writeAirframeForm();
    this.publishState();
    if (this.viz) {
      this.viz.applyParameters(this.state.aircraft);
    }
  }

  /**
   * Push imported geometry (or the scalar reconstruction) into the 3D view.
   * @param {{positions: Float32Array|null, indices: Uint16Array|Uint32Array|null, metrics: object}} payload
   */
  applyMeshGeometry(payload) {
    if (!this.viz) {
      this.notify("3D ビューが利用できないため、モデル形状は物理量のみ反映しました", "warn");
      return;
    }
    if (payload && payload.positions && payload.positions.length >= 9) {
      try {
        const stats = this.viz.loadBufferGeometry(payload.positions, payload.indices, payload.metrics);
        this.notify(`3D ビューに取込モデル（${stats.vertices} 頂点 / ${stats.triangles} 面）を反映しました`, "ok");
        return;
      } catch (err) {
        this.reportError(err, "モデルジオメトリの適用");
      }
    }
    this.viz.applyMetricsGeometry(payload && payload.metrics);
    this.notify("サーバー算出の物理量から翼形状を再構築しました", "ok");
  }

  /** Wire the optimiser panel and the header ETA widget. */
  bindOptimizer() {
    const { OptimizerPanel, EtaDisplay } = this.modules;
    this.eta = new EtaDisplay(el("eta-widget"));

    const objectiveCheckbox = {};
    const objectiveWeight = {};
    const objectiveWeightValue = {};
    for (const name of Object.keys(this.objectiveInfo)) {
      objectiveCheckbox[name] = el(`opt-obj-${name}`);
      objectiveWeight[name] = el(`opt-weight-${name}`);
      objectiveWeightValue[name] = el(`opt-weight-val-${name}`);
    }

    this.optimizer = new OptimizerPanel({
      api: this.api,
      elements: {
        objectiveCheckbox,
        objectiveWeight,
        objectiveWeightValue,
        algorithm: el("opt-algorithm"),
        maxEvaluations: el("opt-max-evaluations"),
        populationSize: el("opt-population-size"),
        processes: el("opt-processes"),
        seed: el("opt-seed"),
        runButton: el("opt-run"),
        cancelButton: el("opt-cancel"),
        progressBar: el("opt-progress-bar"),
        progressMeta: el("opt-progress-meta"),
        etaText: el("opt-eta-text"),
        paretoCanvas: el("opt-pareto-canvas"),
        paretoFallback: el("opt-pareto-fallback"),
        axesLabel: el("opt-pareto-axes"),
        bestBody: el("opt-best-body"),
        loadBestButton: el("opt-load-best"),
        exportCsvButton: el("opt-export-csv"),
        message: el("opt-message"),
      },
      getConfigBundle: () => this.getConfigBundle(),
      onStatus: (record) => this.eta.render(record),
      onApplyParams: (params) => this.applyOptimiserParams(params),
      onExportCsv: (jobId) => {
        if (!jobId) {
          this.notify("出力する最適化ジョブがありません", "warn");
          return;
        }
        this.storage.exportOptimizerCsv(jobId, this.api)
          .catch((err) => this.reportError(err, "Pareto CSV 出力"));
      },
      notify: (message, kind) => this.notify(message, kind),
    });
    this.optimizer.bind();
  }

  /**
   * Write optimiser parameters back into the airframe / launch forms.
   * @param {object} params
   */
  applyOptimiserParams(params) {
    const mapping = {
      wingspan_m: ["aircraft", "wingspan_m"],
      wing_area_m2: ["aircraft", "wing_area_m2"],
      taper_ratio: ["aircraft", "taper_ratio"],
      aspect_ratio: ["aircraft", "aspect_ratio"],
      dihedral_deg: ["aircraft", "dihedral_deg"],
      fold_leading_deg: ["aircraft", "fold_leading_deg"],
      fold_trailing_deg: ["aircraft", "fold_trailing_deg"],
      fold_tip_deg: ["aircraft", "fold_tip_deg"],
      fold_center_deg: ["aircraft", "fold_center_deg"],
      elevon_deg: ["aircraft", "elevon_deg"],
      paper_gsm: ["aircraft", "paper_gsm"],
      paper_stiffness: ["aircraft", "paper_stiffness"],
      mass_kg: ["aircraft", "mass_kg"],
      launch_angle_deg: ["launch", "launch_angle_deg"],
      launch_roll_deg: ["launch", "launch_roll_deg"],
      launch_speed: ["launch", "launch_speed"],
    };
    let applied = 0;
    for (const [key, value] of Object.entries(params || {})) {
      const route = mapping[key];
      const n = Number(value);
      if (!route || !Number.isFinite(n)) {
        continue;
      }
      this.state[route[0]][route[1]] = n;
      applied += 1;
    }
    if (applied === 0) {
      this.notify("最適パラメータに機体へ反映できる項目がありませんでした", "warn");
      return;
    }
    this.writeAirframeForm();
    this.writeLaunchForm();
    this.publishState();
    // The launch condition is part of the state, so re-initialise the flight.
    this.resetSimulation("optimiser parameters")
      .then(() => this.notify(`最適パラメータ ${applied} 件を適用しシミュレーションを再初期化しました`, "ok"))
      .catch((err) => this.reportError(err, "最適パラメータの適用"));
  }

  // ------------------------------------------------------ config plumbing

  /** @returns {{aircraft: object, environment: object, launch: object}} */
  getConfigBundle() {
    return {
      aircraft: { ...this.state.aircraft },
      environment: { ...this.state.environment },
      launch: { ...this.state.launch },
    };
  }

  /**
   * Adopt a config bundle (from localStorage or a JSON import).
   * @param {object} bundle
   * @param {boolean} [silent]
   */
  applyBundle(bundle, silent) {
    if (!bundle || typeof bundle !== "object") {
      return;
    }
    if (bundle.aircraft && typeof bundle.aircraft === "object") {
      this.state.aircraft = { ...DEFAULT_AIRCRAFT, ...bundle.aircraft };
    }
    if (bundle.environment && typeof bundle.environment === "object") {
      this.state.environment = { ...DEFAULT_ENVIRONMENT, ...bundle.environment };
    }
    if (bundle.launch && typeof bundle.launch === "object") {
      this.state.launch = { ...DEFAULT_LAUNCH, ...bundle.launch };
    }
    this.writeAirframeForm();
    this.writeLaunchForm();
    this.writeEnvironmentForm();
    this.publishState();
    if (!silent) {
      this.notify("設定バンドルを適用しました", "ok");
    }
  }

  /**
   * Reset the flight and re-apply the current configuration on the server.
   *
   * `POST /api/v1/simulation/reset` answers with `{state, aircraft,
   * environment, launch, simulation, backend}` -- a *configuration* payload,
   * not a telemetry packet.  One `POST /api/v1/simulation/step` is therefore
   * issued straight afterwards so frame 0 of the timeline carries the full
   * telemetry (alpha, attitude, L/D and the rest) instead of a bare state
   * vector.
   *
   * @param {string} reason
   * @returns {Promise<object>} the reset response
   */
  async resetSimulation(reason) {
    this.timeController.pause();
    this.stopRequested = true;
    // Let any in-flight playback batch drain before touching server state.
    if (this.activeRun) {
      await this.activeRun.catch(() => {});
    }
    await this.flushEnvironmentAsync();
    const bundle = this.getConfigBundle();
    const response = await this.api.post("/api/v1/simulation/reset", {
      launch: bundle.launch,
      aircraft: bundle.aircraft,
      environment: bundle.environment,
    });
    if (response && response.aircraft && typeof response.aircraft === "object") {
      this.state.aircraft = { ...DEFAULT_AIRCRAFT, ...response.aircraft };
      this.writeAirframeForm();
    }
    this.frames = [];
    this.frameBase = 0;
    this.timeController.setRetainedFrom(0);
    this.timeController.setFrameCount(0);
    this.timeController.setCursor(0);
    this.simulationBackend = (response && response.backend) || this.simulationBackend;

    if (this.viz) {
      // The previous flight becomes the translucent ghost overlay.
      this.viz.archiveTrail();
    }
    for (const chart of Object.values(this.charts)) {
      chart.clear();
    }
    this.updateSampleCount();

    // One step populates frame 0 with real telemetry.  A failure here is not
    // fatal: the timeline simply starts empty and the next step fills it.
    this.stopRequested = false;
    try {
      await this.runSteps(1);
    } catch (err) {
      this.stopRequested = true;
      this.reportError(err, "初期フレームの取得");
    }
    this.publishState();
    if (reason) {
      this.notify(`シミュレーションを初期化しました (${reason})`, "info", 2600);
    }
    return response;
  }

  /** Resolve once any debounced environment patch has been delivered. */
  async flushEnvironmentAsync() {
    if (this.environmentTimer !== null) {
      window.clearTimeout(this.environmentTimer);
      this.environmentTimer = null;
    }
    if (Object.keys(this.pendingEnvironment).length === 0) {
      return;
    }
    const patch = this.pendingEnvironment;
    this.pendingEnvironment = {};
    try {
      const response = await this.api.post("/api/v1/environment/atmosphere", patch);
      this.applyEnvironmentResponse(response, false);
    } catch (err) {
      this.reportError(err, "環境パラメータ更新");
    }
  }

  /** Re-initialise the flight after any configuration change. */
  async afterConfigChange(reason) {
    try {
      await this.resetSimulation(reason);
    } catch (err) {
      this.reportError(err, reason);
    }
  }

  // -------------------------------------------------------------- 3D view

  /**
   * Boot Three.js.  Failure is contained: the viewport shows a message and the
   * rest of the UI keeps working.
   */
  async initViz() {
    const canvas = el("gl-canvas");
    const fallback = el("viewport-fallback");
    const reason = el("viewport-fallback-reason");
    if (!canvas) {
      return null;
    }
    try {
      const module = await import("./airplane_gpu.js");
      this.viz = new module.AirplaneView({
        canvas,
        getConfig: () => this.state.aircraft,
        onFrame: (info) => {
          const node = el("overlay-fps");
          if (node) {
            node.textContent = `fps: ${info.fps.toFixed(0)}`;
          }
        },
      });
      this.viz.applyParameters(this.state.aircraft);
      if (fallback) {
        fallback.hidden = true;
      }
    } catch (err) {
      this.viz = null;
      this.vizError = err;
      if (fallback) {
        fallback.hidden = false;
      }
      if (reason) {
        reason.textContent = `Three.js を読み込めませんでした: ${err && err.message ? err.message : String(err)}`;
      }
      this.notify("3D ビューを初期化できませんでした（ネットワーク / WebGL を確認してください）。他の機能は動作します。", "warn", 8000);
      return null;
    }

    try {
      const windModule = await import("./wind_gpgpu.js");
      this.wind = new windModule.WindGPGPU({
        renderer: this.viz.renderer,
        scene: this.viz.scene,
        particleCount: windModule.DEFAULT_PARTICLE_COUNT,
        fetchField: this.makeWindFetcher(),
        refreshIntervalMs: WIND_INTERVAL_MS,
        notify: (message, kind) => this.notify(message, kind),
      });
      const stats = this.wind.getStats();
      this.updateOverlay(stats);
      if (stats.mode === "cpu") {
        this.notify(
          `風粒子は ${stats.particleCount} 点の CPU フォールバックで描画します (${stats.reason})`,
          "warn",
          7000,
        );
      }
    } catch (err) {
      this.wind = null;
      this.windError = err;
      this.notify(`GPGPU 風粒子を初期化できませんでした: ${err.message}`, "warn");
    }
    return this.viz;
  }

  /**
   * Update the viewport status overlay.
   * @param {object} [stats]
   */
  updateOverlay(stats) {
    const backend = el("overlay-backend");
    const particles = el("overlay-particles");
    if (backend) {
      const fieldBackend = stats && stats.fieldBackend ? stats.fieldBackend : "";
      backend.textContent = `backend: ${this.simulationBackend || "—"}${fieldBackend ? ` / wind ${fieldBackend}` : ""}`;
    }
    if (particles) {
      if (this.wind) {
        const info = this.wind.getStats();
        particles.textContent = `particles: ${info.particleCount.toLocaleString()} (${info.mode.toUpperCase()})`;
      } else {
        particles.textContent = "particles: 0 (unavailable)";
      }
    }
  }

  // ------------------------------------------------------------- main loop

  /** Drive the render loop, the playback clock and the wind refresh. */
  startLoop() {
    let previous = performance.now();
    const frame = (now) => {
      const delta = Math.min(0.1, Math.max(0, (now - previous) / 1000));
      previous = now;
      try {
        if (this.viz) {
          this.viz.update(delta);
        }
        if (this.wind) {
          const calls = this.timeController.tick(now);
          this.wind.update(delta);
          // The field is refreshed on its own ~1 s cadence whether or not the
          // simulation is playing, so the streaks stay live while paused.
          this.wind.refreshWindField(now).then((payload) => {
            if (payload) {
              this.updateOverlay(this.wind.getStats());
            }
          }).catch(() => {});
          if (calls === 0 && Math.floor(now / 500) !== this._lastOverlayTick) {
            this._lastOverlayTick = Math.floor(now / 500);
            this.updateOverlay(this.wind.getStats());
          }
        } else {
          this.timeController.tick(now);
        }
      } catch (err) {
        this.reportError(err, "描画ループ");
      }
      window.requestAnimationFrame(frame);
    };
    window.requestAnimationFrame(frame);
  }

  /** Publish a machine-readable snapshot for inspection and testing. */
  publishState() {
    const app = this;
    window.__vizState = {
      ready: Boolean(this.viz),
      vizAvailable: Boolean(this.viz),
      vizError: this.vizError ? String(this.vizError.message || this.vizError) : null,
      windMode: this.wind ? this.wind.mode : null,
      particleCount: this.wind ? this.wind.particleCount : 0,
      chartsAvailable: chartsAvailable(),
      frameCount: this.timeController ? this.timeController.frameCount : 0,
      currentFrame: this.timeController ? this.timeController.currentFrame : 0,
      retainedFrom: this.timeController ? this.timeController.retainedFrom : 0,
      playing: this.timeController ? this.timeController.isPlaying : false,
      speed: this.timeController ? this.timeController.speed : 1,
      requestCount: this.api.requestCount,
      aircraft: () => app.getConfigBundle().aircraft,
      environment: () => app.getConfigBundle().environment,
      launch: () => app.getConfigBundle().launch,
      derived: () => app.derived,
      stress: () => app.stress,
      lastStep: () => app.lastStep,
      frames: () => app.frames,
      api: this.api,
      app: this,
    };
  }

  // ------------------------------------------------------------------ boot

  /** Full boot sequence. */
  async boot() {
    this.bindChrome();
    await this.loadModules();
    this.publishState();
    this.bindCharts();
    this.bindAirframeForm();
    this.bindEnvironmentForm();
    this.bindTimeDock();
    this.bindStorage();
    this.bindModelImporter();
    this.bindOptimizer();
    this.publishState();

    // Server truth for the environment (requirement A), then the initial state.
    try {
      await this.loadEnvironment();
    } catch (err) {
      this.reportError(err, "環境パラメータ取得");
    }

    try {
      await this.resetSimulation("boot");
    } catch (err) {
      this.reportError(err, "シミュレーション初期化");
      this.fatal = err;
    }

    await this.initViz();
    this.publishState();
    this.startLoop();

    // A first stress sample and wind field so the visualisation is populated
    // before the user hits play.
    this.requestStress(this.lastStep).catch(() => {});
    if (this.wind) {
      this.wind.refreshWindField(performance.now())
        .then((payload) => {
          this.updateOverlay(this.wind.getStats());
          if (payload) {
            this.notify(`風場を取得しました (backend=${this.wind.getStats().fieldBackend})`, "info", 2600);
          }
        })
        .catch(() => {});
    }
  }
}

/* ==========================================================================
 * Entry point
 * ========================================================================== */

function start() {
  const app = new SimulatorApp();
  window.__paperAirplaneApp = app;
  app.boot().catch((err) => {
    // eslint-disable-next-line no-console
    console.error("[boot] fatal", err);
    app.reportError(err, "起動");
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start, { once: true });
} else {
  start();
}

export { SimulatorApp, TimeSeriesChart };
