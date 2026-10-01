/**
 * time_controller.js -- playback clock, frame stepping and timeline scrubbing.
 *
 * The physics state lives on the server: ``POST /api/v1/simulation/step`` moves
 * it forward by one integration step and ``POST /api/v1/simulation/reset``
 * rewinds it to the launch condition.  The server keeps the frames in a ring
 * buffer, so a frame that has already been evicted can no longer be stepped
 * *back* into.
 *
 * This class therefore owns three pieces of state:
 *   - ``frameCount``   : how many frames the server currently holds
 *   - ``currentFrame`` : the cursor the user is looking at
 *   - ``retainedFrom`` : the oldest frame index the server still holds
 *
 * Seeking / stepping to a frame below ``retainedFrom`` cannot be served from
 * the ring buffer.  Instead of silently doing nothing, the controller emits a
 * ``replay`` directive; ``main.js`` answers it with a ``/simulation/reset``
 * followed by enough ``/simulation/step`` calls to walk the state forward to
 * the requested frame.  This is documented behaviour, not a fallback.
 *
 * Playback rate control lives here too: ``tick()`` is called from the
 * animation frame loop and turns the selected speed multiplier into a number
 * of step calls, hard-capped so that no more than
 * ``MAX_STEP_CALLS_PER_SECOND`` HTTP requests are ever issued per second.
 */

/** Hard cap on `/simulation/step` requests per wall-clock second. */
export const MAX_STEP_CALLS_PER_SECOND = 60;

/** Hard cap on step calls dispatched from a single animation frame. */
export const MAX_CALLS_PER_TICK = 8;

/** Speed multipliers offered by the bottom dock. */
export const ALLOWED_SPEEDS = Object.freeze([0.25, 0.5, 1, 2, 5, 10]);

const MIN_TICK_INTERVAL_MS = 1000 / MAX_STEP_CALLS_PER_SECOND;

/**
 * Clamp an arbitrary value into an integer frame index range.
 * @param {*} value
 * @param {number} low
 * @param {number} high
 * @returns {number}
 */
function clampFrame(value, low, high) {
  const n = Math.floor(Number(value));
  if (!Number.isFinite(n)) {
    return low;
  }
  if (n < low) {
    return low;
  }
  if (n > high) {
    return high;
  }
  return n;
}

export class TimeController {
  /**
   * @param {object} [options]
   * @param {number} [options.frameCount=0]
   * @param {number} [options.currentFrame=0]
   * @param {number} [options.retainedFrom=0]
   * @param {number} [options.speed=1]
   * @param {number} [options.nowMs=0] initial timestamp for the rate limiter
   */
  constructor(options = {}) {
    this._frameCount = Math.max(0, Math.floor(Number(options.frameCount) || 0));
    this._currentFrame = clampFrame(options.currentFrame || 0, 0, Math.max(0, this._frameCount - 1));
    this._retainedFrom = Math.max(0, Math.floor(Number(options.retainedFrom) || 0));
    if (this._retainedFrom > this._currentFrame) {
      this._retainedFrom = 0;
    }
    this._speed = this.#snapSpeed(options.speed === undefined ? 1 : options.speed);
    this._playing = false;
    this._callbacks = [];
    this._stepAccumulator = 0;
    this._lastDispatchMs = Number.isFinite(options.nowMs) ? options.nowMs : 0;
    this._dispatchCount = 0;
    this._inFlight = 0;
    this._disposed = false;
  }

  // ---------------------------------------------------------------- getters

  /** @returns {boolean} */
  get isPlaying() {
    return this._playing;
  }

  /** @returns {number} current speed multiplier (one of ALLOWED_SPEEDS). */
  get speed() {
    return this._speed;
  }

  /** @returns {number} */
  get currentFrame() {
    return this._currentFrame;
  }

  /** @returns {number} number of frames the server currently holds. */
  get frameCount() {
    return this._frameCount;
  }

  /** @returns {number} oldest frame index still inside the server ring buffer. */
  get retainedFrom() {
    return this._retainedFrom;
  }

  /** @returns {number} step calls per second the current speed asks for. */
  get requestedCallsPerSecond() {
    return this._speed * MAX_STEP_CALLS_PER_SECOND;
  }

  /** @returns {number} number of directives emitted so far. */
  get dispatchCount() {
    return this._dispatchCount;
  }

  /** @returns {number} number of step calls currently awaiting the server. */
  get inFlight() {
    return this._inFlight;
  }

  // ------------------------------------------------------------- callbacks

