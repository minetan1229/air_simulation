/*
 * ga_research.js — the autonomous research (genetic algorithm) workbench.
 *
 * The panel drives the GA engine of the server:
 *
 *   - the parameter table (objective, trial budget, tiers, islands, …) is
 *     built from a spec table and every value is sent with the request, so
 *     the search can be steered while it runs,
 *   - the run is polled for its status, its islands and its journal,
 *   - the best individual is shown in an interactive folding viewer: the
 *     3D step slider, the crease pattern and the human folding recipe are
 *     three views of the very same fold programme and stay in sync.
 *
 * Everything the engine has not published yet is reported as such: the
 * panel never invents a result.
 */

import { clear, downloadText, fmt, isNum, make, safeJSON, sig } from "./lib.js";
import {
  buildOrigamiGeometry,
  evaluateOrigamiGenome,
  formatFoldAngle,
  renderCreasePattern,
  requestOrigamiGenomeSteps,
  requestOrigamiRecipe,
} from "./origami.js";

const POLL_MS = 1200;
const JOURNAL_MS = 2600;

/** localStorage key of the run the monitor is attached to. */
const RUN_KEY = "paper-airplane-console:ga-run-id";

/**
 * The Guinness references the search aims at.  The values are the official
 * records kept in physics/records_domain.py: 88.31 m (Dillon Ruble, longest
 * flight) and 29.2 s (Takuo Toda, longest hang time).
 */
export const RECORD_TARGETS = {
  distance: {
    value: 88.31,
    unit: "m",
    label: "ギネス記録 88.31 m (Dillon Ruble)",
  },
  airtime: {
    value: 29.2,
    unit: "s",
    label: "ギネス記録 29.2 s (Takuo Toda)",
  },
};

/**
 * The parameter table of the search.
 *
 * `fallback` mirrors the documented defaults of the engine
 * (``config/genetic_research.yaml`` and ``physics.ga_config.GAConfig``).
 * The engine declares its effective configuration with every run, and the
 * form is synchronised from it, so the values the user sees are always the
 * ones the request carries.
 */
const PARAM_SPECS = [
  {
    key: "objective",
    kind: "select",
    label: "最適化目標",
    unit: "OBJECTIVE",
    options: [
      ["distance", "水平距離 (distance)"],
      ["airtime", "滞空時間 (airtime)"],
    ],
    fallback: "distance",
  },
  {
    key: "stat_mode",
    kind: "select",
    label: "統計モード",
    unit: "STAT MODE",
    options: [
      ["mean", "平均 (mean)"],
      ["median", "中央値 (median)"],
      ["hybrid_median_mean", "中央値+平均 (hybrid_median_mean)"],
      ["best", "最良 (best)"],
    ],
    fallback: "hybrid_median_mean",
  },
  { key: "max_trials", label: "総試行回数", unit: "回", min: 1, max: 10000000, step: 1, fallback: 100000 },
  { key: "top_k", label: "上位候補数 K", unit: "個", min: 1, max: 100000, step: 1, fallback: 1000 },
  { key: "tier1_trials", label: "Tier1 試行数", unit: "回", min: 0, max: 10000000, step: 1, fallback: 20 },
  { key: "tier2_trials", label: "Tier2 試行数", unit: "回", min: 0, max: 10000000, step: 1, fallback: 1000 },
  { key: "tier3_trials", label: "Tier3 試行数", unit: "回", min: 0, max: 10000000, step: 1, fallback: 100000 },
  { key: "generations", label: "世代数", unit: "世代", min: 1, max: 100000, step: 1, fallback: 20 },
  { key: "islands", label: "島 (Island) 数", unit: "島", min: 1, max: 256, step: 1, fallback: 4 },
  { key: "seed", label: "乱数シード", unit: "", min: 0, max: 2147483647, step: 1, fallback: 7 },
];

/** Terminal states of a run. */
const TERMINAL = new Set([
  "completed", "failed", "cancelled", "stopped", "error", "budget_exhausted",
]);

/**
 * Is the error a "this route does not exist" answer?
 *
 * The console serves a catch-all route for the CORS preflight, so an
 * unknown path is answered with 405 (method not allowed) instead of 404.
 * Both mean the GA engine has not been mounted (yet).
 */
function isMissingEndpoint(error) {
  if (!error) return false;
  const status = Number(error.status);
  return status === 404 || status === 405;
}

/** Human labels for the throw parameters the engine may report. */
const THROW_LABELS = {
  launch_speed: "発射初速",
  launch_speed_mps: "発射初速",
  speed: "発射初速",
  speed_mps: "発射初速",
  launch_angle_deg: "発射角 (軌道角)",
  release_elevation_deg: "放し角度 (仰角)",
  elevation_deg: "放し角度 (仰角)",
  release_pitch_deg: "放し時ピッチ",
  pitch_deg: "放し時ピッチ",
  release_yaw_deg: "放し時ヨー",
  yaw_deg: "放し時ヨー",
  release_roll_deg: "放し時ロール",
  roll_deg: "放し時ロール",
  throw_pitch_rate_radps: "手首ピッチ速度",
  pitch_rate_radps: "手首ピッチ速度",
  throw_roll_rate_radps: "手首ロール速度",
  throw_yaw_rate_radps: "手首ヨー速度",
  release_height_m: "放し高",
  throw_force_n: "手の力",
  throw_duration_s: "押し込み時間",
  throw_grip_x: "握り位置 x",
  throw_grip_y: "握り位置 y",
};

const UNIT_BY_KEY = {
  launch_speed: "m/s",
  launch_speed_mps: "m/s",
  speed: "m/s",
  speed_mps: "m/s",
  launch_angle_deg: "°",
  release_elevation_deg: "°",
  elevation_deg: "°",
  release_pitch_deg: "°",
  pitch_deg: "°",
  release_yaw_deg: "°",
  yaw_deg: "°",
  release_roll_deg: "°",
  roll_deg: "°",
  throw_pitch_rate_radps: "rad/s",
  pitch_rate_radps: "rad/s",
  throw_roll_rate_radps: "rad/s",
  throw_yaw_rate_radps: "rad/s",
  release_height_m: "m",
  throw_force_n: "N",
  throw_duration_s: "s",
};

/** Normalise the many shapes a step mesh list can take. */
function normaliseStepMeshes(value) {
  if (Array.isArray(value)) return value.filter((entry) => entry && Array.isArray(entry.positions));
  if (!value || typeof value !== "object") return [];
  for (const key of ["states", "step_meshes", "meshes", "steps"]) {
    if (Array.isArray(value[key])) {
      return value[key].filter((entry) => entry && Array.isArray(entry.positions));
    }
  }
  return [];
}

/**
 * Format a timestamp of the checkpoint list.
 *
 * The engine reports the modification time of the file as a unix timestamp,
 * a checkpoint written by another tool may carry an ISO string.
 *
 * @param {number|string} value the timestamp
 * @returns {string} a readable timestamp
 */
