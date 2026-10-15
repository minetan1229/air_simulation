/*
 * panels.js — builds the configuration rail from a curated field table.
 *
 * The table is deliberately not the raw dataclass: every slider carries an
 * engineering range, a unit and a digit count, so the panel reads like an
 * instrument sheet instead of a form dump.  Ranges that the server publishes
 * (environment / launch schemas) win over the table.
 */

import { clamp, fmt, isNum, make, paintRange } from "./lib.js";

const DEG = "°";

const FIELD_TABLE = {
  airframe: [
    { key: "shape_flag", kind: "select", label: "機体形", options: [[0, "ダート"], [1, "グライダー"], [2, "ワイド機体"]] },
    { key: "mass_kg", label: "質量", unit: "g", scale: 1000, min: 1, max: 30, step: 0.1, digits: 1 },
    { key: "paper_gsm", label: "坪量", unit: "g/m²", min: 40, max: 300, step: 1, digits: 0 },
    { key: "paper_stiffness", label: "紙剛性", unit: "", min: 0, max: 1, step: 0.01, digits: 2 },
    { key: "sheet_area_m2", label: "用紙面積", unit: "m²", min: 0.004, max: 0.06, step: 0.0005, digits: 4 },
    { key: "wingspan_m", label: "翼幅 b", unit: "m", min: 0.08, max: 0.5, step: 0.002, digits: 3 },
    { key: "wing_area_m2", label: "翼面積 S", unit: "m²", min: 0.004, max: 0.06, step: 0.0005, digits: 4 },
    { key: "taper_ratio", label: "テーパ比 λ", unit: "", min: 0.05, max: 1, step: 0.01, digits: 2 },
    { key: "dihedral_deg", label: "二面角 Γ", unit: DEG, min: -10, max: 30, step: 0.5, digits: 1 },
    { key: "cg_offset_x", label: "重心後移", unit: "mm", scale: 1000, min: -40, max: 40, step: 0.5, digits: 1 },
    { key: "paper_grain_angle_deg", label: "紙の繊維角", unit: DEG, min: -90, max: 90, step: 1, digits: 0 },
    { key: "paper_anisotropy_ratio", label: "紙の異方性比", unit: "×", min: 1.0, max: 3.0, step: 0.05, digits: 2 },
    { key: "aeroelastic_flex", label: "翼しなり係数", unit: "", min: 0, max: 2.0, step: 0.02, digits: 2 },
    { key: "aeroelastic_twist", label: "翼端ねじれ係数", unit: "", min: 0, max: 2.0, step: 0.02, digits: 2 },
  ],
  fold: [
    { key: "fold_leading_deg", label: "前縁折り角", unit: DEG, min: 0, max: 90, step: 0.5, digits: 1 },
    { key: "fold_trailing_deg", label: "後縁折り角", unit: DEG, min: -45, max: 45, step: 0.5, digits: 1 },
    { key: "fold_tip_deg", label: "翼端折り角", unit: DEG, min: 0, max: 90, step: 0.5, digits: 1 },
    { key: "fold_center_deg", label: "中央折り角", unit: DEG, min: 0, max: 90, step: 0.5, digits: 1 },
    { key: "elevon_deg", label: "エレボン角 δe", unit: DEG, min: -20, max: 40, step: 0.25, digits: 2 },
    { key: "aileron_deg", label: "左右揚力差 / エルロン", unit: DEG, min: -15, max: 15, step: 0.25, digits: 2 },
    { key: "elevon_chord_ratio", label: "操作翼弦比", unit: "", min: 0.05, max: 0.5, step: 0.01, digits: 2 },
  ],
  throw: [
    { key: "launch_speed", label: "発射初速", unit: "m/s", min: 2, max: 40, step: 0.1, digits: 1 },
    { key: "launch_angle_deg", label: "発射角", unit: DEG, min: -30, max: 85, step: 0.5, digits: 1 },
    { key: "launch_roll_deg", label: "ロール角", unit: DEG, min: -90, max: 90, step: 0.5, digits: 1 },
    { key: "launch_yaw_deg", label: "ヨー角", unit: DEG, min: -90, max: 90, step: 0.5, digits: 1 },
    { key: "launch_height_m", label: "放し高", unit: "m", min: 0, max: 6, step: 0.05, digits: 2 },
    { key: "throw_force_n", label: "手の力 F", unit: "N", min: 0, max: 1.2, step: 0.01, digits: 2 },
    { key: "throw_duration_s", label: "押し込み時間", unit: "s", min: 0.02, max: 0.6, step: 0.005, digits: 3 },
    { key: "throw_force_pitch_deg", label: "力の仰角", unit: DEG, min: -60, max: 80, step: 0.5, digits: 1 },
    { key: "throw_force_yaw_deg", label: "力の偏角", unit: DEG, min: -80, max: 80, step: 0.5, digits: 1 },
    { key: "throw_grip_x", label: "握り位置 x", unit: "MAC", min: -0.5, max: 1, step: 0.01, digits: 2 },
    { key: "throw_grip_y", label: "握り位置 y", unit: "半スパン", min: -1, max: 1, step: 0.01, digits: 2 },
    { key: "throw_torque_efficiency", label: "トルク伝達率", unit: "", min: 0, max: 1, step: 0.01, digits: 2 },
    { key: "throw_runup_m", label: "助走", unit: "m", min: 0, max: 3.04, step: 0.01, digits: 2 },
    { key: "throw_wrist_spin_radps", label: "手首ロール", unit: "rad/s", min: -40, max: 40, step: 0.5, digits: 1 },
    { key: "throw_pitch_rate_radps", label: "手首ピッチ", unit: "rad/s", min: -8, max: 8, step: 0.05, digits: 2 },
    { key: "throw_yaw_rate_radps", label: "手首ヨー", unit: "rad/s", min: -12, max: 12, step: 0.05, digits: 2 },
    { key: "release_pitch_deg", label: "放し時ピッチ", unit: DEG, min: -45, max: 45, step: 0.5, digits: 1 },
    { key: "release_yaw_deg", label: "放し時ヨー", unit: DEG, min: -45, max: 45, step: 0.5, digits: 1 },
    { key: "release_roll_deg", label: "放し時ロール", unit: DEG, min: -90, max: 90, step: 0.5, digits: 1 },
  ],
  atmosphere: [
    { key: "isa_mode", kind: "toggle", label: "ISA 標準大気" },
    { key: "temperature_K", label: "気温", unit: "°C", offset: -273.15, min: -60, max: 50, step: 0.5, digits: 1 },
    { key: "pressure_Pa", label: "気圧", unit: "hPa", scale: 0.01, min: 600, max: 1100, step: 0.5, digits: 1 },
    { key: "humidity_rel", label: "相対湿度", unit: "%", scale: 100, min: 0, max: 100, step: 1, digits: 0 },
    { key: "altitude_m", label: "基準高度", unit: "m", min: -100, max: 3000, step: 5, digits: 0 },
    { key: "sea_level_temperature_K", label: "海面気温", unit: "°C", offset: -273.15, min: -40, max: 40, step: 0.5, digits: 1 },
    { key: "lapse_rate_K_per_m", label: "温度勾配", unit: "K/m", min: -0.02, max: 0.02, step: 0.0005, digits: 4 },
    { key: "reference_height_m", label: "基準高さ", unit: "m", min: 0.1, max: 10, step: 0.1, digits: 1 },
  ],
  wind: [
    { key: "wind_speed", label: "風速", unit: "m/s", min: 0, max: 40, step: 0.1, digits: 1 },
    { key: "wind_direction_deg", label: "風向", unit: DEG, min: 0, max: 360, step: 1, digits: 0 },
    { key: "gust_intensity", label: "乱気流強度", unit: "", min: 0, max: 1, step: 0.01, digits: 2 },
    { key: "thermal_strength", label: "熱上昇強度", unit: "m/s", min: 0, max: 15, step: 0.05, digits: 2 },
    { key: "turbulence_scale", label: "乱流スケール", unit: "m", min: 0.05, max: 3, step: 0.01, digits: 2 },
    { key: "turbulence_seed", kind: "int", label: "乱流シード", min: 1, max: 999999, step: 1 },
    { key: "boundary_layer_enabled", kind: "toggle", label: "境界層プロファイル" },
    { key: "surface_roughness_m", label: "粗度長 z₀", unit: "mm", scale: 1000, min: 0.05, max: 5, step: 0.01, digits: 2 },
    { key: "wind_tunnel_enabled", kind: "toggle", label: "風洞固定" },
    { key: "wind_tunnel_speed", label: "風洞風速", unit: "m/s", min: 0, max: 60, step: 0.1, digits: 1 },
    { key: "wind_tunnel_alpha_deg", label: "風洞迎角 α", unit: DEG, min: -20, max: 35, step: 0.5, digits: 1 },
    { key: "wind_tunnel_beta_deg", label: "風洞滑角 β", unit: DEG, min: -30, max: 30, step: 0.5, digits: 1 },
  ],
  solver: [
    { key: "cd0", label: "抗力係数 C_D0", unit: "", min: 0.002, max: 0.2, step: 0.001, digits: 4, section: "aircraft" },
    { key: "oswald_e", label: "オズワルト係数 e", unit: "", min: 0.3, max: 1, step: 0.01, digits: 2, section: "aircraft" },
    { key: "cl_max", label: "最大揚力係数", unit: "", min: 0.2, max: 2, step: 0.01, digits: 2, section: "aircraft" },
    { key: "stall_alpha_deg", label: "失速迎角", unit: DEG, min: 4, max: 30, step: 0.5, digits: 1, section: "aircraft" },
    { key: "cm0", label: "ピッチモーメント C_m0", unit: "", min: -0.4, max: 0.4, step: 0.005, digits: 3, section: "aircraft" },
    { key: "cm_alpha", label: "C_mα", unit: "/rad", min: -3, max: 1, step: 0.01, digits: 2, section: "aircraft" },
    { key: "static_margin", label: "静的マージン", unit: "c", min: 0, max: 0.4, step: 0.005, digits: 3, section: "aircraft" },
    { key: "polhamus_kv", label: "前縁剥離渦 Kv", unit: "", min: 0, max: 3.5, step: 0.05, digits: 2, section: "aircraft" },
    { key: "step_trip_ratio", label: "段差トリップ比", unit: "×10⁻³", scale: 1000, min: 0, max: 20, step: 0.5, digits: 1, section: "aircraft" },
    { key: "cp_shift_gain", label: "風圧中心移動", unit: "", min: 0, max: 1.5, step: 0.05, digits: 2, section: "aircraft" },
    { key: "added_mass_enabled", kind: "toggle", label: "付加質量 (Added Mass)", section: "aircraft" },
    { key: "ground_pitch_coupling", label: "地面ピッチ連成", unit: "", min: 0, max: 2.0, step: 0.05, digits: 2, section: "aircraft" },
    { key: "ground_cushion_lift", label: "地面クッション揚力", unit: "", min: 0, max: 2.0, step: 0.05, digits: 2, section: "aircraft" },
    { key: "roll_inertia_kgm2", label: "ロール慣性", unit: "g·m²", scale: 1000, min: 0, max: 6, step: 0.01, digits: 2, section: "aircraft" },
    { key: "pitch_inertia_kgm2", label: "ピッチ慣性", unit: "g·m²", scale: 1000, min: 0, max: 6, step: 0.01, digits: 2, section: "aircraft" },
    { key: "yaw_inertia_kgm2", label: "ヨー慣性", unit: "g·m²", scale: 1000, min: 0, max: 6, step: 0.01, digits: 2, section: "aircraft" },
    { key: "dt_max", label: "最大刻み Δt", unit: "s", min: 0.001, max: 0.05, step: 0.001, digits: 3, section: "simulation" },
    { key: "dt_min", label: "最小刻み Δt", unit: "s", scale: 1000, min: 0.005, max: 20, step: 0.005, digits: 3, section: "simulation" },
    { key: "max_substeps", kind: "int", label: "サブステップ上限", min: 4, max: 512, step: 1, section: "simulation" },
    { key: "duration_s", label: "記録時間", unit: "s", min: 2, max: 60, step: 1, digits: 0, section: "simulation" },
    { key: "record_interval_s", label: "記録間隔", unit: "s", scale: 1000, min: 1, max: 100, step: 1, digits: 0, section: "simulation" },
    { key: "ring_buffer_capacity", kind: "int", label: "リング長", min: 500, max: 20000, step: 500, section: "simulation" },
    { key: "adaptive", kind: "toggle", label: "可変刻み", section: "simulation" },
    { key: "collision_enabled", kind: "toggle", label: "地面衝突", section: "simulation" },
    { key: "restitution", label: "反発係数", unit: "", min: 0, max: 0.9, step: 0.01, digits: 2, section: "simulation" },
    { key: "gravity", label: "重力 g", unit: "m/s²", min: 0.1, max: 30, step: 0.01, digits: 2, section: "environment" },
    { key: "drag_scale", label: "抗力倍率", unit: "×", min: 0, max: 5, step: 0.01, digits: 2, section: "environment" },
    { key: "lift_scale", label: "揚力倍率", unit: "×", min: 0, max: 5, step: 0.01, digits: 2, section: "environment" },
    { key: "ground_friction", label: "地面摩擦", unit: "", min: 0, max: 2, step: 0.01, digits: 2, section: "environment" },
    { key: "ground_effect_enabled", kind: "toggle", label: "地面効果", section: "environment" },
    { key: "compressibility_enabled", kind: "toggle", label: "圧縮性補正", section: "environment" },
  ],
};

