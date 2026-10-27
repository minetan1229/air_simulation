/*
 * app.js — composition root.
 *
 * Boots the schema/snapshot handshake, builds every panel, and wires the
 * configuration rail, the 3D viewport, the recorder, the workbench and the
 * transport together.  Server writes are debounced and per-section so dragging
 * a slider never produces a request storm.
 */

import { api, ApiError } from "./api.js";
import { airframeSummary } from "./airframe.js";
import { DataPanel } from "./datapanel.js";
import { FlowField } from "./flowfield.js";
import { GaResearchPanel } from "./ga_research.js";
import {
  $,
  clear,
  debounce,
  escapeHtml,
  fmt,
  make,
  paintRamp,
  paintRange,
  sig,
} from "./lib.js";
import { MeshPanel } from "./meshpanel.js";
import { OptimizerPanel } from "./optimizer.js";
import { InvestigatorPanel } from "./investigator.js";
import { ParameterPanels } from "./panels.js";
import {
  ORIGAMI_ARCHETYPES,
  buildOrigamiGeometry,
  requestOrigamiMesh,
  requestOrigamiGenomeSteps,
} from "./origami.js";
import { store } from "./state.js";
import { Gauges, PolarChart, StripChart } from "./telemetry.js";
import { RATES, Transport } from "./transport.js";
import { Viewport } from "./viewport.js";

const GEOMETRY_KEYS = [
  "wingspan_m",
  "wing_area_m2",
  "taper_ratio",
  "dihedral_deg",
  "fold_leading_deg",
  "fold_trailing_deg",
  "fold_tip_deg",
  "fold_center_deg",
  "elevon_deg",
  "aileron_deg",
  "asymmetry_cl",
  "shape_flag",
];

/** localStorage key of the last console mode (simulation / research). */
const MODE_KEY = "paper-airplane-console:mode";

/**
 * Fold progression quantum of the origami slider.
 *
 * The server folds the whole sheet for every request, so a finer quantum only
 * adds traffic without changing the visible geometry.  Two percent keeps the
 * drag responsive while the neighbouring angles stay meaningful (§G-6).
 */
const ORIGAMI_ALPHA_STEP = 0.02;

/** The workbench tabs of the simulation mode, in the order they appear. */
const SIMULATION_TABS = ["investigate", "optimizer", "guinness", "model", "data"];

/** Flight-status dot colours (CSS custom properties from console.css). */
const FLIGHT_STATUS_COLOR = {
  ready: "var(--accent)",
  flying: "var(--ok)",
  landed: "var(--ink-2)",
  error: "var(--bad)",
};

class ConsoleApp {
  constructor() {
    this.store = store;
    this.dom = {};
    this.dirty = new Set();
    this.flightDirty = false;
    this.tunnelMode = false;
    this.geomSignature = "";
    this.mode = "simulation";
    this._activeTab = "investigate";
    this._simulationTab = "investigate";
    this._pushDebounced = debounce(() => this._push(), 180);
    this._polarDebounced = debounce(() => this.refreshPolar(), 340);
    this._skipFirstRank = false;
    this._lastEnsembleResult = null;
  }

  /* ---------------------------------------------------------------- boot */

  async boot() {
    this._cacheDom();
    paintRamp($("#heat-ramp"));
    this._bindGroups();
    this._bindWorkbench();
    this._bindTransport();
    this._bindHeader();
    this._bindKeyboard();
    this._bindModeSwitch();
    this._initChampionActions();

    this.toastHost = $("#toasts");

    try {
      const [schema, snapshot] = await Promise.all([api.schemas(), api.snapshot()]);
      this.meta = schema;
      this.store.setSchema(schema);
      this.store.adopt(snapshot);
      // Local-only origami view state (never pushed to the flight backend).
      this.store.merge(
        "origami",
        {
          enabled: false,
          archetype: "classic_dart",
          progression: 1.0,
        },
        "init"
      );
      // Local-only GA research state (never pushed to the flight backend).
      this.store.merge("research", { mode: this._readStoredMode() }, "init");
    } catch (error) {
      this.notify(`サーバに接続できません: ${this._describe(error)}`, "error");
      return;
    }

    this.panels = new ParameterPanels({
      store: this.store,
      root: document,
      onChange: (section, key, value) => this._onFieldChange(section, key, value),
      onCommit: (section) => this._onCommit(section),
      onLaunch: () => this._launchFlight(),
    });
    this.panels.build();

    this.gauges = new Gauges($("#gauges"), GAUGE_SPECS);
    this.airGauges = new Gauges($("#gauges-air"), AIR_SPECS);
    this.recorder = new StripChart($("#rec-canvas"), {
      onCursor: (sample) => {
        const node = this.dom["rec-cursor"];
        if (!node) return;
        node.textContent = sample
          ? `t ${fmt(sample.time_s, 3)}s  ${fmt(sample.altitude_m, 2)}m  ${fmt(sample.speed_mps, 2)}m/s  α ${fmt(sample.alpha_deg, 1)}°  ${fmt(sample.max_stress_pa, 0)}Pa`
          : "—";
      },
    });
    this.polar = new PolarChart($("#polar-canvas"));

    this.transport = new Transport({ api, store: this.store });
    this.transport.attach();
    this._bindTransportEvents();

    this.viewport = this._createViewport();
    this._bindViewportToggles();
    this._placeAirframeAtLaunch();

    this.optimizer = new OptimizerPanel({
      api,
      store: this.store,
      dom: {
        objectives: $("#opt-objectives"),
        variables: $("#opt-variables"),
        settings: $("#opt-settings"),
        run: $("#opt-run"),
        cancel: $("#opt-cancel"),
        csv: $("#opt-csv"),
        adopt: $("#opt-adopt"),
        chart: $("#opt-chart"),
        chartEmpty: $("#opt-chart-empty"),
        best: $("#opt-best"),
        jobs: $("#opt-jobs"),
        fill: $("#opt-fill"),
        count: $("#opt-count"),
        rate: $("#opt-rate"),
        eta: $("#eta-widget"),
        etaClock: $("#eta-clock"),
        etaLeft: $("#eta-left"),
        etaRow: $("#opt-eta-row"),
        etaRowClock: $("#opt-eta-clock"),
        etaRowLeft: $("#opt-eta-left"),
        frontAxes: $("#opt-front-axes"),
      },
      notify: (message, tone) => this.notify(message, tone),
      setGlobalEta: (opts) => this.setGlobalEta(opts),
      onError: (opts) => this.showSimulationError(opts),
    });
    this.optimizer.mount(this.meta);

    this.investigator = new InvestigatorPanel({
      api,
      store: this.store,
      dom: {
        run: $("#inv-run"),
        cancel: $("#inv-cancel"),
        adopt: $("#inv-adopt"),
        targetSelect: $("#inv-target-select"),
        progFill: $("#inv-fill"),
        progText: $("#inv-prog-pct"),
        statusText: $("#inv-status"),
        etaRow: $("#inv-eta-row"),
        etaRowClock: $("#inv-eta-clock"),
        etaRowLeft: $("#inv-eta-left"),
        resultArea: $("#inv-result-area"),
        emptyPrompt: $("#inv-empty-prompt"),
        diagBadge: $("#inv-diag-badge"),
        diagTitle: $("#inv-diag-title"),
        diagDesc: $("#inv-diag-desc"),
        compDistBase: $("#inv-comp-dist-base"),
        compDistRec: $("#inv-comp-dist-rec"),
        compDistGain: $("#inv-comp-dist-gain"),
        compTimeBase: $("#inv-comp-time-base"),
        compTimeRec: $("#inv-comp-time-rec"),
        compTimeGain: $("#inv-comp-time-gain"),
        adviceHost: $("#inv-advice-list"),
        sensHost: $("#inv-sens-list"),
      },
      notify: (message, tone) => this.notify(message, tone),
      setGlobalEta: (opts) => this.setGlobalEta(opts),
      onError: (opts) => this.showSimulationError(opts),
    });
    this.investigator.mount();

    this.meshPanel = new MeshPanel({
      api,
      store: this.store,
      dom: {
        drop: $("#mesh-drop"),
        file: $("#mesh-file"),
        url: $("#mesh-url"),
        fetch: $("#mesh-fetch"),
        options: $("#mesh-opts"),
        list: $("#mesh-list"),
        metrics: $("#mesh-metrics"),
      },
      viewport: this.viewport,
      notify: (message, tone) => this.notify(message, tone),
    });
    this.meshPanel.mount();

    this.dataPanel = new DataPanel({
      api,
      store: this.store,
      dom: {
        presetList: $("#preset-list"),
        presetSave: $("#preset-save"),
        presetDelete: $("#preset-del"),
        exportOptions: $("#export-opts"),
        exportRun: $("#export-run"),
        preview: $("#export-preview"),
        bundleDrop: $("#bundle-drop"),
        bundleApply: $("#bundle-apply"),
        paste: $("#bundle-paste"),
      },
      notify: (message, tone) => this.notify(message, tone),
    });
    this.dataPanel.mount();

    // The GA research state is local: it travels with the saved configuration
    // file but never reaches the flight backend.
    const baseSnapshot = this.dataPanel.snapshot.bind(this.dataPanel);
    this.dataPanel.snapshot = () => {
      const bundle = baseSnapshot();
      bundle.research = { ...(this.store.data.research || {}) };
      return bundle;
    };

    this.gaResearch = new GaResearchPanel({
      api,
      store: this.store,
      dom: {
        params: $("#ga-params"),
        notice: $("#ga-notice"),
        resultNote: $("#ga-result-note"),
        budgetNote: $("#ga-budget-note"),
        run: $("#ga-run"),
        stop: $("#ga-stop"),
        runId: $("#ga-run-id"),
        runStatus: $("#ga-run-status"),
        elapsed: $("#ga-elapsed"),
        tier: $("#ga-tier"),
        evaluations: $("#ga-evaluations"),
        generation: $("#ga-generation"),
        best: $("#ga-best"),
        // 読み取り専用の「目標」表示。#ga-objective は GA パラメータ表が
        // 生成する select の id なので、ここは別 id にする（id 重複回避）。
        objective: $("#ga-objective-value"),
        targetNote: $("#ga-target-note"),
        outputDir: $("#ga-output-dir"),
        foldSource: $("#ga-fold-source"),
        foldSeed: $("#ga-fold-seed"),
        foldSteps: $("#ga-fold-steps"),
        foldStrategy: $("#ga-fold-strategy"),
        foldSlider: $("#ga-fold-slider"),
        foldLabel: $("#ga-fold-label"),
        foldPrev: $("#ga-fold-prev"),
        foldNext: $("#ga-fold-next"),
        foldNotice: $("#ga-fold-notice"),
        markerToggle: $("#ga-marker-toggle"),
        creasePanel: $("#ga-crease-panel"),
        recipeBody: $("#ga-recipe-body"),
        exportJson: $("#ga-export-json"),
        exportMd: $("#ga-export-md"),
        exportSvg: $("#ga-export-svg"),
        throwParams: $("#ga-throw-params"),
        stats: $("#ga-stats"),
        gripSummary: $("#ga-grip-summary"),
        chart: $("#ga-chart"),
        chartEmpty: $("#ga-chart-empty"),
        islandList: $("#ga-island-list"),
        journal: $("#ga-journal"),
        ckptList: $("#ga-ckpt-list"),
        ckptNotice: $("#ga-ckpt-notice"),
        ckptRefresh: $("#ga-ckpt-refresh"),
        ckptSave: $("#ga-ckpt-save"),
        resume: $("#ga-resume"),
        resumeMaxTrials: $("#ga-resume-max-trials"),
        resumeTopK: $("#ga-resume-top-k"),
        resumeGenerations: $("#ga-resume-generations"),
      },
      notify: (message, tone) => this.notify(message, tone),
      viewport: this.viewport,
    });
    this.gaResearch.mount().catch((error) => {
      this.notify(`GA研究パネルを初期化できません: ${this._describe(error)}`, "error");
    });

    this.store.on("change", (event) => this._onStoreChange(event));
    this.store.on("mesh", () => this._adoptServerAirframe());

    this._syncGeometry();
    await this.refreshAir();
    await this.refreshPolar();
    this._updateTransportUi(this.transport.state);
    this._updateModes();
    await this._initGuinnessPresets();
    this._updateLaunchVector();
    await this._detectGpu();
    this.setMode(this._readStoredMode());
  }

  _cacheDom() {
    const ids = [
      "view-canvas",
      "vp-notice",
      "vp-notice-msg",
      "sim-error-banner",
      "sim-error-icon",
      "sim-error-title",
      "sim-error-badge",
      "sim-error-msg",
      "sim-error-hint",
      "sim-error-close",
      "eta-widget",
      "eta-clock",
      "eta-left",
      "inv-eta-row",
      "inv-eta-clock",
      "inv-eta-left",
      "opt-eta-row",
      "opt-eta-clock",
      "opt-eta-left",
      "flight-pill",
      "rec-count",
      "rec-cursor",
      "btn-rec-clear",
      "btn-polar",
      "polar-cdmin",
      "polar-ldmax",
      "polar-trim",
      "gauge-position",
      "gauge-rema",
      "top-state",
      "top-clock",
      "top-range",
      "top-backend",
      "top-fps",
      "readout-solver",
      "readout-render",
      "gpu-modal",
      "gpu-modal-close",
      "gpu-modal-close-x",
      "modal-solver-name",
      "modal-solver-desc",
      "modal-render-name",
      "modal-render-desc",
      "gpu-status-solver-card",
      "gpu-status-render-card",
      "gpu-warning-box",
      "btn-copy-gpu-cmd",
      "gpu-ps-command",
      "btn-force-gpu-chrome",
      "btn-force-gpu-edge",
      "top-ghost",
      "btn-top-launch",
      "btn-top-investigate",
      "btn-top-record",
      "record-preset-select",
      "record-card",
      "record-badge",
      "record-value",
      "record-name",
      "record-holder",
      "record-technique",
      "record-notes",
      "rec-speed",
      "rec-angle",
      "rec-height",
      "rec-style",
      "unified-aircraft-select",
      "unified-mesh-file",
      "btn-unified-mesh-import",
      "unified-model-card",
      "unified-model-badge",
      "unified-model-metric",
      "unified-model-title",
      "unified-model-desc",
      "f-hud-alt",
      "f-hud-alt-peak",
      "f-hud-spd",
      "f-hud-spd-peak",
      "f-hud-dist",
      "f-hud-time",
      "f-hud-glide",
      "f-hud-aoa",
      "f-hud-phase",
      "flight-result-modal",
      "f-res-status-sub",
      "f-res-dist",
      "f-res-time",
      "f-res-alt-max",
      "f-res-spd-max",
      "f-res-glide",
      "f-res-gload",
      "f-res-stress",
      "f-res-btn-relaunch",
      "f-res-btn-dismiss",
      "f-res-btn-close",
      "flow-backend",
      "flow-count",
      "hud-alpha",
      "hud-beta",
      "hud-speed",
      "hud-q",
      "hud-flow",
      "hud-ld-live",
      "hud-glide-ratio",
      "hud-cl",
      "hud-cd",
      "hud-ld",
      "hud-cm",
      "hud-tunnel-status",
      "hud-tunnel-aoa",
      "hud-tunnel-speed",
      "tunnel-slider-aoa",
      "tunnel-slider-aoa-val",
      "tunnel-slider-speed",
      "tunnel-slider-speed-val",
      "cam-mode-mean",
      "cam-mode-lead",
      "cam-mode-fleet",
      "cam-mode-fixed",
      "cam-mode-swarm",
      "tgl-ens-tracks",
      "tgl-ens-mean-only",
      "champ-btn-focus-mean",
      "champ-btn-download-json",
      "champ-btn-download-csv",
      "tunnel-hud",
      "tunnel-toggle",
      "tp-glide",
      "tp-play",
      "tp-play-label",
      "tp-prev",
      "tp-next",
      "tp-reset",
      "tp-rate",
      "tp-scrub",
      "tp-frame",
      "tp-total",
      "tp-time",
      "tp-flight",
      "tp-dist",
      "wb-toggle",
      "help",
      "help-close",
      "btn-help",
      "btn-save",
      "btn-load",
      "btn-csv",
      "config-file",
      "btn-config-reset",
      "bundle-paste",
      "geom-summary",
    "origami-summary",
      "origami-shape-warning",
      "vp-flight-status-announcer",
      "status-dot",
      "status-text",
      "ensemble-champion-card",
      "champ-best-dist",
      "champ-best-detail",
      "champ-best-time",
      "champ-best-time-detail",
      "champ-best-alt",
      "champ-best-alt-detail",
      "champ-avg-dist",
      "champ-avg-range",
      "champ-worst-dist",
      "champ-worst-detail",
      "champ-worst-time",
      "champ-worst-time-detail",
      "champ-btn-adopt",
      "champ-btn-focus",
      "champ-btn-skip-top1",
      "champ-label-skip-top1",
      "champ-icon-skip-top1",
      "champ-skip-status-badge",
      "champ-promoted-tag",
      "champ-btn-focus-worst",
      "champ-btn-toggle-fleet",
      "champ-close-btn",
      "champ-leaderboard-body",
      "champ-backend-sub",
    ];
    for (const id of ids) this.dom[id] = document.getElementById(id);
  }

  _createViewport() {
    try {
      const viewport = new Viewport($("#view-canvas"), {
        onStats: (stats) => this._onStats(stats),
      });
      viewport.setAirframe(this.store.data.aircraft);
      return viewport;
    } catch (error) {
      console.error(error);
      const message = this.dom["vp-notice-msg"];
      if (message) message.textContent = String(error && error.message ? error.message : error);
      if (this.dom["vp-notice"]) this.dom["vp-notice"].hidden = false;
      return null;
    }
  }

  /* -------------------------------------------------------------- chrome */

  _bindGroups() {
    for (const group of document.querySelectorAll(".group")) {
      // Panels without a data-group attribute are informational and open.
      const open = group.hasAttribute("data-group")
        ? group.hasAttribute("open")
        : true;
      group.dataset.open = open ? "1" : "0";
      const head = group.querySelector(".group-head");
      if (!head || head.classList.contains("static")) continue;
      head.addEventListener("click", () => {
        group.dataset.open = group.dataset.open === "1" ? "0" : "1";
        head.setAttribute("aria-expanded", group.dataset.open === "1" ? "true" : "false");
      });
    }
    for (const rail of document.querySelectorAll(".rail")) {
      const head = rail.querySelector(".rail-head");
      if (!head) continue;
      const body = rail.querySelector(".rail-body");
      if (body) body.inert = true;
      rail.setAttribute("aria-hidden", "true");
      head.addEventListener("click", (event) => {
        if (event.target.closest("button") || event.target.closest(".pill")) return;
        if (window.innerWidth > 900) return;
        this._setRailOpen(rail, !rail.classList.contains("is-open"));
      });
    }
    // Below 900 px the rails are off-canvas drawers and their header leaves
    // the viewport with them, so the drawer needs a trigger that stays
    // visible.  Without it the whole configuration panel is unreachable on a
    // tablet or a phone.
    for (const button of document.querySelectorAll(".rail-toggle")) {
      const rail = document.getElementById(button.getAttribute("aria-controls"));
      if (!rail) continue;
      button.addEventListener("click", () => {
        if (window.innerWidth > 900) return;
        this._setRailOpen(rail, !rail.classList.contains("is-open"));
      });
    }
  }