  /**
   * Register a playback directive listener.
   *
   * Directives are plain objects:
   *   ``{ kind: "play" }``
   *   ``{ kind: "pause" }``
   *   ``{ kind: "seek", frame }``    -> serve a recorded frame from the cache
   *   ``{ kind: "advance", steps }`` -> call `/simulation/step` ``steps`` times
   *   ``{ kind: "replay", frame }``  -> `/simulation/reset` + step forward
   *
   * @param {(directive: {kind: string, frame?: number, steps?: number, speed?: number}) => void} cb
   * @returns {() => void} unsubscribe
   */
  onFrame(cb) {
    if (typeof cb !== "function") {
      throw new TypeError("onFrame requires a function");
    }
    this._callbacks.push(cb);
    return () => {
      const index = this._callbacks.indexOf(cb);
      if (index >= 0) {
        this._callbacks.splice(index, 1);
      }
    };
  }

  /**
   * Fan a directive out to every listener, isolating listener failures so a
   * broken chart can never stop the simulation clock.
   * @param {object} directive
   * @returns {number} number of listeners notified
   */
  emit(directive) {
    if (this._disposed) {
      return 0;
    }
    let notified = 0;
    for (const cb of this._callbacks.slice()) {
      try {
        cb(directive);
        notified += 1;
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("[time_controller] listener failed", err);
      }
    }
    return notified;
  }

  // ------------------------------------------------------------------ state

  /**
   * Announce that the server now holds ``n`` frames.
   * @param {number} n
   */
  setFrameCount(n) {
    const next = Math.max(0, Math.floor(Number(n) || 0));
    this._frameCount = next;
    if (this._retainedFrom > this._currentFrame) {
      this._retainedFrom = Math.min(this._retainedFrom, next);
    }
    if (this._currentFrame > Math.max(0, next - 1)) {
      this._currentFrame = Math.max(0, next - 1);
    }
    this.emit({ kind: "frames", frameCount: next, currentFrame: this._currentFrame });
  }

  /**
   * Tell the controller which frame index the server ring buffer still holds.
   * Frames below this value trigger the reset-and-replay path.
   * @param {number} index
   */
  setRetainedFrom(index) {
    const next = Math.max(0, Math.floor(Number(index) || 0));
    this._retainedFrom = Math.min(next, this._currentFrame);
  }

  /**
   * Called by the host after ``steps`` successful `/simulation/step` calls.
   * @param {number} steps
   */
  commitSteps(steps) {
    const k = Math.max(0, Math.floor(Number(steps) || 0));
    if (k === 0) {
      return;
    }
    this._frameCount += k;
    this._currentFrame = this._frameCount - 1;
    this.emit({ kind: "frames", frameCount: this._frameCount, currentFrame: this._currentFrame });
  }

  /**
   * Move the cursor without any server interaction (local cache hit).
   * @param {number} frame
   */
  setCursor(frame) {
    this._currentFrame = clampFrame(frame, 0, Math.max(0, this._frameCount - 1));
    this.emit({ kind: "cursor", frame: this._currentFrame });
  }

  // ------------------------------------------------------------- transport

  /** Start issuing step calls from the animation loop. */
  play() {
    if (this._playing) {
      return;
    }
    this._playing = true;
    this._stepAccumulator = 0;
    this.emit({ kind: "play", speed: this._speed });
  }

  /** Stop issuing step calls.  The cursor is left where it is. */
  pause() {
    if (!this._playing) {
      return;
    }
    this._playing = false;
    this._stepAccumulator = 0;
    this.emit({ kind: "pause", speed: this._speed });
  }

  /** @returns {boolean} the new playing state */
  toggle() {
    if (this._playing) {
      this.pause();
    } else {
      this.play();
    }
    return this._playing;
  }