const HOST_SECTION = {
  airframe: "aircraft",
  fold: "aircraft",
  throw: "launch",
  atmosphere: "environment",
  wind: "environment",
  solver: "aircraft",
};

export class ParameterPanels {
  constructor({ store, root, onChange, onCommit, onLaunch }) {
    this.store = store;
    this.root = root;
    this.onChange = onChange || (() => {});
    this.onCommit = onCommit || (() => {});
    this.onLaunch = onLaunch || (() => {});
    this.controls = [];
    this.throwStyleSelect = null;
    this.throwNote = null;
  }

  /* ------------------------------------------------------------ helpers */

  _toDisplay(spec, raw) {
    const scale = spec.scale === undefined ? 1 : spec.scale;
    const offset = spec.offset === undefined ? 0 : spec.offset;
    return raw * scale + offset;
  }

  _fromDisplay(spec, shown) {
    const scale = spec.scale === undefined ? 1 : spec.scale;
    const offset = spec.offset === undefined ? 0 : spec.offset;
    if (scale === 0) return shown;
    return (shown - offset) / scale;
  }

  /**
   * Slider bounds.  The table wins: it is authored in *display* units (g, hPa,
   * °C) while the schema publishes canonical SI ranges, and mixing the two
   * produces sliders that fight the server clamp.  Server-side clamping still
   * guarantees a legal payload.
   */
  _bounds(spec) {
    return [spec.min, spec.max];
  }