  /**
   * Open or close one of the off-canvas rails.
   *
   * Only one drawer fits on a narrow viewport, so opening one closes the
   * other.  The state is mirrored into ``aria-expanded`` of the trigger and
   * into ``aria-hidden`` / ``inert`` of the rail, so a screen reader is told
   * about the change as well.
   */
  _setRailOpen(rail, open) {
    rail.classList.toggle("is-open", open);
    const body = rail.querySelector(".rail-body");
    if (body) body.inert = !open;
    rail.setAttribute("aria-hidden", String(!open));
    for (const button of document.querySelectorAll(".rail-toggle")) {
      const target = document.getElementById(button.getAttribute("aria-controls"));
      if (target === rail) {
        button.setAttribute("aria-expanded", String(open));
      } else if (open) {
        // The other drawer has to give way: it would cover the open one.
        target.classList.remove("is-open");
        const otherBody = target.querySelector(".rail-body");
        if (otherBody) otherBody.inert = true;
        target.setAttribute("aria-hidden", "true");
        const otherButton = document.querySelector(
          `.rail-toggle[aria-controls="${target.id}"]`,
        );
        if (otherButton) otherButton.setAttribute("aria-expanded", "false");
      }
    }
  }

  _bindWorkbench() {
    const tabs = Array.from(document.querySelectorAll(".wb-tab"));
    for (const tab of tabs) {
      tab.addEventListener("click", () => {
        this.setWorkbench(true);
        this._activeTab = tab.dataset.tab;
        if (this._activeTab !== "research") this._simulationTab = this._activeTab;
        for (const other of tabs) {
          other.setAttribute("aria-selected", other === tab ? "true" : "false");
        }
        for (const pane of document.querySelectorAll(".wb-pane")) {
          pane.hidden = pane.dataset.pane !== tab.dataset.tab;
        }
        if (tab.dataset.tab === "optimizer" && this.optimizer && this.optimizer.chart) {
          this.optimizer.chart.resize();
        }
        if (tab.dataset.tab === "research" && this.gaResearch && this.gaResearch.chart) {
          this.gaResearch.chart.resize();
        }
      });
    }
    const activeTab = tabs.find((t) => t.getAttribute("aria-selected") === "true") || tabs[0];
    if (activeTab) {
      this._activeTab = activeTab.dataset.tab;
      this._simulationTab = this._activeTab;
      for (const pane of document.querySelectorAll(".wb-pane")) {
        pane.hidden = pane.dataset.pane !== activeTab.dataset.tab;
      }
    }
    this.dom["wb-toggle"].addEventListener("click", () => this.setWorkbench(!this._workbenchOpen()));
    this.setWorkbench(false);

    if (this.dom["eta-widget"]) {
      this.dom["eta-widget"].style.cursor = "pointer";
      this.dom["eta-widget"].title = "クリックで完了予想表示を待機中にリセット";
      this.dom["eta-widget"].addEventListener("click", () => {
        this.stopEtaCountdown();
        this.setGlobalEta({ clock: "—", left: "待機中", active: false, title: "計算完了予想時間 (ETA)" });
      });
    }
  }

  /* ------------------------------------------------------------- modes */

  /**
   * The last console mode.
   *
   * The mode is a purely local view state: it is remembered in the shared
   * store, in localStorage (so a reload keeps it) and in the saved
   * configuration file.
   */
  _readStoredMode() {
    try {
      const raw = window.localStorage.getItem(MODE_KEY);
      if (raw === "research" || raw === "simulation") return raw;
    } catch (error) {
      /* localStorage may be unavailable (private mode) */
    }
    const stored = this.store && this.store.get("research", "mode");
    if (stored === "research" || stored === "simulation") return stored;
    return "simulation";
  }

  _bindModeSwitch() {
    this.modeButtons = {
      simulation: document.getElementById("btn-mode-simulation"),
      research: document.getElementById("btn-mode-research"),
    };
    for (const [mode, button] of Object.entries(this.modeButtons)) {
      if (!button) continue;
      button.addEventListener("click", () => this.setMode(mode));
    }
  }

  /**
   * Switch between the simulation console and the autonomous research mode.
   *
   * The two modes really show different tools: the simulation mode keeps the
   * flight rail, the flight instruments and the transport, the research mode
   * hands the whole stage over to the GA workbench.
   */
  setMode(mode) {
    const next = mode === "research" ? "research" : "simulation";
    this.mode = next;
    document.body.dataset.mode = next;
    for (const [key, button] of Object.entries(this.modeButtons || {})) {
      if (button) button.classList.toggle("active", key === next);
    }
    this.store.set("research", "mode", next, "ui");
    try {
      window.localStorage.setItem(MODE_KEY, next);
    } catch (error) {
      /* localStorage may be unavailable (private mode) */
    }
    if (next === "research") {
      if (this._activeTab !== "research" && SIMULATION_TABS.includes(this._activeTab)) {
        this._simulationTab = this._activeTab;
      }
      this.setWorkbench(true);
      const tab = document.getElementById("wb-tab-research");
      if (tab) tab.click();
    } else {
      const wanted = SIMULATION_TABS.includes(this._simulationTab) ? this._simulationTab : "investigate";
      const tab = document.getElementById(`wb-tab-${wanted}`);
      if (tab) tab.click();
    }
    if (this.viewport) requestAnimationFrame(() => this.viewport.resize());
    if (this.viewport && typeof this.viewport.setResearchPresentation === "function") {
      this.viewport.setResearchPresentation(next === "research");
      const followToggle = document.getElementById("tgl-follow");
      if (followToggle) followToggle.dataset.on = this.viewport.follow ? "1" : "0";
      if (next === "research") {
        this._setCameraChipActive("cam-mode-fixed");
      } else {
        const previous = this.viewport.previousCameraMode();
        if (previous) this._setCameraChipActive(`cam-mode-${previous}`);
      }
    }
  }

  /**
   * Restore the GA research section of a loaded configuration file.
   *
   * The server only knows the flight sections, so the research block is
   * applied on the client.
   */
  _applyResearchBundle(text) {
    let bundle = null;
    try {
      bundle = JSON.parse(text);
    } catch (error) {
      return;
    }
    const research = bundle && bundle.research;
    if (!research || typeof research !== "object") return;
    this.store.merge("research", research, "load");
    if (this.gaResearch && typeof this.gaResearch.applyStoreParams === "function") {
      this.gaResearch.applyStoreParams();
    }
    const mode = research.mode === "research" ? "research" : "simulation";
    this.setMode(mode);
  }

  _workbenchOpen() {
    return $("#workbench").dataset.open === "1";
  }

  setWorkbench(open) {
    const workbench = $("#workbench");
    workbench.dataset.open = open ? "1" : "0";
    this.dom["wb-toggle"].setAttribute("aria-expanded", open ? "true" : "false");
    if (this.viewport) requestAnimationFrame(() => this.viewport.resize());
  }

  _bindTransport() {
    const rates = $("#tp-rate");
    clear(rates);
    for (const rate of RATES) {
      const button = make("button", {
        class: "rate-btn",
        type: "button",
        "data-rate": rate,
        text: `${rate}×`,
        "aria-pressed": rate === 1 ? "true" : "false",
      });
      button.addEventListener("click", () => this.transport.setRate(rate));
      rates.append(button);
    }

    this.dom["tp-play"].addEventListener("click", () => this._togglePlay());
    this.dom["tp-prev"].addEventListener("click", () => this._guard(() => this.transport.stepBack()));
    this.dom["tp-next"].addEventListener("click", () => this._guard(() => this.transport.stepForward()));
    this.dom["tp-reset"].addEventListener("click", () => this._guard(() => this._resetFlight(true)));

    const scrub = this.dom["tp-scrub"];
    scrub.addEventListener("input", () => {
      paintRange(scrub);
      const index = Number(scrub.value);
      this._guard(() => this.transport.seek(index));
    });

    this.dom["btn-rec-clear"].addEventListener("click", () => {
      this.recorder.clear();
      this.dom["rec-count"].textContent = "0";
      this.dom["rec-cursor"].textContent = "—";
      if (this.viewport) this.viewport.clearTrack();
    });

    this.dom["btn-polar"].addEventListener("click", () => this.refreshPolar());
  }

  _bindTransportEvents() {
    const t = this.transport;

    // The transport already rate-limits to 60 steps/s, so every frame is used
    // for the sample record; only the canvas repaint is coalesced.
    t.on("frame", (frame) => this._onFrame(frame));
    t.on("state", (state) => this._updateTransportUi(state));
    t.on("reset", () => this._onReset());
    t.on("replay", (sample) => this._onReplay(sample));
    t.on("stress", (payload) => {
      if (this.viewport) this.viewport.setStress(payload);
    });
    t.on("wind", (payload) => {
      if (!this.viewport) return;
      this.field = this.field || new FlowField();
      this.field.ingest(payload);
      this.viewport.setWindField(this.field);
      this.dom["flow-backend"].textContent = `flow: ${this.viewport.particleBackend()}`;
      this.dom["flow-count"].textContent = `${payload.grid_point_count} pts · ${payload.backend}`;
      this.dom["hud-flow"].textContent = `${fmt(payload.mean_speed, 3)} m/s`;
    });
    t.on("metrics", (metrics) => this._onMetrics(metrics));
    t.on("ended", (frame) => this._onEnded(frame));
    t.on("error", (error) => {
      this.showSimulationError({
        title: "シミュレーション計算エラー",
        message: error.message || String(error),
        hint: "機体パラメータの設定値や通信状態をご確認ください。",
        badge: "ERROR",
      });
      this.notify(`シミュレーションエラー: ${error.message}`, "error");
    });
  }

  _bindViewportToggles() {
    const toggles = [
      ["tgl-wind", (on) => this.viewport && this.viewport.setParticlesVisible(on), true],
      ["tgl-track", (on) => this.viewport && this.viewport.setTrackVisible(on), true],
      ["tgl-ghost", (on) => this.viewport && this.viewport.setGhostVisible(on), true],
      ["tgl-heat", (on) => this.viewport && this.viewport.setHeatVisible(on), true],
      ["tgl-panels", (on) => this.viewport && this.viewport.setPanelsVisible(on), false],
      ["tgl-grip", (on) => this.viewport && this.viewport.setGripVisible(on), true],
      ["tgl-follow", (on) => this.viewport && this.viewport.setFollow(on), true],
    ];
    for (const [id, apply, initial] of toggles) {
      const node = document.getElementById(id);
      if (!node) continue;
      if (initial) node.dataset.on = "1";
      apply(node.dataset.on === "1");
      node.addEventListener("click", () => {
        const on = node.dataset.on !== "1";
        node.dataset.on = on ? "1" : "0";
        apply(on);
      });
    }
    const reset = document.getElementById("btn-view-reset");
    if (reset) reset.addEventListener("click", () => {
      this._setCameraChipActive("btn-view-reset");
      if (this.viewport) this.viewport.resetCamera();
    });

    const camModes = [
      { id: "cam-mode-mean", mode: "mean" },
      { id: "cam-mode-lead", mode: "lead" },
      { id: "cam-mode-fleet", mode: "fleet" },
      { id: "cam-mode-fixed", mode: "fixed" },
      { id: "cam-mode-swarm", mode: "swarm" },
    ];
    for (const { id, mode } of camModes) {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener("click", () => {
          this._setCameraChipActive(id);
          if (this.viewport) this.viewport.setCameraMode(mode);
        });
      }
    }

    // Toggle 100-plane ensemble tracks
    const tglTracks = document.getElementById("tgl-ens-tracks");
    if (tglTracks) {
      tglTracks.addEventListener("click", () => {
        const on = tglTracks.dataset.on !== "0";
        const next = on ? "0" : "1";
        tglTracks.dataset.on = next;
        tglTracks.classList.toggle("active", next === "1");
        if (this.viewport) this.viewport.setEnsembleTracksVisible(next === "1");
      });
    }

    // Toggle Mean Only vs All Tracks
    const tglMeanOnly = document.getElementById("tgl-ens-mean-only");
    if (tglMeanOnly) {
      tglMeanOnly.addEventListener("click", () => {
        const current = tglMeanOnly.dataset.mode || "all";
        if (current === "all") {
          tglMeanOnly.dataset.mode = "mean_only";
          tglMeanOnly.textContent = "平均機のみ";
          tglMeanOnly.classList.add("active");
          if (this.viewport) this.viewport.filterEnsembleTracks("mean_only");
        } else {
          tglMeanOnly.dataset.mode = "all";
          tglMeanOnly.textContent = "全機表示";
          tglMeanOnly.classList.remove("active");
          if (this.viewport) this.viewport.filterEnsembleTracks("all");
        }
      });
    }

    this._initTunnelControls();
    this._initUnifiedAircraftSelector();

    if (this.dom["sim-error-close"]) {
      this.dom["sim-error-close"].addEventListener("click", () => this.hideSimulationError());
    }

    // Bind flight result modal buttons
    if (this.dom["f-res-btn-relaunch"]) {
      this.dom["f-res-btn-relaunch"].addEventListener("click", () => {
        this.hideFlightResultModal();
        this._launchFlight();
      });
    }
    if (this.dom["f-res-btn-dismiss"]) {
      this.dom["f-res-btn-dismiss"].addEventListener("click", () => this.hideFlightResultModal());
    }
    if (this.dom["f-res-btn-close"]) {
      this.dom["f-res-btn-close"].addEventListener("click", () => this.hideFlightResultModal());
    }

