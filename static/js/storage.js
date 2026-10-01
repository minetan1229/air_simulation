/**
 * storage.js -- configuration persistence and CSV / JSON export.
 *
 * Three storage paths, all routed through the server so the client never has
 * to know the flight-log column layout:
 *   - ``POST /api/v1/data/export``  {kind:"config"|"flight"|"optimizer", format:"json"|"csv"}
 *   - ``POST /api/v1/data/import``  <config bundle>
 *   - ``localStorage["paper-airplane-sim:config"]`` mirror so a page reload
 *     restores the last airframe / environment / launch without a round trip.
 *
 * Quota-exceeded failures on the localStorage mirror are surfaced as a
 * user-visible error rather than swallowed.
 */

export const LOCAL_STORAGE_KEY = "paper-airplane-sim:config";

/** Blob download helper shared by the JSON and CSV buttons. */
export function downloadText(filename, mimetype, text) {
  const safeName = String(filename || "download.txt").replace(/[\\/:*?"<>|]+/g, "_");
  const blob = new Blob([text], { type: mimetype || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = safeName;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  // Revoke on the next task so Safari has time to start the download.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 2000);
  return safeName;
}

export class StorageManager {
  /**
   * @param {object} options
   * @param {{post:Function, postForm:Function}} options.api ApiClient instance
   * @param {() => object} options.getConfigBundle returns the live config bundle
   * @param {(bundle: object) => void} [options.onApply] called after a successful load
   * @param {(message: string, kind?: string) => void} [options.notify] toast sink
   * @param {string} [options.key] localStorage key override
   */
  constructor(options = {}) {
    if (!options.api) {
      throw new Error("StorageManager requires an ApiClient");
    }
    this.api = options.api;
    this.getConfigBundle = typeof options.getConfigBundle === "function"
      ? options.getConfigBundle
      : () => ({});
    this.onApply = typeof options.onApply === "function" ? options.onApply : () => {};
    this.notify = typeof options.notify === "function" ? options.notify : () => {};
    this.key = options.key || LOCAL_STORAGE_KEY;
  }

  // ------------------------------------------------------------ local mirror

  /**
   * Is localStorage usable at all (private mode, disabled storage, quota 0)?
   * @returns {boolean}
   */
  storageAvailable() {
    try {
      const probe = "__paper_airplane_probe__";
      window.localStorage.setItem(probe, "1");
      window.localStorage.removeItem(probe);
      return true;
    } catch (err) {
      return false;
    }
  }

  /**
   * Write the bundle into localStorage.
   * @param {object} bundle
   * @returns {{ok: boolean, bytes: number, reason: string}}
   */
  mirrorLocal(bundle) {
    if (!this.storageAvailable()) {
      this.notify("localStorage が利用できないため設定を保存できませんでした", "warn");
      return { ok: false, bytes: 0, reason: "unavailable" };
    }
    let serialised;
    try {
      serialised = JSON.stringify(bundle);
    } catch (err) {
      this.notify(`設定を JSON 化できませんでした: ${err.message}`, "error");
      return { ok: false, bytes: 0, reason: "serialise" };
    }
    try {
      window.localStorage.setItem(this.key, serialised);
      return { ok: true, bytes: serialised.length, reason: "" };
    } catch (err) {
      const quota = err && (
        err.name === "QuotaExceededError"
        || err.name === "NS_ERROR_DOM_QUOTA_REACHED"
        || err.code === 22
        || err.code === 1014
      );
      const message = quota
        ? `localStorage の容量を超過しました (${serialised.length} bytes)。JSON 出力で保存してください。`
        : `localStorage への保存に失敗しました: ${err && err.message ? err.message : String(err)}`;
      this.notify(message, "error");
      return { ok: false, bytes: serialised.length, reason: quota ? "quota" : "write" };
    }
  }

  /**
   * Read the mirrored bundle back.
   * @returns {object|null}
   */
  restoreLocal() {
    if (!this.storageAvailable()) {
      return null;
    }
    let raw = null;
    try {
      raw = window.localStorage.getItem(this.key);
    } catch (err) {
      this.notify(`localStorage の読み出しに失敗しました: ${err.message}`, "error");
      return null;
    }
    if (!raw) {
      return null;
    }
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (err) {
      this.notify(`保存済み設定が壊れているため破棄しました: ${err.message}`, "warn");
      this.clearLocal();
      return null;
    }
  }

  /** Remove the mirrored bundle. */
  clearLocal() {
    if (!this.storageAvailable()) {
      return false;
    }
    try {
      window.localStorage.removeItem(this.key);
      return true;
    } catch (err) {
      return false;
    }
  }

  // ---------------------------------------------------------------- exports

  /**
   * ``POST /api/v1/data/export`` with kind=``config``.
   *
   * The server reads the configuration from the ``bundle`` key (falling back
   * to the server-side session snapshot), so the live bundle is sent nested
   * rather than spread across the top level.
   *
   * Mirrors the bundle locally and downloads the server-rendered JSON.
   * @returns {Promise<object>} the export payload
   */
  async saveJson() {
    const bundle = this.getConfigBundle();
    const response = await this.api.post("/api/v1/data/export", {
      kind: "config",
      format: "json",
      bundle,
    });
    const mirrored = this.mirrorLocal(bundle);
    if (response && typeof response.payload === "string") {
      downloadText(response.filename || "paper-airplane-config.json", response.mimetype || "application/json", response.payload);
      this.notify(`設定を保存しました (${response.filename || "config.json"})`, "ok");
    } else {
      this.notify("サーバーが JSON ペイロードを返さなかったため、ローカル保存のみ実施しました", "warn");
    }
    return { response, mirrored };
  }

  /**
   * ``POST /api/v1/data/export`` with kind=``flight``, format=``csv``.
   * @returns {Promise<string>} the file name that was downloaded
   */
  async exportFlightCsv() {
    const response = await this.api.post("/api/v1/data/export", {
      kind: "flight",
      format: "csv",
    });
    const payload = response && typeof response.payload === "string" ? response.payload : "";
    if (payload === "") {
      throw new Error("サーバーが空の CSV を返しました");
    }
    const name = downloadText(response.filename || "flight-log.csv", response.mimetype || "text/csv", payload);
    this.notify(`飛行ログを出力しました (${name})`, "ok");
    return name;
  }

  /**
   * Optimizer results as CSV.
   *
   * ``POST /api/v1/data/export`` with kind=``optimizer`` requires the result
   * payload in the request body, so the JSON result is fetched first through
   * the API client and forwarded verbatim.
   *
   * @param {string} jobId
   * @param {{get: Function}} api ApiClient with a `get` method
   * @returns {Promise<string>} downloaded file name
   */
  async exportOptimizerCsv(jobId, api) {
    if (!jobId) {
      throw new Error("job_id が指定されていません");
    }
    const client = api || this.api;
    if (typeof client.get !== "function") {
      throw new Error("ApiClient に get メソッドがありません");
    }
    const result = await client.get(
      `/api/v1/optimizer/result/${encodeURIComponent(jobId)}`,
      { format: "json" },
    );
    const response = await this.api.post("/api/v1/data/export", {
      kind: "optimizer",
      format: "csv",
      job_id: jobId,
      result,
    });
    const payload = response && typeof response.payload === "string" ? response.payload : "";
    if (payload === "") {
      throw new Error("サーバーが空の CSV を返しました");
    }
    const name = downloadText(
      response.filename || `optimizer-${jobId}.csv`,
      response.mimetype || "text/csv",
      payload,
    );
    this.notify(`最適化結果を出力しました (${name})`, "ok");
    return name;
  }

  // ---------------------------------------------------------------- imports

  /**
   * Parse a JSON file and push it through ``POST /api/v1/data/import``.
   * Accepts either a full config bundle or an export wrapper that carries one
   * under ``bundle`` / ``config``.
   * @param {File|Blob} file
   * @returns {Promise<object>} the import response
   */
  async loadJsonFile(file) {
    if (!file) {
      throw new Error("ファイルが選択されていません");
    }
    const text = await file.text();
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      throw new Error(`JSON を解釈できません: ${err.message}`);
    }
    const bundle = this.#extractBundle(parsed);
    if (!bundle) {
      throw new Error("JSON に設定バンドル (aircraft / environment / launch) がありません");
    }
    const response = await this.api.post("/api/v1/data/import", bundle);
    const applied = response && response.bundle ? response.bundle : bundle;
    this.onApply(applied);
    this.mirrorLocal(applied);
    this.notify(`設定を読み込みました (${file.name})`, "ok");
    return applied;
  }

  /**
   * Normalise the many shapes an exported configuration may arrive in.
   * @param {*} parsed
   * @returns {object|null}
   */
  #extractBundle(parsed) {
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    const candidates = [parsed, parsed.bundle, parsed.config, parsed.payload];
    for (const candidate of candidates) {
      if (candidate && typeof candidate === "object" && (candidate.aircraft || candidate.environment || candidate.launch)) {
        const bundle = {};
        if (candidate.aircraft && typeof candidate.aircraft === "object") {
          bundle.aircraft = candidate.aircraft;
        }
        if (candidate.environment && typeof candidate.environment === "object") {
          bundle.environment = candidate.environment;
        }
        if (candidate.launch && typeof candidate.launch === "object") {
          bundle.launch = candidate.launch;
        }
        if (candidate.simulation && typeof candidate.simulation === "object") {
          bundle.simulation = candidate.simulation;
        }
        return bundle;
      }
      // `payload` is often a JSON string of the bundle itself.
      if (candidate && typeof candidate === "string") {
        try {
          const nested = this.#extractBundle(JSON.parse(candidate));
          if (nested) {
            return nested;
          }
        } catch (err) {
          // Not a nested bundle -- keep scanning the other candidates.
        }
      }
    }
    return null;
  }
}

export default StorageManager;