  /* -------------------------------------------------------------- build */

  build() {
    for (const [host, specs] of Object.entries(FIELD_TABLE)) {
      const container = this.root.querySelector(`[data-host="${host}"]`);
      if (!container) continue;
      container.textContent = "";
      for (const spec of specs) {
        const section = spec.section || HOST_SECTION[host];
        const node = this._buildField(spec, section);
        if (node) container.append(node);
      }
      if (host === "throw") this._buildThrowHead(container);
    }
    this.sync();
  }

  _buildThrowHead(container) {
    const styles = (this.store.schema.launch && this.store.schema.launch.throw_styles) || [];
    const block = make("div", { class: "field" });
    const top = make("div", { class: "field-top" }, [
      make("span", { class: "field-key", text: "投げ方プリセット" }),
      make("span", { class: "field-unit", text: "STYLE" }),
    ]);
    const select = make("select", { class: "select", id: "throw-style" });
    for (const style of styles) {
      select.append(make("option", { value: style.key, text: style.label_ja || style.label }));
    }
    const note = make("div", { class: "label-sm", style: "color:var(--muted);font-size:10.5px;line-height:1.4;margin-top:4px" });
    select.addEventListener("change", () => {
      const style = styles.find((entry) => entry.key === select.value);
      if (!style) return;
      this.store.merge("launch", style.overrides || {}, "style");
      note.textContent = style.notes || style.description || "";
      this.sync();
      this.onCommit("launch");
    });
    block.append(top, select, note);
    container.insertBefore(block, container.firstChild);
    this.throwStyleSelect = select;
    this.throwNote = note;
  }

