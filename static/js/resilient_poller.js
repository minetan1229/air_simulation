export class ResilientPoller {
  constructor(taskFn, { intervalMs = 800, maxBackoffMs = 15000, maxRetries = 8, onFatal } = {}) {
    this.taskFn = taskFn;
    this.baseIntervalMs = intervalMs;
    this.maxBackoffMs = maxBackoffMs;
    this.maxRetries = maxRetries;
    this.onFatal = onFatal;
    this._timer = 0;
    this._abort = null;
    this._running = false;
    this._failures = 0;
  }

  start() {
    if (this._running) return;
    this._running = true;
    this._failures = 0;
    this._schedule(0);
  }

  stop() {
    this._running = false;
    if (this._timer) clearTimeout(this._timer);
    this._timer = 0;
    this._abort?.abort();
    this._abort = null;
  }

  _schedule(delayMs) {
    if (!this._running) return;
    this._timer = setTimeout(() => this._runOnce(), delayMs);
  }

  async _runOnce() {
    if (!this._running) return;
    if (typeof document !== "undefined" && document.hidden) {
      this._schedule(this.maxBackoffMs);
      return;
    }
    this._abort = new AbortController();
    try {
      const shouldContinue = await this.taskFn(this._abort.signal);
      this._failures = 0;
      if (this._running && shouldContinue !== false) {
        this._schedule(this.baseIntervalMs);
      } else {
        this.stop();
      }
    } catch (err) {
      if (err?.name === "AbortError" || !this._running) return;
      this._failures += 1;
      if (this._failures >= this.maxRetries) {
        this.stop();
        this.onFatal?.(err);
        return;
      }
      const backoff = Math.min(this.baseIntervalMs * 2 ** this._failures, this.maxBackoffMs);
      this._schedule(backoff);
    }
  }
}