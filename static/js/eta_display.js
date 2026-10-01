/**
 * eta_display.js -- standalone ETA widget for the header bar.
 *
 * Renders a job record (the shape returned by
 * ``GET /api/v1/optimizer/status/<job_id>``) as:
 *   <algorithm label> / 評価 <evaluated_count>/<total_evaluations>
 *   [progress bar]
 *   HH:MM:SS   (estimated_completion, local time)
 *   残り mm:ss (eta_seconds)
 *
 * ``formatEta`` and ``formatClock`` are exported as pure functions and are
 * defensive against ``null`` / ``undefined`` / ``NaN`` / negative input.
 */

const ALGORITHM_LABELS = {
  "grid": "Coarse Grid Sweep",
  "nelder-mead": "Nelder-Mead",
  "differential-evolution": "Differential Evolution",
  "nsga2": "NSGA-II",
  "surrogate": "Surrogate",
};

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

const STATUS_TEXT = {
  queued: "待機中",
  running: "実行中",
  completed: "完了",
  failed: "失敗",
  cancelled: "中断",
};

/**
 * Coerce anything into a finite number, or return ``null``.
 * @param {*} value
 * @returns {number|null}
 */
function toFinite(value) {
  if (value === null || value === undefined || value === true || value === false) {
    return null;
  }
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) {
    return null;
  }
  return n;
}

/**
 * Format a duration in seconds as ``mm:ss`` (or ``h:mm:ss`` past one hour).
 * Negative, null, NaN and unparsable input yield the idle placeholder.
 * @param {number|string|null|undefined} seconds
 * @returns {string}
 */
export function formatEta(seconds) {
  const n = toFinite(seconds);
  if (n === null || n < 0) {
    return "--:--";
  }
  const total = Math.floor(n);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

/**
 * Format an ISO-8601 timestamp (or epoch milliseconds) as local ``HH:MM:SS``.
 * Anything unparsable yields ``--:--:--``.
 * @param {string|number|null|undefined} iso
 * @returns {string}
 */
export function formatClock(iso) {
  if (iso === null || iso === undefined || iso === "") {
    return "--:--:--";
  }
  let date = null;
  if (typeof iso === "number" && Number.isFinite(iso)) {
    // Heuristic: values below 1e11 are seconds, above are milliseconds.
    date = new Date(iso < 1e11 ? iso * 1000 : iso);
  } else {
    const text = String(iso).trim();
    if (text === "" || text === "None") {
      return "--:--:--";
    }
    date = new Date(text);
  }
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  const pad = (v) => String(v).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/**
 * Human readable algorithm label.
 * @param {string} algorithm
 * @returns {string}
 */
export function algorithmLabel(algorithm) {
  const key = String(algorithm || "").trim();
  if (key === "") {
    return "optimizer";
  }
  if (ALGORITHM_LABELS[key]) {
    return ALGORITHM_LABELS[key];
  }
  return key.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Is the supplied status one the job will never leave?
 * @param {string} status
 * @returns {boolean}
 */
export function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(String(status || "").toLowerCase());
}

const TEMPLATE = `
  <div class="eta-main">
    <span class="eta-name" data-eta="name">optimizer</span>
    <span class="eta-clock" data-eta="clock">--:--:--</span>
  </div>
  <div class="eta-progress">
    <div class="progress-track"><div class="progress-bar" data-eta="bar"></div></div>
    <div class="eta-remain" data-eta="remain">残り --:--</div>
  </div>
`;

/**
 * Header widget rendering a `JobRecord` shape.
 */
export class EtaDisplay {
  /**
   * @param {HTMLElement} root container element (typically `#eta-widget`)
   */
  constructor(root) {
    if (!root) {
      throw new Error("EtaDisplay requires a root element");
    }
    this.root = root;
    this.record = null;
    this.refs = {};
    this.#build();
    this.clear();
  }

  #build() {
    this.root.textContent = "";
    const fragment = document.createElement("div");
    fragment.className = "eta-widget";
    fragment.innerHTML = TEMPLATE;
    this.refs.widget = fragment;
    this.refs.name = fragment.querySelector('[data-eta="name"]');
    this.refs.clock = fragment.querySelector('[data-eta="clock"]');
    this.refs.bar = fragment.querySelector('[data-eta="bar"]');
    this.refs.remain = fragment.querySelector('[data-eta="remain"]');
    this.root.appendChild(fragment);
  }

  /** Drop any job record and show the idle placeholder. */
  clear() {
    this.record = null;
    this.refs.widget.className = "eta-widget";
    this.refs.name.textContent = "optimizer";
    this.refs.clock.textContent = "--:--:--";
    this.refs.bar.style.width = "0%";
    this.refs.bar.className = "progress-bar";
    this.refs.remain.textContent = "残り --:--";
    this.root.title = "実行中のジョブはありません";
  }

  /**
   * Render a job record.  Passing ``null`` resets to the idle placeholder.
   * @param {object|null} record
   */
  render(record) {
    if (!record || typeof record !== "object") {
      this.clear();
      return;
    }
    this.record = record;

    const status = String(record.status || "queued").toLowerCase();
    const algorithm = algorithmLabel(record.algorithm || record.name);
    const done = toFinite(record.evaluated_count);
    const total = toFinite(record.total_evaluations);
    const doneText = done === null ? "0" : String(Math.max(0, Math.round(done)));
    const totalText = total === null || total <= 0 ? "?" : String(Math.round(total));
    const progressRaw = toFinite(record.progress);
    const fraction = progressRaw === null
      ? (total && total > 0 ? Math.min(1, (done || 0) / total) : 0)
      : Math.min(1, Math.max(0, progressRaw));

    this.refs.widget.className = `eta-widget status-${status}`;
    this.refs.name.textContent = `${algorithm} / 評価 ${doneText}/${totalText}`;

    if (status === "completed") {
      this.refs.clock.textContent = `完了 ${formatClock(record.estimated_completion || record.finished_at || record.updated_at)}`;
    } else if (status === "failed") {
      this.refs.clock.textContent = `失敗 ${formatClock(record.updated_at)}`;
    } else if (status === "cancelled") {
      this.refs.clock.textContent = `中断 ${formatClock(record.updated_at)}`;
    } else if (status === "running") {
      this.refs.clock.textContent = formatClock(record.estimated_completion);
    } else {
      this.refs.clock.textContent = formatClock(record.estimated_completion);
    }

    this.refs.bar.style.width = `${(fraction * 100).toFixed(1)}%`;
    this.refs.bar.className = `progress-bar status-${status}`;

    const rate = toFinite(record.evals_per_second);
    const elapsed = toFinite(record.elapsed_seconds);
    let meta = `残り ${formatEta(record.eta_seconds)}`;
    if (rate !== null && rate > 0) {
      meta += ` · ${rate.toFixed(1)} ev/s`;
    }
    if (elapsed !== null && elapsed >= 0) {
      meta += ` · ${elapsed.toFixed(0)}s`;
    }
    this.refs.remain.textContent = meta;

    const message = String(record.message || "").trim();
    this.root.title = message
      ? `${STATUS_TEXT[status] || status}: ${message}`
      : `${STATUS_TEXT[status] || status} — ${algorithm}`;
  }

  /**
   * Convenience accessor used by the optimizer panel and the tests.
   * @returns {object|null}
   */
  getRecord() {
    return this.record;
  }

  /**
   * Is the currently rendered job terminal?
   * @returns {boolean}
   */
  isTerminal() {
    if (!this.record) {
      return true;
    }
    return isTerminalStatus(this.record.status);
  }
}

export default EtaDisplay;
