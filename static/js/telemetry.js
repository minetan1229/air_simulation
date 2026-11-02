/*
 * telemetry.js — instrument read-outs, the flight recorder strip chart and
 * the drag polar.  All three are hand-drawn on 2D canvases so the panels keep
 * a hairline instrument look at any DPR and cost no chart library.
 */

import { clear, clamp, fmt, isNum, make, sig } from "./lib.js";

const _root = typeof document !== "undefined" ? getComputedStyle(document.documentElement) : null;
const _cssColor = (name, fallback) => (_root ? (_root.getPropertyValue(name).trim() || fallback) : fallback);

/* ------------------------------------------------------------------ gauges */

const GAUGE_SPECS = [
  { id: "altitude", label: "高度", unit: "m", digits: 2 },
  { id: "speed", label: "速度", unit: "m/s", digits: 2 },
  { id: "alpha", label: "迎角 α", unit: "°", digits: 2, tone: "stall" },
  { id: "beta", label: "滑角 β", unit: "°", digits: 2 },
  { id: "ld", label: "L/D", unit: "", digits: 2 },
  { id: "gload", label: "G ロード", unit: "g", digits: 2 },
  { id: "q", label: "動圧 q", unit: "Pa", digits: 1 },
  { id: "re", label: "レイノルズ", unit: "", digits: 0, scale: 1e-3, unitScale: "k" },
  { id: "mach", label: "マッハ", unit: "", digits: 3 },
  { id: "stress", label: "最大応力", unit: "Pa", digits: 0, scale: 1e-3, unitScale: "k", tone: "stress" },
  { id: "stressIndex", label: "応力指数", unit: "", digits: 3, tone: "stress" },
  { id: "wind", label: "風速 V∞", unit: "m/s", digits: 2 },
];

const AIR_SPECS = [
  { id: "rho", label: "密度 ρ", unit: "kg/m³", digits: 4 },
  { id: "mu", label: "動粘性 μ", unit: "Pa·s", digits: 3, scale: 1e-5, unitScale: "e-5" },
  { id: "nu", label: "動粘性 ν", unit: "m²/s", digits: 3, scale: 1e-6, unitScale: "e-6" },
  { id: "sound", label: "音速 a", unit: "m/s", digits: 1 },
  { id: "temp", label: "気温", unit: "°C", digits: 1 },
  { id: "press", label: "気圧", unit: "hPa", digits: 1 },
];

export class Gauges {
  constructor(container, specs) {
    this.specs = specs;
    this.nodes = new Map();
    clear(container);
    for (const spec of specs) {
      const value = make("dd", {}, [
        make("span", { class: "value" }),
        make("small", { text: spec.unit }),
        make("span", { class: "peak" }),
      ]);
      const cell = make("div", { class: "gauge" }, [make("dt", { text: spec.label }), value]);
      container.append(cell);
      this.nodes.set(spec.id, {
        cell,
        value: value.querySelector(".value"),
        peak: value.querySelector(".peak"),
        spec,
        peakValue: null,
        showPeak: false,
      });
    }
  }

  resetPeaks() {
    for (const node of this.nodes.values()) {
      node.peakValue = null;
      node.peak.textContent = "";
    }
  }

  set(id, raw, tone) {
    const node = this.nodes.get(id);
    if (!node) return;
    const { spec } = node;
    if (!isNum(raw)) {
      node.value.textContent = "—";
      node.cell.dataset.tone = "";
      return;
    }
    const scaled = spec.scale ? raw * spec.scale : raw;
    node.value.textContent = fmt(scaled, spec.digits);
    let level = "";
    if (tone) level = tone;
    else if (spec.tone === "stall" && Math.abs(raw) > 14) level = "warn";
    else if (spec.tone === "stress" && raw > 0.6) level = "bad";
    else if (spec.tone === "stress" && raw > 0.35) level = "warn";
    else level = "ok";
    node.cell.dataset.tone = level;
    if (node.showPeak) {
      const magnitude = spec.scale ? Math.abs(scaled) : Math.abs(raw);
      if (node.peakValue === null || magnitude > node.peakValue) {
        node.peakValue = magnitude;
        node.peak.textContent = `pk ${fmt(magnitude, spec.digits)}`;
      }
    }
  }
}

/* ------------------------------------------------------------- strip chart */

const CHART_ROWS = [
  { key: "alt", label: "高度", unit: "m", colour: _cssColor("--color-alt", "#2f6fb5"), digits: 2 },
  { key: "spd", label: "速度", unit: "m/s", colour: _cssColor("--color-spd", "#2f7d4f"), digits: 2 },
  { key: "aoa", label: "迎角", unit: "°", colour: _cssColor("--color-aoa", "#ad6a05"), digits: 2 },
  { key: "stress", label: "応力", unit: "kPa", colour: _cssColor("--color-stress", "#bf3b2b"), digits: 2, scale: 1e-3 },
];

