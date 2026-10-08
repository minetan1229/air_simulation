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
import {
  $,
  clear,
  debounce,
  fmt,
  make,
  paintRamp,
  paintRange,
  sig,
} from "./lib.js";
import { MeshPanel } from "./meshpanel.js";
import { OptimizerPanel } from "./optimizer.js";
import { ParameterPanels } from "./panels.js";
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

class ConsoleApp {
  constructor() {
    this.store = store;
    this.dom = {};
    this.dirty = new Set();
    this.flightDirty = false;
    this.tunnelMode = false;
    this.geomSignature = "";
    this._pushDebounced = debounce(() => this._push(), 180);
    this._polarDebounced = debounce(() => this.refreshPolar(), 340);
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

    this.toastHost = $("#toasts");

    try {
      const [schema, snapshot] = await Promise.all([api.schemas(), api.snapshot()]);
      this.meta = schema;
      this.store.setSchema(schema);
      this.store.adopt(snapshot);
    } catch (error) {
      this.notify(`サーバに接続できません: ${this._describe(error)}`, "error");
      return;
    }

    this.panels = new ParameterPanels({
      store: this.store,
      root: document,
      onChange: (section, key, value) => this._onFieldChange(section, key, value),
      onCommit: (section) => this._onCommit(section),
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
        frontAxes: $("#opt-front-axes"),
      },
      notify: (message, tone) => this.notify(message, tone),
    });
    this.optimizer.mount(this.meta);

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
  }

  _cacheDom() {
    const ids = [
      "view-canvas",
      "vp-notice",
      "vp-notice-msg",
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
      "top-ghost",
      "btn-top-record",
      "btn-record-video",
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
      "btn-apply-record",
      "btn-apply-throw-only",
      "flow-backend",
      "flow-count",
      "hud-alpha",
      "hud-beta",
      "hud-speed",
      "hud-q",
      "hud-flow",
      "hud-cl",
      "hud-cd",
      "hud-ld",
      "hud-cm",
      "tunnel-hud",
      "tunnel-toggle",
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
      head.addEventListener("click", (event) => {
        if (event.target.closest("button") || event.target.closest(".pill")) return;
        if (window.innerWidth > 900) return;
        rail.classList.toggle("is-open");
      });
    }
  }

  _bindWorkbench() {
    const tabs = Array.from(document.querySelectorAll(".wb-tab"));
    for (const tab of tabs) {
      tab.addEventListener("click", () => {
        this.setWorkbench(true);
        for (const other of tabs) {
          other.setAttribute("aria-selected", other === tab ? "true" : "false");
        }
        for (const pane of document.querySelectorAll(".wb-pane")) {
          pane.hidden = pane.dataset.pane !== tab.dataset.tab;
        }
        if (tab.dataset.tab === "optimizer" && this.optimizer && this.optimizer.chart) {
          this.optimizer.chart.resize();
        }
      });
    }
    this.dom["wb-toggle"].addEventListener("click", () => this.setWorkbench(!this._workbenchOpen()));
    this.setWorkbench(false);
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
    this.dom["tp-reset"].addEventListener("click", () => this._guard(() => this._resetFlight()));

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
    t.on("error", (error) => this.notify(`シミュレーションエラー: ${error.message}`, "error"));
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
    if (reset) reset.addEventListener("click", () => this.viewport && this.viewport.resetCamera());

    const btnGpuEnsemble = document.getElementById("btn-gpu-ensemble");
    if (btnGpuEnsemble) {
      btnGpuEnsemble.addEventListener("click", () => this._runGpuEnsemble(100));
    }

    // Bind recording buttons
    const recBtns = [this.dom["btn-top-record"], this.dom["btn-record-video"]].filter(Boolean);
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
        this.notify("🎥 動画録画を開始しました（停止時にWebM/MP4動画が保存されます）", "info");
      } else {
        this.notify("録画を開始できませんでした", "warn");
      }
    }
  }

  _updateRecordButtons(isRecording) {
    const btns = [this.dom["btn-top-record"], this.dom["btn-record-video"]].filter(Boolean);
    btns.forEach((btn) => {
      if (isRecording) {
        btn.classList.add("is-recording");
        btn.textContent = "🔴 停止 (REC)";
        btn.title = "動画録画を停止してファイル保存";
      } else {
        btn.classList.remove("is-recording");
        btn.textContent = btn.id === "btn-top-record" ? "🎥 動画録画" : "🎥 録画";
        btn.title = "3D飛行動画を録画して保存 (WebM/MP4)";
      }
    });
  }

  async _runGpuEnsemble(count = 100) {
    await this._guard(async () => {
      this.notify(`⚡ GPU一括並列シミュレーション (${count}機同時計算) を実行中...`, "info");
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

      if (!res || !res.sample_trajectories) {
        throw new Error("アンサンブルシミュレーションの結果が空です");
      }

      // Render all 100 trajectories in Three.js viewport
      if (this.viewport && res.sample_trajectories.length) {
        this.viewport.setEnsembleTracks(res.sample_trajectories);
      }

      const backendStr = res.backend_display || res.backend || "cupy";
      const meanDist = Number(res.mean_distance_m || 0);
      const maxDist = Number(res.max_distance_m || 0);
      const meanTime = Number(res.mean_flight_time_s || 0);
      const maxTime = Number(res.max_flight_time_s || 0);

      this.notify(
        `⚡ GPU 100機同時飛行完了 (${res.trajectories_count || count}機 · ${backendStr})\n最大飛距離: ${maxDist.toFixed(2)}m (平均: ${meanDist.toFixed(2)}m) · 最大滞空: ${maxTime.toFixed(2)}s (平均: ${meanTime.toFixed(2)}s)`,
        "ok"
      );
    });
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
        this.dom["modal-solver-name"].textContent = `✅ ${solverGpu.device_name || "GPU"} (${solverGpu.backend})`;
        this.dom["modal-solver-desc"].textContent = `CUDA ${solverGpu.cuda_version || ""} · 物理演算・風場計算で正常に稼働中`;
        if (this.dom["gpu-status-solver-card"]) {
          this.dom["gpu-status-solver-card"].className = "gpu-status-card status-ok";
        }
      } else {
        this.dom["modal-solver-name"].textContent = `ℹ️ CPU / NumPy (${solverGpu ? solverGpu.backend : "CPU"})`;
        this.dom["modal-solver-desc"].textContent = "物理演算はCPU並列モードで稼働中";
        if (this.dom["gpu-status-solver-card"]) {
          this.dom["gpu-status-solver-card"].className = "gpu-status-card";
        }
      }
    }

    // Update render card
    if (this.dom["modal-render-name"]) {
      if (renderGpu.isNvidia) {
        this.dom["modal-render-name"].textContent = `✅ ${renderGpu.shortName} (NVIDIA)`;
        this.dom["modal-render-desc"].textContent = `WebGL 3D描画: ${renderGpu.raw}`;
        if (this.dom["gpu-status-render-card"]) {
          this.dom["gpu-status-render-card"].className = "gpu-status-card status-ok";
        }
        if (this.dom["gpu-warning-box"]) this.dom["gpu-warning-box"].hidden = true;
      } else if (renderGpu.isIntel) {
        this.dom["modal-render-name"].textContent = `⚠️ ${renderGpu.shortName} (Intel 省電力)`;
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
    this.dom["btn-save"].addEventListener("click", () => this.dataPanel.exportConfigJson());
    this.dom["btn-load"].addEventListener("click", () => this.dom["config-file"].click());
    this.dom["config-file"].addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        await this.dataPanel.applyBundle(text);
        if (this.dom["bundle-paste"]) this.dom["bundle-paste"].value = text;
      } finally {
        event.target.value = "";
      }
    });
    this.dom["btn-csv"].addEventListener("click", () => this.dataPanel.exportFlightCsv());
    this.dom["btn-help"].addEventListener("click", () => {
      this.dom["help"].hidden = false;
    });
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
            this.notify("📋 設定コマンドをクリップボードにコピーしました", "ok");
          } catch {
            this.notify("コピーに失敗しました。コマンド枠から手動でコピーしてください", "warn");
          }
        }
      });
    }

    this.dom["tunnel-toggle"].addEventListener("change", (event) => this.setTunnel(event.target.checked));
    this.dom["btn-config-reset"].addEventListener("click", () => this._guard(() => this._resetConfig()));
  }

  _bindKeyboard() {
    const actions = {
      " ": () => this._togglePlay(),
      ArrowLeft: () => this._guard(() => this.transport.stepBack()),
      ArrowRight: () => this._guard(() => this.transport.stepForward()),
      ",": () => this.transport.cycleRate(-1),
      ".": () => this.transport.cycleRate(1),
      r: () => this._guard(() => this._resetFlight()),
      R: () => this._guard(() => this._resetFlight()),
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
      const tag = event.target && event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return;
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
        this.dom["top-fps"].textContent = `${stats.fps.toFixed(0)} fps (${gpu.shortName}${warnIcon})`;
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
      this.dirty.add(event.section);
      if (event.section === "aircraft" || event.section === "launch") this.flightDirty = true;
      this._schedulePush();
    }
    if (this.panels) this.panels.sync();
    this._syncGeometry();
  }

  _onFieldChange(section, key, value) {
    if (key === "wind_tunnel_enabled") {
      this.setTunnel(Boolean(value));
    } else if (this.tunnelMode && (key === "wind_tunnel_speed" || key === "wind_tunnel_alpha_deg" || key === "wind_tunnel_beta_deg")) {
      this._updateTunnelPose();
      this._updateTrim();
      this.transport._pollStress();
      this.transport._pollWind();
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
    this._syncGripPoint();
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
      this.notify(this._describe(error), "error");
    }
  }

  _describe(error) {
    if (error instanceof ApiError) return `${error.message} (HTTP ${error.status})`;
    return error && error.message ? error.message : String(error);
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
      this._updateTunnelPose();
    }
    return payload;
  }

  _updateTunnelPose() {
    if (!this.viewport || !this.tunnelMode) return;
    const env = this.store.data.environment || {};
    const alpha = Number(env.wind_tunnel_alpha_deg ?? 4.0);
    const beta = Number(env.wind_tunnel_beta_deg ?? 0.0);
    // In wind tunnel bench:
    // Pitch is alpha (nose-up: positive pitch in NED = -alpha rotation in body)
    // Yaw is -beta (sideslip)
    const deg2rad = Math.PI / 180;
    const pitchRad = alpha * deg2rad;
    const yawRad = -beta * deg2rad;
    const euler = new THREE.Euler(0, pitchRad, yawRad, "ZYX");
    const q = new THREE.Quaternion().setFromEuler(euler);
    this.viewport.setPose([0, 0, -1.2], [q.w, q.x, q.y, q.z]);
  }

  /* -------------------------------------------------------------- flight */

  async _togglePlay() {
    await this._guard(async () => {
      if (this.flightDirty) {
        await this._resetFlight();
        this.flightDirty = false;
      } else if (!this.transport.playing && this.viewport && this.viewport.trackPoints.length > 0 && this.transport.frames === 0) {
        this.viewport.archiveTrack();
      }
      if (!this.transport.playing && this.viewport) {
        this.viewport.setLaunchVectorVisible(false);
      }
      this.transport.toggle();
    });
  }

  async _resetFlight() {
    if (this.viewport) {
      this.viewport.archiveTrack();
      this._updateLaunchVector();
    }
    if (this.recorder) this.recorder.clear();
    if (this.gauges) this.gauges.resetPeaks();
    this.dom["rec-count"].textContent = "0";
    await this.transport.reset();
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
  }

  _onFrame(frame) {
    if (!frame) {
      this.recorder.clear();
      return;
    }
    this.recorder.push(frame);
    this.dom["rec-count"].textContent = String(this.recorder.length);
    if (this.viewport && !this.tunnelMode) {
      this.viewport.setPose(frame.position_m, frame.quaternion);
      this.viewport.appendTrack(frame);
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

    const time = Number(frame.time_s) || 0;
    this.dom["tp-time"].textContent = fmt(time, 3);
    this.dom["top-clock"].innerHTML = `${fmt(time, 3)}<small>s</small>`;
    const range = Math.hypot(position[0], position[1]);
    this.dom["top-range"].innerHTML = `${fmt(range, 2)}<small>m</small>`;
    this.dom["tp-dist"].textContent = fmt(range, 2);

    const frames = Number(frame.frames) || 0;
    const scrub = this.dom["tp-scrub"];
    scrub.max = String(Math.max(frames, 1));
    if (document.activeElement !== scrub && this.transport.scrubIndex === null) {
      scrub.value = String(Math.max(frames - 1, 0));
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
    if (frame.crashed) {
      label = "墜落";
      state = "crash";
    } else if (frame.landed) {
      label = "着地";
    } else if (frame.stalled) {
      label = "失速";
      state = "stall";
    }
    this._updatePill(label, state);
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
    if (this.viewport) this.viewport.setPoseFromSample(sample);
    this._renderFrame({
      ...sample,
      x_m: sample.x_m,
      y_m: sample.y_m,
      frames: this.transport.frames,
      backend: "numpy",
      status: "replay",
    });
    this._updatePill("再生", "flying");
  }

  _onMetrics(metrics) {
    this.dom["tp-flight"].textContent = fmt(metrics.flight_time_s, 2);
    this.dom["tp-dist"].textContent = fmt(metrics.distance_m, 2);
  }

  _onEnded(frame) {
    this._updatePill(frame.crashed ? "墜落" : "着地", frame.crashed ? "crash" : "flying");
    this.notify(frame.crashed ? "墜落しました" : "着地しました", frame.crashed ? "error" : "ok");

    // 飛行終了時に到達距離と時間をゴースト記録（トップレール）に保存・表示
    const position = frame.position_m || [0, 0, 0];
    const dist = Math.hypot(position[0], position[1]);
    const time = Number(frame.time_s) || 0;
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
  }

  /* ---------------------------------------------------- guinness presets */

  async _initGuinnessPresets() {
    const sel = this.dom["record-preset-select"];
    if (!sel) return;

    this.recordModeCalibrated = true;

    const btnCal = document.getElementById("btn-mode-calibrated");
    const btnRaw = document.getElementById("btn-mode-raw");

    if (btnCal && btnRaw) {
      btnCal.addEventListener("click", async () => {
        if (this.recordModeCalibrated) return;
        this.recordModeCalibrated = true;
        btnCal.classList.add("active");
        btnRaw.classList.remove("active");
        if (this.selectedRecordPreset) {
          this._updateRecordSpecsDisplay();
          await this._applyRecordPreset(false);
        }
      });

      btnRaw.addEventListener("click", async () => {
        if (!this.recordModeCalibrated) return;
        this.recordModeCalibrated = false;
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
        const cat = p.category === "time_aloft" ? "⏱️ 滞空" : "📏 飛距離";
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

      const btnApplyAll = this.dom["btn-apply-record"];
      if (btnApplyAll) {
        btnApplyAll.addEventListener("click", () => this._applyRecordPreset(false));
      }

      const btnApplyThrow = this.dom["btn-apply-throw-only"];
      if (btnApplyThrow) {
        btnApplyThrow.addEventListener("click", () => this._applyRecordPreset(true));
      }
    } catch (err) {
      console.warn("Failed to load record presets:", err);
    }
  }

  _updateRecordSpecsDisplay() {
    const p = this.selectedRecordPreset;
    if (!p) return;
    const throwInfo = (this.recordModeCalibrated && p.calibration) ? p.calibration : (p.throw || {});

    this.dom["rec-speed"].textContent = `${throwInfo.release_speed_mps || 0} m/s`;
    this.dom["rec-angle"].textContent = `${throwInfo.launch_angle_deg || 0}°`;
    this.dom["rec-height"].textContent = `${throwInfo.release_height_m || 1.8} m`;
    this.dom["rec-style"].textContent = throwInfo.throw_style || "—";

    if (this.recordModeCalibrated && p.calibration_note) {
      this.dom["record-technique"].textContent = `${p.calibration_note} (校正済)`;
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

      this.dom["record-badge"].textContent = p.category === "time_aloft" ? "滞空世界記録" : "飛距離世界記録";
      this.dom["record-value"].textContent = achievement.value_display || (p.category === "time_aloft" ? `${achievement.duration_s || 0} s` : `${achievement.distance_m || 0} m`);
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
      const res = await api.recordApply({ key, throw_only: throwOnly, calibrated });
      if (!res.ok) throw new Error(res.message || "プリセットの適用に失敗しました");

      this.store.adopt(res);
      this.panels.sync();
      this._syncGeometry();
      await this.refreshAir();
      await this.refreshPolar();

      const modeStr = calibrated ? "【🌟 記録再現モード】" : "【⚡ 生公認値モード】";
      this.notify(
        throwOnly
          ? `${modeStr} 投げ方を適用しました: ${res.throw.technique || key}`
          : `${modeStr} 世界記録機体・投げ方をロードしました: ${res.preset_name || key}`,
        "ok"
      );

      await this._resetFlight();
      this.transport.play();
    });
  }

  _updateTransportUi(state) {
    const play = this.dom["tp-play"];
    play.dataset.playing = state.playing ? "1" : "0";
    this.dom["tp-play-label"].textContent =
      state.mode === "replay" ? "続行" : state.playing ? "一時停止" : "再生";
    play.querySelector(".glyph").className = `glyph ${state.playing ? "glyph-pause" : "glyph-play"}`;
    for (const button of document.querySelectorAll(".rate-btn")) {
      button.setAttribute("aria-pressed", Number(button.dataset.rate) === state.rate ? "true" : "false");
    }
    const runNode = this.dom["top-state"];
    if (runNode) {
      runNode.textContent = state.playing ? "RUNNING" : state.frames > 0 ? "PAUSED" : "IDLE";
      runNode.parentElement.dataset.run = state.playing ? "running" : state.frames > 0 ? "paused" : "idle";
    }
  }

  /* -------------------------------------------------------- tunnel mode */

  setTunnel(enabled) {
    this.tunnelMode = Boolean(enabled);
    this.dom["tunnel-toggle"].checked = this.tunnelMode;
    this.dom["tunnel-hud"].hidden = !this.tunnelMode;
    this.transport.tunnelMode = this.tunnelMode;
    if (this.viewport) {
      this.viewport.resetCamera();
      if (this.tunnelMode) {
        this.viewport.setLaunchVectorVisible(false);
        this._updateTunnelPose();
      } else {
        this._placeAirframeAtLaunch();
      }
    }
    this.store.set("environment", "wind_tunnel_enabled", this.tunnelMode, "mode");
    this._updatePill(this.tunnelMode ? "風洞" : "待機", this.tunnelMode ? "tunnel" : "idle");
    if (this.tunnelMode) {
      this._guard(async () => {
        await this._updateTrim();
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