function formatTimestamp(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "number" && Number.isFinite(value)) {
    const date = new Date(value * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("ja-JP", { hour12: false });
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("ja-JP", { hour12: false });
}

/** The recipe steps of a result, whatever shape the engine used. */
function normaliseSteps(recipe, result) {
  if (Array.isArray(recipe)) return recipe;
  if (recipe && Array.isArray(recipe.steps)) return recipe.steps;
  if (result && Array.isArray(result.recipe)) return result.recipe;
  if (result && result.recipe && Array.isArray(result.recipe.steps)) return result.recipe.steps;
  return [];
}

export class GaResearchPanel {
  constructor({ api, store, dom, notify, viewport }) {
    this.api = api;
    this.store = store;
    this.dom = dom;
    this.notify = notify || (() => {});
    this.viewport = viewport || null;

    this.runId = null;
    this.available = null;      // null = unknown, false = engine missing
    this.unavailableReason = "";
    this.params = {};
    this.inputs = new Map();
    this.touched = new Set();   // parameters the user changed by hand
    this.defaultsProbed = false;
    this.status = null;
    this.result = null;
    this.trend = new Map();      // generation -> best fitness
    this.journalSeen = 0;
    this.journalEntries = 0;
    this.chart = null;
    this.pollHandle = 0;
    this.journalHandle = 0;
    this.stepMeshes = [];
    this.steps = [];
    this.svgRoot = null;
    this.currentFold = null;
    this.activeStep = -1;        // recipe step index (0 based)
    this.foldSource = "sample";
    this.markersEnabled = true;
    this.abort = null;
  }

  /* ---------------------------------------------------------------- boot */

  async mount() {
    this._buildParams();
    this._bind();
    this._initParams();
    this._renderStatus();
    this._renderIslands([]);
    this._renderJournal([]);
    await this.loadSampleFold();
    await this._restoreRun();
  }

  /**
   * Re-attach to the last run of this console.
   *
   * A research run outlives a page reload, so the run id is kept in the
   * shared store: the monitor picks the run up again and shows its state.
   */
  async _restoreRun() {
    const stored = this.store.get("research", "run_id") || this._readStoredRunId();
    if (!stored) return;
    this.runId = String(stored);
    this._renderStatus();
    try {
      const record = await this.api.gaResearch.status(this.runId);
      this.available = true;
      this.status = record;
      this._renderStatus(record);
      this._recordTrend(record);
      this._renderIslands(Array.isArray(record.islands) ? record.islands : []);
      await this.pollJournal();
      if (TERMINAL.has(String(record.status))) {
        await this.loadResult();
      } else {
        if (this.dom.stop) this.dom.stop.disabled = false;
        this._startPolling();
      }
    } catch (error) {
      // The server does not know this run any more.
      this.runId = null;
      this.store.merge("research", { run_id: null }, "research");
      this._renderStatus();
    }
  }

  /**
   * Fill the parameter form.
   *
   * The values come from the shared store (a loaded configuration file)
   * or, when the user has nothing stored, from the documented defaults of
   * the table below.  The engine's own defaults replace them as soon as it
   * is reachable (see {@link _probeServerDefaults}).
   */
  _initParams() {
    for (const spec of PARAM_SPECS) {
      const fromStore = this.store.get("research", spec.key);
      const value = fromStore !== undefined && fromStore !== null ? fromStore : spec.fallback;
      this.params[spec.key] = value;
      const input = this.inputs.get(spec.key);
      if (input) input.value = String(value);
    }
    this._syncParamState();
  }

  /**
   * Ask the engine for its own defaults.
   *
   * The route is optional, so it is only probed once the engine proved to
   * be alive; a missing route must not litter the console with errors.
   * Values the user has changed by hand are never overwritten.
   */
  async _probeServerDefaults() {
    if (this.defaultsProbed) return;
    let payload = null;
    try {
      payload = await this.api.gaResearch.defaults();
    } catch (error) {
      return;
    }
    this.defaultsProbed = true;
    const defaults = payload && (payload.defaults || payload.config || payload);
    if (!defaults || typeof defaults !== "object") return;
    let changed = false;
    for (const spec of PARAM_SPECS) {
      const value = defaults[spec.key];
      if (value === undefined || value === null) continue;
      if (this.touched.has(spec.key)) continue;
      if (this.params[spec.key] === value) continue;
      this.params[spec.key] = value;
      changed = true;
    }
    if (changed) {
      this._syncParamState();
      this.notify("サーバーの既定値を反映しました", "info");
    }
  }

  /** Publish the current parameters in the shared store. */
  _storeParams() {
    if (!this.store) return;
    this.store.merge("research", { ...this.params }, "research");
  }

  /** Re-read the parameters from the shared store (configuration load). */
  applyStoreParams() {
    for (const spec of PARAM_SPECS) {
      const value = this.store.get("research", spec.key);
      if (value === undefined || value === null) continue;
      this.params[spec.key] = value;
    }
    this._syncParamState();
    this._storeParams();
  }

  _buildParams() {
    const host = this.dom.params;
    if (!host) return;
    clear(host);
    this.inputs.clear();
    for (const spec of PARAM_SPECS) {
      const unit = make("span", { class: "field-unit", text: spec.unit || "" });
      const value = make("span", { class: "field-val" });
      const top = make("div", { class: "field-top" }, [
        make("span", { class: "field-key", text: spec.label }),
        unit,
        value,
      ]);
      let input;
      if (spec.kind === "select") {
        input = make("select", { class: "select", id: `ga-${spec.key.replace(/_/g, "-")}` });
        for (const [optionValue, optionText] of spec.options) {
          input.append(make("option", { value: optionValue, text: optionText }));
        }
        input.addEventListener("change", () => {
          this.params[spec.key] = input.value;
          this.touched.add(spec.key);
          this._syncParamState();
        });
      } else {
        input = make("input", {
          type: "number",
          class: "num-input",
          id: `ga-${spec.key.replace(/_/g, "-")}`,
          min: spec.min,
          max: spec.max,
          step: spec.step,
          style: "width:100%",
        });
        input.addEventListener("change", () => {
          const parsed = Number(input.value);
          if (!Number.isFinite(parsed)) {
            input.value = String(this.params[spec.key]);
            return;
          }
          this.params[spec.key] = parsed;
          this.touched.add(spec.key);
          this._syncParamState();
        });
      }
      this.inputs.set(spec.key, input);
      host.append(make("div", { class: "field" }, [top, input]));
    }
  }

  _syncParamState() {
    for (const spec of PARAM_SPECS) {
      const input = this.inputs.get(spec.key);
      if (!input) continue;
      const value = this.params[spec.key];
      if (spec.kind === "select") {
        input.value = String(value);
        continue;
      }
      if (document.activeElement !== input) input.value = String(value);
    }
    this._updateTargetLabel();
    this._updateBudgetNote();
    this._storeParams();
  }

  _bind() {
    const d = this.dom;
    if (d.run) d.run.addEventListener("click", () => this.run());
    if (d.stop) d.stop.addEventListener("click", () => this.stop());
    if (d.foldSource) {
      d.foldSource.addEventListener("change", async () => {
        this.foldSource = d.foldSource.value;
        if (this.foldSource === "ga") await this._loadGaFold();
        else await this.loadSampleFold();
      });
    }
    if (d.foldSlider) {
      d.foldSlider.addEventListener("input", () => {
        this.selectStep(Number(d.foldSlider.value));
      });
    }
    if (d.foldPrev) {
      d.foldPrev.addEventListener("click", () => {
        const slider = this.dom.foldSlider;
        if (!slider) return;
        slider.value = String(Math.max(0, Number(slider.value) - 1));
        this.selectStep(Number(slider.value));
      });
    }
    if (d.foldNext) {
      d.foldNext.addEventListener("click", () => {
        const slider = this.dom.foldSlider;
        if (!slider) return;
        slider.value = String(Math.min(Number(slider.max || 0), Number(slider.value) + 1));
        this.selectStep(Number(slider.value));
      });
    }
    if (d.markerToggle) {
      d.markerToggle.addEventListener("change", () => {
        this.markersEnabled = Boolean(d.markerToggle.checked);
        this._applyMarkers();
      });
    }
    if (d.recipeBody) {
      d.recipeBody.addEventListener("click", (event) => {
        const row = event.target.closest("tr[data-step]");
        if (!row) return;
        this.selectStep(Number(row.dataset.step) + 1);
      });
    }
    if (d.exportJson) d.exportJson.addEventListener("click", () => this.exportJson());
    if (d.exportMd) d.exportMd.addEventListener("click", () => this.exportMarkdown());
    if (d.exportSvg) d.exportSvg.addEventListener("click", () => this.exportSvg());
    if (d.ckptRefresh) d.ckptRefresh.addEventListener("click", () => this.refreshCheckpoints());
    if (d.ckptSave) d.ckptSave.addEventListener("click", () => this.saveCheckpoint());
    if (d.resume) d.resume.addEventListener("click", () => this.resume());
  }

  /* --------------------------------------------------------------- run */

  payload() {
    const body = {
      objective: String(this.params.objective || "distance"),
      stat_mode: String(this.params.stat_mode || "mean"),
      max_trials: Number(this.params.max_trials),
      top_k: Number(this.params.top_k),
      tier1_trials: Number(this.params.tier1_trials),
      tier2_trials: Number(this.params.tier2_trials),
      tier3_trials: Number(this.params.tier3_trials),
      generations: Number(this.params.generations),
      islands: Number(this.params.islands),
      seed: Number(this.params.seed),
    };
    const outputDir = this.dom.outputDir ? String(this.dom.outputDir.value || "").trim() : "";
    if (outputDir) body.output_dir = outputDir;
    return body;
  }

  async run() {
    if (this.dom.run) this.dom.run.disabled = true;
    this._setNotice("", "");
    try {
      const started = await this.api.gaResearch.run(this.payload());
      this.available = true;
      this.runId = String(started.run_id || "");
      this._setRunId(this.runId);
      this.trend.clear();
      this.journalSeen = 0;
      this.journalEntries = 0;
      if (this.dom.journal) clear(this.dom.journal);
      this.status = started;
      this._renderStatus(started);
      if (started.config && typeof started.config === "object") {
        // The engine declares its effective configuration with the run.
        this._renderConfig(started.config);
      } else {
        this._probeServerDefaults();
      }
      if (this.dom.stop) this.dom.stop.disabled = false;
      this.notify(`GA研究を開始しました (run_id=${this.runId})`, "ok");
      this._startPolling();
    } catch (error) {
      if (this.dom.run) this.dom.run.disabled = false;
      this._noteUnavailable(error);
    }
  }

  async stop() {
    if (!this.runId) return;
    if (this.dom.stop) this.dom.stop.disabled = true;
    try {
      await this.api.gaResearch.stop(this.runId);
      this.notify("停止を要求しました", "warn");
    } catch (error) {
      if (isMissingEndpoint(error)) {
        this._stopPolling();
        this.notify(
          `サーバーに停止 API が未実装のため、監視のみ停止しました (run_id=${this.runId})`,
          "warn"
        );
      } else {
        this.notify(`停止できません: ${error.message}`, "error");
        if (this.dom.stop) this.dom.stop.disabled = false;
      }
    }
  }

  /* ------------------------------------------------------------ polling */

  _startPolling() {
    this._stopPolling();
    const tick = async () => {
      this.pollHandle = window.setTimeout(tick, POLL_MS);
      await this.pollStatus();
    };
    this.pollHandle = window.setTimeout(tick, POLL_MS);

    const journalTick = async () => {
      this.journalHandle = window.setTimeout(journalTick, JOURNAL_MS);
      await this.pollJournal();
    };
    this.journalHandle = window.setTimeout(journalTick, JOURNAL_MS);
  }

  _stopPolling() {
    if (this.pollHandle) window.clearTimeout(this.pollHandle);
    if (this.journalHandle) window.clearTimeout(this.journalHandle);
    this.pollHandle = 0;
    this.journalHandle = 0;
  }

  async pollStatus() {
    if (!this.runId) return;
    let record;
    try {
      record = await this.api.gaResearch.status(this.runId);
    } catch (error) {
      if (isMissingEndpoint(error)) this._noteUnavailable(error);
      return;
    }
    this.available = true;
    this.status = record;
    this._renderStatus(record);
    this._recordTrend(record);
    this._renderIslands(Array.isArray(record.islands) ? record.islands : []);
    if (TERMINAL.has(String(record.status))) {
      this._stopPolling();
      if (this.dom.run) this.dom.run.disabled = false;
      if (this.dom.stop) this.dom.stop.disabled = true;
      await this.loadResult();
    }
  }

  async pollJournal() {
    if (!this.runId) return;
    let payload;
    try {
      payload = await this.api.gaResearch.journal(this.runId);
    } catch (error) {
      return;
    }
    const entries = Array.isArray(payload && payload.entries) ? payload.entries : [];
    this._renderJournal(entries);
  }

  _recordTrend(record) {
    const generation = Number(record.generation);
    // A run without a finite fitness yet reports `null`; `Number(null)` would
    // be 0 and would draw a fabricated point at the bottom of the chart.
    const rawFitness = record.best_fitness;
    const fitness = rawFitness === null || rawFitness === undefined
      ? NaN
      : Number(rawFitness);
    if (Number.isFinite(generation) && Number.isFinite(fitness)) {
      const previous = this.trend.get(generation);
      if (previous === undefined || fitness > previous) {
        this.trend.set(generation, fitness);
      }
    }
    const history = record.history || record.generations_history;
    if (Array.isArray(history)) {
      for (const entry of history) {
        if (!entry || typeof entry !== "object") continue;
        const gen = Number(entry.generation ?? entry.gen);
        const value = Number(entry.best_fitness ?? entry.fitness ?? entry.best);
        if (Number.isFinite(gen) && Number.isFinite(value)) {
          const previous = this.trend.get(gen);
          if (previous === undefined || value > previous) this.trend.set(gen, value);
        }
      }
    }
    this._drawTrend();
  }

  /* ------------------------------------------------------------- result */

  async loadResult() {
    if (!this.runId) return;
    let payload;
    try {
      payload = await this.api.gaResearch.result(this.runId);
    } catch (error) {
      this._setNotice(
        "結果を取得できません: " + (error && error.message ? error.message : String(error)),
        "error"
      );
      return;
    }
    this.result = payload;
    this._renderResult(payload);
    if (this.dom.foldSource) {
      const option = this.dom.foldSource.querySelector('option[value="ga"]');
      if (option) {
        option.disabled = false;
        option.textContent = "GA最良個体 (研究結果)";
      }
    }
    this.steps = normaliseSteps(payload.recipe, payload);
    this.stepMeshes = normaliseStepMeshes(payload.step_meshes);
    this._renderRecipe(this.steps);
    const svgText = typeof payload.crease_pattern_svg === "string"
      ? payload.crease_pattern_svg
      : (payload.recipe && typeof payload.recipe.crease_pattern_svg === "string"
        ? payload.recipe.crease_pattern_svg
        : "");
    this._renderCreasePattern(svgText, this.steps, payload.genome);
    this._renderThrowParams(payload.throw_params);
    this._renderStats(payload.stats, payload.fitness);
    await this.pollJournal();
    await this._loadGaFold();
  }

  /* -------------------------------------------------------- fold viewer */

  /**
   * Summarise the result of the run.
   *
   * Everything shown here comes from the server response: the fitness, the
   * statistics of the evaluation set and the objective the run pursued.
   */
  _renderResult(payload) {
    const host = this.dom.resultNote;
    if (!host) return;
    const fitness = Number(payload.fitness);
    const metric = Number(payload.metric);
    const stats = payload.stats && typeof payload.stats === "object" ? payload.stats : {};
    const target = this._recordTarget();
    const best = Number(stats.mean ?? stats.overall_median ?? stats.median);
    const parts = [];
    if (Number.isFinite(fitness)) parts.push(`適応度 ${sig(fitness, 5)}`);
    if (Number.isFinite(metric)) {
      const label = this.params.objective === "airtime" ? "滞空時間" : "水平距離";
      const ratio = target.value ? (metric / target.value) * 100 : NaN;
      parts.push(
        `${label} ${sig(metric, 4)} ${target.unit}` +
        (Number.isFinite(ratio) ? ` (記録の ${ratio.toFixed(1)}%)` : "")
      );
    }
    if (Number.isFinite(best)) {
      parts.push(`平均 ${sig(best, 4)} ${target.unit}`);
    }
    if (Number.isFinite(Number(stats.failure_rate))) {
      parts.push(`失敗率 ${(Number(stats.failure_rate) * 100).toFixed(1)}%`);
    }
    host.textContent = parts.length ? parts.join(" / ") : "結果を受信しました";
    host.dataset.tone = "ok";
    if (this.dom.best) {
      this.dom.best.textContent = Number.isFinite(fitness) ? sig(fitness, 5) : "—";
    }
  }

  /**
   * Load the sample fold programme of the origami domain.
   *
   * It is the fallback of the viewer: the engine may not be connected yet,
   * but the folding viewer must still show a real fold programme.
   */
  async loadSampleFold() {
    const seed = this.dom.foldSeed ? Number(this.dom.foldSeed.value) || 0 : 0;
    const stepCount = this.dom.foldSteps ? Math.max(1, Number(this.dom.foldSteps.value) || 8) : 8;
    const strategy = this.dom.foldStrategy ? String(this.dom.foldStrategy.value || "families") : "families";
    this._setFoldNotice("折り方を読み込んでいます…", "");
    try {
      const [states, recipe] = await Promise.all([
        requestOrigamiGenomeSteps({ seed, stepCount, strategy }),
        requestOrigamiRecipe({ seed, stepCount, strategy }),
      ]);
      this.stepMeshes = normaliseStepMeshes(states.states);
      this.steps = normaliseSteps(recipe.steps, recipe);
      this.currentFold = {
        source: "sample",
        genome: recipe.genome || states.genome || null,
        markdown: recipe.markdown || "",
        svg: recipe.svg || "",
      };
      this._renderRecipe(this.steps);
      this._renderCreasePattern(recipe.svg || "", this.steps, recipe.genome);
      this._setFoldNotice(
        `サンプルの折り方 (seed=${seed}, ${stepCount}手) を表示しています`,
        "info"
      );
      const genome = recipe.genome || states.genome || null;
      if (genome) await this._evaluateGenome(genome);
      this._resetSlider();
    } catch (error) {
      this._setFoldNotice(
        "折り方を読み込めません: " + (error && error.message ? error.message : String(error)),
        "error"
      );
    }
  }

  /** Load the best individual the engine found. */
  async _loadGaFold() {
    if (!this.result) {
      this._setFoldNotice("GA結果はまだありません。先に研究を実行してください。", "warn");
      return;
    }
    const payload = this.result;
    this.stepMeshes = normaliseStepMeshes(payload.step_meshes);
    this.steps = normaliseSteps(payload.recipe, payload);
    this._renderRecipe(this.steps);
    const svgText = typeof payload.crease_pattern_svg === "string"
      ? payload.crease_pattern_svg
      : (payload.recipe && typeof payload.recipe.crease_pattern_svg === "string"
        ? payload.recipe.crease_pattern_svg
        : "");
    this.currentFold = {
      source: "ga",
      genome: payload.genome || null,
      markdown: (payload.recipe && payload.recipe.markdown) || "",
      svg: svgText,
    };
    this._renderCreasePattern(svgText, this.steps, payload.genome);
    this._setFoldNotice(`GA最良個体の折り方を表示 (run_id=${this.runId})`, "ok");
    if (payload.genome) await this._evaluateGenome(payload.genome);
    this._resetSlider();
  }

  /**
   * Ask the origami domain to evaluate a genome.
   *
   * The evaluation returns the centre of gravity and the grip report of the
   * folded sheet; both are shown in the viewer and marked in the 3D view.
   */
  async _evaluateGenome(genome) {
    try {
      const payload = await evaluateOrigamiGenome(genome);
      this._renderGrip(payload.grip, payload.geometry);
      this._applyMarkers(payload.grip, payload.geometry);
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      this._renderGrip(null, null, message);
      this._applyMarkers(null, null);
    }
  }

  _resetSlider() {
    const slider = this.dom.foldSlider;
    if (!slider) return;
    const max = Math.max(this.stepMeshes.length - 1, 0);
    slider.max = String(max);
    slider.value = String(max);
    this.selectStep(max);
    if (this.viewport && typeof this.viewport.focusResearch === "function") {
      this.viewport.focusResearch();
    }
  }

  /**
   * Show one step of the folding programme.
   *
   * Step 0 is the flat sheet, step n the finished aeroplane.  The crease of
   * the fold that has just been performed is highlighted in the crease
   * pattern and the matching row of the recipe is marked.
   */
  selectStep(index) {
    const max = Math.max(this.stepMeshes.length - 1, 0);
    const value = Math.min(Math.max(Math.round(index) || 0, 0), max);
    const slider = this.dom.foldSlider;
    if (slider) {
      slider.max = String(max);
      if (document.activeElement !== slider || Number(slider.value) !== value) {
        slider.value = String(value);
      }
    }
    this.activeStep = value > 0 ? value - 1 : -1;
    this._showMesh(value);
    this._highlightCrease(this.activeStep);
    this._highlightRecipeRow(this.activeStep);
    const label = this.dom.foldLabel;
    if (label) {
      if (value === 0) {
        label.textContent = "Step 0 — 展開紙 (flat sheet)";
      } else {
        const step = this.steps[value - 1];
        const name = step && (step.label || step.instruction) ? ` — ${step.label || step.instruction}` : "";
        label.textContent = `Step ${value} / ${max}${name}`;
      }
    }
  }

  _showMesh(index) {
    if (!this.viewport) return;
    const payload = this.stepMeshes[index];
    if (!payload) {
      this.viewport.clearResearchGeometry();
      return;
    }
    try {
      const geometry = buildOrigamiGeometry(payload);
      const bounds = payload.bounds || { min: [0, 0, 0], max: [0, 0, 0] };
      const semispan = Math.max(Math.abs(bounds.max[2] - bounds.min[2]) * 0.5, 1e-3);
      this.viewport.setResearchGeometry(geometry, semispan);
    } catch (error) {
      this.viewport.clearResearchGeometry();
    }
  }

  _highlightCrease(stepIndex) {
    if (!this.svgRoot) return;
    const nodes = this.svgRoot.querySelectorAll("[data-step]");
    for (const node of nodes) {
      const active = Number(node.getAttribute("data-step")) === stepIndex;
      node.classList.toggle("ga-crease-active", active);
    }
  }

  _highlightRecipeRow(stepIndex) {
    const body = this.dom.recipeBody;
    if (!body) return;
    for (const row of body.querySelectorAll("tr[data-step]")) {
      const active = Number(row.dataset.step) === stepIndex;
      row.classList.toggle("is-active", active);
      if (active && typeof row.scrollIntoView === "function") {
        row.scrollIntoView({ block: "nearest" });
      }
    }
  }

  /* ------------------------------------------------------------ rendering */

  _renderRecipe(steps) {
    const body = this.dom.recipeBody;
    if (!body) return;
    clear(body);
    if (!steps.length) {
      body.append(make("tr", {}, [
        make("td", { colspan: "6", class: "muted", text: "折り手順がありません" }),
      ]));
      return;
    }
    for (const step of steps) {
      const line = Array.isArray(step.crease_line_mm) ? step.crease_line_mm : null;
      const lineText = line
        ? `(${Number(line[0][0]).toFixed(1)}, ${Number(line[0][1]).toFixed(1)}) → (${Number(line[1][0]).toFixed(1)}, ${Number(line[1][1]).toFixed(1)})`
        : "—";
      const mountain = step.fold_type !== "valley";
      const symmetry = { symmetric: "左右対称", left: "左", right: "右" }[step.symmetry] || step.symmetry || "—";
      const targets = Array.isArray(step.target_face_ids) && step.target_face_ids.length
        ? step.target_face_ids.join(", ")
        : "—";
      const row = make("tr", { "data-step": String(step.step_index) }, [
        make("td", { class: "num", text: String(Number(step.step_index) + 1) }),
        make("td", { class: "num ga-recipe-line", text: lineText }),
        make("td", { text: `${symmetry}${mountain ? "山" : "谷"}` }),
        make("td", { class: "num", text: formatFoldAngle(step.fold_angle_deg) }),
        make("td", { class: "num", text: targets }),
        make("td", { text: step.instruction || step.layer_order || "—" }),
      ]);
      body.append(row);
    }
  }

  _renderCreasePattern(svgText, steps, genome) {
    const host = this.dom.creasePanel;
    if (!host) return;
    clear(host);
    if (!svgText) {
      host.append(make("p", { class: "ga-empty", text: "展開図がまだありません" }));
      this.svgRoot = null;
      return;
    }
    try {
      const root = renderCreasePattern(svgText, steps, genome);
      root.setAttribute("class", "ga-crease-svg");
      root.removeAttribute("width");
      root.removeAttribute("height");
      this.svgRoot = root;
      host.append(root);
      root.addEventListener("click", (event) => {
        const target = event.target.closest("[data-step]");
        if (!target) return;
        this.selectStep(Number(target.getAttribute("data-step")) + 1);
      });
      this._highlightCrease(this.activeStep);
    } catch (error) {
      this.svgRoot = null;
      host.append(make("p", {
        class: "ga-empty",
        text: "展開図を表示できません: " + (error && error.message ? error.message : String(error)),
      }));
    }
  }

  _renderThrowParams(throwParams) {
    const host = this.dom.throwParams;
    if (!host) return;
    clear(host);
    if (!throwParams || typeof throwParams !== "object" || !Object.keys(throwParams).length) {
      host.append(make("p", { class: "ga-empty", text: "投げパラメータは未取得です" }));
      return;
    }
    const list = make("div", { class: "ga-kv-list" });
    for (const [key, value] of Object.entries(throwParams)) {
      const numeric = typeof value === "number" && Number.isFinite(value);
      list.append(make("div", { class: "ga-kv" }, [
        make("span", { class: "ga-kv-k", text: THROW_LABELS[key] || key }),
        make("span", {
          class: "ga-kv-v num",
          text: numeric ? `${fmt(value, 3)} ${UNIT_BY_KEY[key] || ""}`.trim() : String(value),
        }),
      ]));
    }
    host.append(list);
  }

  _renderStats(stats, fitness) {
    const host = this.dom.stats;
    if (!host) return;
    clear(host);
    const entries = [];
    if (stats && typeof stats === "object") {
      const labels = {
        mean: "平均",
        median: "中央値",
        overall_median: "全体中央値",
        failure_rate: "失敗率",
      };
      for (const [key, value] of Object.entries(stats)) {
        entries.push([labels[key] || key, value]);
      }
    }
    if (fitness !== undefined && fitness !== null) entries.push(["適応度 fitness", fitness]);
    if (!entries.length) {
      host.append(make("p", { class: "ga-empty", text: "統計は未取得です" }));
      return;
    }
    const list = make("div", { class: "ga-kv-list" });
    for (const [key, value] of entries) {
      const numeric = typeof value === "number" && Number.isFinite(value);
      list.append(make("div", { class: "ga-kv" }, [
        make("span", { class: "ga-kv-k", text: key }),
        make("span", { class: "ga-kv-v num", text: numeric ? sig(value, 4) : String(value) }),
      ]));
    }
    host.append(list);
  }

  _renderGrip(grip, geometry, errorMessage) {
    const host = this.dom.gripSummary;
    if (!host) return;
    clear(host);
    if (!grip) {
      host.append(make("p", {
        class: "ga-empty",
        text: errorMessage
          ? `持ち手評価を取得できません: ${errorMessage}`
          : "持ち手評価は未取得です",
      }));
      return;
    }
    const rows = [
      ["判定", grip.valid ? "良好 (throwable)" : `要改善 (${(grip.reasons || []).join(", ") || "unknown"})`],
      ["持ち手高さ", `${fmt(Number(grip.grip_height_m) * 1000, 1)} mm`],
      ["持ち手長さ", `${fmt(Number(grip.grip_length_m) * 1000, 1)} mm`],
      ["重ね枚数", `${grip.grip_layers} 枚`],
      ["重心ずれ", `${fmt(Number(grip.cg_offset_m) * 1000, 1)} mm`],
    ];
    if (geometry && Array.isArray(geometry.cg_m)) {
      rows.push(["重心 CG", geometry.cg_m.map((value) => fmt(Number(value) * 1000, 1)).join(", ") + " mm"]);
    }
    const list = make("div", { class: "ga-kv-list" });
    for (const [key, value] of rows) {
      list.append(make("div", { class: "ga-kv" }, [
        make("span", { class: "ga-kv-k", text: key }),
        make("span", { class: "ga-kv-v", text: String(value) }),
      ]));
    }
    host.append(list);
  }

  _renderStatus(record) {
    const d = this.dom;
    const data = record || {};
    if (d.runId) d.runId.textContent = this.runId || "—";
    if (d.runStatus) {
      d.runStatus.textContent = String(data.status || (this.runId ? "起動中" : "待機中"));
      const state = String(data.status || "");
      d.runStatus.dataset.state = state;
    }
    if (d.elapsed) d.elapsed.textContent = fmt(Number(data.elapsed_s) || 0, 1);
    if (d.tier) d.tier.textContent = data.tier === undefined || data.tier === null ? "—" : String(data.tier);
    if (d.evaluations) d.evaluations.textContent = String(data.evaluations ?? 0);
    if (d.generation) {
      const generation = data.generation ?? 0;
      const generations = data.generations ?? this.params.generations;
      d.generation.textContent = `${generation} / ${generations}`;
    }
    if (d.best) {
      const fitness = Number(data.best_fitness);
      d.best.textContent = Number.isFinite(fitness) ? sig(fitness, 5) : "—";
    }
    if (d.objective) {
      d.objective.textContent = String(data.objective || this.params.objective || "—");
    }
  }

  _renderConfig(config) {
    if (!config || typeof config !== "object") return;
    for (const spec of PARAM_SPECS) {
      if (!(spec.key in config)) continue;
      const value = config[spec.key];
      if (value === null || value === undefined) continue;
      this.params[spec.key] = value;
    }
    this._syncParamState();
  }

  _renderIslands(islands) {
    const host = this.dom.islandList;
    if (!host) return;
    clear(host);
    if (!islands.length) {
      host.append(make("p", {
        class: "ga-empty",
        text: this.runId
          ? "島の要約は実行完了時に公開されます"
          : "島の情報はまだありません",
      }));
      return;
    }
    const table = make("table", { class: "ga-island-table" });
    table.append(make("thead", {}, [
      make("tr", {}, [
        make("th", { text: "島" }),
        make("th", { text: "個体数" }),
        make("th", { text: "最良" }),
        make("th", { text: "多様性" }),
      ]),
    ]));
    const body = make("tbody");
    for (const island of islands) {
      const diversity = Number(island.diversity);
      body.append(make("tr", {}, [
        make("td", { text: `#${island.id ?? "?"}` }),
        make("td", { class: "num", text: String(island.size ?? "—") }),
        make("td", { class: "num", text: isNum(Number(island.best)) ? sig(Number(island.best), 4) : "—" }),
        make("td", { class: "num", text: Number.isFinite(diversity) ? sig(diversity, 3) : "—" }),
      ]));
    }
    table.append(body);
    host.append(table);
  }

  _renderJournal(entries) {
    const host = this.dom.journal;
    if (!host) return;
    if (!entries.length) {
      if (!this.journalEntries) {
        clear(host);
        host.append(make("p", { class: "ga-empty", text: "研究ジャーナルはまだありません" }));
      }
      return;
    }
    if (entries.length < this.journalSeen) {
      // The journal was truncated (new run): start over.
      this.journalSeen = 0;
      this.journalEntries = 0;
    }
    if (this.journalEntries === 0) clear(host);
    for (let index = this.journalSeen; index < entries.length; index += 1) {
      const entry = entries[index];
      if (!entry || typeof entry !== "object") continue;
      const changes = entry.changes && typeof entry.changes === "object"
        ? Object.entries(entry.changes)
          .map(([key, value]) => `${key}=${typeof value === "number" ? sig(value, 4) : String(value)}`)
          .join(", ")
        : "";
      const row = make("div", { class: "ga-journal-row" }, [
        make("span", { class: "num ga-journal-time", text: String(entry.timestamp || "") }),
        make("span", { class: "num ga-journal-gen", text: `gen ${entry.generation ?? "?"}` }),
        make("span", { class: "num ga-journal-island", text: `island ${entry.island ?? "?"}` }),
        make("span", { class: "ga-journal-decision", text: String(entry.decision || "") }),
        make("span", { class: "muted ga-journal-trigger", text: entry.trigger ? `(${entry.trigger})` : "" }),
        make("span", { class: "muted ga-journal-changes", text: changes }),
      ]);
      host.append(row);
    }
    this.journalSeen = entries.length;
    this.journalEntries = entries.length;
    host.scrollTop = host.scrollHeight;
  }

  /** Publish the run id in the shared store (it survives a reload). */
  _setRunId(runId) {
    this.store.merge("research", { run_id: runId || null }, "research");
    try {
      if (runId) window.localStorage.setItem(RUN_KEY, String(runId));
      else window.localStorage.removeItem(RUN_KEY);
    } catch (error) {
      /* localStorage may be unavailable (private mode) */
    }
  }

  /** The run id of the last run of this browser. */
  _readStoredRunId() {
    try {
      return window.localStorage.getItem(RUN_KEY) || null;
    } catch (error) {
      return null;
    }
  }

  /* -------------------------------------------------------------- chart */

  /**
   * The Guinness reference the search aims at.
   *
   * The engine normalises the objective metric by the reference target, so
   * a fitness of 1.0 *is* the record: the chart draws the reference line at
   * 1.0 and names the metric it stands for.
   */
  _recordTarget() {
    const stats = this.result && this.result.stats ? this.result.stats : {};
    const base = RECORD_TARGETS[this.params.objective] || RECORD_TARGETS.distance;
    const reference = Number(stats.reference_target);
    if (Number.isFinite(reference) && reference > 0) {
      return {
        value: reference,
        fitness: 1.0,
        unit: base.unit,
        label: `ギネス記録 ${reference} ${base.unit}`,
      };
    }
    return { value: base.value, fitness: 1.0, unit: base.unit, label: base.label };
  }

  _updateTargetLabel() {
    const host = this.dom.targetNote;
    if (!host) return;
    const target = this._recordTarget();
    host.textContent = `目標: ${target.label}`;
  }

  /**
   * The size of the run the current parameters ask for.
   *
   * The engine executes every trial of the budget, so the number is shown
   * next to the start button instead of being discovered halfway through.
   */
  _updateBudgetNote() {
    const host = this.dom.budgetNote;
    if (!host) return;
    const total = Number(this.params.max_trials) || 0;
    const tier = (Number(this.params.tier1_trials) || 0)
      + (Number(this.params.tier2_trials) || 0)
      + (Number(this.params.tier3_trials) || 0);
    const generations = Number(this.params.generations) || 0;
    const islands = Number(this.params.islands) || 0;
    host.textContent =
      `実行予算: 総 ${total} 試行 (tier1+2+3 = ${tier}) / ${generations} 世代 / ${islands} 島`;
    host.dataset.tone = total > 10000 ? "warn" : "info";
  }

  _drawTrend() {
    const canvas = this.dom.chart;
    if (!canvas) return;
    const Ctor = window.Chart;
    if (!Ctor) {
      if (this.dom.chartEmpty) {
        this.dom.chartEmpty.hidden = false;
        this.dom.chartEmpty.textContent = "Chart.js を読み込めません";
      }
      return;
    }
    const points = Array.from(this.trend.entries()).sort((a, b) => a[0] - b[0]);
    const target = this._recordTarget();
    const labels = points.map(([generation]) => String(generation));
    const values = points.map(([, fitness]) => fitness);
    // The engine normalises the metric by the record, so the record sits at
    // a fitness of 1.0.
    const reference = points.map(() => target.fitness);
    if (!points.length) {
      if (this.dom.chartEmpty) {
        this.dom.chartEmpty.hidden = false;
        // The engine publishes the progress of every generation while the
        // run is still going, so the curve fills in as the generations
        // arrive.
        this.dom.chartEmpty.textContent = this.runId
          ? "世代データを待機中"
          : "まだ世代データがありません";
      }
      if (this.chart) {
        this.chart.data.labels = [];
        this.chart.data.datasets[0].data = [];
        this.chart.data.datasets[1].data = [];
        this.chart.update("none");
      }
      return;
    }
    if (this.dom.chartEmpty) this.dom.chartEmpty.hidden = true;
    if (this.chart) {
      this.chart.data.labels = labels;
      this.chart.data.datasets[0].data = values;
      this.chart.data.datasets[1].data = reference;
      this.chart.options.scales.y.title.text = "最良適応度 (ギネス記録 = 1.0)";
      this.chart.update("none");
      return;
    }
    this.chart = new Ctor(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "最良適応度",
            data: values,
            borderColor: "#1a73e8",
            backgroundColor: "rgba(26,115,232,0.14)",
            fill: true,
            tension: 0.25,
            pointRadius: 2.5,
            borderWidth: 2,
          },
          {
            label: `${target.label} (適応度 1.0)`,
            data: reference,
            borderColor: "#d93025",
            borderDash: [6, 4],
            borderWidth: 1.5,
            pointRadius: 0,
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: true, labels: { boxWidth: 10, font: { size: 9 } } },
          tooltip: {
            callbacks: {
              label: (item) => {
                if (item.datasetIndex === 1) return item.dataset.label;
                const value = Number(item.parsed.y);
                return `${item.dataset.label}: ${sig(value, 5)} (記録の ${(value * 100).toFixed(1)}%)`;
              },
            },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "世代 (generation)", font: { size: 9 } },
            grid: { color: "#eceae4" },
            ticks: { font: { size: 9 }, color: "#858a91" },
          },
          y: {
            title: { display: true, text: "最良適応度 (ギネス記録 = 1.0)", font: { size: 9 } },
            grid: { color: "#eceae4" },
            ticks: { font: { size: 9 }, color: "#858a91" },
          },
        },
      },
    });
  }

  /* ---------------------------------------------------------- checkpoint */

  async refreshCheckpoints() {
    if (!this.runId) {
      this._setCheckpointNotice("実行 ID がありません。先に研究を開始してください。", "warn");
      return;
    }
    try {
      const payload = await this.api.gaResearch.checkpoints(this.runId);
      this.available = true;
      const list = Array.isArray(payload && payload.checkpoints) ? payload.checkpoints : [];
      const host = this.dom.ckptList;
      if (host) {
        clear(host);
        if (!list.length) {
          host.append(make("p", { class: "ga-empty", text: "チェックポイントはまだありません" }));
        } else {
          const table = make("div", { class: "ga-kv-list" });
          for (const entry of list) {
            table.append(make("div", { class: "ga-kv" }, [
              make("span", { class: "ga-kv-k ga-ckpt-path", text: String(entry.path || entry.name || "—") }),
              make("span", {
                class: "ga-kv-v num",
                text: `gen ${entry.generation ?? "—"} · ${formatTimestamp(entry.created_at)}`,
              }),
            ]));
          }
          host.append(table);
        }
      }
      this._setCheckpointNotice(`チェックポイント ${list.length} 件を読み込みました`, "ok");
    } catch (error) {
      if (isMissingEndpoint(error)) {
        this._setCheckpointNotice(
          Number(error && error.status) === 405
            ? "サーバーにチェックポイント API が未実装です (GET /checkpoints/<run_id>)"
            : "この run_id のチェックポイントはまだサーバー上にありません",
          "warn"
        );
      } else {
        this._setCheckpointNotice(
          "チェックポイントを取得できません: " + (error && error.message ? error.message : String(error)),
          "error"
        );
      }
    }
  }

  async saveCheckpoint() {
    if (!this.runId) {
      this._setCheckpointNotice("実行 ID がありません。先に研究を開始してください。", "warn");
      return;
    }
    try {
      await this.api.gaResearch.saveCheckpoint(this.runId);
      this._setCheckpointNotice("チェックポイントの保存を要求しました", "ok");
      await this.refreshCheckpoints();
    } catch (error) {
      if (isMissingEndpoint(error)) {
        this._setCheckpointNotice(
          "サーバーにチェックポイント保存 API が未実装です (GET のみ)。保存済みのチェックポイントを表示します",
          "warn"
        );
        await this.refreshCheckpoints();
      } else {
        this._setCheckpointNotice(
          "チェックポイントを保存できません: " + (error && error.message ? error.message : String(error)),
          "error"
        );
      }
    }
  }

  async resume() {
    if (!this.runId) {
      this._setCheckpointNotice("再開元の run_id がありません", "warn");
      return;
    }
    // The engine honours these overrides on a resume: the trial budget, the
    // size of the hall of fame and the number of generations.
    const overrides = {};
    const maxTrials = this.dom.resumeMaxTrials ? Number(this.dom.resumeMaxTrials.value) : NaN;
    const topK = this.dom.resumeTopK ? Number(this.dom.resumeTopK.value) : NaN;
    const generations = this.dom.resumeGenerations ? Number(this.dom.resumeGenerations.value) : NaN;
    if (Number.isFinite(maxTrials) && maxTrials > 0) overrides.max_trials = maxTrials;
    if (Number.isFinite(topK) && topK > 0) overrides.top_k = topK;
    if (Number.isFinite(generations) && generations > 0) overrides.generations = generations;
    try {
      const payload = await this.api.gaResearch.resume({
        run_id: this.runId,
        ...overrides,
      });
      this.available = true;
      const resumedFrom = payload.resumed_from;
      const nextRun = String(payload.run_id || this.runId);
      this.runId = nextRun;
      this._setRunId(nextRun);
      this.status = payload;
      this._renderStatus(payload);
      this._renderConfig(payload.config);
      this.trend.clear();
      this.journalSeen = 0;
      this.journalEntries = 0;
      if (this.dom.journal) clear(this.dom.journal);
      if (this.dom.stop) this.dom.stop.disabled = false;
      this._setCheckpointNotice(
        `再開しました: ${resumedFrom ? `${resumedFrom} から ` : ""}run_id=${nextRun} / 上書き ` +
        Object.entries(overrides).map(([key, value]) => `${key}=${value}`).join(", ") || "上書きなし",
        "ok"
      );
      this.notify(`GA研究を再開しました (run_id=${nextRun})`, "ok");
      this._startPolling();
    } catch (error) {
      if (isMissingEndpoint(error)) {
        this._setCheckpointNotice(
          "サーバーに再開 API が未実装です (POST /resume)",
          "error"
        );
      } else {
        this._setCheckpointNotice(
          "再開できません: " + (error && error.message ? error.message : String(error)),
          "error"
        );
      }
    }
  }

  /* ------------------------------------------------------------- export */

  /**
   * The fold programme that is currently on screen.
   *
   * Either the best individual of the run or the sample programme of the
   * origami domain.  The exports always write what the user sees.
   */
  exportPayload() {
    const fold = this.currentFold || {};
    if (this.result) {
      return {
        source: "ga",
        run_id: this.runId,
        objective: this.params.objective,
        genome: this.result.genome || fold.genome || null,
        recipe: {
          steps: this.steps,
          markdown: (this.result.recipe && this.result.recipe.markdown) || fold.markdown || "",
        },
        crease_pattern_svg: this.result.crease_pattern_svg || fold.svg || "",
        throw_params: this.result.throw_params || null,
        stats: this.result.stats || null,
        fitness: this.result.fitness ?? null,
      };
    }
    return {
      source: "sample",
      run_id: null,
      objective: this.params.objective,
      genome: fold.genome || null,
      recipe: { steps: this.steps, markdown: fold.markdown || "" },
      crease_pattern_svg: fold.svg || "",
      throw_params: null,
      stats: null,
      fitness: null,
    };
  }

  exportJson() {
    const payload = this.exportPayload();
    if (!payload.genome && !payload.recipe.steps.length) {
      this.notify("書き出す折り方がありません", "warn");
      return;
    }
    downloadText(
      `ga_genome_${this.runId || "sample"}.json`,
      "application/json",
      safeJSON(payload)
    );
    this.notify(
      payload.source === "ga" ? "GA 結果を JSON で書き出しました" : "折り方を JSON で書き出しました",
      "ok"
    );
  }

  exportMarkdown() {
    const payload = this.exportPayload();
    if (!payload.recipe.steps.length) {
      this.notify("書き出す折り方がありません", "warn");
      return;
    }
    const throwParams = payload.throw_params || {};
    const lines = [];
    if (payload.recipe.markdown) lines.push(payload.recipe.markdown);
    lines.push("");
    lines.push("## 最適な投げ方 / Optimal throw parameters");
    lines.push("");
    if (Object.keys(throwParams).length) {
      lines.push("| 項目 | 値 |");
      lines.push("|---|---|");
      for (const [key, value] of Object.entries(throwParams)) {
        const label = THROW_LABELS[key] || key;
        const shown = typeof value === "number" && Number.isFinite(value)
          ? `${fmt(value, 3)} ${UNIT_BY_KEY[key] || ""}`.trim()
          : String(value);
        lines.push(`| ${label} (${key}) | ${shown} |`);
      }
    } else {
      lines.push("投げパラメータは未取得です (GA結果待ち)。");
    }
    lines.push("");
    lines.push("## 展開図 / Crease pattern");
    lines.push("");
    lines.push("`crease_pattern_svg` は JSON 書き出しに含まれます。");
    downloadText(
      `ga_recipe_${this.runId || "sample"}.md`,
      "text/markdown",
      lines.join("\n")
    );
    this.notify("折り手順を Markdown で書き出しました", "ok");
  }

  exportSvg() {
    if (!this.svgRoot) {
      this.notify("書き出す展開図がありません", "warn");
      return;
    }
    const source = new XMLSerializer().serializeToString(this.svgRoot);
    downloadText(`ga_crease_pattern_${this.runId || "sample"}.svg`, "image/svg+xml", source);
    this.notify("展開図を SVG で書き出しました", "ok");
  }

  /* ------------------------------------------------------------ notices */

  _setNotice(text, tone) {
    const host = this.dom.notice;
    if (!host) return;
    host.textContent = text || "";
    host.hidden = !text;
    host.dataset.tone = tone || "info";
  }

  _setFoldNotice(text, tone) {
    const host = this.dom.foldNotice;
    if (!host) return;
    host.textContent = text || "";
    host.dataset.tone = tone || "info";
  }

  _setCheckpointNotice(text, tone) {
    const host = this.dom.ckptNotice;
    if (!host) return;
    host.textContent = text || "";
    host.dataset.tone = tone || "info";
  }

  /**
   * Report that the GA engine is not reachable.
   *
   * The panel keeps working (the folding viewer, the exports) and states
   * plainly that the engine is missing instead of pretending to run.
   */
  _noteUnavailable(error) {
    if (isMissingEndpoint(error)) {
      const first = this.available !== false;
      this.available = false;
      if (this.dom.run) this.dom.run.disabled = true;
      if (this.dom.resume) this.dom.resume.disabled = true;
      if (this.dom.stop) this.dom.stop.disabled = true;
      if (!first) return;
      this.unavailableReason = "GA研究エンジンが未接続です (404 / 405)";
      this._setNotice(
        `${this.unavailableReason}: ${this.api.gaResearch.base}/run が存在しません。` +
        "エンジンが実装されるまで研究は実行できません。",
        "error"
      );
    } else if (error && Number(error.status) === 503) {
      this.available = false;
      this.unavailableReason = "GA研究エンジンのバックエンドが利用できません (503)";
      this._setNotice(this.unavailableReason, "error");
      if (this.dom.run) this.dom.run.disabled = true;
    }
  }

  /* ------------------------------------------------------------ markers */

  _applyMarkers(grip, geometry) {
    if (!this.viewport) return;
    const cg = geometry && Array.isArray(geometry.cg_m)
      ? geometry.cg_m
      : (grip && Array.isArray(grip.cg_m) ? grip.cg_m : null);
    let keel = null;
    if (grip && Array.isArray(grip.grip_x_range_m)) {
      const bounds = geometry && geometry.bounds ? geometry.bounds : null;
      const yMin = bounds && Array.isArray(bounds.min) ? Number(bounds.min[1]) : 0;
      const height = Math.max(Number(grip.grip_height_m) || 0, 0.002);
      keel = {
        x_min: Number(grip.grip_x_range_m[0]) || 0,
        x_max: Number(grip.grip_x_range_m[1]) || 0,
        z_half: 0.012,
        y_min: yMin,
        y_max: yMin + height,
      };
    }
    this.viewport.setResearchMarkers({ cg, keel });
    this.viewport.setResearchMarkersVisible(this.markersEnabled);
  }
}
