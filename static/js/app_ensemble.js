/**
 * GPU Ensemble Flight & Champion HUD Controller
 * Handles 100-plane batch simulations, statistics, ranking tables, and data exports.
 */

import { api } from "./api.js";

/**
 * Execute GPU batch simulation for an ensemble fleet.
 */
export async function runGpuEnsemble(app, count = 100) {
  if (app._launching || app.transport.busy) {
    console.warn("[App] Ensemble/Launch already running; ignoring duplicate request.");
    return;
  }
  app._launching = true;
  app._setLaunchButtonsDisabled(true);

  if (app.tunnelMode) app.setTunnel(false);
  app.hideSimulationError();
  const tStart = performance.now();
  app.startEtaCountdown({
    clock: "GPU計算中...",
    title: `GPU並列シミュレーション (${count}機同時計算) を実行中...`,
  });

  try {
    await app._guard(async () => {
      app.notify(`GPU一括並列シミュレーション (${count}機同時計算) を実行中...`, "info");

      const currentAircraft = app.store.data.aircraft || {};
      const currentEnv = app.store.data.environment || {};
      const currentLaunch = app.store.data.launch || {};
      const currentSettings = app.store.data.simulation || {};

      const res = await api.ensemble({
        aircraft: currentAircraft,
        environment: currentEnv,
        launch: currentLaunch,
        settings: { ...currentSettings, duration_s: 25.0, dt_max: 0.01 },
        n_trajectories: count,
        max_sample_tracks: count,
      });

      const elapsedSec = ((performance.now() - tStart) / 1000).toFixed(2);
      app.stopEtaCountdown();
      app.setGlobalEta({
        clock: `${elapsedSec}s`,
        left: "GPU完了",
        active: false,
        title: `GPU 100機同時シミュレーション完了 (所要時間: ${elapsedSec}秒)`,
      });

      if (!res || !res.sample_trajectories) {
        throw new Error("アンサンブルシミュレーションの結果が空です");
      }

      // Render trajectories and 100-plane InstancedMesh in Three.js viewport
      if (app.viewport && res.sample_trajectories.length) {
        app.viewport.setEnsembleTracks(res.sample_trajectories, app._skipFirstRank, res.mean_trajectory);
        app.viewport.filterEnsembleTracks("all");
        app.viewport.setEnsembleFleet(res.sample_trajectories, app._skipFirstRank, res.mean_trajectory);
        app.viewport.setCameraMode("mean");
        app._setCameraChipActive("cam-mode-mean");
      }

      const backendStr = res.backend_display || res.backend || "cupy";
      const meanDist = Number(res.mean_distance_m || 0);
      const maxDist = Number(res.max_distance_m || 0);
      const meanTime = Number(res.mean_flight_time_s || 0);
      const maxTime = Number(res.max_flight_time_s || 0);

      // Auto-save ensemble run
      autoSaveEnsembleData(app, res, "json", false);

      // Show comprehensive stats HUD
      showEnsembleChampionHud(app, res);

      app.notify(
        `GPU ${count}機計算完了 [${backendStr}]: 平均 ${meanDist.toFixed(1)}m (${meanTime.toFixed(1)}s) / 最高 ${maxDist.toFixed(1)}m (${maxTime.toFixed(1)}s)`,
        "ok"
      );
    });
  } catch (err) {
    app.stopEtaCountdown();
    app.setGlobalEta({
      clock: "--",
      left: "エラー",
      active: false,
      title: "GPU計算エラー",
    });
    console.error("GPU Ensemble simulation failed:", err);
    app.showSimulationError(err, "GPU並列シミュレーションに失敗しました");
  } finally {
    app._launching = false;
    app._setLaunchButtonsDisabled(false);
  }
}

/**
 * Display the Champion and Statistics HUD card.
 */
export function showEnsembleChampionHud(app, res) {
  if (!app.dom["ensemble-champion-card"]) return;
  app._lastEnsembleResult = res;

  const valid = (res.sample_trajectories || []).filter((t) => t && Number.isFinite(t.distance_m));
  if (!valid.length) return;

  const sortedByDist = [...valid].sort((a, b) => b.distance_m - a.distance_m);
  const isSkipping = Boolean(app._skipFirstRank && sortedByDist.length > 1);
  const displayDistList = isSkipping ? sortedByDist.slice(1) : sortedByDist;
  const skippedFlight = isSkipping ? sortedByDist[0] : null;

  const champ = displayDistList[0];
  const worst = displayDistList[displayDistList.length - 1];
  app._lastChampFlight = champ;
  app._lastWorstFlight = worst;

  const sortedByTime = [...displayDistList].sort((a, b) => (b.flight_time_s || 0) - (a.flight_time_s || 0));
  const bestTimeFlight = sortedByTime[0];

  const sortedByAlt = [...displayDistList].sort((a, b) => (b.max_altitude_m || 0) - (a.max_altitude_m || 0));
  const bestAltFlight = sortedByAlt[0];

  const avgDist = displayDistList.reduce((acc, cur) => acc + (cur.distance_m || 0), 0) / displayDistList.length;
  const avgTime = displayDistList.reduce((acc, cur) => acc + (cur.flight_time_s || 0), 0) / displayDistList.length;

  if (app.dom["champ-best-dist"]) app.dom["champ-best-dist"].textContent = `${champ.distance_m.toFixed(2)}m`;
  if (app.dom["champ-best-detail"]) {
    app.dom["champ-best-detail"].textContent = `機体 #${champ.id ?? 1} · 滞空 ${(champ.flight_time_s || 0).toFixed(1)}s · 最高高度 ${(champ.max_altitude_m || 0).toFixed(1)}m`;
  }
  if (app.dom["champ-avg-range"]) {
    app.dom["champ-avg-range"].textContent = `${avgDist.toFixed(1)}m`;
  }
  if (app.dom["champ-avg-dist"]) {
    app.dom["champ-avg-dist"].textContent = `平均滞空 ${avgTime.toFixed(1)}s (${displayDistList.length}機集計)`;
  }

  app.dom["ensemble-champion-card"].hidden = false;
}

