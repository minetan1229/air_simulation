/**
 * optimizer_panel.js -- optimisation control surface for the right overlay.
 *
 *   POST /api/v1/optimizer/run            -> {job_id} immediately (non blocking)
 *   GET  /api/v1/optimizer/status/<job_id> -> polled every 500 ms until terminal
 *   POST /api/v1/optimizer/cancel/<job_id>
 *   GET  /api/v1/optimizer/result/<job_id>?format=json -> Pareto front + best vector
 *
 * The status record is also forwarded to the header {@link EtaDisplay} so the
 * progress bar, remaining time and estimated completion clock are always in
 * sync with the optimiser.
 */

const POLL_INTERVAL_MS = 500;

/** Objective metadata: label, direction and display unit. */
export const OBJECTIVE_INFO = {
  max_distance: { label: "距離", unit: "m", direction: 1, axis: "distance" },
  max_flight_time: { label: "滞空時間", unit: "s", direction: 1, axis: "flight_time" },
  max_ld_ratio: { label: "L/D", unit: "-", direction: 1, axis: "ld_ratio" },
  min_max_stress: { label: "最大応力", unit: "Pa", direction: -1, axis: "max_stress" },
  max_stall_margin: { label: "失速マージン", unit: "deg", direction: 1, axis: "stall_margin" },
  min_launch_speed: { label: "発射初速", unit: "m/s", direction: -1, axis: "launch_speed" },
  max_stability: { label: "安定性", unit: "-", direction: 1, axis: "stability" },
};

/** The canonical design-variable catalogue (mirrors config_schema.py). */
export const DESIGN_VARIABLES = Object.freeze([
  "wingspan_m",
  "wing_area_m2",
  "taper_ratio",
  "aspect_ratio",
  "dihedral_deg",
  "fold_leading_deg",
  "fold_trailing_deg",
  "fold_tip_deg",
  "fold_center_deg",
  "elevon_deg",
  "paper_gsm",
  "paper_stiffness",
  "mass_kg",
  "launch_angle_deg",
  "launch_roll_deg",
  "launch_speed",
]);

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

/** Is Chart.js present?  The panel degrades to a text message when not. */
function chartsAvailable() {
  return typeof window !== "undefined" && typeof window.Chart === "function";
}

export class OptimizerPanel {
  /**
   * @param {object} options
   * @param {{post:Function, get:Function}} options.api ApiClient instance
   * @param {object} options.elements DOM lookups (see index.html ids)
   * @param {() => object} options.getConfigBundle live aircraft/environment/launch
   * @param {(record: object) => void} [options.onStatus] ETA widget sink
   * @param {(params: object) => void} [options.onApplyParams] "load these settings"
   * @param {(jobId: string) => void} [options.onExportCsv] Pareto CSV export
   * @param {(message: string, kind?: string) => void} [options.notify]
   */
  constructor(options = {}) {
    if (!options.api) {
      throw new Error("OptimizerPanel requires an ApiClient");
    }
    this.api = options.api;
    this.el = options.elements || {};
    this.getConfigBundle = typeof options.getConfigBundle === "function"
      ? options.getConfigBundle
      : () => ({});
    this.onStatus = typeof options.onStatus === "function" ? options.onStatus : () => {};
    this.onApplyParams = typeof options.onApplyParams === "function" ? options.onApplyParams : () => {};
    this.onExportCsv = typeof options.onExportCsv === "function"
      ? options.onExportCsv
      : (jobId) => this.notify(`job ${jobId} の CSV 出力ハンドラが未登録です`, "warn");
    this.notify = typeof options.notify === "function" ? options.notify : () => {};

    this.jobId = null;
    this.lastStatus = null;
    this.result = null;
    this.pollTimer = null;
    this._chart = null;
    this._objectives = ["max_distance", "min_max_stress"];
  }

  // ------------------------------------------------------------------ setup