const PAD = { left: 52, right: 46, top: 8, bottom: 18 };

class Ring {
  constructor(capacity, stride) {
    this.capacity = capacity;
    this.stride = stride;
    this.data = new Float32Array(capacity * stride);
    this.size = 0;
    this.head = 0;
  }

  push(values) {
    const offset = this.head * this.stride;
    for (let i = 0; i < this.stride; i += 1) this.data[offset + i] = values[i];
    this.head = (this.head + 1) % this.capacity;
    if (this.size < this.capacity) this.size += 1;
  }

  clear() {
    this.size = 0;
    this.head = 0;
    this.data.fill(0);
  }

  at(index) {
    const start = (this.head - this.size + this.capacity) % this.capacity;
    const offset = ((start + index) % this.capacity) * this.stride;
    return offset;
  }

  get(index, lane) {
    return this.data[this.at(index) + lane];
  }
}

export class StripChart {
  constructor(canvas, { capacity = 3000, onCursor = null } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.ring = new Ring(capacity, 5);
    this.hoverIndex = null;
    this.dpr = 1;
    this.onCursor = onCursor;
    this.resize();
    canvas.addEventListener("mousemove", (event) => this._onMove(event));
    canvas.addEventListener("mouseleave", () => {
      this.hoverIndex = null;
      if (this.onCursor) this.onCursor(null);
      this.draw();
    });
    this._observer = new ResizeObserver(() => {
      this.resize();
      this.draw();
    });
    this._observer.observe(canvas);
  }

  get length() {
    return this.ring.size;
  }

  push(sample) {
    this.ring.push([
      Number(sample.time_s) || 0,
      Number(sample.altitude_m) || 0,
      Number(sample.speed_mps) || 0,
      Number(sample.alpha_deg) || 0,
      Number(sample.max_stress_pa) || 0,
    ]);
  }

  clear() {
    this.ring.clear();
    this.hoverIndex = null;
    this.draw();
  }

  resize() {
    const host = this.canvas.parentElement || this.canvas;
    const width = Math.max(host.clientWidth, 80);
    const height = Math.max(this.canvas.clientHeight || 232, 120);
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(width * this.dpr);
    this.canvas.height = Math.round(height * this.dpr);
    this.width = width;
    this.height = height;
  }

  _onMove(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const inner = this.width - PAD.left - PAD.right;
    if (this.ring.size < 2 || inner <= 0) return;
    const ratio = clamp((x - PAD.left) / inner, 0, 1);
    this.hoverIndex = Math.round(ratio * (this.ring.size - 1));
    if (this.onCursor) this.onCursor(this.cursorLine());
    this.draw();
  }

  _scales() {
    const rows = CHART_ROWS.map((spec, lane) => {
      let low = 0;
      let high = 0;
      for (let i = 0; i < this.ring.size; i += 1) {
        const value = this.ring.get(i, lane + 1);
        if (value < low) low = value;
        if (value > high) high = value;
      }
      const span = Math.max(high - low, Math.abs(high) * 0.08, 1e-6);
      return { spec, lane, dataLow: low, dataHigh: high, low: low - span * 0.12, high: high + span * 0.18 };
    });
    return rows;
  }

  draw() {
    const ctx = this.ctx;
    if (!ctx) return;
    const { width, height, dpr } = this;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const plotLeft = PAD.left;
    const plotRight = width - PAD.right;
    const plotTop = PAD.top;
    const plotBottom = height - PAD.bottom;
    const innerW = plotRight - plotLeft;
    const innerH = plotBottom - plotTop;
    const rowH = innerH / CHART_ROWS.length;
    const rows = this._scales();

    const first = this.ring.size ? this.ring.get(0, 0) : 0;
    const last = this.ring.size ? this.ring.get(this.ring.size - 1, 0) : 1;
    const tMin = first;
    const tMax = Math.max(last, first + 0.5);

    ctx.lineWidth = 1;
    ctx.strokeStyle = "#eceae4";
    ctx.fillStyle = "#858a91";
    ctx.font = '9px ui-monospace, Consolas, monospace';
    ctx.textAlign = "center";
    ctx.textBaseline = "top";

    const seconds = Math.max(1, Math.ceil(tMax - tMin));
    const tickStep = seconds > 20 ? 5 : 1;
    for (let t = Math.ceil(tMin); t <= tMax; t += tickStep) {
      const x = Math.round(plotLeft + ((t - tMin) / (tMax - tMin)) * innerW) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, plotTop);
      ctx.lineTo(x, plotBottom);
      ctx.stroke();
      ctx.fillText(`${t.toFixed(0)}s`, x, plotBottom + 4);
    }

