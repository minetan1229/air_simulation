/* lib.js — DOM, formatting and colour primitives shared by every module. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function on(target, type, handler, options) {
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}

export function make(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* ---------------------------------------------------------------- numbers */

export function isNum(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function clamp(value, low, high) {
  return value < low ? low : value > high ? high : value;
}

/** Fixed-width decimal with a dash for anything non-finite. */
export function fmt(value, digits = 2) {
  if (!isNum(value)) return "—";
  const fixed = value.toFixed(digits);
  return fixed === "-0" || /^-0\.0*$/.test(fixed) ? (0).toFixed(digits) : fixed;
}

/** Engineering-ish magnitude label: 1234 -> "1.23k". */
export function sig(value, digits = 3) {
  if (!isNum(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e6) return `${(value / 1e6).toFixed(digits - 1)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(digits - 1)}k`;
  if (abs >= 100) return value.toFixed(Math.max(0, digits - 3));
  if (abs >= 1) return value.toFixed(Math.max(0, digits - 1));
  if (abs === 0) return "0";
  return value.toPrecision(digits);
}

/* ------------------------------------------------------------------ time */

/** Seconds -> "MM:SS" (or "H:MM:SS" past an hour). */
export function clockFmt(seconds) {
  if (!isNum(seconds) || seconds < 0) return "--:--";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (v) => String(v).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/* -------------------------------------------------------------- scheduling */

export function debounce(fn, ms) {
  let handle = 0;
  const wrapped = (...args) => {
    if (handle) clearTimeout(handle);
    handle = setTimeout(() => {
      handle = 0;
      fn(...args);
    }, ms);
  };
  wrapped.cancel = () => {
    if (handle) clearTimeout(handle);
    handle = 0;
  };
  wrapped.flush = (...args) => {
    if (!handle) return;
    clearTimeout(handle);
    handle = 0;
    fn(...args);
  };
  return wrapped;
}

/* ------------------------------------------------------------------ files */

export function downloadText(filename, mimetype, text) {
  const blob = new Blob([text], { type: `${mimetype};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = make("a", { href: url, download: filename });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export function readArrayBuffer(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("file read failed"));
    reader.readAsArrayBuffer(file);
  });
}

export function readTextFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error || new Error("file read failed"));
    reader.readAsText(file);
  });
}

/* ------------------------------------------------------------------ misc */

export class Emitter {
  constructor() {
    this.handlers = new Map();
  }

  on(type, handler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type).add(handler);
    return () => this.off(type, handler);
  }

  off(type, handler) {
    const set = this.handlers.get(type);
    if (set) set.delete(handler);
  }

  emit(type, payload) {
    const set = this.handlers.get(type);
    if (!set) return;
    for (const handler of Array.from(set)) {
      try {
        handler(payload);
      } catch (error) {
        console.error(`[${type}] handler failed`, error);
      }
    }
  }
}

/** Serialise `value` as pretty JSON, never throwing on cycles. */
export function safeJSON(value, space = 2) {
  try {
    return JSON.stringify(value, null, space);
  } catch (error) {
    return String(value);
  }
}

/* ---------------------------------------------------------------- colour */

/**
 * Stress heat ramp: pale paper blue -> blue -> teal -> amber -> red.
 * The low end stays light so an unloaded wing still reads as paper instead of
 * turning the whole airframe navy.  Shared by the vertex colours, the
 * trajectory and the viewport legend so all three agree.
 */
const RAMP = [
  [0.0, [0.79, 0.86, 0.94]],
  [0.2, [0.33, 0.65, 0.87]],
  [0.45, [0.24, 0.76, 0.58]],
  [0.7, [0.95, 0.74, 0.18]],
  [1.0, [0.78, 0.13, 0.08]],
];

export function heatColour(index) {
  const t = clamp(isNum(index) ? index : 0, 0, 1);
  for (let i = 1; i < RAMP.length; i += 1) {
    if (t <= RAMP[i][0]) {
      const [t0, c0] = RAMP[i - 1];
      const [t1, c1] = RAMP[i];
      const f = (t - t0) / (t1 - t0 || 1);
      return [
        Math.round(255 * (c0[0] + (c1[0] - c0[0]) * f)),
        Math.round(255 * (c0[1] + (c1[1] - c0[1]) * f)),
        Math.round(255 * (c0[2] + (c1[2] - c0[2]) * f)),
      ];
    }
  }
  const last = RAMP[RAMP.length - 1][1];
  return [Math.round(last[0] * 255), Math.round(last[1] * 255), Math.round(last[2] * 255)];
}

export function paintRamp(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  for (let x = 0; x < w; x += 1) {
    const [r, g, b] = heatColour(x / (w - 1));
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(x, 0, 1, h);
  }
}

/** Set a range input's painted fill without touching its value. */
export function paintRange(input) {
  const min = Number(input.min || 0);
  const max = Number(input.max || 100);
  const value = Number(input.value || 0);
  const ratio = max === min ? 0 : clamp((value - min) / (max - min), 0, 1);
  input.style.setProperty("--fill", `${(ratio * 100).toFixed(2)}%`);
}