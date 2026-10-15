/*
 * datapanel.js — presets in localStorage plus the JSON/CSV import & export
 * routes of /api/v1/data.
 */

import { clear, downloadText, fmt, make, readTextFile, safeJSON } from "./lib.js";

const PRESET_KEY = "paper-airplane-console:presets";
const PRESET_LIMIT = 24;

function loadPresets() {
  try {
    const raw = window.localStorage.getItem(PRESET_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function savePresets(presets) {
  try {
    window.localStorage.setItem(PRESET_KEY, JSON.stringify(presets.slice(0, PRESET_LIMIT)));
    return true;
  } catch (error) {
    return false;
  }
}

export class DataPanel {
  constructor({ api, store, dom, notify }) {
    this.api = api;
    this.store = store;
    this.dom = dom;
    this.notify = notify || (() => {});
    this.presets = loadPresets();
    this.selected = null;
    this.lastExport = null;
  }

  mount() {
    const { dom } = this;
    this._buildExportOptions(dom.exportOptions);

    dom.presetList.addEventListener("click", (event) => {
      const row = event.target.closest(".preset");
      if (!row) return;
      if (event.target.closest(".preset-del")) return;
      this.applyPreset(row.dataset.name);
    });
    dom.presetSave.addEventListener("click", () => this.savePreset());
    dom.presetDelete.addEventListener("click", () => this.deletePreset());

    dom.exportRun.addEventListener("click", () => this.runExport());
    dom.bundleApply.addEventListener("click", () => this.applyBundle(dom.paste.value));
    dom.bundleDrop.addEventListener("click", () => this._openBundleFile());
    dom.bundleDrop.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this._openBundleFile();
      }
    });
    for (const type of ["dragenter", "dragover"]) {
      dom.bundleDrop.addEventListener(type, (event) => {
        event.preventDefault();
        dom.bundleDrop.classList.add("is-over");
      });
    }
    for (const type of ["dragleave", "drop"]) {
      dom.bundleDrop.addEventListener(type, (event) => {
        event.preventDefault();
        dom.bundleDrop.classList.remove("is-over");
      });
    }
    dom.bundleDrop.addEventListener("drop", async (event) => {
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      dom.paste.value = await readTextFile(file);
      this.applyBundle(dom.paste.value);
    });

    this.renderPresets();
  }

  _openBundleFile() {
    const input = make("input", { type: "file", accept: "application/json,.json" });
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const text = await readTextFile(file);
      this.dom.paste.value = text;
      this.applyBundle(text);
    });
    input.click();
  }

  _buildExportOptions(host) {
    clear(host);
    this.exportKind = this._select(host, "対象", [
      ["flight", "飛行ログ"],
      ["config", "設定バンドル"],
    ], "flight");
    this.exportFormat = this._select(host, "書式", [
      ["csv", "CSV"],
      ["json", "JSON"],
    ], "csv");
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

  /* ------------------------------------------------------------- presets */

  snapshot() {
    return {
      aircraft: { ...this.store.data.aircraft },
      environment: { ...this.store.data.environment },
      launch: { ...this.store.data.launch },
      simulation: { ...this.store.data.simulation },
    };
  }

  savePreset() {
    const name = window.prompt("プリセット名", `preset ${this.presets.length + 1}`);
    if (!name) return;
    const entry = {
      name,
      saved_at: new Date().toISOString(),
      bundle: this.snapshot(),
      summary: {
        span: this.store.get("aircraft", "wingspan_m"),
        gsm: this.store.get("aircraft", "paper_gsm"),
        speed: this.store.get("launch", "launch_speed"),
        style: this.store.get("launch", "throw_style"),
      },
    };
    this.presets = [entry, ...this.presets.filter((item) => item.name !== name)].slice(0, PRESET_LIMIT);
    if (!savePresets(this.presets)) {
      this.notify("プリセットを保存できません (localStorage)", "error");
      return;
    }
    this.renderPresets();
    this.notify(`プリセット「${name}」を保存しました`, "ok");
  }

  deletePreset() {
    if (!this.selected) return;
    this.presets = this.presets.filter((item) => item.name !== this.selected);
    savePresets(this.presets);
    this.selected = null;
    this.renderPresets();
  }

  renderPresets() {
    const host = this.dom.presetList;
    clear(host);
    if (!this.presets.length) {
      host.append(make("p", { class: "chart-empty", text: "プリセットなし" }));
      this.dom.presetDelete.disabled = true;
      return;
    }
    for (const entry of this.presets) {
      host.append(
        make("button", {
          class: "preset",
          type: "button",
          "data-name": entry.name,
          "aria-current": entry.name === this.selected ? "true" : null,
        }, [
          make("span", {}, [
            make("div", { text: entry.name }),
            make("div", { class: "preset-meta", text: `${entry.summary.style || "direct"} · b=${fmt(entry.summary.span, 3)}m · ${entry.summary.gsm}gsm` }),
          ]),
          make("span", { class: "preset-meta", text: entry.saved_at.slice(0, 10) }),
        ])
      );
    }
    this.dom.presetDelete.disabled = false;
  }

  async applyPreset(name) {
    const entry = this.presets.find((item) => item.name === name);
    if (!entry) return;
    this.selected = name;
    this.renderPresets();
    try {
      const payload = await this.api.importBundle(entry.bundle);
      this.store.adopt(payload.bundle);
      this.store.emit("change", { section: null, bulk: true, origin: "preset" });
      this.notify(`プリセット「${name}」を適用しました`, "ok");
    } catch (error) {
      this.notify(`適用できません: ${error.message}`, "error");
    }
  }

  /* -------------------------------------------------------------- bundle */

  async applyBundle(text) {
    const raw = (text || "").trim();
    if (!raw) {
      this.notify("JSON を貼り付けてください", "warn");
      return;
    }
    let bundle;
    try {
      bundle = JSON.parse(raw);
    } catch (error) {
      this.notify(`JSON として解釈できません: ${error.message}`, "error");
      return;
    }
    try {
      const payload = await this.api.importBundle(bundle);
      this.store.adopt(payload.bundle);
      this.store.emit("change", { section: null, bulk: true, origin: "bundle" });
      this.notify("設定バンドルを適用しました", "ok");
    } catch (error) {
      this.notify(`適用できません: ${error.message}`, "error");
    }
  }

  /* -------------------------------------------------------------- export */

  async runExport() {
    const kind = this.exportKind.value;
    const format = this.exportFormat.value;
    try {
      const payload = await this.api.exportData({ kind, format });
      this.lastExport = payload;
      this.dom.preview.textContent = payload.payload.slice(0, 2400);
      downloadText(payload.filename, payload.mimetype, payload.payload);
      this.notify(`${payload.filename} を書き出しました`, "ok");
    } catch (error) {
      this.notify(`書き出しできません: ${error.message}`, "error");
    }
  }

  /** Used by the header CSV button: export whatever the server holds. */
  async exportFlightCsv() {
    try {
      const payload = await this.api.exportData({ kind: "flight", format: "csv" });
      downloadText(payload.filename, payload.mimetype, payload.payload);
      this.notify(`${payload.filename} を書き出しました`, "ok");
      return payload;
    } catch (error) {
      this.notify(`飛行ログがありません: ${error.message}`, "error");
      return null;
    }
  }

  exportConfigJson() {
    const bundle = this.snapshot();
    const filename = `paper_airplane_config_${Date.now()}.json`;
    downloadText(filename, "application/json", safeJSON(bundle));
    this.notify(`${filename} を書き出しました`, "ok");
  }
}