    rows.forEach((row, index) => {
      const top = plotTop + index * rowH;
      const bottom = top + rowH;
      const scale = Math.max(row.high - row.low, 1e-9);

      ctx.strokeStyle = "#e3e0d9";
      ctx.beginPath();
      ctx.moveTo(plotLeft, Math.round(bottom) + 0.5);
      ctx.lineTo(plotRight, Math.round(bottom) + 0.5);
      ctx.stroke();

      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "#6f747b";
      ctx.font = '9.5px ui-monospace, Consolas, monospace';
      ctx.fillText(`${row.spec.label} ${row.spec.unit}`, plotLeft - 6, top + rowH / 2 - 6);
      ctx.fillStyle = "#a9a49b";
      ctx.font = '8.5px ui-monospace, Consolas, monospace';
      // The unit factor of the row (Pa -> kPa, 1e-3 for the stress lane) and
      // the vertical scale of the plot are two different things; they used to
      // share the name ``scale``, which is a redeclaration in the same block
      // and therefore a SyntaxError that killed the whole module -- and with
      // it the application, because app.js imports the gauges from here.
      const unit_scale = row.spec.scale || 1;
      ctx.fillText(this.ring.size ? sig(row.dataHigh * unit_scale, 3) : "—", plotLeft - 6, top + rowH / 2 + 6);

      if (this.ring.size < 2) return;
      const value = this.ring.get(this.ring.size - 1, row.lane + 1);
      ctx.textAlign = "left";
      ctx.fillStyle = row.spec.colour;
      ctx.font = '10px ui-monospace, Consolas, monospace';
      ctx.fillText(fmt(value * (row.spec.scale || 1), row.spec.digits), plotRight + 5, top + rowH / 2);

      ctx.strokeStyle = row.spec.colour;
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      for (let i = 0; i < this.ring.size; i += 1) {
        const t = this.ring.get(i, 0);
        const v = this.ring.get(i, row.lane + 1);
        const x = plotLeft + ((t - tMin) / (tMax - tMin)) * innerW;
        const y = bottom - ((v - row.low) / scale) * rowH;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.lineWidth = 1;
    });

    if (this.ring.size >= 2 && this.hoverIndex !== null) {
      const index = clamp(this.hoverIndex, 0, this.ring.size - 1);
      const t = this.ring.get(index, 0);
      const x = Math.round(plotLeft + ((t - tMin) / (tMax - tMin)) * innerW) + 0.5;
      ctx.strokeStyle = "#8a9099";
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, plotTop);
      ctx.lineTo(x, plotBottom);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  cursorLine() {
    if (this.ring.size < 2 || this.hoverIndex === null) return null;
    const index = clamp(this.hoverIndex, 0, this.ring.size - 1);
    return {
      time_s: this.ring.get(index, 0),
      altitude_m: this.ring.get(index, 1),
      speed_mps: this.ring.get(index, 2),
      alpha_deg: this.ring.get(index, 3),
      max_stress_pa: this.ring.get(index, 4),
    };
  }

  sampleAt(index) {
    if (this.ring.size < 1) return null;
    const i = clamp(index, 0, this.ring.size - 1);
    return {
      time_s: this.ring.get(i, 0),
      altitude_m: this.ring.get(i, 1),
      speed_mps: this.ring.get(i, 2),
      alpha_deg: this.ring.get(i, 3),
      max_stress_pa: this.ring.get(i, 4),
      x_m: NaN,
      y_m: NaN,
      z_m: NaN,
    };
  }
}

/* -------------------------------------------------------------- polar plot */

export class PolarChart {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.payload = null;
    this.operating = null;
    this.resize();
    this._observer = new ResizeObserver(() => {
      this.resize();
      this.draw();
    });
    this._observer.observe(canvas);
  }

  resize() {
    const host = this.canvas.parentElement || this.canvas;
    const width = Math.max(host.clientWidth, 80);
    const height = Math.max(this.canvas.clientHeight || 168, 100);
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(width * this.dpr);
    this.canvas.height = Math.round(height * this.dpr);
    this.width = width;
    this.height = height;
  }

  setPayload(payload) {
    this.payload = payload;
    this.draw();
  }

  setOperating(point) {
    this.operating = point;
    this.draw();
  }

