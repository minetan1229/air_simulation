/**
 * Application Modes Controller
 * Manages mode switching between "simulation" and "research" (GA) environments.
 */

export const MODE_KEY = "paper-airplane-console:mode";

export const SIMULATION_TABS = ["investigate", "optimizer", "guinness", "model", "data"];

/**
 * Read the stored mode from localStorage or store.
 */
export function readStoredMode(store) {
  try {
    const raw = window.localStorage.getItem(MODE_KEY);
    if (raw === "research" || raw === "simulation") return raw;
  } catch (error) {
    /* localStorage may be unavailable (private mode) */
  }
  const stored = store && store.get("research", "mode");
  if (stored === "research" || stored === "simulation") return stored;
  return "simulation";
}

/**
 * Bind mode switch buttons in the UI.
 */
export function bindModeSwitch(app) {
  app.modeButtons = {
    simulation: document.getElementById("btn-mode-simulation"),
    research: document.getElementById("btn-mode-research"),
  };
  for (const [mode, button] of Object.entries(app.modeButtons)) {
    if (!button) continue;
    button.addEventListener("click", () => setMode(app, mode));
  }
}

/**
 * Switch between the simulation console and the autonomous research mode.
 */
export function setMode(app, mode) {
  const next = mode === "research" ? "research" : "simulation";
  app.mode = next;
  document.body.dataset.mode = next;
  for (const [key, button] of Object.entries(app.modeButtons || {})) {
    if (button) button.classList.toggle("active", key === next);
  }
  app.store.set("research", "mode", next, "ui");
  try {
    window.localStorage.setItem(MODE_KEY, next);
  } catch (error) {
    /* localStorage may be unavailable (private mode) */
  }
  if (next === "research") {
    if (app._activeTab !== "research" && SIMULATION_TABS.includes(app._activeTab)) {
      app._simulationTab = app._activeTab;
    }
    app.setWorkbench(true);
    const tab = document.getElementById("wb-tab-research");
    if (tab) tab.click();
  } else {
    const wanted = SIMULATION_TABS.includes(app._simulationTab) ? app._simulationTab : "investigate";
    const tab = document.getElementById(`wb-tab-${wanted}`);
    if (tab) tab.click();
  }
  if (app.viewport) requestAnimationFrame(() => app.viewport.resize());
  if (app.viewport && typeof app.viewport.setResearchPresentation === "function") {
    app.viewport.setResearchPresentation(next === "research");
    const followToggle = document.getElementById("tgl-follow");
    if (followToggle) followToggle.dataset.on = app.viewport.follow ? "1" : "0";
    if (next === "research") {
      app._setCameraChipActive("cam-mode-fixed");
    } else {
      const previous = app.viewport.previousCameraMode();
      if (previous) app._setCameraChipActive(`cam-mode-${previous}`);
    }
  }
}

/**
 * Restore the GA research section of a loaded configuration file.
 */
export function applyResearchBundle(app, text) {
  let bundle = null;
  try {
    bundle = JSON.parse(text);
  } catch (error) {
    return;
  }
  const research = bundle && bundle.research;
  if (!research || typeof research !== "object") return;
  app.store.merge("research", research, "load");
  if (app.gaResearch && typeof app.gaResearch.applyStoreParams === "function") {
    app.gaResearch.applyStoreParams();
  }
  const mode = research.mode === "research" ? "research" : "simulation";
  setMode(app, mode);
}
