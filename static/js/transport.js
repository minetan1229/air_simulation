/*
 * transport.js — playback engine.
 *
 * Owns the /simulation/step cadence, the rate control, frame stepping,
 * scrubbing through the server ring buffer and the periodic wind/stress
 * refreshes that feed the viewport.  Everything else in the console reacts to
 * the events emitted here.
 */

import { Emitter, clamp, fmt, isNum } from "./lib.js";

export const RATES = [0.25, 0.5, 1, 2, 5, 10];
const STRESS_INTERVAL_MS = 220;
const WIND_INTERVAL_MS = 900;
const MAX_STEP_CALLS_PER_SECOND = 60;

export class Transport extends Emitter {
  constructor({ api, store }) {
    super();
    this.api = api;
    this.store = store;
    this.playing = false;
    this.rate = 1;
    this.accumulator = 0;
    this.inFlight = false;
    this.lastFrame = null;
    this.frames = 0;
    this.scrubIndex = null;
    this.history = null;
    this.stressIndex = null;
    this.windPayload = null;
    this.tunnelMode = false;
    this.stopped = false;
    this.generation = 0;
    this.lastStressAt = 0;
    this.lastWindAt = 0;
    this.lastWall = 0;
    this.windDomain = { min: [-3, -3, -0.6], max: [3, 3, 1.6] };
    this.busy = false;
    this._tick = this._tick.bind(this);
    this._handle = 0;
  }

  attach() {
    if (!this._handle) this._handle = requestAnimationFrame(this._tick);
    this.lastWall = performance.now();
  }

  detach() {
    if (this._handle) cancelAnimationFrame(this._handle);
    this._handle = 0;
  }

  get state() {
    return {
      playing: this.playing,
      rate: this.rate,
      frames: this.frames,
      time: this.lastFrame ? Number(this.lastFrame.time_s) || 0 : 0,
      mode: this.scrubIndex === null ? "live" : "replay",
    };
  }

  /* ------------------------------------------------------------ controls */

  async reset(reason = "user") {
    this.playing = false;
    this.scrubIndex = null;
    this.history = null;
    this.stopped = false;
    this.accumulator = 0;
    this.lastFrame = null;
    this.frames = 0;
    // Invalidate responses of requests that were in flight: they belong to the
    // flight that just ended and must not repopulate the new one.
    this.generation += 1;
    const payload = await this.api.reset({
      aircraft: this.store.data.aircraft,
      environment: this.store.data.environment,
      launch: this.store.data.launch,
      simulation: this.store.data.simulation,
    });
    this.store.adopt(payload);
    this.emit("reset", payload);
    this.emit("frame", null);
    this.emit("state", this.state);
    return payload;
  }

  async play() {
    if (this.stopped) await this.reset("auto");
    if (this.scrubIndex !== null) {
      // A scrubbed view is a read-only replay: jump back to the live tail.
      this.scrubIndex = null;
      this.emit("live", null);
    }
    this.playing = true;
    this.lastWall = performance.now();
    this.emit("state", this.state);
  }