    // Bind recording buttons
    const recBtns = [this.dom["btn-top-record"]].filter(Boolean);
    recBtns.forEach((btn) => {
      btn.addEventListener("click", () => this._toggleRecording());
    });
  }

  _toggleRecording() {
    if (!this.viewport) return;
    if (this.viewport.isRecording()) {
      this.viewport.stopRecording();
    } else {
      const ok = this.viewport.startRecording((recording) => {
        this._updateRecordButtons(recording);
      });
      if (ok) {
        this.notify("動画録画を開始しました（停止時にWebM動画が保存されます）", "info");
      } else {
        this.notify("録画を開始できませんでした", "warn");
      }
    }
  }

  _updateRecordButtons(isRecording) {
    const btns = [this.dom["btn-top-record"]].filter(Boolean);
    btns.forEach((btn) => {
      if (isRecording) {
        btn.classList.add("is-recording");
        btn.innerHTML = `<img src="https://api.iconify.design/material-symbols/stop-circle.svg?color=%23d93025" class="ui-icon" alt=""> 停止 (REC)`;
        btn.title = "動画録画を停止してファイル保存";
      } else {
        btn.classList.remove("is-recording");
        btn.innerHTML = `<img src="https://api.iconify.design/material-symbols/videocam.svg?color=%23d93025" class="ui-icon" alt=""> 録画`;
        btn.title = "3D飛行動画を録画して保存 (WebM/MP4)";
      }
    });
  }

  async _runGpuEnsemble(count = 100) {
    if (this._launching || this.transport.busy) {
      console.warn("[App] Ensemble/Launch already running; ignoring duplicate request.");
      return;
    }
    this._launching = true;
    this._setLaunchButtonsDisabled(true);

    if (this.tunnelMode) this.setTunnel(false);
    this.hideSimulationError();
    const tStart = performance.now();
    this.startEtaCountdown({
      clock: "GPU計算中...",
      title: `GPU並列シミュレーション (${count}機同時計算) を実行中...`,
    });

    try {
      await this._guard(async () => {
        this.notify(`GPU一括並列シミュレーション (${count}機同時計算) を実行中...`, "info");

        const currentAircraft = this.store.data.aircraft || {};
        const currentEnv = this.store.data.environment || {};
        const currentLaunch = this.store.data.launch || {};
        const currentSettings = this.store.data.simulation || {};

        const res = await api.ensemble({
          aircraft: currentAircraft,
          environment: currentEnv,
          launch: currentLaunch,
          settings: { ...currentSettings, duration_s: 25.0, dt_max: 0.01 },
          n_trajectories: count,
          max_sample_tracks: count,
        });

        const elapsedSec = ((performance.now() - tStart) / 1000).toFixed(2);
        this.stopEtaCountdown();
        this.setGlobalEta({
          clock: `${elapsedSec}s`,
          left: "GPU完了",
          active: false,
          title: `GPU 100機同時シミュレーション完了 (所要時間: ${elapsedSec}秒)`,
        });

        if (!res || !res.sample_trajectories) {
          throw new Error("アンサンブルシミュレーションの結果が空です");
        }

        // Render trajectories and 100-plane InstancedMesh in Three.js viewport
        if (this.viewport && res.sample_trajectories.length) {
          this.viewport.setEnsembleTracks(res.sample_trajectories, this._skipFirstRank, res.mean_trajectory);
          this.viewport.filterEnsembleTracks("all");
          this.viewport.setEnsembleFleet(res.sample_trajectories, this._skipFirstRank, res.mean_trajectory);
          this.viewport.setCameraMode("mean");
          this._setCameraChipActive("cam-mode-mean");
        }

        const backendStr = res.backend_display || res.backend || "cupy";
        const meanDist = Number(res.mean_distance_m || 0);
        const maxDist = Number(res.max_distance_m || 0);
        const meanTime = Number(res.mean_flight_time_s || 0);
        const maxTime = Number(res.max_flight_time_s || 0);

        this.notify(
          `GPU 100機同時飛行完了 (${res.trajectories_count || count}機 · ${backendStr})\n最大飛距離: ${maxDist.toFixed(2)}m (平均: ${meanDist.toFixed(2)}m) · 最大滞空: ${maxTime.toFixed(2)}s (平均: ${meanTime.toFixed(2)}s)`,
          "ok"
        );

        // Auto-save simulation data with date and timestamp
        this._autoSaveEnsembleData(res, "json", false);

        // Show comprehensive champion metrics & leaderboard HUD
        this._showEnsembleChampionHud(res);
        this._updateFlightStatus("landed", `100機同時飛行完了 — 1位最高飛距離: ${maxDist.toFixed(2)}m (平均: ${meanDist.toFixed(2)}m)`);
      });
    } finally {
      this._launching = false;
      this._setLaunchButtonsDisabled(false);
      this.stopEtaCountdown();
      const widget = this.dom["eta-widget"];
      if (widget && widget.dataset.active === "1") {
        const elapsedSec = ((performance.now() - tStart) / 1000).toFixed(2);
        this.setGlobalEta({
          clock: `${elapsedSec}s`,
          left: "待機中",
          active: false,
        });
      }
    }

  }

  async _autoSaveEnsembleData(res, format = "json", isUserRequested = false) {
    try {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
      const filename = `sim_data_${stamp}.${format}`;

      const payload = {
        executed_at: now.toISOString(),
        timestamp_formatted: stamp,
        parameters: {
          aircraft: this.store.data.aircraft || {},
          environment: this.store.data.environment || {},
          launch: this.store.data.launch || {},
          simulation: this.store.data.simulation || {},
        },
        mean_trajectory: res.mean_trajectory || null,
        statistics: res.statistics || {},
        trajectories_count: res.trajectories_count || 100,
        mean_distance_m: res.mean_distance_m,
        max_distance_m: res.max_distance_m,
        mean_flight_time_s: res.mean_flight_time_s,
        max_flight_time_s: res.max_flight_time_s,
        sample_trajectories: res.sample_trajectories || [],
        leaderboard: res.leaderboard || [],
        bottomboard: res.bottomboard || [],
      };

      // Call server data export endpoint
      const exportRes = await api.export({
        kind: "ensemble",
        format,
        data: payload,
      });

      if (exportRes && exportRes.payload) {
        const mime = format === "csv" ? "text/csv" : "application/json";
        const fname = exportRes.filename || filename;
        const blob = new Blob([exportRes.payload], { type: mime });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 2000);

        const notifyMsg = isUserRequested
          ? `シミュレーションデータを保存しました: ${fname}`
          : `📁 100機シミュレーションデータを自動保存しました: ${fname}`;
        this.notify(notifyMsg, "ok");
      }
    } catch (err) {
      console.warn("[App] Auto-save ensemble data failed:", err);
      // Fallback local client save if API fails
      try {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
        const filename = `sim_data_${stamp}.json`;
        const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        this.notify(`📁 シミュレーションデータを自動保存しました: ${filename}`, "ok");
      } catch (clientErr) {
        console.error("[App] Fallback client save also failed:", clientErr);
      }
    }
  }

  _showEnsembleChampionHud(res) {
    if (!res) return;
    this._lastEnsembleResult = res;
    const card = this.dom["ensemble-champion-card"];
    if (!card) return;

    // Full trajectories list sorted descending by distance
    const fullList = (res.sample_trajectories && res.sample_trajectories.length)
      ? [...res.sample_trajectories].sort((a, b) => (Number(b.landing_distance_m) || 0) - (Number(a.landing_distance_m) || 0))
      : (res.leaderboard ? [...res.leaderboard] : []);

    const isSkipping = Boolean(this._skipFirstRank && fullList.length > 1);
    const skippedFlight = isSkipping ? fullList[0] : null;
    const effectiveList = isSkipping ? fullList.slice(1) : fullList;

    const bestFlight = effectiveList.length ? effectiveList[0] : (res.best_flight || {});
    const bestTimeFlight = res.best_hangtime || {};
    const bestAltFlight = res.best_altitude || {};

    const worstFlight = res.worst_flight || (fullList.length ? fullList[fullList.length - 1] : {});
    const worstTimeFlight = res.worst_hangtime || {};

    const maxDist = Number(bestFlight.landing_distance_m || res.max_distance_m || 0);
    const meanDist = Number(res.mean_distance_m || 0);
    const minDist = Number(res.min_distance_m || worstFlight.landing_distance_m || 0);
    const maxTime = Number(res.max_flight_time_s || bestTimeFlight.flight_time_s || 0);
    const meanTime = Number(res.mean_flight_time_s || 0);
    const minTime = Number(res.min_flight_time_s || worstTimeFlight.flight_time_s || 0);
    const maxAlt = Number(bestAltFlight.max_altitude_m || 0);

    const bestSpeed = bestFlight.speed_mps != null ? bestFlight.speed_mps : "—";
    const bestPitch = bestFlight.pitch_deg != null ? bestFlight.pitch_deg : "—";
    const bestRoll = bestFlight.roll_deg != null ? bestFlight.roll_deg : "—";
    const bestId = bestFlight.id != null ? bestFlight.id : "—";

    const worstSpeed = worstFlight.speed_mps != null ? worstFlight.speed_mps : "—";
    const worstPitch = worstFlight.pitch_deg != null ? worstFlight.pitch_deg : "—";
    const worstRoll = worstFlight.roll_deg != null ? worstFlight.roll_deg : "—";
    const worstId = worstFlight.id != null ? worstFlight.id : "—";

    // 1. 最高値 (Max)
    if (this.dom["champ-best-dist"]) this.dom["champ-best-dist"].textContent = maxDist.toFixed(2);
    if (this.dom["champ-best-detail"]) {
      const extraLabel = isSkipping ? ` (※元1位 #${skippedFlight.id} 除外中)` : "";
      this.dom["champ-best-detail"].textContent = `機体 #${bestId}${extraLabel} · 初速: ${bestSpeed} m/s · 仰角: ${bestPitch}° · ロール: ${bestRoll}°`;
    }
    if (this.dom["champ-promoted-tag"]) {
      this.dom["champ-promoted-tag"].style.display = isSkipping ? "inline-block" : "none";
    }
    if (this.dom["champ-skip-status-badge"]) {
      this.dom["champ-skip-status-badge"].style.display = isSkipping ? "inline-block" : "none";
      if (isSkipping && skippedFlight) {
        this.dom["champ-skip-status-badge"].textContent = `⚠️ 元1位(#${skippedFlight.id} ${Number(skippedFlight.landing_distance_m || 0).toFixed(2)}m) スキップ中 · 2位(#${bestId})を最高記録に繰り上げ`;
      }
    }

    // Update skip button UI state
    const skipBtn = this.dom["champ-btn-skip-top1"];
    const skipLbl = this.dom["champ-label-skip-top1"];
    if (skipBtn) {
      if (isSkipping) {
        skipBtn.classList.add("active");
        if (skipLbl) skipLbl.textContent = "↩️ 1位を含める (元に戻す)";
        skipBtn.title = `現在、異常値の機体 #${skippedFlight.id} を除外して2位(機体 #${bestId})を繰り上げています。クリックで元に戻します。`;
      } else {
        skipBtn.classList.remove("active");
        if (skipLbl) skipLbl.textContent = "1位をスキップ (2位繰上)";
        skipBtn.title = "1位のデータに異常値がある場合に除外（スキップ）し、2位を繰り上げます";
      }
    }

    if (this.dom["champ-best-time"]) this.dom["champ-best-time"].textContent = maxTime.toFixed(2);
    if (this.dom["champ-best-time-detail"]) {
      this.dom["champ-best-time-detail"].textContent = `機体 #${bestTimeFlight.id ?? "—"} · 飛距離: ${Number(bestTimeFlight.landing_distance_m || 0).toFixed(2)}m`;
    }
    if (this.dom["champ-best-alt"]) this.dom["champ-best-alt"].textContent = maxAlt.toFixed(2);
    if (this.dom["champ-best-alt-detail"]) {
      this.dom["champ-best-alt-detail"].textContent = `機体 #${bestAltFlight.id ?? "—"}`;
    }

    // 2. 平均値 (Mean / Average)
    if (this.dom["champ-avg-dist"]) this.dom["champ-avg-dist"].textContent = meanDist.toFixed(2);
    if (this.dom["champ-avg-range"]) {
      this.dom["champ-avg-range"].textContent = `平均滞空: ${meanTime.toFixed(2)}s (飛距離 分散: 最小 ${minDist.toFixed(2)}m 〜 最大 ${maxDist.toFixed(2)}m)`;
    }

    // 3. 最低値 (Min / Worst)
    if (this.dom["champ-worst-dist"]) this.dom["champ-worst-dist"].textContent = minDist.toFixed(2);
    if (this.dom["champ-worst-detail"]) {
      this.dom["champ-worst-detail"].textContent = `機体 #${worstId} · 初速: ${worstSpeed} m/s · 仰角: ${worstPitch}° · ロール: ${worstRoll}°`;
    }
    if (this.dom["champ-worst-time"]) this.dom["champ-worst-time"].textContent = minTime.toFixed(2);
    if (this.dom["champ-worst-time-detail"]) {
      this.dom["champ-worst-time-detail"].textContent = `機体 #${worstTimeFlight.id ?? "—"} · 飛距離: ${Number(worstTimeFlight.landing_distance_m || 0).toFixed(2)}m`;
    }

    if (this.dom["champ-backend-sub"]) {
      const skipNote = isSkipping ? " [1位除外中]" : "";
      this.dom["champ-backend-sub"].textContent = `${res.backend_display || res.backend || "CuPy GPU"} · 計 ${res.trajectories_count || 100}機${skipNote}`;
    }

    // Leaderboard table body
    const tbody = this.dom["champ-leaderboard-body"];
    if (tbody) {
      tbody.innerHTML = "";
      const totalCount = res.trajectories_count || (res.sample_trajectories ? res.sample_trajectories.length : 100);

      // スキップ中なら元の1位を除外行として明示表示
      if (isSkipping && skippedFlight) {
        const trSkip = document.createElement("tr");
        trSkip.className = "clickable champ-rank-skipped";
        trSkip.innerHTML = `
          <td><strong>🚫 [除外] 元1位</strong></td>
          <td>#${skippedFlight.id}</td>
          <td><strong>${Number(skippedFlight.landing_distance_m || 0).toFixed(2)} m</strong></td>
          <td>${Number(skippedFlight.flight_time_s || 0).toFixed(2)} s</td>
          <td>${Number(skippedFlight.max_altitude_m || 0).toFixed(2)} m</td>
          <td>${skippedFlight.speed_mps != null ? skippedFlight.speed_mps : "—"} m/s</td>
          <td>${skippedFlight.pitch_deg != null ? skippedFlight.pitch_deg : "—"}°</td>
        `;
        trSkip.title = `除外中の元1位 機体 #${skippedFlight.id} (クリックでフォーカス)`;
        trSkip.addEventListener("click", () => {
          if (this.viewport && skippedFlight.id != null) {
            this.viewport.focusTrajectory(skippedFlight.id);
            this.notify(`除外中の元1位 機体 #${skippedFlight.id} (${Number(skippedFlight.landing_distance_m || 0).toFixed(2)}m) にカメラをフォーカスしました`, "info");
          }
        });
        tbody.appendChild(trSkip);
      }

      const topList = effectiveList.slice(0, 5);

      topList.forEach((item, idx) => {
        const tr = document.createElement("tr");
        tr.className = `clickable ${idx === 0 ? "champ-rank-1" : ""}`;
        const rankMedal = idx === 0
          ? (isSkipping ? "🥇 1位 (繰上最高)" : "🥇 1位 (最高)")
          : idx === 1 ? "🥈 2位" : idx === 2 ? "🥉 3位" : `${idx + 1}位`;
        tr.innerHTML = `
          <td><strong>${rankMedal}</strong></td>
          <td>#${item.id}</td>
          <td><strong>${Number(item.landing_distance_m || 0).toFixed(2)} m</strong></td>
          <td>${Number(item.flight_time_s || 0).toFixed(2)} s</td>
          <td>${Number(item.max_altitude_m || 0).toFixed(2)} m</td>
          <td>${item.speed_mps != null ? item.speed_mps : "—"} m/s</td>
          <td>${item.pitch_deg != null ? item.pitch_deg : "—"}°</td>
        `;
        tr.title = `クリックして機体 #${item.id} にカメラをフォーカス`;
        tr.addEventListener("click", () => {
          if (this.viewport && item.id != null) {
            this.viewport.focusTrajectory(item.id);
            this.notify(`機体 #${item.id} (${rankMedal}: ${Number(item.landing_distance_m || 0).toFixed(2)}m) にカメラをフォーカスしました`, "info");
          }
        });
        tbody.appendChild(tr);
      });

      // 最低値 (最下位機体) をリスト末尾に追加表示
      if (worstFlight && worstFlight.id != null && (topList.length === 0 || worstFlight.id !== topList[0].id)) {
        const trWorst = document.createElement("tr");
        trWorst.className = "clickable champ-rank-worst";
        const worstRankLabel = `🔻 ${worstFlight.rank || totalCount}位 (最低)`;
        trWorst.innerHTML = `
          <td><strong>${worstRankLabel}</strong></td>
          <td>#${worstFlight.id}</td>
          <td><strong>${Number(worstFlight.landing_distance_m || minDist).toFixed(2)} m</strong></td>
          <td>${Number(worstFlight.flight_time_s || 0).toFixed(2)} s</td>
          <td>${Number(worstFlight.max_altitude_m || 0).toFixed(2)} m</td>
          <td>${worstFlight.speed_mps != null ? worstFlight.speed_mps : "—"} m/s</td>
          <td>${worstFlight.pitch_deg != null ? worstFlight.pitch_deg : "—"}°</td>
        `;
        trWorst.title = `クリックして最下位機体 #${worstFlight.id} にカメラをフォーカス`;
        trWorst.addEventListener("click", () => {
          if (this.viewport && worstFlight.id != null) {
            this.viewport.focusTrajectory(worstFlight.id);
            this.notify(`最下位 機体 #${worstFlight.id} (${Number(worstFlight.landing_distance_m || minDist).toFixed(2)}m) にカメラをフォーカスしました`, "info");
          }
        });
        tbody.appendChild(trWorst);
      }
    }

    this._lastChampionFlight = bestFlight;
    this._lastWorstFlight = worstFlight;
    card.hidden = false;
  }

  _updateFlightStatus(status, text) {
    const dot = this.dom["status-dot"];
    const txt = this.dom["status-text"];
    if (!dot || !txt) return;

    dot.className = `status-dot ${status}`;
    dot.dataset.state = status;
    dot.style.backgroundColor = FLIGHT_STATUS_COLOR[status] || "var(--line-2)";
    txt.textContent = text;
  }

  /** Champion card actions (ensemble leaderboard, throw adoption, focus). */
  _initChampionActions() {
    // Champion Card Actions
    if (this.dom["champ-close-btn"]) {
      this.dom["champ-close-btn"].addEventListener("click", () => {
        if (this.dom["ensemble-champion-card"]) this.dom["ensemble-champion-card"].hidden = true;
      });
    }
    // ESCキーでモーダル・カードを閉じる
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (this.dom["ensemble-champion-card"] && !this.dom["ensemble-champion-card"].hidden) {
          this.dom["ensemble-champion-card"].hidden = true;
        }
      }
    });
    if (this.dom["champ-btn-adopt"]) {
      this.dom["champ-btn-adopt"].addEventListener("click", () => {
        if (!this._lastChampionFlight) return;
        const f = this._lastChampionFlight;
        const speed = Number(f.speed_mps);
        const pitch = Number(f.pitch_deg);
        const roll = Number(f.roll_deg);
        if (!Number.isFinite(speed) || !Number.isFinite(pitch) || !Number.isFinite(roll)) {
          this.notify("1位の機体の投げ方データ (初速・仰角・ロール) が取得できないため反映できません", "warn");
          return;
        }

        // 投げ方パラメータの正は左レール「投擲設定」だけなので、
        // ここでもストアへ直接書き込み、同じ範囲補正を適用する。
        const appliedSpeed = this._clampLaunchParam("launch_speed", speed);
        const appliedPitch = this._clampLaunchParam("launch_angle_deg", pitch);
        const appliedRoll = this._clampLaunchParam("launch_roll_deg", roll);
        if (appliedSpeed == null || appliedPitch == null || appliedRoll == null) {
          this.notify("投げ方設定が利用できないため、1位の投げ方を設定に反映できません", "warn");
          return;
        }

        this.store.set("launch", "launch_speed", appliedSpeed, "adopt");
        this.store.set("launch", "launch_angle_deg", appliedPitch, "adopt");
        this.store.set("launch", "launch_roll_deg", appliedRoll, "adopt");
        // 直接指定の初速・角を適用するので、投げ方も直接指定へ寄せる。
        this.store.set("launch", "throw_speed_source", "direct", "adopt");
        this.store.set("launch", "throw_style", "direct", "adopt");
        if (this.panels) this.panels.sync();

        const rankTag = this._skipFirstRank ? "繰り上げ1位" : "1位";
        this.notify(`${rankTag} (機体 #${f.id ?? "—"}) の投げ方を設定に反映しました！ (初速: ${appliedSpeed}m/s, 仰角: ${appliedPitch}°, ロール: ${appliedRoll}°)`, "ok");
      });
    }
    if (this.dom["champ-btn-focus-mean"]) {
      this.dom["champ-btn-focus-mean"].addEventListener("click", () => {
        if (this.viewport) {
          this.viewport.focusMeanTrajectory();
          this.viewport.setCameraMode("mean");
          this._setCameraChipActive("cam-mode-mean");
          this.notify("代表平均機 (Mean Aircraft) にフォーカスしました", "info");
        }
      });
    }
    if (this.dom["champ-btn-download-json"]) {
      this.dom["champ-btn-download-json"].addEventListener("click", () => {
        if (!this._lastEnsembleResult) return;
        this._autoSaveEnsembleData(this._lastEnsembleResult, "json", true);
      });
    }
    if (this.dom["champ-btn-download-csv"]) {
      this.dom["champ-btn-download-csv"].addEventListener("click", () => {
        if (!this._lastEnsembleResult) return;
        this._autoSaveEnsembleData(this._lastEnsembleResult, "csv", true);
      });
    }
    if (this.dom["champ-btn-focus"]) {
      this.dom["champ-btn-focus"].addEventListener("click", () => {
        if (!this._lastChampionFlight || this._lastChampionFlight.id == null) return;
        if (this.viewport) {
          this.viewport.focusTrajectory(this._lastChampionFlight.id);
          const rankTag = this._skipFirstRank ? "繰り上げ1位" : "1位";
          this.notify(`${rankTag} (機体 #${this._lastChampionFlight.id}) にフォーカスしました`, "info");
        }
      });
    }
    if (this.dom["champ-btn-skip-top1"]) {
      this.dom["champ-btn-skip-top1"].addEventListener("click", () => {
        this._skipFirstRank = !this._skipFirstRank;
        if (this._lastEnsembleResult) {
          this._showEnsembleChampionHud(this._lastEnsembleResult);
        }
        if (this.viewport) {
          this.viewport.setSkipFirstRank(this._skipFirstRank);
        }
        const msg = this._skipFirstRank
          ? "1位のデータを除外（スキップ）し、2位を最高記録に繰り上げました"
          : "1位のデータ除外を解除し、元のランキングに戻しました";
        this.notify(msg, "ok");
      });
    }
    if (this.dom["champ-btn-focus-worst"]) {
      this.dom["champ-btn-focus-worst"].addEventListener("click", () => {
        if (!this._lastWorstFlight || this._lastWorstFlight.id == null) return;
        if (this.viewport) {
          this.viewport.focusTrajectory(this._lastWorstFlight.id);
          this.notify(`最下位 (機体 #${this._lastWorstFlight.id}) にフォーカスしました`, "info");
        }
      });
    }
    let ensembleFleetMode = "top1";
    if (this.dom["champ-btn-toggle-fleet"]) {
      this.dom["champ-btn-toggle-fleet"].textContent = "表示: 1位のみ (少しずつ飛ばす ⇄)";
      this.dom["champ-btn-toggle-fleet"].addEventListener("click", () => {
        if (!this.viewport) return;
        if (ensembleFleetMode === "top1") {
          ensembleFleetMode = "stagger";
          this.dom["champ-btn-toggle-fleet"].textContent = "表示: 少しずつ順次飛行中 (上位3機へ)";
          this.viewport.filterEnsembleTracks("stagger");
          this.notify("少しずつ順次軌跡を表示中...", "info");
        } else if (ensembleFleetMode === "stagger") {
          ensembleFleetMode = "top3";
          this.dom["champ-btn-toggle-fleet"].textContent = "表示: 上位3機のみ (全100機へ)";
          this.viewport.filterEnsembleTracks("top3");
        } else if (ensembleFleetMode === "top3") {
          ensembleFleetMode = "all";
          this.dom["champ-btn-toggle-fleet"].textContent = "表示: 全100機 (1位のみへ戻す)";
          this.viewport.filterEnsembleTracks("all");
        } else {
          ensembleFleetMode = "top1";
          this.dom["champ-btn-toggle-fleet"].textContent = "表示: 1位のみ (少しずつ飛ばす ⇄)";
          this.viewport.filterEnsembleTracks("top1");
        }
      });
    }
  }

  /**
   * Clamp a launch parameter to the range the console exposes for it.
   * The bounds come from the parameter panel (single source of truth), so a
   * value adopted from a flight result can never exceed what the UI allows.
   * Returns null when the parameter is not exposed by the panel.
   */
  _clampLaunchParam(key, value) {
    const control = (this.panels?.controls || []).find(
      (entry) => entry.section === "launch" && entry.spec.key === key && entry.kind === "slider"
    );
    if (!control) return null;
    const raw = Number(value);
    if (!Number.isFinite(raw)) return null;
    const min = Number.isFinite(control.min) ? control.min : -Infinity;
    const max = Number.isFinite(control.max) ? control.max : Infinity;
    return Math.min(Math.max(raw, min), max);
  }

  async _detectGpu() {
    try {
      const gpu = await api.gpuInfo();
      if (gpu && gpu.available) {
        this.gpuInfo = gpu;
        const name = gpu.device_name || "GPU";
        const shortName = name.replace("NVIDIA GeForce ", "").replace(" Laptop GPU", "");
        if (this.dom["top-backend"]) {
          this.dom["top-backend"].textContent = `${gpu.backend} (${shortName})`;
          this.dom["top-backend"].title = `物理演算: ${name} (CUDA ${gpu.cuda_version || "12.x"})\nクリックしてGPU診断・切り替えガイドを表示`;
          this.dom["top-backend"].classList.add("gpu-active");
        }
      }
    } catch (e) {
      console.warn("GPU検出エラー:", e);
    }
  }

  _openGpuModal() {
    if (!this.dom["gpu-modal"]) return;
    const solverGpu = this.gpuInfo;
    const renderGpu = (this.viewport && this.viewport.gpuInfo) || { shortName: "WebGL", raw: "Unknown", isIntel: false, isNvidia: false };

    // Update solver card
    if (this.dom["modal-solver-name"]) {
      if (solverGpu && solverGpu.available) {
        const vramStr = solverGpu.total_memory_mb ? ` · VRAM: ${solverGpu.total_memory_mb}MB` : "";
        this.dom["modal-solver-name"].textContent = `[OK] ${solverGpu.device_name || "GPU"} (${solverGpu.backend})`;
        this.dom["modal-solver-desc"].textContent = `CUDA ${solverGpu.cuda_version || ""} · 物理演算(6DoF RK4)・オプティマイザ(NSGA-II)・風場計算で正常にフル稼働中${vramStr}`;
        if (this.dom["gpu-status-solver-card"]) {
          this.dom["gpu-status-solver-card"].className = "gpu-status-card status-ok";
        }
      } else {
        this.dom["modal-solver-name"].textContent = `[CPU] CPU / NumPy (${solverGpu ? solverGpu.backend : "CPU"})`;
        this.dom["modal-solver-desc"].textContent = "物理演算はCPU並列モードで稼働中";
        if (this.dom["gpu-status-solver-card"]) {
          this.dom["gpu-status-solver-card"].className = "gpu-status-card";
        }
      }
    }

    // Update render card
    if (this.dom["modal-render-name"]) {
      if (renderGpu.isNvidia) {
        this.dom["modal-render-name"].textContent = `[OK] ${renderGpu.shortName} (NVIDIA)`;
        this.dom["modal-render-desc"].textContent = `3D描画: ${renderGpu.raw}`;
        if (this.dom["gpu-status-render-card"]) {
          this.dom["gpu-status-render-card"].className = "gpu-status-card status-ok";
        }
        if (this.dom["gpu-warning-box"]) this.dom["gpu-warning-box"].hidden = true;
      } else if (renderGpu.isIntel) {
        this.dom["modal-render-name"].textContent = `[注意] ${renderGpu.shortName} (Intel 省電力)`;
        this.dom["modal-render-desc"].textContent = `WebGL 3D描画: ${renderGpu.raw}`;
        if (this.dom["gpu-status-render-card"]) {
          this.dom["gpu-status-render-card"].className = "gpu-status-card status-warn";
        }
        if (this.dom["gpu-warning-box"]) this.dom["gpu-warning-box"].hidden = false;
      } else {
        this.dom["modal-render-name"].textContent = `${renderGpu.shortName}`;
        this.dom["modal-render-desc"].textContent = `WebGL 3D描画: ${renderGpu.raw}`;
        if (this.dom["gpu-status-render-card"]) {
          this.dom["gpu-status-render-card"].className = "gpu-status-card";
        }
      }
    }

    this.dom["gpu-modal"].hidden = false;
  }

  _bindHeader() {
    if (this.dom["btn-top-launch"]) {
      this.dom["btn-top-launch"].addEventListener("click", () => this._launchFlight());
    }
    const btnTopGpu = document.getElementById("btn-top-gpu-ensemble");
    if (btnTopGpu) {
      btnTopGpu.addEventListener("click", () => {
        this._runGpuEnsemble(100);
      });
    }

    this.dom["btn-save"].addEventListener("click", () => this.dataPanel.exportConfigJson());
    this.dom["btn-load"].addEventListener("click", () => this.dom["config-file"].click());
    this.dom["config-file"].addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        await this.dataPanel.applyBundle(text);
        // The GA research block is local: the server does not know it.
        this._applyResearchBundle(text);
        if (this.dom["bundle-paste"]) this.dom["bundle-paste"].value = text;
      } finally {
        event.target.value = "";
      }
    });
    this.dom["btn-csv"].addEventListener("click", () => this.dataPanel.exportFlightCsv());
    this.dom["btn-help"].addEventListener("click", () => {
      this.dom["help"].hidden = false;
    });

    if (this.dom["btn-top-investigate"]) {
      this.dom["btn-top-investigate"].addEventListener("click", () => {
        this.setWorkbench(true);
        const invTab = $("#wb-tab-investigate");
        if (invTab) invTab.click();
      });
    }
    this.dom["help-close"].addEventListener("click", () => {
      this.dom["help"].hidden = true;
    });
    this.dom["help"].addEventListener("click", (event) => {
      if (event.target === this.dom["help"]) this.dom["help"].hidden = true;
    });

    // GPU Diagnostic modal bindings
    if (this.dom["readout-solver"]) {
      this.dom["readout-solver"].addEventListener("click", () => this._openGpuModal());
    }
    if (this.dom["readout-render"]) {
      this.dom["readout-render"].addEventListener("click", () => this._openGpuModal());
    }
    if (this.dom["gpu-modal-close"]) {
      this.dom["gpu-modal-close"].addEventListener("click", () => {
        this.dom["gpu-modal"].hidden = true;
      });
    }
    if (this.dom["gpu-modal-close-x"]) {
      this.dom["gpu-modal-close-x"].addEventListener("click", () => {
        this.dom["gpu-modal"].hidden = true;
      });
    }
    if (this.dom["gpu-modal"]) {
      this.dom["gpu-modal"].addEventListener("click", (event) => {
        if (event.target === this.dom["gpu-modal"]) this.dom["gpu-modal"].hidden = true;
      });
    }
    if (this.dom["btn-copy-gpu-cmd"]) {
      this.dom["btn-copy-gpu-cmd"].addEventListener("click", async () => {
        const cmd = this.dom["gpu-ps-command"] ? this.dom["gpu-ps-command"].textContent : "";
        if (cmd) {
          try {
            await navigator.clipboard.writeText(cmd);
            this.notify("設定コマンドをクリップボードにコピーしました", "ok");
          } catch {
            this.notify("コピーに失敗しました。コマンド枠から手動でコピーしてください", "warn");
          }
        }
      });
    }

    const btnModalTest = document.getElementById("btn-modal-test-gpu");
    if (btnModalTest) {
      btnModalTest.addEventListener("click", () => {
        if (this.dom["gpu-modal"]) this.dom["gpu-modal"].hidden = true;
        this._runGpuEnsemble(100);
      });
    }

    if (this.dom["btn-force-gpu-chrome"]) {
      this.dom["btn-force-gpu-chrome"].addEventListener("click", async () => {
        try {
          this.notify("Chrome を RTX 4060 強制モードで起動リクエスト中...", "info");
          const res = await api.forceGpuRender({ action: "launch_chrome" });
          if (res.status === "success" || res.launched) {
            this.notify("[OK] Chrome (RTX 4060 強制フラグ付) を起動しました！新しいウィンドウでご確認ください", "ok");
          } else {
            this.notify("[注意] 起動に失敗しました。launch_gpu_render.bat を実行してください", "warn");
          }
        } catch {
          this.notify("起動エラー: プロジェクト直下の launch_gpu_render.bat を実行してください", "warn");
        }
      });
    }

    if (this.dom["btn-force-gpu-edge"]) {
      this.dom["btn-force-gpu-edge"].addEventListener("click", async () => {
        try {
          this.notify("Edge を RTX 4060 強制モードで起動リクエスト中...", "info");
          const res = await api.forceGpuRender({ action: "launch_edge" });
          if (res.status === "success" || res.launched) {
            this.notify("[OK] Edge (RTX 4060 強制) を起動しました！", "ok");
          } else {
            this.notify("[注意] 起動に失敗しました。launch_gpu_render.bat を実行してください", "warn");
          }
        } catch {
          this.notify("起動エラー: プロジェクト直下の launch_gpu_render.bat を実行してください", "warn");
        }
      });
    }

    this.dom["tunnel-toggle"].addEventListener("change", (event) => this.setTunnel(event.target.checked));
    this.dom["btn-config-reset"].addEventListener("click", () => this._guard(() => this._resetConfig()));
  }

  _bindKeyboard() {
    const actions = {
      Enter: () => this._launchFlight(),
      " ": () => this._togglePlay(),
      ArrowLeft: () => this._guard(() => this.transport.stepBack()),
      ArrowRight: () => this._guard(() => this.transport.stepForward()),
      ",": () => this.transport.cycleRate(-1),
      ".": () => this.transport.cycleRate(1),
      r: () => this._guard(() => this._resetFlight(true)),
      R: () => this._guard(() => this._resetFlight(true)),
      t: () => this._flip("tgl-track"),
      T: () => this._flip("tgl-track"),
      g: () => this._flip("tgl-ghost"),
      G: () => this._flip("tgl-ghost"),
      f: () => this._flip("tgl-wind"),
      F: () => this._flip("tgl-wind"),
      h: () => this._flip("tgl-heat"),
      H: () => this._flip("tgl-heat"),
      p: () => this._flip("tgl-panels"),
      P: () => this._flip("tgl-panels"),
      m: () => this._flip("tgl-grip"),
      M: () => this._flip("tgl-grip"),
      c: () => this._flip("tgl-follow"),
      C: () => this._flip("tgl-follow"),
      v: () => this.viewport && this.viewport.resetCamera(),
      V: () => this.viewport && this.viewport.resetCamera(),
      w: () => this.setWorkbench(!this._workbenchOpen()),
      W: () => this.setWorkbench(!this._workbenchOpen()),
      u: () => this.setTunnel(!this.tunnelMode),
      U: () => this.setTunnel(!this.tunnelMode),
      e: () => this.dataPanel.exportFlightCsv(),
      E: () => this.dataPanel.exportFlightCsv(),
      "?": () => {
        this.dom["help"].hidden = !this.dom["help"].hidden;
      },
    };
    window.addEventListener("keydown", (event) => {
      const helpOpen = this.dom["help"] && !this.dom["help"].hidden;
      const gpuOpen = this.dom["gpu-modal"] && !this.dom["gpu-modal"].hidden;
      if (helpOpen || gpuOpen) {
        if (event.key === "Escape") {
          if (helpOpen) this.dom["help"].hidden = true;
          if (gpuOpen) this.dom["gpu-modal"].hidden = true;
        }
        return;
      }
      const tag = event.target && event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (tag === "BUTTON" && (event.key === " " || event.key === "Enter")) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const action = actions[event.key];
      if (!action) return;
      event.preventDefault();
      action();
    });
  }

  _flip(id) {
    const node = document.getElementById(id);
    if (node) node.click();
  }

  /* -------------------------------------------------------------- events */

  _onStats(stats) {
    if (this.dom["top-fps"]) {
      const gpu = stats.gpu || (this.viewport && this.viewport.gpuInfo);
      if (gpu && gpu.shortName) {
        const warnIcon = gpu.isIntel ? " ⚠️" : "";
        // Below 1740 px the top rail cannot hold the GPU name next to the
        // frame rate: the read-out showed "144 fps (Intel(R)" cut in the
        // middle of the name.  The short form keeps the frame rate legible
        // and the GPU name stays available in the tooltip and in the
        // diagnostic dialog.
        const compact = window.innerWidth <= 1800;
        this.dom["top-fps"].textContent = compact
          ? `${stats.fps.toFixed(0)} fps${gpu.isIntel ? " ⚠" : ""}`
          : `${stats.fps.toFixed(0)} fps (${gpu.shortName}${warnIcon})`;
        this.dom["top-fps"].title = `3D描画 (WebGL): ${gpu.raw}\nベンダー: ${gpu.vendor || "N/A"}${gpu.isIntel ? "\n※省電力GPU(Intel)で動作しています。クリックしてRTX切り替えガイドを表示" : "\n※高性能GPUで動作中。クリックして詳細表示"}`;
        if (gpu.isIntel) {
          this.dom["top-fps"].classList.add("gpu-warning");
          this.dom["top-fps"].classList.remove("gpu-active");
        } else if (gpu.isNvidia) {
          this.dom["top-fps"].classList.add("gpu-active");
          this.dom["top-fps"].classList.remove("gpu-warning");
        }
      } else {
        this.dom["top-fps"].textContent = `${stats.fps.toFixed(0)} fps`;
      }
    }
    if (this.viewport && this.dom["flow-backend"]) {
      this.dom["flow-backend"].textContent = `flow: ${this.viewport.particleBackend()}`;
    }
  }

  _onStoreChange(event) {
    if (event.section) {
      if (event.section === "research") {
        // The GA research state is local only and never reaches the backend.
      } else {
        this.dirty.add(event.section);
        if (event.section === "aircraft" || event.section === "launch" || event.section === "environment") {
          this.flightDirty = true;
        }
        this._schedulePush();
      }
    } else {
      this.flightDirty = true;
    }
    if (this.panels) this.panels.sync();
    this._syncGeometry();
    this._updateLaunchVector();
    this._updateTransportUi(this.transport.state);
  }

  _onFieldChange(section, key, value) {
    if (key === "wind_tunnel_enabled") {
      this.setTunnel(Boolean(value));
    } else if (this.tunnelMode && (key === "wind_tunnel_speed" || key === "wind_tunnel_alpha_deg" || key === "wind_tunnel_beta_deg")) {
      this._updateTunnelPose();
      this._guard(async () => {
        await api.atmosphere(this.store.data.environment);
        await this._updateTrim();
        await this.transport._pollStress();
        await this.transport._pollWind();
      });
    }
  }

  _onCommit(section) {
    if (section === "aircraft" || section === "launch") this._schedulePolar();
    if (this.tunnelMode) {
      this._updateTrim();
      this.transport._pollWind();
      this.transport._pollStress();
    }
  }

  _syncGeometry() {
    const aircraft = this.store.data.aircraft;
    const summary = airframeSummary(aircraft);
    const note = this.dom["geom-summary"];
    if (note) {
      note.textContent = `AR ${fmt(summary.aspect, 2)} · c̄ ${fmt(summary.meanChord * 1000, 0)}mm · ${summary.shape}`;
    }
    if (!this.viewport) return;
    const signature = GEOMETRY_KEYS.map((key) => aircraft[key]).join("|");
    if (signature !== this.geomSignature) {
      this.geomSignature = signature;
      this.viewport.setAirframe(aircraft);
    }
    this._syncOrigami();
    this._syncGripPoint();
  }

  /**
   * Keep the folded origami model in sync with the origami panel.
   *
   * The mesh is generated by the server so that the viewer and the unit
   * tests always use the very same geometry.  The request is debounced so
   * that dragging the folding slider stays smooth.
   */
  _syncOrigami() {
    if (!this.viewport) return;
    // The research mode shows the folded solid of a GA genome in its own
    // group; the flight origami panel must not fight over the same mesh.
    if (this.mode === "research") return;
    const state = this.store.data.origami || {};
    const enabled = Boolean(state.enabled);
    const archetype = String(state.archetype || "classic_dart");
    // Quantise the fold progression so that dragging the slider cannot emit
    // more distinct fold requests than the geometry actually shows (§G-6).
    const rawProgression = Math.max(0, Math.min(1, Number(state.progression ?? 1)));
    const progression = Math.round(rawProgression / ORIGAMI_ALPHA_STEP) * ORIGAMI_ALPHA_STEP;
    const genomeEnabled = Boolean(state.genome_enabled);
    const genomeSeed = Math.round(Number(state.genome_seed ?? 0) || 0);
    const genomeSteps = Math.max(2, Math.round(Number(state.genome_steps ?? 8) || 8));
    const genomeStrategy = String(state.genome_strategy || "families");
    const genomeStep = Math.max(0, Math.round(Number(state.genome_step ?? 0) || 0));
    const note = this.dom["origami-summary"];
    if (note) {
      if (genomeEnabled) {
        note.textContent = `遺伝子 #${genomeSeed} · ${genomeStrategy} · 手 ${genomeStep}/${genomeSteps}`;
      } else {
        const label = ORIGAMI_ARCHETYPES.find((a) => a.key === archetype);
        note.textContent = enabled
          ? `α ${fmt(progression * 100, 0)}% · ${label ? label.key : archetype}`
          : "OFF";
      }
    }
    // The analysis airframe (`shape_flag`) and the folded archetype are two
    // independent selections (§A-17), so a mismatch is legal — but it must be
    // visible, otherwise the viewer shows a different airframe than the one
    // the flight model integrates (§G-8).
    const warning = this.dom["origami-shape-warning"];
    if (warning) {
      const shapeFlag = Math.round(Number(this.store.data.aircraft?.shape_flag ?? 1));
      const archetypeFlag = ORIGAMI_ARCHETYPES.find((a) => a.key === archetype)?.shapeFlag;
      warning.textContent =
        enabled && !genomeEnabled && archetypeFlag !== undefined && archetypeFlag !== shapeFlag
          ? `機体形と折り紙が不一致（解析 ${shapeFlag} / 折り紙 ${archetypeFlag}）`
          : "";
    }
    if (!enabled) {
      this._origamiAbort = null;
      this.viewport.clearOrigamiGeometry();
      return;
    }
    if (genomeEnabled) {
      const wingspan = this.store.data.aircraft.wingspan_m;
      const signature = `genome|${genomeSeed}|${genomeSteps}|${genomeStrategy}|${genomeStep}|${wingspan}`;
      if (signature === this._origamiSignature) return;
      this._origamiSignature = signature;
      if (this._origamiTimer) clearTimeout(this._origamiTimer);
      this._origamiTimer = setTimeout(
        () => this._loadOrigamiGenome(genomeSeed, genomeSteps, genomeStrategy, genomeStep, wingspan),
        60
      );
      return;
    }
    const signature = `${archetype}|${progression.toFixed(3)}|${this.store.data.aircraft.wingspan_m}|${this.store.data.aircraft.dihedral_deg}|${this.store.data.aircraft.elevon_deg}|${this.store.data.aircraft.fold_tip_deg}|${this.store.data.aircraft.fold_trailing_deg}`;
    if (signature === this._origamiSignature) return;
    this._origamiSignature = signature;
    if (this._origamiTimer) clearTimeout(this._origamiTimer);
    this._origamiTimer = setTimeout(() => this._loadOrigami(archetype, progression), 60);
  }

  /**
   * Load a random fold programme and show the state of one of its steps.
   *
   * The server folds the programme and returns the solid of every
   * intermediate state, so the step slider walks through the folding one
   * move at a time.
   */
  async _loadOrigamiGenome(seed, stepCount, strategy, step, wingspan_m) {
    if (this._origamiAbort) this._origamiAbort.abort();
    const controller = new AbortController();
    this._origamiAbort = controller;
    try {
      const payload = await requestOrigamiGenomeSteps({
        seed,
        stepCount,
        strategy,
        wingspan_m,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      const states = payload.states || [];
      if (!states.length) return;
      const index = Math.min(Math.max(step, 0), states.length - 1);
      const state = states[index];
      const geometry = buildOrigamiGeometry(state);
      const bounds = state.bounds || { min: [0, 0, 0], max: [0, 0, 0] };
      const semispan = Math.max(Math.abs(bounds.max[2] - bounds.min[2]) * 0.5, 1e-3);
      this.viewport.setOrigamiGeometry(geometry, semispan);
      this._origamiStates = states;
    } catch (error) {
      if (error && error.name === "AbortError") return;
      this.notify(
        `折り方遺伝子の読み込みに失敗しました: ${this._describe(error)}`,
        "error"
      );
    }
  }

  async _loadOrigami(archetype, progression) {
    if (this._origamiAbort) this._origamiAbort.abort();
    const controller = new AbortController();
    this._origamiAbort = controller;
    const aircraft = this.store.data.aircraft || {};
    const params = {
      keel_beta_deg: 3.0,
      dihedral_deg: Number(aircraft.dihedral_deg ?? 5),
      elevon_deg: Number(aircraft.elevon_deg ?? 2),
      fold_tip_deg: Number(aircraft.fold_tip_deg ?? 0),
      fold_trailing_deg: Number(aircraft.fold_trailing_deg ?? 15),
      fold_leading_deg: Number(aircraft.fold_leading_deg ?? 0),
    };
    try {
      const payload = await requestOrigamiMesh({
        archetype,
        alpha: progression,
        wingspan: Number(aircraft.wingspan_m ?? 0.2),
        params,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      const geometry = buildOrigamiGeometry(payload);
      const bounds = payload.bounds || { min: [0, 0, 0], max: [0, 0, 0] };
      const semispan = Math.max(
        Math.abs(bounds.max[2] - bounds.min[2]) * 0.5,
        1e-3
      );
      this.viewport.setOrigamiGeometry(geometry, semispan);
      this._prefetchOrigami(archetype, progression, params);
    } catch (error) {
      if (error && error.name === "AbortError") return;
      this.notify(`折り紙モデルの読み込みに失敗しました: ${this._describe(error)}`, "error");
    }
  }

  /**
   * Warm the server side fold cache with the neighbouring fold angles.
   *
   * Every fold request re-folds the whole sheet, so dragging the slider is
   * only smooth when the neighbouring angles are already cached.  The
   * prefetch is bounded (at most the two neighbours, one batch in flight) and
   * never changes what is displayed.
   */
  _prefetchOrigami(archetype, progression, params) {
    if (this._origamiPrefetch) return;
    const neighbours = [progression - ORIGAMI_ALPHA_STEP, progression + ORIGAMI_ALPHA_STEP]
      .map((value) => Math.round(value / ORIGAMI_ALPHA_STEP) * ORIGAMI_ALPHA_STEP)
      .filter((value) => value >= 0 && value <= 1);
    if (!neighbours.length) return;
    this._origamiPrefetch = true;
    const wingspan = Number(this.store.data.aircraft?.wingspan_m ?? 0.2);
    (async () => {
      for (const alpha of neighbours) {
        try {
          await requestOrigamiMesh({ archetype, alpha, wingspan, params });
        } catch (error) {
          // A failed prefetch is not user visible: the real request reports it.
        }
      }
      this._origamiPrefetch = false;
    })();
  }

  _syncGripPoint() {
    if (!this.viewport) return;
    const aircraft = this.store.data.aircraft || {};
    const launch = this.store.data.launch || {};
    const geom = airframeSummary(aircraft);
    const rootChord = geom.rootChord;
    
    // Throw grip position along root chord (t: 0 at nose apex to 1 at tail)
    // Default ergonomic grip position is 38% to 42% back from the nose apex
    const userGripX = Number(launch.throw_grip_x ?? 0.10);
    // Map throw_grip_x (-0.5 .. 1.0) into normalized chord station t (typically 0.35..0.45)
    const tGrip = Math.min(Math.max(0.38 + userGripX * 0.15, 0.15), 0.75);
    
    // In airframe space:
    // x = rootChord * (1 - tGrip)
    const x = rootChord * (1.0 - tGrip);
    
    // Keel profile depth at tGrip (matches makeRealisticKeelSampler exactly at s=1):
    const sIdx = Number(aircraft.shape_flag) || 1;
    const centreFold = Math.max(0, Number(aircraft.fold_center_deg) || 0);
    const baseDepthRatio = sIdx === 2 ? 0.26 : (sIdx === 1 ? 0.24 : 0.18);
    const keelHeight = rootChord * (baseDepthRatio + 0.30 * Math.sin((centreFold * Math.PI) / 180));
    const gripBell = Math.sin(Math.PI * Math.min(tGrip * 1.5, 1.0));
    const depthCurve = 0.35 + 0.65 * Math.pow(gripBell, 0.7);
    const keelBottomY = -keelHeight * depthCurve;
    
    // Lateral offset (z) if user offsets grip
    const span = geom.span;
    const semispan = span / 2;
    const gripY = Number(launch.throw_grip_y ?? 0.0);
    const z = gripY * semispan * 0.1;
    
    this.viewport.updateGripPoint([x, keelBottomY, z]);
  }

  async _guard(action) {
    try {
      await action();
    } catch (error) {
      this.stopEtaCountdown();
      this.setGlobalEta({ clock: "エラー", left: "中断", active: false, title: "シミュレーション計算エラー" });
      const msg = this._describe(error);
      this.notify(msg, "error");
      this.showSimulationError({
        title: "シミュレーション実行エラー",
        message: msg,
        hint: "入力パラメータの設定値またはサーバー通信状態をご確認ください。",
        badge: "ERROR",
      });
    }
  }

  _describe(error) {
    if (error instanceof ApiError) return `${error.message} (HTTP ${error.status})`;
    return error && error.message ? error.message : String(error);
  }

  showSimulationError({
    title = "シミュレーションエラー",
    message = "",
    hint = "",
    badge = "ERROR",
    icon = '<svg viewBox="0 0 24 24" fill="currentColor" style="width:20px;height:20px;color:#d93025;"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>',
  } = {}) {
    const banner = this.dom["sim-error-banner"];
    if (!banner) return;
    if (this.dom["sim-error-icon"]) this.dom["sim-error-icon"].innerHTML = icon;
    if (this.dom["sim-error-title"]) this.dom["sim-error-title"].textContent = title;
    if (this.dom["sim-error-badge"]) {
      this.dom["sim-error-badge"].textContent = badge;
      this.dom["sim-error-badge"].dataset.type = badge.toLowerCase();
    }
    if (this.dom["sim-error-msg"]) this.dom["sim-error-msg"].textContent = message;
    const hintEl = this.dom["sim-error-hint"];
    if (hintEl) {
      if (hint) {
        hintEl.textContent = `対処ヒント: ${hint}`;
        hintEl.hidden = false;
      } else {
        hintEl.hidden = true;
      }
    }
    banner.hidden = false;
    if (this.dom["vp-flight-status-announcer"]) {
      this.dom["vp-flight-status-announcer"].style.display = "none";
    }
    this._updatePill(badge === "DIVERGED" ? "発散" : "エラー", "error");
    if (this.dom["top-state"]) this.dom["top-state"].textContent = "ERROR";
  }

  hideSimulationError() {
    if (this.dom["sim-error-banner"]) {
      this.dom["sim-error-banner"].hidden = true;
    }
    if (this.dom["vp-flight-status-announcer"]) {
      this.dom["vp-flight-status-announcer"].style.display = "";
    }
  }

  startEtaCountdown({
    clock = "計算中...",
    title = "計算を実行中...",
  } = {}) {
    this.stopEtaCountdown();
    const t0 = performance.now();
    const update = () => {
      const elapsed = (performance.now() - t0) / 1000;
      let leftStr = "";
      if (this._lastSimDuration && elapsed < this._lastSimDuration) {
        const remaining = Math.max(0.1, this._lastSimDuration - elapsed);
        leftStr = `残 約${remaining.toFixed(1)}s`;
      } else {
        leftStr = `実行中 (${elapsed.toFixed(1)}s)`;
      }
      this.setGlobalEta({
        clock: clock,
        left: leftStr,
        active: true,
        title: title,
        skipStopCountdown: true,
      });
    };
    update();
    this._etaTimer = setInterval(update, 80);
  }

  stopEtaCountdown() {
    if (this._etaTimer) {
      clearInterval(this._etaTimer);
      this._etaTimer = null;
    }
  }

  setGlobalEta({
    clock = "—",
    left = "待機中",
    active = false,
    title = "計算完了予想時間 (ETA)",
    skipStopCountdown = false,
  } = {}) {
    if (!skipStopCountdown && !active) {
      this.stopEtaCountdown();
    }
    const widget = this.dom["eta-widget"];
    const clockEl = this.dom["eta-clock"];
    const leftEl = this.dom["eta-left"];
    if (clockEl) clockEl.textContent = clock;
    if (leftEl) leftEl.textContent = left;
    if (widget) {
      widget.dataset.active = active ? "1" : "0";
      widget.title = title;
    }
  }

  /* ------------------------------------------------------- configuration */

  _schedulePush() {
    this._pushDebounced();
  }

  _schedulePolar() {
    this._polarDebounced();
  }

  async _push() {
    if (!this.dirty.size) return;
    const sections = Array.from(this.dirty);
    this.dirty.clear();
    try {
      if (sections.includes("environment")) {
        const payload = await api.atmosphere(this.store.data.environment);
        this.store.setAir(payload);
        this._renderAir();
      }
      const patch = {};
      for (const section of sections) {
        if (section === "environment") continue;
        // The origami panel is a local view state and never reaches the
        // flight backend.
        if (section === "origami") continue;
        // The GA research state is local as well.
        if (section === "research") continue;
        patch[section] = this.store.data[section];
      }
      if (Object.keys(patch).length) {
        await api.pushConfig(patch);
        if (sections.includes("aircraft") || sections.includes("launch")) this.flightDirty = true;
      }
    } catch (error) {
      this.notify(`設定を反映できません: ${this._describe(error)}`, "error");
    }
  }

  async _resetConfig() {
    try {
      const payload = await api.importBundle({
        aircraft: {},
        environment: {},
        launch: {},
        simulation: {},
      });
      this.store.adopt(payload.bundle);
      this.panels.sync();
      this.store.emit("change", { section: null, bulk: true, origin: "reset" });
      this._syncGeometry();
      await this.refreshAir();
      await this.refreshPolar();
      this.notify("既定値に戻しました", "ok");
    } catch (error) {
      this.notify(`既定値に戻せません: ${this._describe(error)}`, "error");
    }
  }

  async refreshAir() {
    try {
      const payload = await api.currentAir();
      this.store.setAir(payload);
      this._renderAir();
    } catch (error) {
      this.notify(`大気情報を取得できません: ${this._describe(error)}`, "warn");
    }
  }

  _renderAir() {
    const air = this.store.air || {};
    this.airGauges.set("rho", Number(air.density_kgm3));
    this.airGauges.set("mu", Number(air.dynamic_viscosity_Pa_s));
    this.airGauges.set("nu", Number(air.kinematic_viscosity_m2s));
    this.airGauges.set("sound", Number(air.sound_speed_mps));
    this.airGauges.set("temp", Number(air.temperature_C));
    this.airGauges.set("press", Number(air.pressure_hPa));
  }

  async refreshPolar() {
    try {
      const payload = await api.polar({
        aircraft: this.store.data.aircraft,
        alpha_min_deg: -8,
        alpha_max_deg: 24,
        points: 65,
        reynolds: 2.0e4,
      });
      this.polarPayload = payload;
      this.polar.setPayload(payload);
      this.dom["polar-cdmin"].textContent = `${sig(Number(payload.cd_min), 4)} @ α ${fmt(payload.alpha_cd_min_deg, 1)}°`;
      this.dom["polar-ldmax"].textContent = `${sig(Number(payload.ld_max), 4)} @ α ${fmt(payload.alpha_ld_max_deg, 1)}°`;
      await this._updateTrim();
    } catch (error) {
      this.notify(`極線を計算できません: ${this._describe(error)}`, "warn");
    }
  }

  async _updateTrim() {
    let speed = 12;
    let alpha = 0;
    let beta = 0;
    if (this.tunnelMode) {
      const env = this.store.data.environment || {};
      speed = Number(env.wind_tunnel_speed ?? 12);
      alpha = Number(env.wind_tunnel_alpha_deg ?? 4.0);
      beta = Number(env.wind_tunnel_beta_deg ?? 0.0);
    } else {
      const frame = this.transport ? this.transport.lastFrame : null;
      speed = frame ? Number(frame.speed_mps) || 12 : 12;
      alpha = frame ? Number(frame.alpha_deg) || 0 : 0;
      beta = frame ? Number(frame.beta_deg) || 0 : 0;
    }
    const frame = this.transport ? this.transport.lastFrame : null;
    const payload = await api.aero({
      aircraft: this.store.data.aircraft,
      speed_mps: speed,
      alpha_deg: alpha,
      beta_deg: beta,
      ground_height_m: frame ? Math.max(Number(frame.altitude_m) || 0, 0.01) : 1,
      environment: this.store.data.environment,
    });
    this.aeroPayload = payload;
    this.polar.setOperating({ cl: Number(payload.cl), cd: Number(payload.cd) });
    this.dom["polar-trim"].textContent = `${fmt(payload.trim_alpha_deg, 2)}° / ${fmt(payload.trim_elevon_deg, 2)}°`;
    if (this.tunnelMode) {
      this.dom["hud-cl"].textContent = fmt(payload.cl, 3);
      this.dom["hud-cd"].textContent = fmt(payload.cd, 4);
      this.dom["hud-ld"].textContent = fmt(payload.ld_ratio, 2);
      this.dom["hud-cm"].textContent = fmt(payload.cm, 4);
      this.dom["hud-alpha"].textContent = `${fmt(alpha, 1)}°`;
      this.dom["hud-beta"].textContent = `${fmt(beta, 1)}°`;
      this.dom["hud-speed"].textContent = `${fmt(speed, 1)} m/s`;
      this.dom["hud-flow"].textContent = `${fmt(speed, 1)} m/s`;
      this.dom["hud-q"].textContent = `${fmt(payload.dynamic_pressure_pa, 1)} Pa`;
      const statusEl = this.dom["hud-tunnel-status"];
      if (statusEl) {
        if (speed < 0.1) {
          statusEl.textContent = "風速ゼロ・静止";
          statusEl.style.color = "#5f6368";
        } else if (alpha > 14.0 || alpha < -8.0) {
          statusEl.textContent = "失速バフェッティング";
          statusEl.style.color = "#ea4335";
        } else {
          statusEl.textContent = "動的空力バフェッティング";
          statusEl.style.color = "#1557b0";
        }
      }
      this._updateTunnelPose();
    }
    return payload;
  }

  _setCameraChipActive(activeId) {
    const ids = [
      "cam-mode-mean",
      "cam-mode-lead",
      "cam-mode-fleet",
      "cam-mode-fixed",
      "cam-mode-swarm",
      "btn-view-reset",
    ];
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        if (id === activeId) {
          el.classList.add("active");
          el.style.backgroundColor = "var(--accent)";
          el.style.color = "#ffffff";
        } else {
          el.classList.remove("active");
          el.style.backgroundColor = "";
          el.style.color = "";
        }
      }
    });
  }

  _initTunnelControls() {
    const aoaSlider = this.dom["tunnel-slider-aoa"];
    const speedSlider = this.dom["tunnel-slider-speed"];

    const stopDrag = (e) => {
      e.stopPropagation();
    };

    if (aoaSlider) {
      aoaSlider.addEventListener("pointerdown", stopDrag);
      aoaSlider.addEventListener("mousedown", stopDrag);
      aoaSlider.addEventListener("input", (e) => {
        const val = Number(e.target.value);
        this.store.set("environment", "wind_tunnel_alpha_deg", val, "mode");
        if (this.dom["tunnel-slider-aoa-val"]) this.dom["tunnel-slider-aoa-val"].textContent = `${val.toFixed(1)}°`;
        if (this.dom["hud-tunnel-aoa"]) this.dom["hud-tunnel-aoa"].textContent = `${val.toFixed(1)}°`;
        this._updateTunnelPose();
        this._updateTrim();
      });
    }

    if (speedSlider) {
      speedSlider.addEventListener("pointerdown", stopDrag);
      speedSlider.addEventListener("mousedown", stopDrag);
      speedSlider.addEventListener("input", (e) => {
        const val = Number(e.target.value);
        this.store.set("environment", "wind_tunnel_speed", val, "mode");
        if (this.dom["tunnel-slider-speed-val"]) this.dom["tunnel-slider-speed-val"].textContent = `${val.toFixed(1)} m/s`;
        if (this.dom["hud-tunnel-speed"]) this.dom["hud-tunnel-speed"].textContent = `${val.toFixed(1)} m/s`;
        this._updateTunnelPose();
        this._updateTrim();
      });
    }

    const presetBtns = document.querySelectorAll(".tunnel-preset-btn");
    presetBtns.forEach((btn) => {
      btn.addEventListener("pointerdown", stopDrag);
      btn.addEventListener("mousedown", stopDrag);
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        const targetAoa = Number(btn.getAttribute("data-aoa"));
        if (Number.isFinite(targetAoa)) {
          if (aoaSlider) aoaSlider.value = targetAoa;
          this.store.set("environment", "wind_tunnel_alpha_deg", targetAoa, "mode");
          if (this.dom["tunnel-slider-aoa-val"]) this.dom["tunnel-slider-aoa-val"].textContent = `${targetAoa.toFixed(1)}°`;
          if (this.dom["hud-tunnel-aoa"]) this.dom["hud-tunnel-aoa"].textContent = `${targetAoa.toFixed(1)}°`;
          this._updateTunnelPose();
          this._updateTrim();
        }
      });
    });

    const tunnelHud = this.dom["tunnel-hud"];
    if (tunnelHud) {
      tunnelHud.addEventListener("pointerdown", stopDrag);
      tunnelHud.addEventListener("mousedown", stopDrag);
    }
  }

  _updateTunnelPose() {
    if (!this.viewport || !this.tunnelMode) return;
    const env = this.store.data.environment || {};
    const alpha = Number(env.wind_tunnel_alpha_deg ?? 4.0);
    const beta = Number(env.wind_tunnel_beta_deg ?? 0.0);
    const speed = Number(env.wind_tunnel_speed ?? 12.0);
    const aero = this.aeroPayload || {};
    const q = Number(aero.dynamic_pressure_pa ?? (0.5 * 1.225 * speed * speed));
    const cl = Number(aero.cl ?? 0.35);
    const cd = Number(aero.cd ?? 0.08);
    const cm = Number(aero.cm ?? 0.0);

    // Sync HUD labels and slider positions
    if (this.dom["hud-tunnel-aoa"]) this.dom["hud-tunnel-aoa"].textContent = `${alpha.toFixed(1)}°`;
    if (this.dom["hud-tunnel-speed"]) this.dom["hud-tunnel-speed"].textContent = `${speed.toFixed(1)} m/s`;
    if (this.dom["tunnel-slider-aoa-val"]) this.dom["tunnel-slider-aoa-val"].textContent = `${alpha.toFixed(1)}°`;
    if (this.dom["tunnel-slider-speed-val"]) this.dom["tunnel-slider-speed-val"].textContent = `${speed.toFixed(1)} m/s`;
    if (this.dom["tunnel-slider-aoa"] && Math.abs(Number(this.dom["tunnel-slider-aoa"].value) - alpha) > 0.01) {
      this.dom["tunnel-slider-aoa"].value = alpha;
    }
    if (this.dom["tunnel-slider-speed"] && Math.abs(Number(this.dom["tunnel-slider-speed"].value) - speed) > 0.01) {
      this.dom["tunnel-slider-speed"].value = speed;
    }

    // Update active highlight on tunnel preset buttons
    const presetBtns = document.querySelectorAll(".tunnel-preset-btn");
    presetBtns.forEach((btn) => {
      const bAoa = Number(btn.getAttribute("data-aoa"));
      const isActive = Number.isFinite(bAoa) && Math.abs(bAoa - alpha) < 0.25;
      btn.classList.toggle("active", isActive);
      if (isActive) {
        btn.style.borderColor = "#1a73e8";
        btn.style.color = "#1557b0";
      } else {
        btn.style.borderColor = "";
        btn.style.color = "";
      }
    });

    this.viewport.updateTunnelParams({
      speed,
      alpha,
      beta,
      q,
      cl,
      cd,
      cm,
    });
  }

  async _launchFlight() {
    if (this._launching || this.transport.busy) {
      console.warn("[App] Launch already executing, ignoring duplicate trigger.");
      return;
    }
    this._launching = true;
    this._setLaunchButtonsDisabled(true);

    if (this.tunnelMode) this.setTunnel(false);
    this.hideSimulationError();
    this.hideFlightResultModal();
    const tStart = performance.now();
    this.startEtaCountdown({
      clock: "6DoF計算中...",
      title: "6DoF RK4 飛行シミュレーション軌跡を計算中...",
    });

    try {
      await this._guard(async () => {
        if (this.dirty.size > 0) {
          await this._push();
        }
        this.notify("飛行シミュレーション計算中...", "info");
        if (this.viewport) {
          this.viewport.archiveTrack();
          this.viewport.setLaunchVectorVisible(false);
        }
        if (this.recorder) this.recorder.clear();
        if (this.gauges) this.gauges.resetPeaks();


        const result = await this.transport.launch({
          aircraft: this.store.data.aircraft,
          environment: this.store.data.environment,
          launch: this.store.data.launch,
          simulation: this.store.data.simulation,
          duration_s: Math.max(Number(this.store.data.simulation?.duration_s) || 25,
            this.store.data.launch?.flight_mode === "full_flight" ? 60 : 25),
        });

        const elapsedSec = ((performance.now() - tStart) / 1000).toFixed(2);
        this._lastSimDuration = Number(elapsedSec);
        this.stopEtaCountdown();
        this.setGlobalEta({
          clock: `${elapsedSec}s`,
          left: "計算完了",
          active: false,
          title: `飛行シミュレーション計算完了 (所要時間: ${elapsedSec}秒)`,
        });

        this.flightDirty = false;
        if (this.viewport && result.sample_trajectories && result.sample_trajectories.length) {
          this.viewport.setEnsembleTracks(result.sample_trajectories);
        }

        const metrics = result.metrics || {};
        if (this.viewport && this.store.data.launch?.flight_mode !== "legacy") {
          this.viewport.frameTrajectory(this.transport.history);
        }
        const dist = metrics.horizontal_range_m || metrics.distance_m || 0;
        const time = metrics.flight_time_s || 0;
        const speed = Number((this.store.data.launch && this.store.data.launch.launch_speed) || 10).toFixed(1);
        const angle = Number((this.store.data.launch && this.store.data.launch.launch_angle_deg) || 15).toFixed(1);
        const gpuNote = result.backend && result.backend.includes("cupy") ? " (GPU並列計算)" : "";

        if (metrics.status === "diverged" || metrics.diverged) {
          this.notify("数値計算が発散しました。初速や迎角の設定をご確認ください。", "warn");
        } else {
          this.notify(`発射完了 (${speed}m/s · ${angle}°): 飛距離 ${fmt(dist, 2)}m / 滞空 ${fmt(time, 2)}s${gpuNote}`, "ok");
        }
        this._updateTransportUi(this.transport.state);
      });
    } finally {
      this._launching = false;
      this._setLaunchButtonsDisabled(false);
      this.stopEtaCountdown();
      const widget = this.dom["eta-widget"];
      if (widget && widget.dataset.active === "1") {
        const elapsedSec = ((performance.now() - tStart) / 1000).toFixed(2);
        this.setGlobalEta({
          clock: `${elapsedSec}s`,
          left: "待機中",
          active: false,
        });
      }
    }
  }

  _setLaunchButtonsDisabled(disabled) {
    // 発射の入口は btn-top-launch (単機) と btn-top-gpu-ensemble (100機一括) のみ。
    const ids = ["btn-top-launch", "btn-top-gpu-ensemble"];
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        el.disabled = disabled;
        el.classList.toggle("btn--loading", disabled);
      }
    });
  }


  async _togglePlay() {
    if (this.tunnelMode) this.setTunnel(false);
    await this._guard(async () => {
      if (this.flightDirty || !this.transport.history || !this.transport.history.length) {
        await this._launchFlight();
        return;
      }
      if (!this.transport.playing && this.transport.playIndex >= (this.transport.history.length - 1)) {
        this.transport.seek(0);
        this.viewport?.frameTrajectory(this.transport.history);
      }
      if (!this.transport.playing && this.viewport) {
        this.viewport.setLaunchVectorVisible(false);
      }
      this.transport.toggle();
    });
  }

  async _resetFlight(clearHistory = true) {
    if (this.tunnelMode) this.setTunnel(false);
    this.hideSimulationError();
    this.hideFlightResultModal();
    this.setGlobalEta({ clock: "—", left: "待機中", active: false });
    if (this.viewport) {
      this.viewport.archiveTrack();
      this.viewport.setLaunchVectorVisible(true);
      this._updateLaunchVector();
    }
    if (this.recorder) this.recorder.clear();
    if (this.gauges) this.gauges.resetPeaks();
    this.flightDirty = true;
    await this.transport.reset(clearHistory);
    this._placeAirframeAtLaunch();
    this._updateTransportUi(this.transport.state);
  }

  _placeAirframeAtLaunch() {
    const launch = this.store.data.launch || {};
    const position = launch.launch_position_m || [0, 0, 0];
    const altitude = Number(launch.launch_height_m) || 1.2;
    if (!this.viewport) return;
    this.viewport.setPose([position[0], position[1], -altitude], null);
    this.viewport.frameOn(this.viewport.lastPose, this.viewport.frameRadius());
    this._updateLaunchVector();
  }

  _updateLaunchVector() {
    if (!this.viewport) return;
    const launch = this.store.data.launch || {};
    const speed = Number(launch.launch_speed || launch.release_speed_mps || 10);
    const angle = Number(launch.launch_angle_deg || launch.launch_pitch_deg || 15);
    const height = Number(launch.launch_height_m || 1.8);
    this.viewport.setLaunchVector(speed, angle, height);
  }

  _onReset() {
    this.dom["rec-count"].textContent = "0";
    this.dom["rec-cursor"].textContent = "—";
    this.dom["tp-time"].textContent = "0.000";
    this.dom["tp-flight"].textContent = "0.00";
    this.dom["tp-dist"].textContent = "0.00";
    this.dom["tp-frame"].textContent = "0";
    this.dom["tp-total"].textContent = "0";
    this.dom["top-clock"].innerHTML = "0.000<small>s</small>";
    this.dom["top-range"].innerHTML = "0.00<small>m</small>";
    this.dom["gauge-position"].textContent = "—";
    this.dom["gauge-rema"].textContent = "—";
    const scrub = this.dom["tp-scrub"];
    scrub.max = "1";
    scrub.value = "0";
    paintRange(scrub);
    this.gauges.resetPeaks();
    this.flightDirty = false;
    this._placeAirframeAtLaunch();
    this._updatePill("待機", "idle");
    this._updateFlightStatus("ready", "投擲待機中 — [投げる] ボタンまたは Space キーで発射");
  }

  _onFrame(frame) {
    if (!frame) {
      this.recorder.clear();
      return;
    }
    this.recorder.push(frame);
    this.dom["rec-count"].textContent = String(this.recorder.length);
    if (this.viewport && !this.tunnelMode) {
      if (frame.position_m) this.viewport.setPose(frame.position_m, frame.quaternion);
      else this.viewport.setPoseFromSample(frame);
      this.viewport.appendTrack(frame);
      // Passive aero-elasticity of the folded paper: the wing bends and the
      // tip twists with the aerodynamic load.
      this.viewport.setOrigamiAeroelastic(
        Number(frame.aeroelastic_dihedral_deg) || 0,
        Number(frame.aeroelastic_twist_deg) || 0
      );
    }
    this._renderFrame(frame);
  }

  /** Canvas repaint, coalesced to ~30 Hz so 60 Hz stepping stays cheap. */
  _paint() {
    const now = performance.now();
    if (now - (this._lastPaint || 0) < 32) return;
    this._lastPaint = now;
    this.recorder.draw();
    this.polar.draw();
  }

  _renderFrame(frame) {
    const g = this.gauges;
    g.set("altitude", Number(frame.altitude_m));
    g.set("speed", Number(frame.speed_mps));
    g.set("alpha", Number(frame.alpha_deg));
    g.set("beta", Number(frame.beta_deg));
    g.set("ld", Number(frame.ld_ratio));
    g.set("gload", Number(frame.g_load));
    g.set("q", Number(frame.dynamic_pressure_pa));
    g.set("re", Number(frame.reynolds));
    g.set("mach", Number(frame.mach));
    const stressIndex = Number(frame.max_stress_index) || 0;
    g.set("stress", Number(frame.max_stress_pa), stressIndex > 0.6 ? "bad" : stressIndex > 0.35 ? "warn" : "ok");
    g.set("stressIndex", stressIndex);
    g.set("wind", Number(frame.ground_speed_mps));

    const position = frame.position_m || [0, 0, 0];
    this.dom["gauge-position"].textContent = `${fmt(position[0], 2)} / ${fmt(position[1], 2)} / ${fmt(position[2], 2)}`;
    this.dom["gauge-rema"].textContent = `${sig(Number(frame.reynolds), 4)} / ${fmt(Number(frame.mach), 4)}`;

    this.dom["hud-alpha"].textContent = `${fmt(frame.alpha_deg, 2)}°`;
    this.dom["hud-beta"].textContent = `${fmt(frame.beta_deg, 2)}°`;
    this.dom["hud-speed"].textContent = `${fmt(frame.speed_mps, 2)} m/s`;
    this.dom["hud-q"].textContent = `${fmt(frame.dynamic_pressure_pa, 1)} Pa`;

    // Live Aerodynamic L/D and Flight Glide Ratio
    const ldLive = Number(frame.ld_ratio) || 0;
    if (this.dom["hud-ld-live"]) this.dom["hud-ld-live"].textContent = `${fmt(ldLive, 2)}`;

    const vel = frame.velocity_mps || [0, 0, 0];
    const horizSpeed = Math.hypot(vel[0], vel[1]);
    const sinkRate = Number(vel[2]) || 0; // canonical frame: +z is downwards
    let glideRatio = 0;
    if (sinkRate > 0.05) {
      glideRatio = horizSpeed / sinkRate;
    } else if (horizSpeed > 0.1 && Math.abs(sinkRate) <= 0.05) {
      glideRatio = 99.9;
    }
    const grStr = glideRatio > 0 ? (glideRatio > 50 ? "水平" : fmt(glideRatio, 2)) : "—";
    if (this.dom["hud-glide-ratio"]) this.dom["hud-glide-ratio"].textContent = grStr;
    if (this.dom["tp-glide"]) this.dom["tp-glide"].textContent = grStr;

    const time = Number(frame.time_s) || 0;
    this.dom["tp-time"].textContent = fmt(time, 3);
    this.dom["top-clock"].innerHTML = `${fmt(time, 3)}<small>s</small>`;
    const range = Math.hypot(position[0], position[1]);
    this.dom["top-range"].innerHTML = `${fmt(range, 2)}<small>m</small>`;
    this.dom["tp-dist"].textContent = fmt(range, 2);

    // Rich Japanese Text Telemetry HUD with Current and Peak Values
    const alt = Math.max(-position[2], 0);
    const spd = Number(frame.speed_mps) || 0;
    if (this._peakAlt === undefined || time <= 0.05) this._peakAlt = alt;
    else this._peakAlt = Math.max(this._peakAlt, alt);

    if (this._peakSpd === undefined || time <= 0.05) this._peakSpd = spd;
    else this._peakSpd = Math.max(this._peakSpd, spd);

    if (this.dom["f-hud-alt"]) this.dom["f-hud-alt"].textContent = fmt(alt, 2);
    if (this.dom["f-hud-alt-peak"]) this.dom["f-hud-alt-peak"].textContent = fmt(this._peakAlt, 2);
    if (this.dom["f-hud-spd"]) this.dom["f-hud-spd"].textContent = fmt(spd, 2);
    if (this.dom["f-hud-spd-peak"]) this.dom["f-hud-spd-peak"].textContent = fmt(this._peakSpd, 2);
    if (this.dom["f-hud-dist"]) this.dom["f-hud-dist"].textContent = fmt(range, 2);
    if (this.dom["f-hud-time"]) this.dom["f-hud-time"].textContent = fmt(time, 2);
    if (this.dom["f-hud-glide"]) this.dom["f-hud-glide"].textContent = grStr;
    if (this.dom["f-hud-aoa"]) this.dom["f-hud-aoa"].textContent = `迎角: ${fmt(frame.alpha_deg, 1)}°`;

    // Flight Phase Detection and Display
    if (this.dom["f-hud-phase"]) {
      let phaseLabel = "待機中";
      let phaseBg = "#e8f0fe";
      let phaseColor = "#1557b0";

      if (time > 0.05) {
        if (alt <= 0.04 && time > 0.5) {
          phaseLabel = "着地完了 (LANDED)";
          phaseBg = "#ceead6";
          phaseColor = "#137333";
        } else if (sinkRate < -0.8) {
          phaseLabel = "上昇中 (ASCENT)";
          phaseBg = "#feefe3";
          phaseColor = "#d93025";
        } else if (Math.abs(sinkRate) <= 0.8 && spd < 4.5) {
          phaseLabel = "頂点遷移 (APOGEE)";
          phaseBg = "#fef7e0";
          phaseColor = "#b06000";
        } else if (sinkRate > 0) {
          phaseLabel = "安定滑空 (GLIDE)";
          phaseBg = "#e6f4ea";
          phaseColor = "#137333";
        }
      }
      this.dom["f-hud-phase"].textContent = phaseLabel;
      this.dom["f-hud-phase"].style.background = phaseBg;
      this.dom["f-hud-phase"].style.color = phaseColor;
    }

    const frames = Number(frame.frames) || 0;
    const scrub = this.dom["tp-scrub"];
    const maxVal = Math.max(frames, 1);
    scrub.max = String(maxVal);
    scrub.setAttribute("aria-valuemin", "0");
    scrub.setAttribute("aria-valuemax", String(maxVal));
    if (document.activeElement !== scrub && this.transport.scrubIndex === null) {
      const curVal = Math.max(frames - 1, 0);
      scrub.value = String(curVal);
      scrub.setAttribute("aria-valuenow", String(curVal));
      scrub.setAttribute("aria-valuetext", `${curVal} フレーム / ${fmt(time, 2)}秒`);
      paintRange(scrub);
    }
    this.dom["tp-frame"].textContent = String(this.transport.scrubIndex === null ? Math.max(frames - 1, 0) : this.transport.scrubIndex);
    this.dom["tp-total"].textContent = String(frames);
    let backendLabel = String(frame.backend || "numpy");
    if (this.gpuInfo && this.gpuInfo.available) {
      const dev = (frame.device_name || this.gpuInfo.device_name || "").replace("NVIDIA GeForce ", "").replace(" Laptop GPU", "");
      backendLabel = dev ? `${frame.backend || this.gpuInfo.backend} (${dev})` : (frame.backend || this.gpuInfo.backend);
      this.dom["top-backend"].classList.add("gpu-active");
    }
    this.dom["top-backend"].textContent = backendLabel;

    let label = "飛行";
    let state = "flying";
    if (this.tunnelMode) {
      label = "風洞";
      state = "tunnel";
      if (frame.cl !== undefined) this.dom["hud-cl"].textContent = fmt(frame.cl, 3);
      if (frame.cd !== undefined) this.dom["hud-cd"].textContent = fmt(frame.cd, 4);
      if (frame.ld_ratio !== undefined) this.dom["hud-ld"].textContent = fmt(frame.ld_ratio, 2);
      if (frame.cm !== undefined) this.dom["hud-cm"].textContent = fmt(frame.cm, 4);
    } else if (frame.crashed) {
      label = "墜落";
      state = "crash";
    } else if (frame.landed) {
      label = "着地";
    } else if (frame.stalled) {
      label = "失速";
      state = "stall";
    }
    this._updatePill(label, state);
    if (!frame.crashed && !frame.landed && !frame.diverged) {
      this._updateFlightStatus("flying", `飛行中 — 速度: ${fmt(frame.speed_mps, 1)}m/s · 高度: ${fmt(frame.altitude_m, 1)}m · 距離: ${fmt(range, 1)}m`);
    }
    this._paint();
  }

  _updatePill(label, state = "idle") {
    const pill = this.dom["flight-pill"];
    if (!pill) return;
    pill.textContent = label;
    pill.dataset.state = state;
  }

  _onReplay(sample) {
    if (!sample) return;
    if (this.viewport) {
      if (Number(sample.time_s) < (this._lastReplayTime ?? -1)) {
        this.viewport.clearTrack();
        for (const previous of this.transport.history.slice(0, this.transport.playIndex + 1)) {
          this.viewport.appendTrack(previous);
        }
      }
      this.viewport.setPoseFromSample(sample);
      this._lastReplayTime = Number(sample.time_s);
    }
    this._renderFrame({
      ...sample,
      position_m: [sample.x_m, sample.y_m, sample.z_m],
      x_m: sample.x_m,
      y_m: sample.y_m,
      frames: this.transport.frames,
      backend: "numpy",
      status: "replay",
    });
    const metrics = this.transport.metrics || {};
    const glideStart = metrics.glide_entry_time_s;
    const label = sample.landed ? "着地" : sample.crashed ? "墜落" : sample.stalled ? "失速" :
      Number(sample.vz_mps) < 0 ? "上昇" :
      glideStart != null && Number(sample.time_s) >= glideStart ? "下降・滑空" : "頂点後・遷移";
    this._updatePill(label, sample.stalled ? "stall" : "flying");
  }

  _onMetrics(metrics) {
    this.dom["tp-flight"].textContent = fmt(metrics.flight_time_s, 2);
    this.dom["tp-dist"].textContent = fmt(metrics.distance_m, 2);
    this.dom["tp-flight"].title = `頂点後 ${fmt(metrics.post_apogee_time_s || 0, 2)}秒 / 最長安定滑空 ${fmt(metrics.stable_glide_duration_s || 0, 2)}秒`;
  }

  _onEnded(frame) {
    const position = frame.position_m || [frame.x_m || 0, frame.y_m || 0, frame.z_m || 0];
    const dist = Math.hypot(position[0], position[1]);
    const time = Number(frame.time_s) || 0;

    const metrics = this.transport.metrics || {};
    if (frame.diverged || metrics.diverged || metrics.status === "diverged") {
      this._updatePill("発散", "error");
      this.notify("数値計算が発散しました。初速や迎角の設定をご確認ください。", "warn");
    } else if (frame.crashed || metrics.crashed) {
      this._updatePill("墜落", "crash");
      this.notify(`衝突終了 — 飛距離: ${dist.toFixed(2)}m · 飛行時間: ${time.toFixed(2)}s`, "warn");
    } else if (frame.landed || metrics.landed || metrics.status === "landed") {
      this._updatePill("着地", "flying");
      const launchH = Number(this.store.data.launch?.launch_height_m) || 1.8;
      const initialAlt = metrics.max_altitude_m || launchH;
      const altDrop = Math.max(initialAlt - (Number(frame.altitude_m) || 0), 0.5);
      const avgGR = dist / altDrop;
      const grText = avgGR > 0 ? ` · 平均滑空比: ${avgGR.toFixed(2)}` : "";
      this.notify(`着地完了 — 飛距離: ${dist.toFixed(2)}m · 滞空時間: ${time.toFixed(2)}s${grText}`, "ok");
    } else {
      this._updatePill("計算区間終了", "idle");
      this.notify(`計算時間 ${time.toFixed(2)}s に到達しました。着地は未確認です。`, "info");
    }

    // 飛行終了時に到達距離と時間をゴースト記録（トップレール）に保存・表示
    this.ghostRecord = { distance: dist, time: time };
    if (this.dom["top-ghost"]) {
      this.dom["top-ghost"].textContent = `${fmt(dist, 2)}m / ${fmt(time, 2)}s`;
    }

    if (this.viewport) {
      this.viewport.setLaunchVectorVisible(true);
    }

    this._guard(async () => {
      await this._updateTrim();
    });

    this.showFlightResultModal(frame);
  }

  showFlightResultModal(frame) {
    const modal = this.dom["flight-result-modal"];
    if (!modal) return;

    const position = frame.position_m || [frame.x_m || 0, frame.y_m || 0, frame.z_m || 0];
    const dist = Math.hypot(position[0], position[1]);
    const time = Number(frame.time_s) || 0;
    const metrics = this.transport.metrics || {};

    const launchH = Number(this.store.data.launch?.launch_height_m) || 1.8;
    const peakAlt = this._peakAlt != null ? this._peakAlt : Math.max(metrics.max_altitude_m || launchH, -position[2]);
    const peakSpd = this._peakSpd != null ? this._peakSpd : Math.max(Number(frame.speed_mps) || 0, Number(this.store.data.launch?.launch_speed) || 10);

    const altDrop = Math.max(peakAlt - (Number(frame.altitude_m) || 0), 0.5);
    const avgGR = dist / altDrop;

    let statusText = "滑空着地完了";
    if (frame.diverged || metrics.diverged) statusText = "数値計算発散";
    else if (frame.crashed || metrics.crashed) statusText = "地表衝突・墜落";
    else if (frame.stalled || metrics.stalled) statusText = "失速着地";

    if (this.dom["f-res-status-sub"]) this.dom["f-res-status-sub"].textContent = statusText;
    if (this.dom["f-res-dist"]) this.dom["f-res-dist"].textContent = fmt(dist, 2);
    if (this.dom["f-res-time"]) this.dom["f-res-time"].textContent = fmt(time, 2);
    if (this.dom["f-res-alt-max"]) this.dom["f-res-alt-max"].textContent = fmt(peakAlt, 2);
    if (this.dom["f-res-spd-max"]) this.dom["f-res-spd-max"].textContent = fmt(peakSpd, 2);
    if (this.dom["f-res-glide"]) this.dom["f-res-glide"].textContent = avgGR > 0 ? (avgGR > 50 ? "水平滑空" : fmt(avgGR, 2)) : "—";
    if (this.dom["f-res-gload"]) this.dom["f-res-gload"].textContent = fmt(Number(frame.g_load) || 1.0, 2) + " G";
    if (this.dom["f-res-stress"]) this.dom["f-res-stress"].textContent = fmt(Number(frame.max_stress_pa) || 0, 0) + " Pa";

    modal.hidden = false;
  }

  hideFlightResultModal() {
    const modal = this.dom["flight-result-modal"];
    if (modal) modal.hidden = true;
  }

  /* ---------------------------------------------------- unified aircraft selector */

  async _initUnifiedAircraftSelector() {
    const sel = this.dom["unified-aircraft-select"];
    if (!sel) return;

    const optGuinness = document.getElementById("optgroup-guinness");
    const optCustom = document.getElementById("optgroup-custom");
    const btnImport = this.dom["btn-unified-mesh-import"];
    const fileInput = this.dom["unified-mesh-file"];

    // 1. 3Dモデルファイル読込ボタン連携
    if (btnImport && fileInput) {
      btnImport.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", async () => {
        if (fileInput.files && fileInput.files[0]) {
          const file = fileInput.files[0];
          this.notify(`3Dモデル ${file.name} を読み込み中...`, "info");
          if (this.meshPanel) {
            await this.meshPanel.importFile(file);
            if (optCustom) {
              optCustom.hidden = false;
              const opt = document.createElement("option");
              opt.value = `custom_${file.name}`;
              opt.textContent = `3D: ${file.name}`;
              optCustom.appendChild(opt);
              sel.value = opt.value;
              this._updateUnifiedAircraftCard({
                badge: "3Dモデル",
                metric: "カスタム",
                title: file.name,
                desc: "3Dモデルデータから物理量（翼面積、スパン、アスペクト比、重心）が自動抽出され適用されています。",
              });
            }
          }
        }
      });
    }

    // 2. ギネス世界記録プリセットを読み込んでセレクトボックスに追加
    try {
      const data = await api.recordPresets();
      const presets = data.presets || [];
      this.recordPresetsMap = new Map();

      if (optGuinness) {
        optGuinness.innerHTML = "";
        for (const p of presets) {
          this.recordPresetsMap.set(p.key, p);
          const opt = document.createElement("option");
          opt.value = `record_${p.key}`;
          const cat = p.category === "indoor_duration" ? "滞空" : "飛距離";
          opt.textContent = `${p.name_ja || p.name} (${cat})`;
          optGuinness.appendChild(opt);
        }
      }
    } catch (err) {
      console.warn("Failed to load guinness presets for unified selector:", err);
    }

    // 3. 機体選択イベント (投げ方は変更せず、機体形状・重量・トリムのみ適用)
    sel.addEventListener("change", async () => {
      const val = sel.value;
      if (val.startsWith("record_")) {
        const key = val.replace("record_", "");
        const p = this.recordPresetsMap?.get(key);
        if (p) {
          await this._guard(async () => {
            const res = await api.recordApply({ key, throw_only: false, calibrated: true, flight_mode: this.recordFlightMode || "full_flight" });
            if (res.ok) {
              this.store.adopt(res);
              this.panels.sync();
              this._syncGeometry();
              await this.refreshAir();
              await this.refreshPolar();
              this._updateUnifiedAircraftCard({
                badge: "世界記録機",
                metric: p.achievement?.value_display || "世界記録",
                title: p.name_ja || p.name,
                desc: `${p.holder || ""} (${p.year || ""}) · ${p.description_ja || p.notes_ja || "世界記録の機体形状と剛性パラメータです。"}`,
              });
              this.notify(`機体モデルをロードしました: ${p.name_ja || p.name}`, "ok");
            }
          });
        }
      } else if (val === "preset_glider") {
        this._applyStandardPreset({
          name: "Paper Glider",
          wingspan_m: 0.20,
          wing_area_m2: 0.015,
          mass_kg: 0.005,
          paper_gsm: 100.0,
          aspect_ratio: 2.67,
          taper_ratio: 0.70,
          dihedral_deg: 5.0,
          elevon_deg: 2.0,
        }, "標準機", "AR 2.67", "ペーパーグライダー", "高アスペクト比で安定した滑空性能を持つ標準モデルです。");
      } else if (val === "preset_dart") {
        this._applyStandardPreset({
          name: "Dart High-Speed",
          wingspan_m: 0.12,
          wing_area_m2: 0.009,
          mass_kg: 0.006,
          paper_gsm: 120.0,
          aspect_ratio: 1.60,
          taper_ratio: 0.35,
          dihedral_deg: 2.0,
          elevon_deg: 1.0,
        }, "高速ダーツ", "AR 1.60", "高速ダーツ機", "後退角が大きく空気抵抗を極限まで低減したスピード重視モデルです。");
      } else if (val === "preset_wide") {
        this._applyStandardPreset({
          name: "Wide-Body Glider",
          wingspan_m: 0.26,
          wing_area_m2: 0.024,
          mass_kg: 0.0045,
          paper_gsm: 80.0,
          aspect_ratio: 2.82,
          taper_ratio: 0.85,
          dihedral_deg: 6.5,
          elevon_deg: 2.5,
        }, "広翼型", "AR 2.82", "広翼型グライダー", "軽量・低翼面荷重で微風でも長時間滞空できるグライダーモデルです。");
      }
    });
  }

  _updateUnifiedAircraftCard({ badge, metric, title, desc }) {
    if (this.dom["unified-model-badge"]) this.dom["unified-model-badge"].textContent = badge;
    if (this.dom["unified-model-metric"]) this.dom["unified-model-metric"].textContent = metric;
    if (this.dom["unified-model-title"]) this.dom["unified-model-title"].textContent = title;
    if (this.dom["unified-model-desc"]) this.dom["unified-model-desc"].textContent = desc;
  }

  async _applyStandardPreset(params, badge, metric, title, desc) {
    await this._guard(async () => {
      const inertiaKeys = [
        "roll_inertia_kgm2",
        "pitch_inertia_kgm2",
        "yaw_inertia_kgm2",
        "inertia_xy_kgm2",
        "inertia_xz_kgm2",
        "inertia_yz_kgm2",
      ];
      for (const k of inertiaKeys) {
        this.store.set("aircraft", k, 0, "preset");
      }
      for (const [k, v] of Object.entries(params)) {
        this.store.set("aircraft", k, v, "preset");
      }
      this.panels.sync();
      this._syncGeometry();
      await this.refreshAir();
      await this.refreshPolar();
      this._updateUnifiedAircraftCard({ badge, metric, title, desc });
      this.notify(`機体モデルを切り替えました: ${title}`, "ok");
    });
  }

  /* ---------------------------------------------------- guinness presets */

  async _initGuinnessPresets() {
    const sel = this.dom["record-preset-select"];
    if (!sel) return;

    this.recordFlightMode = "full_flight";
    this.recordModeCalibrated = false;

    const btnCal = document.getElementById("btn-mode-calibrated");
    const btnRaw = document.getElementById("btn-mode-raw");

    if (btnCal && btnRaw) {
      btnCal.addEventListener("click", async () => {
        if (this.recordFlightMode === "full_flight") return;
        this.recordFlightMode = "full_flight";
        btnCal.classList.add("active");
        btnRaw.classList.remove("active");
        if (this.selectedRecordPreset) {
          this._updateRecordSpecsDisplay();
          await this._applyRecordPreset(false);
        }
      });

      btnRaw.addEventListener("click", async () => {
        if (this.recordFlightMode === "apex_glide") return;
        this.recordFlightMode = "apex_glide";
        btnRaw.classList.add("active");
        btnCal.classList.remove("active");
        if (this.selectedRecordPreset) {
          this._updateRecordSpecsDisplay();
          await this._applyRecordPreset(false);
        }
      });
    }

    try {
      const data = await api.recordPresets();
      const presets = data.presets || [];
      this.recordPresetsMap = new Map();

      for (const p of presets) {
        this.recordPresetsMap.set(p.key, p);
        const opt = document.createElement("option");
        opt.value = p.key;
        const throwData = p.throw || {};
        const cat = p.category === "indoor_duration" ? "滞空" : "飛距離";
        opt.textContent = `${cat}: ${p.name} (${throwData.release_speed_mps || 0}m/s, ${throwData.launch_angle_deg || 0}°)`;
        sel.appendChild(opt);
      }

      sel.addEventListener("change", async () => {
        const key = sel.value;
        if (!key) {
          if (this.dom["record-card"]) this.dom["record-card"].style.display = "none";
          return;
        }
        await this._showRecordDetails(key);
        // Instant 1-click apply and launch for immediate seamless record flight experience
        await this._applyRecordPreset(false);
      });

      // Initialize dedicated Guinness Studio workbench pane
      await this._initGuinnessStudioPane(presets);
    } catch (err) {
      console.error("Failed to load record presets:", err);
      this.notify(`世界記録プリセットの読込に失敗しました: ${err.message}`, "error");
      const sel = document.getElementById("record-preset-select");
      if (sel) sel.innerHTML = '<option value="">プリセット読込エラー (再試行してください)</option>';
    }
  }

  async _initGuinnessStudioPane(presets) {
    const studioSel = document.getElementById("wb-guinness-select");
    if (!studioSel) return;

    this.studioFlightMode = "full_flight";
    this.studioGuinnessCalibrated = false;
    const btnRaw = document.getElementById("wb-guinness-btn-raw");
    const btnCal = document.getElementById("wb-guinness-btn-cal");

    if (btnRaw && btnCal) {
      btnRaw.addEventListener("click", () => {
        this.studioFlightMode = "full_flight";
        btnRaw.classList.add("active");
        btnCal.classList.remove("active");
        this._updateGuinnessStudioSpecs();
      });
      btnCal.addEventListener("click", () => {
        this.studioFlightMode = "apex_glide";
        btnCal.classList.add("active");
        btnRaw.classList.remove("active");
        this._updateGuinnessStudioSpecs();
      });
    }

    // Populate studio select dropdown
    for (const p of presets) {
      const opt = document.createElement("option");
      opt.value = p.key;
      const throwData = p.throw || {};
      const cat = p.category === "indoor_duration" ? "滞空" : "飛距離";
      opt.textContent = `【${cat}】${p.name_ja || p.name} (${throwData.release_speed_mps || 0} m/s)`;
      studioSel.appendChild(opt);
    }

    studioSel.addEventListener("change", async () => {
      const key = studioSel.value;
      if (!key) return;
      await this._loadGuinnessStudioPreset(key);
    });

    // Default select first entry if available
    if (presets.length > 0) {
      studioSel.value = presets[0].key;
      await this._loadGuinnessStudioPreset(presets[0].key);
    }

    const btnFly = document.getElementById("wb-guinness-btn-fly");
    if (btnFly) {
      btnFly.addEventListener("click", () => this._runGuinnessStudioFlight());
    }

    const btnMatrix = document.getElementById("wb-guinness-btn-verify-matrix");
    if (btnMatrix) {
      btnMatrix.addEventListener("click", () => this._showGuinnessVerificationMatrix());
    }
  }

  async _loadGuinnessStudioPreset(key) {
    try {
      const p = await api.recordPreset(key);
      this.studioGuinnessPreset = p;
      this._updateGuinnessStudioSpecs();
    } catch (err) {
      this.notify(`ギネス諸元の読込失敗: ${err.message}`, "error");
    }
  }

  _updateGuinnessStudioSpecs() {
    const p = this.studioGuinnessPreset;
    if (!p) return;
    if (!p.flight_modes?.[this.studioFlightMode]) this.studioFlightMode = "full_flight";
    const apexButton = document.getElementById("wb-guinness-btn-cal");
    if (apexButton) {
      apexButton.disabled = !p.flight_modes?.apex_glide;
      apexButton.classList.toggle("active", this.studioFlightMode === "apex_glide");
    }
    document.getElementById("wb-guinness-btn-raw")?.classList.toggle("active", this.studioFlightMode === "full_flight");

    const ach = p.achievement || {};
    const throwInfo = p.flight_modes?.[this.studioFlightMode] || p.throw || {};
    const geom = p.derived_geometry || {};
    const air = p.aircraft || {};

    const nameEl = document.getElementById("wb-g-name");
    const holderEl = document.getElementById("wb-g-holder");
    const valEl = document.getElementById("wb-g-val");
    if (nameEl) nameEl.textContent = p.name_ja || p.name;
    if (holderEl) holderEl.textContent = `${p.holder || ach.holder || ""} (${p.year || ach.date || ""}) · ${ach.venue || ""}`;
    if (valEl) valEl.textContent = ach.value_display || (p.category === "indoor_duration" ? `${ach.value || ach.duration_s || 0} 秒` : `${ach.value || ach.distance_m || 0} m`);

    const spdEl = document.getElementById("wb-g-speed");
    const angEl = document.getElementById("wb-g-angle");
    const massEl = document.getElementById("wb-g-mass");
    const geomEl = document.getElementById("wb-g-geom");
    const techEl = document.getElementById("wb-g-technique");
    const citEl = document.getElementById("wb-g-citation");

    if (spdEl) spdEl.textContent = `${throwInfo.release_speed_mps || 0} m/s (時速 ${( (throwInfo.release_speed_mps || 0) * 3.6 ).toFixed(1)} km/h)`;
    if (angEl) angEl.textContent = `${throwInfo.launch_angle_deg || 0}° (高度 ${throwInfo.release_height_m || 1.8} m)`;
    if (massEl) massEl.textContent = `${((air.mass_kg || geom.mass_kg || 0.005) * 1000).toFixed(2)} g (A4用紙: ${air.paper_gsm || geom.paper_gsm || 100} g/m²)`;
    if (geomEl) geomEl.textContent = `翼幅 ${((geom.wingspan_m || air.wingspan_m || 0.32) * 100).toFixed(1)} cm / 翼面積 ${( (geom.wing_area_m2 || air.wing_area_m2 || 0.014) * 10000 ).toFixed(1)} cm²`;
    if (techEl) techEl.textContent = throwInfo.technique_ja || throwInfo.technique || "公認投擲フォーム";
    if (citEl) {
      const cits = (p.citations || []).map(c => `・${escapeHtml(c.title || c.citation || c.key || "")}`).join("<br>");
      citEl.innerHTML = `<strong>【公認ルール・出処】</strong><br>${cits || "Guinness World Records 公認レギュレーション"}<br><span style="color:#1557b0; margin-top:4px; display:inline-block;">${escapeHtml(p.notes_ja || p.notes || "")}</span>`;
    }
  }

  async _runGuinnessStudioFlight() {
    const p = this.studioGuinnessPreset;
    if (!p) {
      this.notify("ギネス記録を選択してください", "warn");
      return;
    }

    await this._guard(async () => {
      this.startEtaCountdown({
        title: "記録機体の6DoF軌道計算中...",
        clock: "計算中...",
        estimatedSec: 0.8,
      });

      const res = await api.guinnessSimulate({
        key: p.key,
        calibrated: Boolean(this.studioGuinnessCalibrated),
        flight_mode: this.studioFlightMode,
        duration_s: 60.0,
      });

      this.stopEtaCountdown();
      this.setGlobalEta({ active: false });

      if (!res.ok) throw new Error(res.message || "ギネス計算に失敗しました");

      // Display verification results
      const emptyMsg = document.getElementById("wb-guinness-empty-msg");
      const content = document.getElementById("wb-guinness-result-content");
      if (emptyMsg) emptyMsg.style.display = "none";
      if (content) content.style.display = "block";

      const badge = document.getElementById("wb-g-res-badge");
      const title = document.getElementById("wb-g-res-title");
      const summary = document.getElementById("wb-g-res-summary");
      const target = document.getElementById("wb-g-res-target");
      const sim = document.getElementById("wb-g-res-sim");
      const ratio = document.getElementById("wb-g-res-ratio");
      const gEl = document.getElementById("wb-g-res-g");
      const list = document.getElementById("wb-g-limitations-list");

      const comp = res.comparison || {};
      const unit = comp.unit || "";
      if (target) target.textContent = `${comp.measured_value || 0} ${unit}`;
      if (sim) sim.textContent = `${(comp.simulated_value || 0).toFixed(2)} ${unit}`;
      if (ratio) ratio.textContent = `${((comp.ratio || 0) * 100).toFixed(1)} % (${comp.gap_percent > 0 ? "+" : ""}${(comp.gap_percent || 0).toFixed(1)}%)`;
      if (gEl) gEl.textContent = `${(res.pull_factor_g || 0).toFixed(1)} G`;

      const ratioVal = comp.ratio || 0;
      if (badge) { badge.textContent = "MODEL RESULT"; badge.className = "inv-badge warn"; }
      if (title) title.textContent = this.studioFlightMode === "apex_glide" ? "頂点からの滑空計算結果" : "手元からの全飛行計算結果";

      if (summary) {
        const modeLabel = this.studioFlightMode === "apex_glide" ? "頂点滑空のみ（上昇時間を含みません）" : "フルフライト（手元から着地まで）";
        summary.textContent = `${modeLabel}。【性能未検証 / performance_validated: false】記録値に対する計算値の比は ${(ratioVal * 100).toFixed(1)}%。この比は較正レシピに基づく再現であり、実機性能の検証ではありません。`;
      }

      if (list) {
        list.innerHTML = "";
        const limitations = res.limitations || [];
        for (const lim of limitations) {
          const item = document.createElement("div");
          item.style.padding = "6px 8px";
          item.style.background = lim.severity === "high" ? "#fce8e6" : "#f1f3f4";
          item.style.borderLeft = lim.severity === "high" ? "3px solid #d93025" : "3px solid #1a73e8";
          item.style.borderRadius = "2px";
          item.innerHTML = `<strong>${lim.severity === "high" ? "【過大負荷】" : "【診断】"}</strong> ${escapeHtml(lim.message_ja || lim.message || "")}`;
          list.appendChild(item);
        }
      }

      // Replay trajectory seamlessly on 3D viewport without contaminating user session
      if (res.samples && res.samples.length > 0) {
        if (this.viewport && this.viewport.trackPoints.length > 0) {
          this.viewport.archiveTrack();
        }
        this.transport.pause();
        this.transport.history = res.samples;
        this.transport.metrics = res.metrics;
        this.transport.frames = res.samples.length;
        this.transport.playIndex = 0;
        this.transport.seek(0);
        this.viewport?.frameTrajectory(res.samples);
        this.transport.emit("metrics", res.metrics);
        this.transport.emit("state", this.transport.state);
        this.transport.play();
      }

      this.notify(`記録機体の飛行計算が完了しました (${(res.metrics.flight_time_s || 0).toFixed(2)}s, ${(res.metrics.distance_m || 0).toFixed(2)}m)`, "ok");
    });
  }

  async _showGuinnessVerificationMatrix() {
    await this._guard(async () => {
      const rep = await api.guinnessVerification();
      const rows = rep.rows || [];
      const content = document.getElementById("wb-guinness-result-content");
      const emptyMsg = document.getElementById("wb-guinness-empty-msg");
      if (emptyMsg) emptyMsg.style.display = "none";
      if (content) {
        content.style.display = "block";
        const validated = rep.performance_validated === true;
        const evidence = rep.performance_evidence || "";
        const note = rep.performance_evidence_note || "";
        const method = rep.method || "";
        let html = `<div style="margin-bottom:10px;padding:8px;background:#fdf8e2;border:1px solid #f9ab00;border-radius:6px;">`;
        html += `<div style="font-weight:700;color:#b06000;">性能未検証 (performance_validated: ${String(validated)})</div>`;
        html += `<div style="font-size:11px;color:#5f6368;margin-top:4px;">証拠レベル: ${escapeHtml(String(evidence))}</div>`;
        if (note) html += `<div style="font-size:11px;color:#5f6368;margin-top:4px;">${escapeHtml(note)}</div>`;
        if (method) html += `<div style="font-size:11px;color:#5f6368;margin-top:4px;"><strong>計算方法:</strong> ${escapeHtml(method)}</div>`;
        html += `</div>`;
        html += `<table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;"><thead><tr><th style="text-align:left;padding:4px 6px;border-bottom:1px solid #dadce0;">機体</th><th style="text-align:left;padding:4px 6px;border-bottom:1px solid #dadce0;">カテゴリ</th><th style="text-align:right;padding:4px 6px;border-bottom:1px solid #dadce0;">公認値</th><th style="text-align:right;padding:4px 6px;border-bottom:1px solid #dadce0;">シミュ値</th><th style="text-align:right;padding:4px 6px;border-bottom:1px solid #dadce0;">乖離</th><th style="text-align:left;padding:4px 6px;border-bottom:1px solid #dadce0;">検証</th></tr></thead><tbody>`;
        for (const r of rows) {
          const gap = r.gap_percent != null ? `${r.gap_percent > 0 ? "+" : ""}${r.gap_percent.toFixed(1)}%` : "N/A";
          const pv = r.performance_validated === true ? "検証済" : "未検証";
          html += `<tr><td style="padding:4px 6px;border-bottom:1px solid #eee;">${escapeHtml(r.name_ja || r.name || "")}</td><td style="padding:4px 6px;border-bottom:1px solid #eee;">${escapeHtml(r.category || "")}</td><td style="padding:4px 6px;border-bottom:1px solid #eee;text-align:right;">${r.measured_value != null ? r.measured_value : "—"} ${escapeHtml(r.unit || "")}</td><td style="padding:4px 6px;border-bottom:1px solid #eee;text-align:right;">${r.simulated_value != null ? r.simulated_value.toFixed(1) : "—"}</td><td style="padding:4px 6px;border-bottom:1px solid #eee;text-align:right;">${gap}</td><td style="padding:4px 6px;border-bottom:1px solid #eee;color:${r.performance_validated === true ? "#34a853" : "#d93025"};font-weight:600;">${pv}</td></tr>`;
        }
        html += `</tbody></table>`;
        content.innerHTML = html;
      }
    });
  }


  _updateRecordSpecsDisplay() {
    const p = this.selectedRecordPreset;
    if (!p) return;
    if (!p.flight_modes?.[this.recordFlightMode]) this.recordFlightMode = "full_flight";
    const apexButton = document.getElementById("btn-mode-raw");
    if (apexButton) {
      apexButton.disabled = !p.flight_modes?.apex_glide;
      apexButton.classList.toggle("active", this.recordFlightMode === "apex_glide");
    }
    document.getElementById("btn-mode-calibrated")?.classList.toggle("active", this.recordFlightMode === "full_flight");
    const throwInfo = p.flight_modes?.[this.recordFlightMode] || p.throw || {};

    this.dom["rec-speed"].textContent = `${throwInfo.release_speed_mps || 0} m/s`;
    this.dom["rec-angle"].textContent = `${throwInfo.launch_angle_deg || 0}°`;
    this.dom["rec-height"].textContent = `${throwInfo.release_height_m || 1.8} m`;
    this.dom["rec-style"].textContent = throwInfo.throw_style || "—";

    if (this.recordFlightMode === "apex_glide" && p.calibration_note) {
      this.dom["record-technique"].textContent = `${p.calibration_note}（上昇を含まないモデル条件）`;
    } else {
      const rawThrow = p.throw || {};
      this.dom["record-technique"].textContent = rawThrow.technique_ja || rawThrow.technique || "公認投擲値";
    }

    if (this.viewport) {
      this.viewport.setLaunchVector(
        throwInfo.release_speed_mps,
        throwInfo.launch_angle_deg,
        throwInfo.release_height_m
      );
    }
  }

  async _showRecordDetails(key) {
    const card = this.dom["record-card"];
    if (!card) return;
    try {
      const p = await api.recordPreset(key);
      this.selectedRecordPreset = p;

      const achievement = p.achievement || {};

      this.dom["record-badge"].textContent = p.category === "indoor_duration" ? "滞空世界記録" : "飛距離世界記録";
      this.dom["record-value"].textContent = achievement.value_display || `${achievement.value || 0} ${achievement.unit || ""}`;
      this.dom["record-name"].textContent = p.name_ja || p.name;
      this.dom["record-holder"].textContent = `${p.holder || ""} (${p.year || achievement.year || ""}) · ${achievement.venue || ""}`;
      this.dom["record-notes"].textContent = p.description_ja || p.description || p.notes_ja || p.notes || "世界記録の機体と初期パラメータです。";

      this._updateRecordSpecsDisplay();

      card.style.display = "flex";
    } catch (err) {
      this.notify(`記録情報の取得に失敗: ${err.message}`, "error");
    }
  }

  async _applyRecordPreset(throwOnly = false) {
    const sel = this.dom["record-preset-select"];
    const key = sel ? sel.value : null;
    if (!key) {
      this.notify("世界記録プリセットを選択してください", "warn");
      return;
    }

    await this._guard(async () => {
      if (this.viewport && this.viewport.trackPoints.length > 0) {
        this.viewport.archiveTrack();
      }

      const calibrated = Boolean(this.recordModeCalibrated !== false);
      const res = await api.recordApply({ key, throw_only: throwOnly, calibrated, flight_mode: this.recordFlightMode });
      if (!res.ok) throw new Error(res.message || "プリセットの適用に失敗しました");

      this.store.adopt(res);
      this.panels.sync();
      this._syncGeometry();
      await this.refreshAir();
      await this.refreshPolar();

      const modeStr = this.recordFlightMode === "apex_glide" ? "【頂点滑空のみ】" : "【フルフライト】";
      this.notify(
        throwOnly
          ? `${modeStr} 投げ方を適用しました: ${res.throw.technique || key}`
          : `${modeStr} 世界記録機体・投げ方をロードしました: ${res.preset_name || key}`,
        "ok"
      );

      await this._launchFlight();
    });
  }

  _updateTransportUi(state) {
    const isReadyToLaunch = this.flightDirty || !this.transport.history || !this.transport.history.length;
    const isPlaying = Boolean(state.playing);

    // 1. Transport Play Button (再生 / 一時停止 の唯一の入口)
    const play = this.dom["tp-play"];
    if (play) {
      play.dataset.playing = isPlaying ? "1" : "0";
      const playImg = document.getElementById("tp-play-img");
      if (playImg) {
        if (isPlaying) {
          playImg.innerHTML = '<path d="M6 19h4V5H6zm8-14v14h4V5z"/>';
        } else {
          playImg.innerHTML = '<path d="M8 5v14l11-7z"/>';
        }
      }
      if (this.dom["tp-play-label"]) {
        this.dom["tp-play-label"].textContent = isPlaying ? "一時停止" : "再生";
      }
    }

    // 2. Playback rate buttons
    for (const button of document.querySelectorAll(".rate-btn")) {
      button.setAttribute("aria-pressed", Number(button.dataset.rate) === state.rate ? "true" : "false");
    }
    const runNode = this.dom["top-state"];
    if (runNode) {
      const statusText = state.playing ? "PLAYING" : isReadyToLaunch ? "READY" : state.frames > 0 ? "PAUSED" : "IDLE";
      runNode.textContent = statusText;
      runNode.parentElement.dataset.run = state.playing ? "running" : isReadyToLaunch ? "ready" : state.frames > 0 ? "paused" : "idle";
    }
  }

  /* -------------------------------------------------------- tunnel mode */

  setTunnel(enabled) {
    this.tunnelMode = Boolean(enabled);
    if (this.dom["tunnel-toggle"]) this.dom["tunnel-toggle"].checked = this.tunnelMode;
    if (this.dom["tunnel-hud"]) {
      this.dom["tunnel-hud"].hidden = !this.tunnelMode;
      this.dom["tunnel-hud"].style.display = this.tunnelMode ? "" : "none";
    }
    this.transport.tunnelMode = this.tunnelMode;
    if (this.viewport) {
      const env = this.store.data.environment || {};
      const speed = Number(env.wind_tunnel_speed ?? 12.0);
      const alpha = Number(env.wind_tunnel_alpha_deg ?? 4.0);
      const beta = Number(env.wind_tunnel_beta_deg ?? 0.0);
      this.viewport.setTunnelMode(this.tunnelMode, { speed, alpha, beta });
      this.viewport.resetCamera();
      if (this.tunnelMode) {
        this.viewport.setLaunchVectorVisible(false);
        this.viewport.setParticlesVisible(true);
        this._updateTunnelPose();
      } else {
        this.viewport.setLaunchVectorVisible(true);
        this._placeAirframeAtLaunch();
      }
    }
    this.store.set("environment", "wind_tunnel_enabled", this.tunnelMode, "mode");
    this._updatePill(this.tunnelMode ? "風洞" : "待機", this.tunnelMode ? "tunnel" : "idle");
    if (this.tunnelMode) {
      this._guard(async () => {
        await api.atmosphere(this.store.data.environment);
        await this._updateTrim();
        await this.transport._pollWind();
        await this.transport._pollStress();
      });
    } else {
      this._guard(async () => {
        await this.transport._pollWind();
        await this.transport._pollStress();
      });
    }
  }

  _updateModes() {
    this.setTunnel(Boolean(this.store.get("environment", "wind_tunnel_enabled")));
    this.dom["flow-backend"].textContent = `flow: ${this.viewport ? this.viewport.particleBackend() : "—"}`;
  }

  async _adoptServerAirframe() {
    try {
      const snapshot = await api.snapshot();
      this.store.adopt(snapshot);
      this.panels.sync();
      this._syncGeometry();
    } catch (error) {
      this.notify(`機体設定を再取得できません: ${this._describe(error)}`, "warn");
    }
  }

  /* -------------------------------------------------------------- toast */

  notify(message, tone = "info") {
    const host = this.toastHost;
    if (!host) return;
    const node = make("div", { class: "toast", "data-tone": tone, text: message });
    host.append(node);
    setTimeout(() => node.remove(), tone === "error" ? 7000 : 3800);
  }
}

