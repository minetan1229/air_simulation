/*
 * optimizer.js — search panel: objectives, design variables, non-blocking
 * job submission, ETA tracking, Pareto front and the "adopt this design" path
 * back into the configuration rail.
 */

import { clockFmt, clear, fmt, isNum, make, sig } from "./lib.js";

const POLL_MS = 700;
const JOBS_MS = 2000;

const OBJECTIVE_LABELS = {
  max_distance: { label: "最大水平距離", unit: "m", dir: " maximise" },
  max_flight_time: { label: "最大滞空時間", unit: "s", dir: " maximise" },
  max_ld_ratio: { label: "最大 L/D", unit: "", dir: " maximise" },
  min_max_stress: { label: "最小最大応力", unit: "Pa", dir: " minimise" },
  max_stall_margin: { label: "最大失速マージン", unit: "", dir: " maximise" },
  min_launch_speed: { label: "最小発射初速", unit: "m/s", dir: " minimise" },
  max_stability: { label: "最大静的安定性", unit: "", dir: " maximise" },
};

const ALGORITHM_LABELS = {
  grid: "粗い格子スイープ",
  "nelder-mead": "Nelder-Mead",
  "differential-evolution": "差分進化 (DE)",
  nsga2: "NSGA-II / Pareto",
  surrogate: "Surrogate (k-NN / RBF + EI)",
};

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

export class OptimizerPanel {
  constructor({ api, store, dom, notify }) {
    this.api = api;
    this.store = store;
    this.dom = dom;
    this.notify = notify || (() => {});
    this.objectives = new Map();
    this.variables = new Map();
    this.weights = { max_distance: 1, min_max_stress: 0.5 };
    this.enabledObjectives = new Set(["max_distance", "min_max_stress"]);
    this.enabledVariables = new Set([
      "wingspan_m",
      "wing_area_m2",
      "taper_ratio",
      "dihedral_deg",
      "elevon_deg",
      "paper_gsm",
      "launch_angle_deg",
      "launch_speed",
    ]);
    this.jobId = null;
    this.pollHandle = 0;
    this.jobsHandle = 0;
    this.result = null;
    this.chart = null;
  }

  async mount(meta) {
    this.meta = meta;
    this._buildObjectives(meta.objectives || []);
    this._buildVariables(meta.design_variables || []);
    this._buildSettings(meta.algorithms || []);
    this._bind();
    this.startJobPolling();
  }

  /* ------------------------------------------------------------- build */