  draw() {
    const ctx = this.ctx;
    if (!ctx) return;
    const { width, height, dpr } = this;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const left = 34;
    const right = width - 30;
    const top = 10;
    const bottom = height - 18;
    const payload = this.payload;

    ctx.strokeStyle = "#e3e0d9";
    ctx.lineWidth = 1;
    ctx.font = '8.5px ui-monospace, Consolas, monospace';
    ctx.fillStyle = "#a9a49b";

    if (!payload || !payload.cl || !payload.cl.length) {
      ctx.fillStyle = "#858a91";
      ctx.font = '10px ui-monospace, Consolas, monospace';
      ctx.textAlign = "center";
      ctx.fillText("極線未計算", width / 2, height / 2);
      this._axes(left, right, top, bottom, -0.5, 0.6, 0, 0.12);
      return;
    }

    const clValues = payload.cl;
    let clMin = Math.min(...clValues);
    let clMax = Math.max(...clValues);
    let cdMin = Math.min(...payload.cd);
    let cdMax = Math.max(...payload.cd);
    const clPad = Math.max((clMax - clMin) * 0.12, 0.05);
    const cdPad = Math.max((cdMax - cdMin) * 0.15, 0.004);
    clMin -= clPad;
    clMax += clPad;
    cdMin = Math.max(0, cdMin - cdPad);
    cdMax += cdPad;

    const ldValues = payload.ld_ratio.filter((v) => isNum(v));
    const ldMin = ldValues.length ? Math.min(...ldValues) : 0;
    const ldMax = ldValues.length ? Math.max(...ldValues) : 0;

    const px = (cl) => left + ((cl - clMin) / (clMax - clMin || 1)) * (right - left);
    const py = (cd) => bottom - ((cd - cdMin) / (cdMax - cdMin || 1)) * (bottom - top);

    this._axes(left, right, top, bottom, clMin, clMax, cdMin, cdMax, ldMin, ldMax);

    // C_D(C_L)
    ctx.strokeStyle = "#2f6fb5";
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    payload.cl.forEach((cl, i) => {
      const x = px(cl);
      const y = py(payload.cd[i]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // (L/D) on the right-hand axis
    ctx.strokeStyle = "#2f7d4f";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 2]);
    ctx.beginPath();
    payload.cl.forEach((cl, i) => {
      const ld = payload.ld_ratio[i];
      if (!isNum(ld)) return;
      const x = px(cl);
      const ratio = (ld - ldMin) / (ldMax - ldMin || 1);
      const y = bottom - ratio * (bottom - top);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "#6f747b";
    ctx.font = '8.5px ui-monospace, Consolas, monospace';
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(`C_D ${cdMin.toFixed(3)}–${cdMax.toFixed(3)}`, left + 2, top + 2);
    ctx.textAlign = "right";
    ctx.fillStyle = "#2f7d4f";
    ctx.fillText(`L/D max ${sig(payload.ld_max, 4)}`, right, top + 2);

    // markers
    if (isNum(payload.cl_at_cd_min)) {
      ctx.fillStyle = "#ad6a05";
      ctx.fillRect(px(payload.cl_at_cd_min) - 2.5, py(payload.cd_at_cd_min) - 2.5, 5, 5);
    }
    if (isNum(payload.cl_at_ld_max)) {
      ctx.strokeStyle = "#ad6a05";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(px(payload.cl_at_ld_max), py(payload.cd_at_ld_max), 4.5, 0, Math.PI * 2);
      ctx.stroke();
    }
    const op = this.operating;
    if (op && isNum(op.cl)) {
      ctx.fillStyle = "#bf3b2b";
      ctx.beginPath();
      ctx.arc(px(op.cl), py(op.cd), 3.2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.lineWidth = 1;
  }

  _axes(left, right, top, bottom, clMin, clMax, cdMin, cdMax, ldMin, ldMax) {
    const ctx = this.ctx;
    ctx.strokeStyle = "#dedad1";
    ctx.beginPath();
    ctx.moveTo(left + 0.5, top);
    ctx.lineTo(left + 0.5, bottom + 0.5);
    ctx.lineTo(right, bottom + 0.5);
    ctx.stroke();

    ctx.fillStyle = "#858a91";
    ctx.font = '8px ui-monospace, Consolas, monospace';
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    // 左軸：C_D（抗力係数）
    for (let i = 0; i <= 3; i += 1) {
      const cd = cdMin + ((cdMax - cdMin) * i) / 3;
      const y = bottom - (i / 3) * (bottom - top);
      ctx.fillText(cd.toFixed(3), left - 4, y - 4);
    }
    // 右軸：L/D（滑空比）。描画曲線と同一軸にするため C_D ではなく L/D 目盛を並べる
    ctx.textAlign = "right";
    for (let i = 0; i <= 3; i += 1) {
      const ld = ldMin + ((ldMax - ldMin) * i) / 3;
      const y = bottom - (i / 3) * (bottom - top);
      ctx.fillText(ld.toFixed(1), right + 4, y - 4);
    }
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i += 1) {
      const cl = clMin + ((clMax - clMin) * i) / 4;
      const x = left + (i / 4) * (right - left);
      ctx.fillText(cl.toFixed(2), x, bottom + 4);
    }
    ctx.textAlign = "right";
    ctx.fillText("C_L →", right, bottom + 4);
    ctx.textAlign = "left";
    ctx.fillText("L/D →", right + 6, top - 10);
  }
}