const GAUGE_SPECS = [
  { id: "altitude", label: "高度", unit: "m", digits: 2 },
  { id: "speed", label: "速度", unit: "m/s", digits: 2 },
  { id: "alpha", label: "迎角 α", unit: "°", digits: 2, tone: "stall" },
  { id: "beta", label: "滑角 β", unit: "°", digits: 2 },
  { id: "ld", label: "L/D", unit: "", digits: 2 },
  { id: "gload", label: "G ロード", unit: "g", digits: 2 },
  { id: "q", label: "動圧 q", unit: "Pa", digits: 1 },
  { id: "re", label: "レイノルズ", unit: "", digits: 0 },
  { id: "mach", label: "マッハ", unit: "", digits: 4 },
  { id: "stress", label: "最大応力", unit: "kPa", digits: 2, scale: 1e-3 },
  { id: "stressIndex", label: "応力指数", unit: "", digits: 3 },
  { id: "wind", label: "対地速度", unit: "m/s", digits: 2 },
];

const AIR_SPECS = [
  { id: "rho", label: "密度 ρ", unit: "kg/m³", digits: 4 },
  { id: "mu", label: "動粘性 μ", unit: "×10⁻⁵ Pa·s", digits: 3, scale: 1e-5 },
  { id: "nu", label: "動粘性 ν", unit: "×10⁻⁶ m²/s", digits: 3, scale: 1e-6 },
  { id: "sound", label: "音速 a", unit: "m/s", digits: 1 },
  { id: "temp", label: "気温", unit: "°C", digits: 1 },
  { id: "press", label: "気圧", unit: "hPa", digits: 1 },
];

const app = new ConsoleApp();
window.consoleApp = app;
app.boot().catch((error) => {
  console.error(error);
  const host = document.getElementById("toasts");
  if (host) {
    host.append(
      make("div", { class: "toast", "data-tone": "error", text: `初期化に失敗しました: ${error.message}` })
    );
  }
});

export { ConsoleApp, GAUGE_SPECS, AIR_SPECS };