  _buildField(spec, section) {
    const field = make("div", { class: "field" });
    const label = make("span", { class: "field-key", text: spec.label });
    const unit = make("span", { class: "field-unit", text: spec.unit || "" });
    const value = make("span", { class: "field-val" });
    const top = make("div", { class: "field-top" }, [label, unit, value]);

    if (spec.kind === "toggle") {
      const input = make("input", { type: "checkbox" });
      input.checked = Boolean(this.store.get(section, spec.key));
      input.addEventListener("change", () => {
        this.store.set(section, spec.key, input.checked, "ui");
        this.onChange(section, spec.key, input.checked);
        this.onCommit(section);
      });
      const box = make("span", { class: "switch-box" });
      const toggle = make("label", { class: "switch", style: "margin-top:2px" }, [
        input,
        box,
        make("span", { class: "switch-label muted", text: spec.hint || "有効" }),
      ]);
      field.append(top, toggle);
      this.controls.push({ spec, section, input, value, kind: "toggle" });
      return field;
    }

    if (spec.kind === "select") {
      const select = make("select", { class: "select" });
      for (const [optionValue, optionText] of spec.options) {
        select.append(make("option", { value: optionValue, text: optionText }));
      }
      select.addEventListener("change", () => {
        const parsed = Number(select.value);
        this.store.set(section, spec.key, Number.isFinite(parsed) ? parsed : select.value, "ui");
        this.onChange(section, spec.key, this.store.get(section, spec.key));
        this.onCommit(section);
      });
      field.append(top, select);
      this.controls.push({ spec, section, input: select, value, kind: "select" });
      return field;
    }

    const [min, max] = this._bounds(spec);
    const step = spec.step || (max - min) / 100;
    const digits = spec.digits === undefined ? 2 : spec.digits;
    const inputId = `param-${section}-${spec.key}`;

    const range = make("input", {
      type: "range",
      id: inputId,
      min,
      max,
      step,
      class: "field-range",
      "aria-label": `${spec.label}${spec.unit ? ` (${spec.unit})` : ""}`,
      "aria-valuemin": String(min),
      "aria-valuemax": String(max),
    });
    const number = make("input", {
      type: "number",
      class: "num-input",
      min,
      max,
      step,
      "aria-label": `${spec.label} 数値入力`,
    });
    range.addEventListener("input", () => {
      const raw = Number(range.value);
      if (!Number.isFinite(raw)) return;
      number.value = spec.kind === "int" ? String(Math.round(raw)) : raw.toFixed(digits);
      paintRange(range);
      this._write(spec, section, raw, number, range, value);
    });
    range.addEventListener("change", () => this.onCommit(section));
    number.addEventListener("change", () => {
      const raw = Number(number.value);
      if (!Number.isFinite(raw)) {
        this.sync();
        return;
      }
      const bounded = clamp(raw, min, max);
      range.value = String(bounded);
      paintRange(range);
      this._write(spec, section, bounded, number, range, value);
      this.onCommit(section);
    });

    const row = make("div", { class: "field-row" }, [range, number]);
    field.append(top, row);
    this.controls.push({ spec, section, input: range, number, value, kind: "slider", digits, min, max });
    return field;
  }