  /**
   * Snap a requested multiplier onto the nearest offered speed.
   * @param {number} raw
   * @returns {number}
   */
  #snapSpeed(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) {
      return 1;
    }
    let best = ALLOWED_SPEEDS[0];
    let bestError = Infinity;
    for (const candidate of ALLOWED_SPEEDS) {
      const error = Math.abs(candidate - n);
      if (error < bestError - 1e-9) {
        bestError = error;
        best = candidate;
      }
    }
    return best;
  }

  /**
   * Select the playback multiplier.  Values are snapped to the closest entry
   * of {@link ALLOWED_SPEEDS} so the dock buttons always match the state.
   * @param {number} value
   * @returns {number} the snapped multiplier
   */
  setSpeed(value) {
    const snapped = this.#snapSpeed(value);
    const changed = snapped !== this._speed;
    this._speed = snapped;
    if (changed) {
      this._stepAccumulator = 0;
    }
    this.emit({ kind: "speed", speed: snapped });
    return snapped;
  }

  // -------------------------------------------------------------- stepping

  /**
   * Advance the simulation by ``n`` frames.  Pauses playback first (a manual
   * step must not race the transport).  When the requested frame already
   * exists the cursor simply moves; otherwise fresh `/simulation/step` calls
   * are issued.
   * @param {number} [n=1]
   * @returns {{kind: string, frame: number, steps: number}}
   */
  stepForward(n = 1) {
    const count = Math.max(1, Math.floor(Number(n) || 1));
    this.pause();
    const desired = this._currentFrame + count;
    const lastRecorded = Math.max(0, this._frameCount - 1);
    if (desired <= lastRecorded) {
      this._currentFrame = desired;
      this.emit({ kind: "seek", frame: desired });
      return { kind: "seek", frame: desired, steps: 0 };
    }
    const missing = desired - lastRecorded;
    this._inFlight += missing;
    this.emit({ kind: "advance", steps: missing, frame: desired });
    return { kind: "advance", frame: desired, steps: missing };
  }

  /**
   * Move the cursor back by ``n`` frames.  When the target frame is still in
   * the server ring buffer this is a pure local seek; otherwise the ``replay``
   * directive asks the host to reset and re-run the flight.
   * @param {number} [n=1]
   * @returns {{kind: string, frame: number, steps: number}}
   */
  stepBackward(n = 1) {
    const count = Math.max(1, Math.floor(Number(n) || 1));
    this.pause();
    const desired = this._currentFrame - count;
    if (desired < this._retainedFrom) {
      this._inFlight += 1;
      this.emit({ kind: "replay", frame: Math.max(0, desired), steps: count });
      return { kind: "replay", frame: Math.max(0, desired), steps: count };
    }
    this._currentFrame = Math.max(0, desired);
    this.emit({ kind: "seek", frame: this._currentFrame });
    return { kind: "seek", frame: this._currentFrame, steps: 0 };
  }

  /**
   * Scrub the timeline.
   *
   * Target below ``retainedFrom``: the server no longer holds the frame, so a
   * ``replay`` directive is emitted and the host performs
   * ``/simulation/reset`` + N x ``/simulation/step``.  Anything inside the
   * retained window is served locally with a ``seek`` directive.
   * @param {number} frameIndex
   * @returns {{kind: string, frame: number, steps: number}}
   */
  seek(frameIndex) {
    this.pause();
    const lastRecorded = Math.max(0, this._frameCount - 1);
    const target = clampFrame(frameIndex, 0, lastRecorded);
    if (target < this._retainedFrom) {
      this._inFlight += 1;
      this.emit({ kind: "replay", frame: target, steps: this._currentFrame - target });
      return { kind: "replay", frame: target, steps: this._currentFrame - target };
    }
    this._currentFrame = target;
    this.emit({ kind: "seek", frame: target });
    return { kind: "seek", frame: target, steps: 0 };
  }

  // ---------------------------------------------------------------- pacing

  /**
   * Animation-loop entry point.  Converts the elapsed wall time multiplied by
   * the speed selector into a number of `/simulation/step` calls, bounded by
   * {@link MAX_STEP_CALLS_PER_SECOND} and {@link MAX_CALLS_PER_TICK}.
   *
   * 0.25x accumulates a quarter call per frame and fires every fourth frame;
   * 10x asks for ten calls per frame, capped to eight so a single slow
   * response can never build an unbounded request queue.
   *
   * @param {number} nowMs high-resolution timestamp from `requestAnimationFrame`
   * @returns {number} number of step calls requested this tick (0 or more)
   */
  tick(nowMs) {
    if (!this._playing || this._disposed) {
      return 0;
    }
    const now = Number(nowMs);
    if (!Number.isFinite(now)) {
      return 0;
    }
    if (this._lastDispatchMs > 0 && now - this._lastDispatchMs < MIN_TICK_INTERVAL_MS) {
      return 0;
    }
    this._stepAccumulator += this._speed;
    let calls = Math.floor(this._stepAccumulator);
    if (calls <= 0) {
      return 0;
    }
    this._stepAccumulator -= calls;
    if (calls > MAX_CALLS_PER_TICK) {
      // Keep the fractional credit so 10x does not silently become 8x.
      this._stepAccumulator = 0;
      calls = MAX_CALLS_PER_TICK;
    }
    this._lastDispatchMs = now;
    this._inFlight += calls;
    this._dispatchCount += 1;
    this.emit({ kind: "advance", steps: calls, frame: null });
    return calls;
  }

  /**
   * Register the completion of dispatched step calls.
   * @param {number} steps
   */
  settle(steps) {
    const k = Math.max(0, Math.floor(Number(steps) || 0));
    this._inFlight = Math.max(0, this._inFlight - k);
  }

  /** Release every listener; used when the page is torn down. */
  dispose() {
    this._disposed = true;
    this._playing = false;
    this._callbacks.length = 0;
  }
}

export default TimeController;