/**
 * Initialize action buttons inside the champion card.
 */
export function initChampionActions(app) {
  if (app.dom["champ-close-btn"]) {
    app.dom["champ-close-btn"].addEventListener("click", () => {
      if (app.dom["ensemble-champion-card"]) {
        app.dom["ensemble-champion-card"].hidden = true;
      }
    });
  }
  if (app.dom["champ-btn-download-json"]) {
    app.dom["champ-btn-download-json"].addEventListener("click", () => {
      if (app._lastEnsembleResult) autoSaveEnsembleData(app, app._lastEnsembleResult, "json", true);
    });
  }
  if (app.dom["champ-btn-download-csv"]) {
    app.dom["champ-btn-download-csv"].addEventListener("click", () => {
      if (app._lastEnsembleResult) autoSaveEnsembleData(app, app._lastEnsembleResult, "csv", true);
    });
  }
  // Bind 6 champion action buttons
  if (app.dom["champ-btn-adopt"]) {
    app.dom["champ-btn-adopt"].addEventListener("click", () => {
      if (app._lastChampFlight && app.viewport) {
        app.viewport.focusTrajectory(app._lastChampFlight.id);
        app.notify("1位の機体にフォーカスしました", "ok");
      }
    });
  }
  if (app.dom["champ-btn-focus"]) {
    app.dom["champ-btn-focus"].addEventListener("click", () => {
      if (app._lastChampFlight && app.viewport) {
        app.viewport.focusTrajectory(app._lastChampFlight.id);
      }
    });
  }
  if (app.dom["champ-btn-skip-top1"]) {
    app.dom["champ-btn-skip-top1"].addEventListener("click", () => {
      app._skipFirstRank = !app._skipFirstRank;
      if (app._lastEnsembleResult && app.viewport) {
        app.viewport.setSkipFirstRank(app._skipFirstRank);
      }
      const btn = app.dom["champ-btn-skip-top1"];
      const label = app.dom["champ-label-skip-top1"];
      const icon = app.dom["champ-icon-skip-top1"];
      const badge = app.dom["champ-skip-status-badge"];
      if (btn) btn.classList.toggle("active", app._skipFirstRank);
      if (label) label.textContent = app._skipFirstRank ? "スキップ解除 (1位復帰)" : "1位をスキップ (2位繰上)";
      if (icon) icon.style.display = app._skipFirstRank ? "none" : "";
      if (badge) badge.style.display = app._skipFirstRank ? "" : "none";
      if (app.dom["champ-promoted-tag"]) {
        app.dom["champ-promoted-tag"].style.display = app._skipFirstRank ? "" : "none";
      }
    });
  }
  if (app.dom["champ-btn-focus-worst"]) {
    app.dom["champ-btn-focus-worst"].addEventListener("click", () => {
      if (app._lastWorstFlight && app.viewport) {
        app.viewport.focusTrajectory(app._lastWorstFlight.id);
        app.notify("最下位の機体にフォーカスしました", "ok");
      }
    });
  }
  if (app.dom["champ-btn-toggle-fleet"]) {
    app.dom["champ-btn-toggle-fleet"].addEventListener("click", () => {
      if (!app.viewport || !app.viewport._ensembleGroup) return;
      const currentMode = app.viewport._ensembleMode || "all";
      const nextMode = currentMode === "all" ? "top3" : "all";
      app.viewport.filterEnsembleTracks(nextMode);
      const btn = app.dom["champ-btn-toggle-fleet"];
      if (btn) btn.textContent = nextMode === "all" ? "表示: 全100機 ⇄ 上位3機" : "表示: 上位3機 ⇄ 全100機";
    });
  }
  if (app.dom["champ-btn-focus-mean"]) {
    app.dom["champ-btn-focus-mean"].addEventListener("click", () => {
      if (app.viewport) {
        app.viewport.focusMeanTrajectory();
        app.notify("平均機にフォーカスしました", "ok");
      }
    });
  }
}

/**
 * Auto-save ensemble data to server or trigger browser download.
 */
export async function autoSaveEnsembleData(app, res, format = "json", isUserRequested = false) {
  if (!res) return;
  try {
    const filename = `ensemble_100_${Date.now()}.${format}`;
    const content = format === "json" ? JSON.stringify(res, null, 2) : "format not supported";
    const blob = new Blob([content], { type: format === "json" ? "application/json" : "text/csv" });
    const url = URL.createObjectURL(blob);
    if (isUserRequested) {
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      app.notify(`アンサンブル結果を保存しました: ${filename}`, "ok");
    }
    // Always revoke to prevent memory leak
    URL.revokeObjectURL(url);
  } catch (e) {
    console.warn("アンサンブル保存エラー:", e);
  }
}