  _write(spec, section, shown, number, range, value) {
    const raw = this._fromDisplay(spec, shown);
    const stored = spec.kind === "int" ? Math.round(raw) : raw;
    this.store.set(section, spec.key, stored, "ui");
    value.textContent = spec.kind === "int" ? String(stored) : fmt(this._toDisplay(spec, stored), digits(spec));
    value.dataset.dirty = this._isDirty(section, spec.key, stored) ? "1" : "0";
    this.onChange(section, spec.key, stored);
    if (number && document.activeElement !== number) {
      number.value = spec.kind === "int" ? String(stored) : fmt(shown, digits(spec));
    }
  }

  _isDirty(section, key, value) {
    const fields = this.store.schema[section] && this.store.schema[section].fields;
    if (!fields || !fields[key]) return false;
    const fallback = fields[key].default;
    return isNum(fallback) && isNum(value) ? Math.abs(fallback - value) > 1e-9 : false;
  }

  /* --------------------------------------------------------------- sync */

  sync() {
    for (const control of this.controls) {
      const { spec, section, input, value } = control;
      const raw = this.store.get(section, spec.key);
      if (spec.kind === "toggle") {
        if (document.activeElement !== input) input.checked = Boolean(raw);
        continue;
      }
      if (spec.kind === "select") {
        input.value = String(raw ?? "");
        value.textContent = "";
        continue;
      }
      if (raw === undefined || raw === null) continue;
      const shown = this._toDisplay(spec, raw);
      if (document.activeElement !== input) {
        input.value = String(shown);
        paintRange(input);
      }
      if (control.number && document.activeElement !== control.number) {
        control.number.value = spec.kind === "int" ? String(Math.round(shown)) : fmt(shown, digits(spec));
      }
      value.textContent = spec.kind === "int" ? String(Math.round(shown)) : fmt(shown, digits(spec));
      value.dataset.dirty = this._isDirty(section, spec.key, raw) ? "1" : "0";
    }
    if (this.throwStyleSelect) {
      const key = this.store.get("launch", "throw_style");
      if (key) this.throwStyleSelect.value = key;
      const styles = (this.store.schema.launch && this.store.schema.launch.throw_styles) || [];
      const style = styles.find((entry) => entry.key === this.throwStyleSelect.value);
      if (style && this.throwNote) this.throwNote.textContent = style.notes || style.description || "";
    }
  }
}

function digits(spec) {
  return spec.digits === undefined ? 2 : spec.digits;
}