  _buildObjectives(catalogue) {
    const host = this.dom.objectives;
    clear(host);
    this.objectives.clear();
    catalogue.forEach((entry, index) => {
      const info = OBJECTIVE_LABELS[entry.name] || { label: entry.name, unit: "", dir: "" };
      const checkbox = make("input", {
        type: "checkbox",
        checked: this.enabledObjectives.has(entry.name) ? true : null,
      });
      const weightValue = make("span", { class: "num", text: fmt(this.weights[entry.name] ?? 0.5, 2) });
      const slider = make("input", {
        type: "range",
        min: 0,
        max: 1,
        step: 0.05,
        value: this.weights[entry.name] ?? 0.5,
      });
      slider.addEventListener("input", () => {
        const value = Number(slider.value);
        this.weights[entry.name] = value;
        weightValue.textContent = fmt(value, 2);
      });
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) this.enabledObjectives.add(entry.name);
        else this.enabledObjectives.delete(entry.name);
        row.dataset.on = checkbox.checked ? "1" : "0";
      });
      const row = make("div", { class: "obj", "data-on": checkbox.checked ? "1" : "0" }, [
        checkbox,
        make("span", { class: "obj-name", text: info.label }),
        make("span", { class: "obj-dir", text: info.dir.trim() }),
        weightValue,
      ]);
      slider.addEventListener("change", () => {
        weightValue.textContent = fmt(Number(slider.value), 2);
      });
      host.append(row, slider);
      this.objectives.set(entry.name, { checkbox, slider, index });
    });
  }

  _buildVariables(catalogue) {
    const host = this.dom.variables;
    clear(host);
    this.variables.clear();
    for (const entry of catalogue) {
      const checkbox = make("input", {
        type: "checkbox",
        checked: this.enabledVariables.has(entry.name) ? true : null,
      });
      const low = make("input", { type: "number", value: entry.low, step: (entry.high - entry.low) / 200 || 0.01 });
      const high = make("input", { type: "number", value: entry.high, step: (entry.high - entry.low) / 200 || 0.01 });
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) this.enabledVariables.add(entry.name);
        else this.enabledVariables.delete(entry.name);
        row.dataset.on = checkbox.checked ? "1" : "0";
      });
      const row = make("div", { class: "var" }, [
        checkbox,
        make("span", { class: "var-name", text: `${entry.label || entry.name}` }),
        make("span", { class: "var-bounds" }, [
          low,
          make("span", { text: "–" }),
          high,
        ]),
      ]);
      const commit = () => {
        const lo = Number(low.value);
        const hi = Number(high.value);
        if (isNum(lo) && isNum(hi) && hi > lo) {
          entry.low = lo;
          entry.high = hi;
        } else {
          low.value = entry.low;
          high.value = entry.high;
        }
      };
      low.addEventListener("change", commit);
      high.addEventListener("change", commit);
      host.append(row);
      this.variables.set(entry.name, { entry, checkbox });
    }
  }

  _buildSettings(algorithms) {
    const host = this.dom.settings;
    clear(host);
    this.select = make("select", { class: "select", id: "opt-algorithm" });
    for (const name of algorithms) {
      this.select.append(make("option", { value: name, text: ALGORITHM_LABELS[name] || name }));
    }
    this.select.value = "differential-evolution";
    this.evaluations = this._number(host, "評価回数", 8, 20000, 1, 120);
    this.population = this._number(host, "母集団数", 4, 512, 1, 40);
    this.processes = this._number(host, "並列プロセス", 0, 64, 1, 0);
    this.seed = this._number(host, "乱数 seed", 1, 999999, 1, 7);
    this.duration = this._number(host, "模擬時間", 0.5, 60, 0.1, 6);
    this.dtMax = this._number(host, "刻み Δt", 0.002, 0.05, 0.001, 0.02, 4);
  }

  _number(host, label, min, max, step, value, digits = 0) {
    const field = make("div", { class: "field" }, [
      make("div", { class: "field-top" }, [make("span", { class: "field-key", text: label })]),
      make("input", { type: "number", class: "num-input", min, max, step, value, style: "width:100%" }),
    ]);
    host.append(field);
    return field.querySelector("input");
  }

  _bind() {
    this.dom.run.addEventListener("click", () => this.run());
    this.dom.cancel.addEventListener("click", () => this.cancel());
    this.dom.csv.addEventListener("click", () => this.exportCsv());
    this.dom.adopt.addEventListener("click", () => this.adopt());
    this.dom.jobs.addEventListener("click", (event) => {
      const row = event.target.closest(".job");
      if (row && row.dataset.job) this.selectJob(row.dataset.job);
    });
  }

  /* --------------------------------------------------------------- run */

  payload() {
    const objectives = Array.from(this.enabledObjectives);
    const variables = Array.from(this.enabledVariables);
    const bounds = {};
    for (const name of variables) {
      const entry = this.variables.get(name);
      if (entry) bounds[name] = [entry.entry.low, entry.entry.high];
    }
    return {
      objectives,
      weights: this.weights,
      algorithm: this.select.value,
      design_variables: variables,
      variable_bounds: bounds,
      max_evaluations: Number(this.evaluations.value),
      population_size: Number(this.population.value),
      processes: Number(this.processes.value),
      seed: Number(this.seed.value),
      sim_duration_s: Number(this.duration.value),
      dt_max: Number(this.dtMax.value),
    };
  }

  async run() {
    if (!this.enabledObjectives.size) {
      this.notify("目的関数を 1 つ以上選択してください", "warn");
      return;
    }
    if (!this.enabledVariables.size) {
      this.notify("設計変数を 1 つ以上選択してください", "warn");
      return;
    }
    this.dom.run.disabled = true;
    try {
      const payload = this.payload();
      const job = await this.api.optimizerRun({
        ...payload,
        aircraft: this.store.data.aircraft,
        environment: this.store.data.environment,
        launch: this.store.data.launch,
      });
      this.jobId = job.job_id;
      this.dom.cancel.disabled = false;
      this.dom.chartEmpty.hidden = true;
      this.dom.eta.dataset.active = "1";
      this.notify(`探索を開始: ${ALGORITHM_LABELS[job.algorithm] || job.algorithm}`, "ok");
      this.refreshJobs();
    } catch (error) {
      this.dom.run.disabled = false;
      this.notify(`探索を開始できません: ${error.message}`, "error");
    }
  }

  async cancel() {
    if (!this.jobId) return;
    try {
      await this.api.optimizerCancel(this.jobId);
      this.notify("中断を要求しました", "warn");
    } catch (error) {
      this.notify(`中断できません: ${error.message}`, "error");
    }
  }

  async selectJob(jobId) {
    this.jobId = jobId;
    this.dom.eta.dataset.active = "1";
    await this.pollJob();
  }

  /* ------------------------------------------------------------- polling */

  startJobPolling() {
    if (this.pollHandle) return;
    const tick = async () => {
      this.pollHandle = window.setTimeout(tick, POLL_MS);
      if (this.jobId) await this.pollJob();
      await this.refreshJobs();
    };
    this.pollHandle = window.setTimeout(tick, JOBS_MS);
  }

  stop() {
    if (this.pollHandle) window.clearTimeout(this.pollHandle);
    this.pollHandle = 0;
  }

  async pollJob() {
    if (!this.jobId) return;
    let record;
    try {
      record = await this.api.optimizerStatus(this.jobId);
    } catch (error) {
      return;
    }
    const total = Number(record.total_evaluations) || 0;
    const done = Number(record.evaluated_count) || 0;
    const ratio = total ? Math.min(done / total, 1) : Number(record.progress) || 0;
    this.dom.fill.style.width = `${(ratio * 100).toFixed(1)}%`;
    this.dom.count.textContent = `${done} / ${total}`;
    this.dom.rate.textContent = Number(record.evals_per_second) > 0
      ? `${fmt(record.evals_per_second, 2)} eval/s`
      : record.backend || "—";
    this.setEta(record);

    if (TERMINAL.has(record.status) && this.result?.job_id !== this.jobId) {
      this.dom.run.disabled = false;
      this.dom.cancel.disabled = true;
      this.dom.eta.dataset.active = "0";
      if (record.status === "completed") {
        await this.loadResult();
      } else if (record.status === "failed") {
        this.notify(`探索が失敗しました: ${record.message || record.status}`, "error");
        this.dom.chartEmpty.hidden = false;
        this.dom.chartEmpty.textContent = record.message || "failed";
      } else {
        this.dom.chartEmpty.hidden = false;
        this.dom.chartEmpty.textContent = "中断されました";
      }
    }
  }

  setEta(record) {
    const terminal = TERMINAL.has(record.status);
    if (terminal) {
      const at = record.finished_at || record.estimated_completion;
      this.dom.etaClock.textContent = at ? at.slice(11, 19) : "—";
      this.dom.etaLeft.textContent = record.status === "completed" ? "完了" : record.status;
      if (this.dom.eta) {
        this.dom.eta.dataset.active = "0";
        this.dom.eta.title = `状態: ${record.status} (評価数: ${record.evaluated_count || 0})`;
      }
      return;
    }
    if (!record.active) {
      this.dom.etaClock.textContent = "—";
      this.dom.etaLeft.textContent = record.status || "idle";
      if (this.dom.eta) {
        this.dom.eta.dataset.active = "0";
        this.dom.eta.title = "最適化エンジン待機中";
      }
      return;
    }
    this.dom.etaClock.textContent = record.completion_clock || clockFmt(record.eta_seconds);
    this.dom.etaLeft.textContent = `残 ${record.eta_display || clockFmt(record.eta_seconds)}`;
    if (this.dom.eta) {
      this.dom.eta.dataset.active = "1";
      const total = Number(record.total_evaluations) || 0;
      const done = Number(record.evaluated_count) || 0;
      const pct = total > 0 ? ((done / total) * 100).toFixed(1) : "0.0";
      const rate = Number(record.evals_per_second) > 0 ? `${fmt(record.evals_per_second, 1)} eval/s` : record.backend || "";
      this.dom.eta.title = `探索進捗: ${done}/${total} (${pct}%)\n速度: ${rate}\n完了予定: ${record.completion_clock || "—"}`;
    }
  }

  async refreshJobs() {
    let payload;
    try {
      payload = await this.api.jobs();
    } catch (error) {
      return;
    }
    const jobs = (payload.jobs || []).slice(-8).reverse();
    const host = this.dom.jobs;
    clear(host);
    if (!jobs.length) {
      host.append(make("p", { class: "chart-empty", text: "ジョブなし" }));
      return;
    }
    for (const job of jobs) {
      host.append(
        make("div", { class: "job", "data-status": job.status, "data-job": job.job_id }, [
          make("span", { class: "job-dot" }),
          make("span", { class: "job-id", text: `${job.job_id.slice(0, 8)} ${ALGORITHM_LABELS[job.algorithm] || job.algorithm}` }),
          make("span", { class: "job-eta", text: TERMINAL.has(job.status) ? job.status : clockFmt(job.eta_seconds) }),
        ])
      );
    }
  }

  /* -------------------------------------------------------------- result */

  async loadResult() {
    let result;
    try {
      result = await this.api.optimizerResult(this.jobId);
    } catch (error) {
      this.notify(`結果を取得できません: ${error.message}`, "error");
      return;
    }
    if (!result || !result.pareto_front) {
      this.dom.chartEmpty.hidden = false;
      this.dom.chartEmpty.textContent = "結果なし";
      return;
    }
    this.result = result;
    this.dom.chartEmpty.hidden = true;
    this.dom.csv.disabled = false;
    this.dom.adopt.disabled = false;
    this.renderFront(result);
    this.renderBest(result);
    this.notify(`${result.evaluations} 評価完了 (${result.backend})`, "ok");
  }

  renderFront(result) {
    const names = result.objectives || [];
    const xName = names[0] || "objective";
    const yName = names[1] || names[0] || "objective";
    this.dom.frontAxes.textContent = `${OBJECTIVE_LABELS[xName]?.label || xName} × ${OBJECTIVE_LABELS[yName]?.label || yName}`;
    const points = result.pareto_front
      .map((entry) => {
        const x = Number(entry.objectives[xName]);
        const y = Number(entry.objectives[yName]);
        return isNum(x) && isNum(y) ? { x, y } : null;
      })
      .filter(Boolean);
    this._drawScatter(points, xName, yName);
  }

  _drawScatter(points, xName, yName) {
    const canvas = this.dom.chart;
    const Ctor = window.Chart;
    if (!Ctor) {
      this.dom.chartEmpty.hidden = false;
      this.dom.chartEmpty.textContent = "Chart.js を読み込めません";
      return;
    }
    if (this.chart) {
      this.chart.data.datasets[0].data = points;
      const xLabel = OBJECTIVE_LABELS[xName]?.label || xName;
      const yLabel = OBJECTIVE_LABELS[yName]?.label || yName;
      if (this.chart.options.scales?.x?.title) {
        this.chart.options.scales.x.title.text = xLabel;
      }
      if (this.chart.options.scales?.y?.title) {
        this.chart.options.scales.y.title.text = yLabel;
      }
      if (this.chart.options.plugins?.tooltip?.callbacks) {
        this.chart.options.plugins.tooltip.callbacks.label = (item) =>
          `${xLabel}: ${sig(item.parsed.x, 4)}  ${yLabel}: ${sig(item.parsed.y, 4)}`;
      }
      this.chart.update("none");
      return;
    }
    this.chart = new Ctor(canvas.getContext("2d"), {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "Pareto front",
            data: points,
            pointRadius: 3.5,
            pointHoverRadius: 5.5,
            borderColor: "#1a73e8",
            backgroundColor: "rgba(26,115,232,0.18)",
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => `${OBJECTIVE_LABELS[xName]?.label || xName}: ${sig(item.parsed.x, 4)}  ${OBJECTIVE_LABELS[yName]?.label || yName}: ${sig(item.parsed.y, 4)}`,
            },
          },
        },
        scales: {
          x: {
            title: { display: true, text: OBJECTIVE_LABELS[xName]?.label || xName, font: { size: 9 } },
            grid: { color: "#eceae4" },
            ticks: { font: { size: 9 }, color: "#858a91" },
          },
          y: {
            title: { display: true, text: OBJECTIVE_LABELS[yName]?.label || yName, font: { size: 9 } },
            grid: { color: "#eceae4" },
            ticks: { font: { size: 9 }, color: "#858a91" },
          },
        },
      },
    });
  }

  renderBest(result) {
    const host = this.dom.best;
    clear(host);
    const table = make("table");
    const objectives = result.objectives || [];
    table.append(
      make("thead", {}, [
        make("tr", {}, [
          make("th", { text: "パラメータ" }),
          make("th", { text: "値" }),
        ]),
      ])
    );
    const body = make("tbody");
    for (const [key, value] of Object.entries(result.best_params || {})) {
      body.append(
        make("tr", {}, [
          make("td", { text: key }),
          make("td", { text: sig(Number(value), 4) }),
        ])
      );
    }
    for (const name of objectives) {
      const value = Number(result.best_objectives?.[name]);
      body.append(
        make("tr", {}, [
          make("td", { text: OBJECTIVE_LABELS[name]?.label || name }),
          make("td", { class: "obj-val", text: isNum(value) ? sig(value, 4) : "—" }),
        ])
      );
    }
    table.append(body);
    host.append(table);
  }

  async exportCsv() {
    if (!this.jobId) return;
    try {
      const { text } = await this.api.optimizerCsv(this.jobId);
      const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = make("a", { href: url, download: `pareto_${this.jobId.slice(0, 8)}.csv` });
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      this.notify("Pareto CSV を書き出しました", "ok");
    } catch (error) {
      this.notify(`CSV を書き出せません: ${error.message}`, "error");
    }
  }

  /** Copy the winning design vector into the live configuration. */
  adopt() {
    if (!this.result || !this.result.best_params) return;
    let applied = 0;
    for (const [key, value] of Object.entries(this.result.best_params)) {
      const numeric = Number(value);
      if (!isNum(numeric)) continue;
      for (const section of ["aircraft", "launch", "environment"]) {
        if (this.store.data[section] && key in this.store.data[section]) {
          this.store.set(section, key, numeric, "optimizer");
          applied += 1;
          break;
        }
      }
    }
    if (applied) {
      this.notify(`${applied} 個のパラメータを設定に反映しました`, "ok");
      this.store.emit("change", { section: null, bulk: true, origin: "optimizer" });
    } else {
      this.notify("反映できるパラメータがありません", "warn");
    }
  }
}