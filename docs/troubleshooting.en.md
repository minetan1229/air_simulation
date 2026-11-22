# Troubleshooting & FAQ

This document summarizes common issues and solutions during environment setup, launch, and simulation execution.

---

## 1. Launch & Environment Issues

### Q1: `ModuleNotFoundError` occurs, cannot launch
- **Cause**: Virtual environment not activated, or dependency packages not fully installed.
- **Solution**:
  ```powershell
  # Verify virtual environment activation
  .\.venv\Scripts\Activate.ps1
  # Reinstall dependencies
  pip install -r requirements.txt
  ```

### Q2: GPU (CUDA / CuPy) not recognized
- **Cause**: Installed CUDA version may not match CuPy / PyTorch version.
- **Solution**:
  This simulator **automatically falls back to NumPy CPU computation** when GPU is unavailable (not an operational error). To enable GPU acceleration, verify CUDA toolkit version and install appropriate packages (e.g., `cupy-cuda12x`).

---

## 2. 3D Rendering & UI Issues

### Q1: 3D viewer is black or nothing renders
- **Cause**: Browser hardware acceleration may be disabled, or WebGL 2.0 not supported.
- **Solution**:
  1. Use latest Google Chrome, Microsoft Edge, etc.
  2. Verify "Use graphics acceleration (hardware acceleration)" is enabled in browser settings.
  3. Open `chrome://gpu` or `edge://gpu` to check WebGL 2.0 status.

### Q2: 3D model (STL / OBJ / GLB) import fails
- **Cause**:
  - File size exceeds Flask limit (64MB).
  - Polygon mesh is corrupted or normal vectors are severely disordered.
- **Solution**:
  - Ensure 3D model file size is within 64MB.
  - Repair non-manifold geometry in Blender or MeshLab, then re-upload.

---

## 3. Simulation & Optimization Issues

### Q1: Flight trajectory stops abruptly or bounces unnaturally
- **Cause**:
  Extreme launch velocity (supersonic) or excessive initial angle of attack may cause adaptive sub-stepping to subdivide to the limit, making timestep microscopic or reaching landing judgment.
- **Solution**:
  Set initial velocity (typically $3 \sim 15\text{ m/s}$) and launch angle ($0^\circ \sim 20^\circ$) to realistic ranges.

### Q2: Optimization job (NSGA-II) takes too long to complete
- **Cause**:
  Population size and generations set too large, increasing evaluation flight count.
- **Solution**:
  - First test with local search algorithms like "Nelder-Mead" or small population (12–24).
  - Adjust job while monitoring estimated completion time (ETA) and remaining time display.

---

## 4. Related Documents

- [Quick Start (docs/getting-started.md)](./getting-started.md)
- [User Guide (docs/usage.md)](./usage.md)
- [Development & Testing Guide (docs/development.md)](./development.md)

---

[Back to README](../README.md)