  /** Wire the objective checkboxes, weight sliders and transport buttons. */
  bind() {
    for (const name of Object.keys(OBJECTIVE_INFO)) {
      const checkbox = this.el.objectiveCheckbox && this.el.objectiveCheckbox[name];
      const slider = this.el.objectiveWeight && this.el.objectiveWeight[name];
      const readout = this.el.objectiveWeightValue && this.el.objectiveWeightValue[name];
      const row = checkbox ? checkbox.closest(".objective-row") : null;
      if (slider && readout) {
        const sync = () => {
          readout.textContent = Number(slider.value).toFixed(2);
        };
        sync();
        slider.addEventListener("input", () => {
          sync();
          this.#syncRowState();
        });
      }
      if (checkbox) {
        checkbox.addEventListener("change", () => {
          if (checkbox.checked && slider) {
            slider.disabled = false;
          } else if (slider) {
            slider.disabled = true;
          }
          if (row) {
            row.classList.toggle("is-disabled", !checkbox.checked);
          }
          this.#syncRowState();
        });
        if (row) {
          row.classList.toggle("is-disabled", !checkbox.checked);
        }
      }
    }
    this.#syncRowState();

    if (this.el.runButton) {
      this.el.runButton.addEventListener("click", () => {
        this.run().catch((err) => this.#fail(err));
      });
    }
    if (this.el.cancelButton) {
      this.el.cancelButton.addEventListener("click", () => {
        this.cancel().catch((err) => this.#fail(err));
      });
    }
    if (this.el.loadBestButton) {
      this.el.loadBestButton.addEventListener("click", () => this.#applyBest());
    }
    if (this.el.exportCsvButton) {
      this.el.exportCsvButton.addEventListener("click", () => {
        this.onExportCsv(this.jobId);
      });
    }
    return this;
  }

  /** Grey out the rows whose objective is not selected. */
  #syncRowState() {
    for (const name of Object.keys(OBJECTIVE_INFO)) {
      const checkbox = this.el.objectiveCheckbox && this.el.objectiveCheckbox[name];
      const row = checkbox ? checkbox.closest(".objective-row") : null;
      if (row) {
        row.classList.toggle("is-disabled", !checkbox.checked);
      }
    }
  }

  // -------------------------------------------------------------- request

  /**
   * Read the form into an optimiser request body.
   * @returns {object}
   */
  collectRequest() {
    const bundle = this.getConfigBundle() || {};
    const objectives = [];
    for (const name of Object.keys(OBJECTIVE_INFO)) {
      const checkbox = this.el.objectiveCheckbox && this.el.objectiveCheckbox[name];
      if (!checkbox || !checkbox.checked) {
        continue;
      }
      const slider = this.el.objectiveWeight && this.el.objectiveWeight[name];
      const weight = slider ? Number(slider.value) : 1;
      objectives.push({ name, weight: Number.isFinite(weight) ? weight : 1 });
    }
    const readNumber = (input, fallback) => {
      if (!input) {
        return fallback;
      }
      const n = Number(input.value);
      return Number.isFinite(n) ? n : fallback;
    };
    // The server derives the weight map from `objectives[{name, weight}]`, so
    // no separate `weights` key is sent.
    const body = {
      objectives,
      algorithm: this.el.algorithm ? this.el.algorithm.value : "differential-evolution",
      design_variables: DESIGN_VARIABLES.slice(),
      variable_bounds: {},
      max_evaluations: Math.max(1, Math.round(readNumber(this.el.maxEvaluations, 200))),
      population_size: Math.max(4, Math.round(readNumber(this.el.populationSize, 40))),
      seed: Math.max(1, Math.round(readNumber(this.el.seed, 7))),
      processes: Math.max(0, Math.round(readNumber(this.el.processes, 0))),
    };
    if (bundle.aircraft) {
      body.aircraft = bundle.aircraft;
    }
    if (bundle.environment) {
      body.environment = bundle.environment;
    }
    if (bundle.launch) {
      body.launch = bundle.launch;
    }
    return body;
  }

  /**
   * Launch an optimisation job and start polling.
   * @returns {Promise<object>} the status record after the first poll
   */
  async run() {
    const body = this.collectRequest();
    if (body.objectives.length === 0) {
      throw new Error("目的関数が 1 つ以上選択されている必要があります");
    }
    if (this.el.runButton) {
      this.el.runButton.disabled = true;
    }
    this.#setMessage("ジョブを投入中…", "info");
    this.#renderProgress(null);
    const started = await this.api.post("/api/v1/optimizer/run", body);
    if (!started || !started.job_id) {
      throw new Error("サーバーが job_id を返しませんでした");
    }
    this.jobId = started.job_id;
    this.result = null;
    this._objectives = body.objectives.map((o) => o.name);
    if (this.el.cancelButton) {
      this.el.cancelButton.disabled = false;
    }
    if (this.el.loadBestButton) {
      this.el.loadBestButton.disabled = true;
    }
    if (this.el.exportCsvButton) {
      this.el.exportCsvButton.disabled = true;
    }
    this.#setAxesLabel();
    this.#setMessage(`algorithm=${body.algorithm} / evaluations=${body.max_evaluations}`, "info");
    this.#schedulePoll(0);
    return started;
  }

  /**
   * Cancel the running job.
   * @returns {Promise<object>} the job record
   */
  async cancel() {
    if (!this.jobId) {
      throw new Error("実行中のジョブがありません");
    }
    if (this.el.cancelButton) {
      this.el.cancelButton.disabled = true;
    }
    const record = await this.api.post(`/api/v1/optimizer/cancel/${encodeURIComponent(this.jobId)}`);
    this.#applyStatus(record || { status: "cancelled", job_id: this.jobId });
    return record;
  }

  // ------------------------------------------------------------- polling

  /** Schedule the next status poll. */
  #schedulePoll(delayMs) {
    if (this.pollTimer !== null) {
      window.clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    if (!this.jobId) {
      return;
    }
    this.pollTimer = window.setTimeout(() => {
      this.pollTimer = null;
      this.#pollOnce().catch((err) => this.#fail(err));
    }, delayMs);
  }

  /** Fetch the job status once and react. */
  async #pollOnce() {
    if (!this.jobId) {
      return null;
    }
    const record = await this.api.get(`/api/v1/optimizer/status/${encodeURIComponent(this.jobId)}`);
    this.#applyStatus(record);
    if (record && TERMINAL.has(String(record.status).toLowerCase())) {
      this.#stopPolling();
      const status = String(record.status).toLowerCase();
      // `GET /api/v1/optimizer/result/<job_id>` only serves completed or
      // cancelled jobs, so a failure must not trigger the fetch.
      if (status !== "failed") {
        await this.#loadResult();
      } else if (this.el.loadBestButton) {
        this.el.loadBestButton.disabled = true;
      }
      return record;
    }
    this.#schedulePoll(POLL_INTERVAL_MS);
    return record;
  }

  /**
   * Push a status record into the UI + the ETA widget.
   * @param {object} record
   */
  #applyStatus(record) {
    this.lastStatus = record;
    this.#renderProgress(record);
    this.onStatus(record);
    const status = String(record.status || "").toLowerCase();
    if (status === "failed") {
      this.#setMessage(record.message || "最適化が失敗しました", "error");
    } else if (status === "cancelled") {
      this.#setMessage(record.message || "最適化を中断しました", "warn");
    } else if (status === "completed") {
      this.#setMessage(record.message || "最適化が完了しました", "ok");
    } else if (record.message) {
      this.#setMessage(record.message, "info");
    }
  }