  pause() {
    this.playing = false;
    this.emit("state", this.state);
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  setRate(rate) {
    const value = Number(rate);
    if (!Number.isFinite(value) || value <= 0) return;
    this.rate = clamp(value, RATES[0], RATES[RATES.length - 1]);
    this.emit("state", this.state);
  }

  cycleRate(direction) {
    const index = RATES.indexOf(this.rate);
    const next = clamp(index + (direction > 0 ? 1 : -1), 0, RATES.length - 1);
    this.setRate(RATES[next]);
  }

  /** Advance exactly one recorded sample towards the live edge. */
  async stepForward() {
    this.playing = false;
    await this._loadHistory();
    if (!this.history || !this.history.length) {
      await this._singleStep();
      return;
    }
    const current = this.scrubIndex === null ? this.history.length - 1 : this.scrubIndex;
    const next = clamp(current + 1, 0, this.history.length - 1);
    await this.seek(next);
  }

  async stepBack() {
    this.playing = false;
    await this._loadHistory();
    if (!this.history || !this.history.length) return;
    const current = this.scrubIndex === null ? this.history.length - 1 : this.scrubIndex;
    await this.seek(clamp(current - 1, 0, this.history.length - 1));
  }

  async seek(index) {
    await this._loadHistory();
    if (!this.history || !this.history.length) return null;
    const i = clamp(Math.round(index), 0, this.history.length - 1);
    this.scrubIndex = i;
    const sample = await this.api.frame(i).catch(() => null);
    const payload = sample ? sample.sample : this.history[i];
    if (payload) {
      this.emit("replay", payload);
      this.emit("state", this.state);
    }
    return payload;
  }

  /** Nearest recorded frame for a wall-clock time. */
  async seekTime(seconds) {
    await this._loadHistory();
    if (!this.history || !this.history.length) return null;
    let best = 0;
    let bestDelta = Infinity;
    this.history.forEach((sample, index) => {
      const delta = Math.abs((Number(sample.time_s) || 0) - seconds);
      if (delta < bestDelta) {
        bestDelta = delta;
        best = index;
      }
    });
    return this.seek(best);
  }

  async _loadHistory() {
    if (this.history) return this.history;
    const payload = await this.api.history().catch(() => null);
    this.history = payload ? payload.samples : null;
    if (payload && payload.metrics) this.emit("metrics", payload.metrics);
    return this.history;
  }

  async _singleStep() {
    const settings = this.store.data.simulation;
    const payload = await this.api.step({
      dt: Number(settings.dt_max) || 0.02,
      substeps: Number(settings.max_substeps) || 64,
    });
    this._absorb(payload);
    return payload;
  }

  /* --------------------------------------------------------------- loop */

  _tick(now) {
    if (!this._handle) return;
    const wallDelta = clamp((now - this.lastWall) / 1000, 0, 0.25);
    this.lastWall = now;

    if (this.playing && !this.inFlight) {
      const budget = 1000 / MAX_STEP_CALLS_PER_SECOND;
      if (now - (this.lastCallAt || 0) >= budget) {
        this.lastCallAt = now;
        this.accumulator += wallDelta * this.rate;
        const settings = this.store.data.simulation;
        const dtMax = Number(settings.dt_max) || 0.02;
        const dtMaxEffective = Math.min(dtMax * Math.max(1, this.rate), 0.16);
        const dt = Math.min(this.accumulator, dtMaxEffective);
        if (dt > 1e-4) {
          this.inFlight = true;
          this.busy = true;
          const baseSubsteps = Number(settings.max_substeps) || 64;
          const substeps = Math.min(Math.round(baseSubsteps * Math.sqrt(Math.max(1, this.rate))), 512);
          const generation = this.generation;
          this.api
            .step({ dt, substeps })
            .then((payload) => {
              if (generation !== this.generation) return; // superseded by a reset
              const advanced = isNum(payload.dt) ? payload.dt : dt;
              this.accumulator = Math.max(this.accumulator - advanced, 0);
              this._absorb(payload);
            })
            .catch((error) => {
              this.emit("error", error);
              this.playing = false;
            })
            .finally(() => {
              this.inFlight = false;
              this.busy = false;
            });
        }
      }
    }

    if (this.playing || this.tunnelMode) {
      if (now - this.lastStressAt > STRESS_INTERVAL_MS) {
        this.lastStressAt = now;
        this._pollStress();
      }
      if (now - this.lastWindAt > WIND_INTERVAL_MS) {
        this.lastWindAt = now;
        this._pollWind();
      }
    }

    this._handle = requestAnimationFrame(this._tick);
  }

  _absorb(payload) {
    if (!payload) return;
    this.lastFrame = payload;
    this.frames = Number(payload.frames) || this.frames;
    if (this.history) {
      this.history.push(payload);
      if (this.history.length > 20000) this.history.shift();
    }
    this.emit("frame", payload);
    if (payload.landed || payload.crashed || payload.diverged) {
      this.playing = false;
      this.stopped = true;
      this.emit("ended", payload);
    }
    this.emit("state", this.state);
  }

  async _pollStress() {
    let speed = 0;
    let beta = 0;
    let loadFactor = 1.0;
    if (this.tunnelMode) {
      const env = this.store.data.environment || {};
      speed = Number(env.wind_tunnel_speed) || 12;
      beta = Number(env.wind_tunnel_beta_deg) || 0;
    } else {
      const frame = this.lastFrame;
      if (!frame) return;
      speed = Number(frame.speed_mps) || 0;
      beta = Number(frame.beta_deg) || 0;
      loadFactor = isNum(frame.g_load) ? frame.g_load : 1.0;
    }
    const payload = await this.api
      .stress({
        speed_mps: speed,
        density_kgm3: Number(this.store.air.density_kgm3) || 1.225,
        beta_deg: beta,
        load_factor: loadFactor,
        aircraft: this.store.data.aircraft,
        environment: this.store.data.environment,
        spanwise: 20,
        chordwise: 12,
      })
      .catch(() => null);
    if (!payload) return;
    this.stressIndex = payload;
    this.emit("stress", payload);
  }

  async _pollWind() {
    const frame = this.lastFrame;
    const half = this.tunnelMode ? 1.4 : 2.6;
    let centreX = 0;
    let centreY = 0;
    let altitudeM = 1.2;

    if (this.tunnelMode) {
      centreX = 0;
      centreY = 0;
      altitudeM = 1.2;
    } else if (frame && frame.position_m) {
      centreX = Number(frame.position_m[0]) || 0;
      centreY = Number(frame.position_m[1]) || 0;
      altitudeM = Math.max(-Number(frame.position_m[2]) || 1.2, 0.1);
    }

    const minZ = Math.max(altitudeM - 0.9, 0.05);
    const maxZ = altitudeM + 1.1;
    const min = [centreX - half, centreY - half, minZ];
    const max = [centreX + half, centreY + half, maxZ];
    this.windDomain = { min, max };
    const resolution = this.tunnelMode ? [9, 9, 6] : [11, 11, 7];
    const time = frame ? Number(frame.time_s) || 0 : 0;
    const payload = await this.api
      .wind({
        bounds_min: min,
        bounds_max: max,
        resolution,
        time_s: time,
        environment: this.store.data.environment,
      })
      .catch(() => null);
    if (!payload) return;
    payload.tunnelMode = this.tunnelMode;
    this.windPayload = payload;
    this.emit("wind", payload);
  }

  /* ----------------------------------------------------------- summaries */

  describe() {
    const frame = this.lastFrame;
    return {
      time: frame ? Number(frame.time_s) || 0 : 0,
      speed: frame ? Number(frame.speed_mps) || 0 : 0,
      alpha: frame ? Number(frame.alpha_deg) || 0 : 0,
      status: frame ? frame.status || (frame.landed ? "landed" : "flying") : "idle",
      text: frame ? `${fmt(frame.time_s, 3)} s` : "—",
    };
  }
}