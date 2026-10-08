/*
 * investigator.js — Auto Investigation & Tuning Panel.
 *
 * Automatically inspects the current plane's flight characteristics,
 * diagnoses failure modes (nose-dive, stall-dive, spiral-dive, etc.),
 * performs sensitivity scanning and focused local tuning across hand-adjustable
 * variables only (elevon, dihedral, launch pitch, speed, clip CG offset),
 * and generates actionable folding and throwing guidance.
 */

import { clockFmt, clear, fmt, make, sig } from "./lib.js";

const POLL_INTERVAL_MS = 600;

export class InvestigatorPanel {
  constructor({ api, store, dom, notify }) {
    this.api = api;
    this.store = store;
    this.dom = dom;
    this.notify = notify || (() => {});
    this.jobId = null;
    this.pollTimer = 0;
    this.result = null;
  }

  mount() {
    this._bind();
  }

  _bind() {
    if (this.dom.run) {
      this.dom.run.addEventListener("click", () => this.start());
    }
    if (this.dom.cancel) {
      this.dom.cancel.addEventListener("click", () => this.cancel());
    }
    if (this.dom.adopt) {
      this.dom.adopt.addEventListener("click", () => this.adoptRecommended());
    }
  }

  async start() {
    if (this.jobId) return;

    const targetMetric = this.dom.targetSelect ? this.dom.targetSelect.value : "distance";
    const payload = {
      aircraft: this.store.aircraft,
      environment: this.store.environment,
      launch: this.store.launch,
      target_metric: targetMetric,
    };

    if (this.dom.run) this.dom.run.disabled = true;
    if (this.dom.cancel) this.dom.cancel.disabled = false;
    if (this.dom.statusText) this.dom.statusText.textContent = "調査準備中...";
    if (this.dom.progFill) this.dom.progFill.style.width = "0%";
    if (this.dom.resultArea) this.dom.resultArea.style.display = "none";

    try {
      const res = await this.api.investigateRun(payload);
      this.jobId = res.job_id;
      this.notify("Auto調査を開始しました。機体特性を診断中...", "info");
      this._startPolling();
    } catch (err) {
      this.notify(`調査開始に失敗しました: ${err.message}`, "error");
      if (this.dom.run) this.dom.run.disabled = false;
      if (this.dom.cancel) this.dom.cancel.disabled = true;
    }
  }

