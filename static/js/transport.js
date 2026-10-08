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
    this.playIndex = 0;
    this.playTime = 0;
    this.history = null;
    this.metrics = null;
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
    const total = this.history ? this.history.length : this.frames;
    return {
      playing: this.playing,
      rate: this.rate,
      frames: total,
      index: this.playIndex,
      time: this.lastFrame ? Number(this.lastFrame.time_s) || 0 : 0,
      mode: "playback",
    };
  }

  /* ------------------------------------------------------------ controls */

  /** Launch a new flight simulation: precomputes the entire trajectory. */
  async launch(customPayload = null) {
    this.playing = false;
    this.busy = true;
    this.generation += 1;
    const currentGen = this.generation;

    const payload = customPayload || {
      aircraft: this.store.data.aircraft,
      environment: this.store.data.environment,
      launch: this.store.data.launch,
      simulation: this.store.data.simulation,
      duration_s: 25.0,
    };

    try {
      const result = await this.api.launch(payload);
      if (currentGen !== this.generation) return result;
      this.history = result.samples || [];
      this.metrics = result.metrics || null;
      this.frames = this.history.length;
      this.playIndex = 0;
      this.playTime = 0;
      this.stopped = false;

      if (this.history.length > 0) {
        this.seek(0);
      }
      if (this.metrics) {
        this.emit("metrics", this.metrics);
      }
      this.emit("launched", result);
      this.emit("state", this.state);
      // Auto-start playback of the computed flight
      this.play();
      return result;
    } catch (err) {
      this.emit("error", err);
      throw err;
    } finally {
      this.busy = false;
    }
  }

  /** Rewind current flight to start (t=0) without deleting computed trajectory. */
  async reset(reason = "user") {
    this.pause();
    if (this.history && this.history.length > 0) {
      this.seek(0);
      return;
    }
    // Fallback: request initial frame from backend
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

  play() {
    if (!this.history || !this.history.length) {
      // If no trajectory exists yet, compute a new flight
      this.launch();
      return;
    }
    // If at the end of the trajectory, loop back to start
    if (this.playIndex >= this.history.length - 1) {
      this.seek(0);
    }
    this.playing = true;
    this.stopped = false;
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

  /** Advance exactly one recorded sample towards the end. */
  stepForward() {
    this.pause();
    if (!this.history || !this.history.length) return;
    const next = clamp(this.playIndex + 1, 0, this.history.length - 1);
    this.seek(next);
  }

  /** Step one recorded sample backwards towards start. */
  stepBack() {
    this.pause();
    if (!this.history || !this.history.length) return;
    const prev = clamp(this.playIndex - 1, 0, this.history.length - 1);
    this.seek(prev);
  }

  seek(index) {
    if (!this.history || !this.history.length) return null;
    const i = clamp(Math.round(index), 0, this.history.length - 1);
    this.playIndex = i;
    const sample = this.history[i];
    this.lastFrame = sample;
    this.playTime = Number(sample.time_s) || 0;
    this.emit("frame", sample);
    this.emit("replay", sample);
    this.emit("state", this.state);
    return sample;
  }

  /** Nearest recorded frame for a wall-clock flight time. */
  seekTime(seconds) {
    if (!this.history || !this.history.length) return null;
    let best = 0;
    let bestDelta = Infinity;
    for (let i = 0; i < this.history.length; i++) {
      const delta = Math.abs((Number(this.history[i].time_s) || 0) - seconds);
      if (delta < bestDelta) {
        bestDelta = delta;
        best = i;
      }
    }
    return this.seek(best);
  }

  /* --------------------------------------------------------------- loop */

  _tick(now) {
    if (!this._handle) return;
    const wallDelta = clamp((now - this.lastWall) / 1000, 0, 0.25);
    this.lastWall = now;

    if (this.playing && this.history && this.history.length > 0) {
      this.playTime += wallDelta * this.rate;
      
      // Advance playIndex based on playTime
      let idx = this.playIndex;
      while (idx < this.history.length - 1 && (Number(this.history[idx].time_s) || 0) < this.playTime) {
        idx++;
      }

      if (idx !== this.playIndex) {
        this.playIndex = idx;
        const frame = this.history[idx];
        this.lastFrame = frame;
        this.emit("frame", frame);
        this.emit("replay", frame);
        this.emit("state", this.state);

        if (idx >= this.history.length - 1) {
          // Reached end of flight
          this.playing = false;
          this.stopped = true;
          this.emit("ended", frame);
          this.emit("state", this.state);
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