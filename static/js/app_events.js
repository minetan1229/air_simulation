/**
 * Application Events & Shortcuts Controller
 * Manages keyboard shortcuts, GPU diagnostic dialogs, and top-level interactions.
 */

import { api } from "./api.js";

/**
 * Detect GPU capabilities from the backend.
 */
export async function detectGpu(app) {
  try {
    const gpu = await api.gpuInfo();
    if (gpu && gpu.available) {
      app.gpuInfo = gpu;
      const name = gpu.device_name || "GPU";
      const shortName = name.replace("NVIDIA GeForce ", "").replace(" Laptop GPU", "");
      if (app.dom["top-backend"]) {
        app.dom["top-backend"].textContent = `${gpu.backend} (${shortName})`;
        app.dom["top-backend"].title = `物理演算: ${name} (CUDA ${gpu.cuda_version || "12.x"})\nクリックしてGPU診断・切り替えガイドを表示`;
        app.dom["top-backend"].classList.add("gpu-active");
      }
    }
  } catch (e) {
    console.warn("GPU検出エラー:", e);
  }
}

/**
 * Open the GPU diagnostic and status modal.
 */
export function openGpuModal(app) {
  if (!app.dom["gpu-modal"]) return;
  const solverGpu = app.gpuInfo;
  const renderGpu = (app.viewport && app.viewport.gpuInfo) || {
    shortName: "WebGL",
    raw: "Unknown",
    isIntel: false,
    isNvidia: false,
  };

  // Update solver card
  if (app.dom["modal-solver-name"]) {
    if (solverGpu && solverGpu.available) {
      const vramStr = solverGpu.total_memory_mb ? ` · VRAM: ${solverGpu.total_memory_mb}MB` : "";
      app.dom["modal-solver-name"].textContent = `[OK] ${solverGpu.device_name || "GPU"} (${solverGpu.backend})`;
      app.dom["modal-solver-desc"].textContent = `CUDA ${solverGpu.cuda_version || ""} · 物理演算(6DoF RK4)・オプティマイザ(NSGA-II)・風場計算で正常にフル稼働中${vramStr}`;
      if (app.dom["gpu-status-solver-card"]) {
        app.dom["gpu-status-solver-card"].className = "gpu-status-card status-ok";
      }
    } else {
      app.dom["modal-solver-name"].textContent = `[CPU] CPU / NumPy (${solverGpu ? solverGpu.backend : "CPU"})`;
      app.dom["modal-solver-desc"].textContent = "物理演算はCPU並列モードで稼働中";
      if (app.dom["gpu-status-solver-card"]) {
        app.dom["gpu-status-solver-card"].className = "gpu-status-card";
      }
    }
  }

  // Update render card
  if (app.dom["modal-render-name"]) {
    if (renderGpu.isNvidia) {
      app.dom["modal-render-name"].textContent = `[OK] ${renderGpu.shortName} (NVIDIA)`;
      app.dom["modal-render-desc"].textContent = `3D描画: ${renderGpu.raw}`;
      if (app.dom["gpu-status-render-card"]) {
        app.dom["gpu-status-render-card"].className = "gpu-status-card status-ok";
      }
      if (app.dom["gpu-warning-box"]) app.dom["gpu-warning-box"].hidden = true;
    } else if (renderGpu.isIntel) {
      app.dom["modal-render-name"].textContent = `[注意] ${renderGpu.shortName} (Intel 省電力)`;
      app.dom["modal-render-desc"].textContent = `WebGL 3D描画: ${renderGpu.raw}`;
      if (app.dom["gpu-status-render-card"]) {
        app.dom["gpu-status-render-card"].className = "gpu-status-card status-warn";
      }
      if (app.dom["gpu-warning-box"]) app.dom["gpu-warning-box"].hidden = false;
    } else {
      app.dom["modal-render-name"].textContent = `${renderGpu.shortName}`;
      app.dom["modal-render-desc"].textContent = `WebGL 3D描画: ${renderGpu.raw}`;
      if (app.dom["gpu-status-render-card"]) {
        app.dom["gpu-status-render-card"].className = "gpu-status-card";
      }
    }
  }

  app.dom["gpu-modal"].hidden = false;
}