  _startPolling() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => this._poll(), POLL_INTERVAL_MS);
  }

  async _poll() {
    if (!this.jobId) return;

    try {
      const snap = await this.api.investigateStatus(this.jobId);
      const pct = Math.min(100, Math.max(0, snap.progress || 0));

      if (this.dom.progFill) {
        this.dom.progFill.style.width = `${pct}%`;
      }
      if (this.dom.progText) {
        this.dom.progText.textContent = `${Math.round(pct)}%`;
      }
      if (this.dom.statusText) {
        this.dom.statusText.textContent = snap.message || "飛行シミュレーション中...";
      }

      if (snap.status === "completed") {
        clearInterval(this.pollTimer);
        this.pollTimer = 0;
        await this._fetchResult();
      } else if (snap.status === "failed" || snap.status === "cancelled") {
        clearInterval(this.pollTimer);
        this.pollTimer = 0;
        this.jobId = null;
        if (this.dom.run) this.dom.run.disabled = false;
        if (this.dom.cancel) this.dom.cancel.disabled = true;
        this.notify(`調査が${snap.status === "failed" ? "失敗" : "中断"}しました: ${snap.message || ""}`, "warn");
      }
    } catch (err) {
      console.warn("Investigate poll error:", err);
    }
  }

  async _fetchResult() {
    try {
      const res = await this.api.investigateResult(this.jobId);
      this.result = res;
      this.jobId = null;

      if (this.dom.run) this.dom.run.disabled = false;
      if (this.dom.cancel) this.dom.cancel.disabled = true;
      if (this.dom.statusText) this.dom.statusText.textContent = "調査完了！分析レポートを作成しました。";

      this._renderResult(res);
      this.notify("Auto調査が完了しました！分析レポートを確認してください。", "success");
    } catch (err) {
      this.notify(`調査結果の取得に失敗しました: ${err.message}`, "error");
      if (this.dom.run) this.dom.run.disabled = false;
      if (this.dom.cancel) this.dom.cancel.disabled = true;
    }
  }

  async cancel() {
    if (!this.jobId) return;
    try {
      await this.api.investigateCancel(this.jobId);
      this.notify("調査を中断しました", "info");
    } catch (err) {
      console.warn("Cancel failed:", err);
    }
  }

  _renderResult(data) {
    if (!this.dom.resultArea) return;
    this.dom.resultArea.style.display = "block";

    const diag = data.diagnosis || {};
    const sum = data.summary || {};
    const adviceList = data.advice_list || [];
    const sensitivities = data.sensitivities || [];

    // 1. Diagnosis
    if (this.dom.diagBadge) {
      this.dom.diagBadge.textContent = diag.issue_code || "STATUS";
      this.dom.diagBadge.className = `inv-badge inv-badge-${(diag.issue_code || "").toLowerCase()}`;
    }
    if (this.dom.diagTitle) {
      this.dom.diagTitle.textContent = diag.headline_ja || "診断完了";
    }
    if (this.dom.diagDesc) {
      this.dom.diagDesc.textContent = diag.description_ja || "";
    }

    // 2. Metrics Comparison (Before vs After)
    if (this.dom.compDistBase) this.dom.compDistBase.textContent = `${fmt(sum.base_distance_m, 2)} m`;
    if (this.dom.compDistRec) this.dom.compDistRec.textContent = `${fmt(sum.rec_distance_m, 2)} m`;
    if (this.dom.compDistGain) {
      const g = sum.distance_gain_pct || 0;
      this.dom.compDistGain.textContent = `${g >= 0 ? "+" : ""}${fmt(g, 1)}%`;
      this.dom.compDistGain.className = `num ${g >= 0 ? "gain-pos" : "gain-neg"}`;
    }

    if (this.dom.compTimeBase) this.dom.compTimeBase.textContent = `${fmt(sum.base_flight_time_s, 2)} s`;
    if (this.dom.compTimeRec) this.dom.compTimeRec.textContent = `${fmt(sum.rec_flight_time_s, 2)} s`;
    if (this.dom.compTimeGain) {
      const tg = sum.time_gain_pct || 0;
      this.dom.compTimeGain.textContent = `${tg >= 0 ? "+" : ""}${fmt(tg, 1)}%`;
      this.dom.compTimeGain.className = `num ${tg >= 0 ? "gain-pos" : "gain-neg"}`;
    }

    // 3. Actionable Advice Cards
    if (this.dom.adviceHost) {
      clear(this.dom.adviceHost);
      adviceList.forEach((adv) => {
        const card = make("div", { class: "inv-advice-card" }, [
          make("div", { class: "inv-advice-head" }, [
            make("span", { class: "inv-advice-pri" }, `#${adv.priority}`),
            make("span", { class: "inv-advice-target" }, adv.title_ja),
            make("span", { class: "inv-advice-delta num" }, adv.delta_str),
          ]),
          make("div", { class: "inv-advice-action" }, adv.action_ja),
          make("div", { class: "inv-advice-reason" }, adv.reason_ja),
        ]);
        this.dom.adviceHost.appendChild(card);
      });
    }

    // 4. Sensitivity Ranking Bars
    if (this.dom.sensHost) {
      clear(this.dom.sensHost);
      const topSens = sensitivities.slice(0, 6);
      const maxScore = Math.max(...topSens.map((s) => s.impact_score_pct), 1.0);

      topSens.forEach((s, idx) => {
        const barPct = Math.min(100, Math.max(4, (s.impact_score_pct / maxScore) * 100));
        const row = make("div", { class: "inv-sens-row" }, [
          make("div", { class: "inv-sens-meta" }, [
            make("span", { class: "inv-sens-rank" }, `${idx + 1}.`),
            make("span", { class: "inv-sens-name" }, s.label_ja),
            make("span", { class: "inv-sens-val num" }, `影響度 ${fmt(s.impact_score_pct, 1)}%`),
          ]),
          make("div", { class: "inv-sens-bar-track" }, [
            make("div", { class: "inv-sens-bar-fill", style: `width: ${barPct}%;` }),
          ]),
          make("div", { class: "inv-sens-desc muted" }, `現在: ${fmt(s.baseline_val, 2)}${s.unit} ➔ 推奨: ${fmt(s.best_val, 2)}${s.unit}`),
        ]);
        this.dom.sensHost.appendChild(row);
      });
    }

    if (this.dom.adopt) {
      this.dom.adopt.disabled = false;
    }
  }

  adoptRecommended() {
    if (!this.result || !this.result.recommended) return;

    const recAir = this.result.recommended.aircraft || {};
    const recLau = this.result.recommended.launch || {};

    // Adopt key adjustable variables into the store
    const keysAir = ["elevon_deg", "dihedral_deg", "fold_trailing_deg", "fold_tip_deg", "aileron_deg", "rudder_deg", "cg_offset_x"];
    const keysLau = ["launch_angle_deg", "launch_roll_deg", "launch_speed"];

    keysAir.forEach((k) => {
      if (k in recAir) {
        this.store.set(["aircraft", k], recAir[k]);
      }
    });

    keysLau.forEach((k) => {
      if (k in recLau) {
        this.store.set(["launch", k], recLau[k]);
      }
    });

    this.notify("推奨セッティングを機体に適用しました！新しい飛行軌道を確認できます。", "success");
  }
}