  /** Release the transport buttons and timers. */
  #stopPolling() {
    if (this.pollTimer !== null) {
      window.clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.el.runButton) {
      this.el.runButton.disabled = false;
    }
    if (this.el.cancelButton) {
      this.el.cancelButton.disabled = true;
    }
    if (this.el.exportCsvButton) {
      this.el.exportCsvButton.disabled = true;
    }
  }

  // -------------------------------------------------------------- rendering

  /**
   * Update the progress bar / counters.
   * @param {object|null} record
   */
  #renderProgress(record) {
    if (!record) {
      if (this.el.progressBar) {
        this.el.progressBar.style.width = "0%";
        this.el.progressBar.className = "progress-bar";
      }
      if (this.el.progressMeta) {
        this.el.progressMeta.textContent = "— / —";
      }
      if (this.el.etaText) {
        this.el.etaText.textContent = "eta --:--";
      }
      return;
    }
    const done = Number(record.evaluated_count) || 0;
    const total = Number(record.total_evaluations) || 0;
    let fraction = Number(record.progress);
    if (!Number.isFinite(fraction)) {
      fraction = total > 0 ? Math.min(1, done / total) : 0;
    }
    fraction = Math.min(1, Math.max(0, fraction));
    const status = String(record.status || "").toLowerCase();
    if (this.el.progressBar) {
      this.el.progressBar.style.width = `${(fraction * 100).toFixed(1)}%`;
      this.el.progressBar.className = `progress-bar status-${status}`;
    }
    if (this.el.progressMeta) {
      const rate = Number(record.evals_per_second) || 0;
      const suffix = rate > 0 ? ` · ${rate.toFixed(1)} ev/s · ${record.backend || "?"}` : ` · ${record.backend || "?"}`;
      this.el.progressMeta.textContent = `${done} / ${total || "?"} (${(fraction * 100).toFixed(1)}%)${suffix}`;
    }
    if (this.el.etaText) {
      const seconds = Number(record.eta_seconds);
      const label = TERMINAL.has(status)
        ? status
        : `eta ${Number.isFinite(seconds) && seconds >= 0 ? this.#fmtDuration(seconds) : "--:--"}`;
      this.el.etaText.textContent = label;
    }
  }

  /**
   * `mm:ss` / `h:mm:ss` formatter used by the inline ETA label.
   * @param {number} seconds
   * @returns {string}
   */
  #fmtDuration(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (v) => String(v).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
  }

  /**
   * Write a message into the panel footer.
   * @param {string} text
   * @param {string} kind info|ok|warn|error
   */
  #setMessage(text, kind) {
    if (!this.el.message) {
      return;
    }
    this.el.message.textContent = text || "";
    this.el.message.classList.toggle("is-error", kind === "error");
    this.el.message.classList.toggle("is-ok", kind === "ok");
  }

  /** Label the scatter axes from the currently selected objectives. */
  #setAxesLabel() {
    if (!this.el.axesLabel) {
      return;
    }
    const names = this._objectives.length ? this._objectives : ["max_distance", "min_max_stress"];
    const x = OBJECTIVE_INFO[names[0]] || { label: names[0] };
    const y = OBJECTIVE_INFO[names[1]] || { label: names[1] || names[0] };
    this.el.axesLabel.textContent = `x: ${x.label} / y: ${y.label}`;
  }

  // ----------------------------------------------------------------- result

  /** Fetch the finished result and draw the Pareto front + best table. */
  async #loadResult() {
    if (!this.jobId) {
      return null;
    }
    try {
      const result = await this.api.get(
        `/api/v1/optimizer/result/${encodeURIComponent(this.jobId)}`,
        { format: "json" },
      );
      this.result = result;
      this.#renderBest(result);
      this.#renderPareto(result);
      if (this.el.loadBestButton) {
        this.el.loadBestButton.disabled = !(result && result.best_params);
      }
      if (this.el.exportCsvButton) {
        this.el.exportCsvButton.disabled = !result;
      }
      return result;
    } catch (err) {
      this.#setMessage(`結果の取得に失敗しました: ${err.message}`, "error");
      return null;
    }
  }

  /**
   * Read an objective value out of a Pareto entry, tolerating both the mapping
   * and the ordered-array encodings the server may use.
   * @param {object} entry
   * @param {string} name
   * @param {number} index
   * @returns {number}
   */
  #objectiveValue(entry, name, index) {
    const raw = entry ? entry.objectives : null;
    if (raw === null || raw === undefined) {
      return NaN;
    }
    if (Array.isArray(raw)) {
      const n = Number(raw[index]);
      return Number.isFinite(n) ? n : NaN;
    }
    if (typeof raw === "object") {
      const n = Number(raw[name]);
      return Number.isFinite(n) ? n : NaN;
    }
    const n = Number(raw);
    return Number.isFinite(n) ? n : NaN;
  }

  /**
   * Draw the Pareto scatter.
   * @param {object} result
   */
  #renderPareto(result) {
    const canvas = this.el.paretoCanvas;
    const fallback = this.el.paretoFallback;
    if (!canvas) {
      return;
    }
    const names = this._objectives.length ? this._objectives : ["max_distance", "min_max_stress"];
    const xName = names[0];
    const yName = names[1] || names[0];
    const front = Array.isArray(result && result.pareto_front) ? result.pareto_front : [];

    if (!chartsAvailable()) {
      if (fallback) {
        fallback.hidden = false;
        fallback.textContent = front.length
          ? `Chart.js CDN に接続できないため散布図を描画できません (Pareto front: ${front.length} 点)`
          : "Chart.js CDN に接続できないため散布図を描画できません。";
      }
      canvas.hidden = true;
      return;
    }
    if (fallback) {
      fallback.hidden = true;
    }
    canvas.hidden = false;

    const points = [];
    for (let i = 0; i < front.length; i += 1) {
      const entry = front[i];
      const x = this.#objectiveValue(entry, xName, 0);
      const y = this.#objectiveValue(entry, yName, 1);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        points.push({ x, y });
      }
    }
    // Single-objective runs have no trade-off; put the solutions on a line so
    // the chart still conveys the spread instead of a single invisible point.
    if (points.length && xName === yName) {
      const xs = points.map((p) => p.x);
      const lo = Math.min(...xs);
      const hi = Math.max(...xs);
      const span = hi - lo || 1;
      points.forEach((p) => {
        p.y = lo + ((p.x - lo) / span) * span;
      });
    }

    const xInfo = OBJECTIVE_INFO[xName] || { label: xName, unit: "" };
    const yInfo = OBJECTIVE_INFO[yName] || { label: yName, unit: "" };

    const dataset = {
      label: `Pareto front (${points.length})`,
      data: points,
      backgroundColor: "rgba(26, 115, 232, 0.65)",
      borderColor: "rgb(26, 115, 232)",
      pointRadius: 3,
      pointHoverRadius: 5,
    };

    if (this._chart && this.el.paretoCanvas.id === this._chartCanvasId) {
      this._chart.data.datasets = [dataset];
      this._chart.data.datasets[0].data = points;
      this._chart.options.scales.x.title.text = `${xInfo.label}${xInfo.unit ? ` (${xInfo.unit})` : ""}`;
      this._chart.options.scales.y.title.text = `${yInfo.label}${yInfo.unit ? ` (${yInfo.unit})` : ""}`;
      this._chart.update("none");
      return;
    }

    const context = canvas.getContext("2d");
    if (!context) {
      if (fallback) {
        fallback.hidden = false;
        fallback.textContent = "2D コンテキストを取得できないため散布図を描画できません。";
      }
      return;
    }
    this._chartCanvasId = canvas.id;
    this._chart = new window.Chart(context, {
      type: "scatter",
      data: { datasets: [dataset] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => `x ${Number(item.parsed.x).toPrecision(5)} / y ${Number(item.parsed.y).toPrecision(5)}`,
            },
          },
        },
        scales: {
          x: {
            type: "linear",
            title: { display: true, text: `${xInfo.label}${xInfo.unit ? ` (${xInfo.unit})` : ""}` },
            grid: { color: "rgba(95, 99, 104, 0.16)" },
          },
          y: {
            type: "linear",
            title: { display: true, text: `${yInfo.label}${yInfo.unit ? ` (${yInfo.unit})` : ""}` },
            grid: { color: "rgba(95, 99, 104, 0.16)" },
          },
        },
      },
    });
  }

  /**
   * Fill the best-parameter table.
   * @param {object} result
   */
  #renderBest(result) {
    const body = this.el.bestBody;
    if (!body) {
      return;
    }
    body.textContent = "";
    const params = (result && result.best_params) || null;
    const vector = (result && Array.isArray(result.best_vector)) ? result.best_vector : [];
    const names = this._objectives.length ? this._objectives : ["max_distance", "min_max_stress"];
    const entries = [];

    if (params && typeof params === "object") {
      for (const [key, value] of Object.entries(params)) {
        entries.push([key, value]);
      }
    } else if (vector.length) {
      DESIGN_VARIABLES.forEach((key, index) => {
        if (index < vector.length) {
          entries.push([key, vector[index]]);
        }
      });
    }

    if (!entries.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 2;
      cell.className = "label-sm";
      cell.textContent = "結果なし";
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }

    for (const [key, value] of entries) {
      const row = document.createElement("tr");
      const nameCell = document.createElement("td");
      nameCell.textContent = key;
      const valueCell = document.createElement("td");
      valueCell.className = "num";
      const n = Number(value);
      valueCell.textContent = Number.isFinite(n) ? formatValue(n, key) : String(value);
      row.appendChild(nameCell);
      row.appendChild(valueCell);
      body.appendChild(row);
    }

    const objectives = (result && result.best_objectives) || null;
    if (objectives) {
      const values = Array.isArray(objectives)
        ? names.map((name, index) => [name, objectives[index]])
        : Object.entries(objectives);
      for (const [name, value] of values) {
        const row = document.createElement("tr");
        const nameCell = document.createElement("td");
        nameCell.textContent = `◦ ${name}`;
        const valueCell = document.createElement("td");
        valueCell.className = "num";
        const n = Number(value);
        valueCell.textContent = Number.isFinite(n) ? formatValue(n, name) : String(value);
        row.appendChild(nameCell);
        row.appendChild(valueCell);
        body.appendChild(row);
      }
    }
  }

  /** Push the best vector back into the airframe form. */
  #applyBest() {
    if (!this.result) {
      this.notify("まだ最適化結果が取得されていません", "warn");
      return;
    }
    let params = this.result.best_params;
    if ((!params || typeof params !== "object") && Array.isArray(this.result.best_vector)) {
      params = {};
      DESIGN_VARIABLES.forEach((key, index) => {
        if (index < this.result.best_vector.length) {
          params[key] = this.result.best_vector[index];
        }
      });
    }
    if (!params || typeof params !== "object") {
      this.notify("最適パラメータを読み込めませんでした", "error");
      return;
    }
    this.onApplyParams(params);
    this.#setMessage(`最適パラメータ ${Object.keys(params).length} 件を機体フォームに読み込みました`, "ok");
  }

  /**
   * Uniform error handling for the async button handlers.
   * @param {Error} err
   */
  #fail(err) {
    const message = err && err.message ? err.message : String(err);
    this.#setMessage(message, "error");
    this.notify(`最適化パネル: ${message}`, "error");
    this.#stopPolling();
    if (this.el.runButton) {
      this.el.runButton.disabled = false;
    }
  }

  /** Cancel any in-flight polling (page teardown). */
  dispose() {
    this.#stopPolling();
  }
}

/**
 * Format a numeric parameter for the best table.
 * @param {number} value
 * @param {string} key
 * @returns {string}
 */
export function formatValue(value, key) {
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude < 1e-3 || magnitude >= 1e6)) {
    return value.toExponential(4);
  }
  if (key === "paper_gsm" || key === "mass_kg") {
    return magnitude < 0.01 ? value.toFixed(5) : value.toFixed(3);
  }
  return value.toFixed(4);
}

export default OptimizerPanel;
