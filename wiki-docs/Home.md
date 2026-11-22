# 3D Paper Airplane High-Performance CFD Simulator Wiki

Welcome! This is the official GitHub Wiki for the **3D Paper Airplane High-Performance CFD Simulator**. This project is a high-precision paper airplane flight simulator based on fluid dynamics (CFD) and aeronautical engineering theory. It integrates a Flask REST API backend with a Three.js / WebGL Google Clean style instrument console and airflow visualization frontend.

---

## 🚀 Key Features

- **Aerodynamics & Environment Models (Aero & Atmosphere)**:
  - ISA Standard Atmosphere Model (pressure, temperature, density variation with altitude)
  - Dynamic viscosity & Reynolds number calculation via Sutherland's Law
  - High AoA & stall transition modeling via Viterna Algorithm
  - Induced drag reduction via Ground Effect
  - Passive Aeroelastic Morphing based on areal density & stiffness
  - Automatic Drag Polar computation & optimal glide angle analysis
- **6-DoF Flight Dynamics & Time Control**:
  - Quaternion attitude representation & 4th-order Runge-Kutta (RK4) numerical integration
  - Adaptive Sub-stepping for numerical stability during high-speed/sharp turns
  - Ring buffer history retention with seek, frame-stepping, variable speed (0.25x–10x)
- **3D Wind Field & Fluid Dynamics**:
  - Spatial vector fields, atmospheric turbulence via Simplex/Perlin noise, thermal updrafts
  - Auto-fallback parallel computation on GPU (CuPy / PyTorch) and NumPy
- **Structural Load & Stress Analysis**:
  - Wing root bending moment analysis from dynamic pressure & G-forces
  - Real-time per-vertex stress heatmap (blue~red) with deformation rendering
- **3D Mesh Import & Panel Method (Mesh Domain)**:
  - Import STL (binary/ASCII), OBJ, glTF 2.0 / GLB formats
  - Automatic geometric analysis: surface area, wingspan, aspect ratio, CG (volume/area), inertia tensor
  - Local AoA, circulation, lift analysis via panel method
- **Multi-Objective Optimization Engine**:
  - Nelder-Mead, Differential Evolution, NSGA-II (Pareto optimal extraction), Surrogate (proxy models)
  - Async job execution, progress tracking, real-time ETA calculation
- **Data Management & Persistence**:
  - Aircraft/environment parameter JSON import/export
  - Flight history telemetry instant CSV download

---

## 🎯 Target Users

- **Aerospace/Fluid Dynamics Educators & Students**: Visually understand lift, drag, stall, ground effect, moment behaviors in 3D
- **Paper Airplane Designers & Researchers**: Explore optimization of flight duration & distance via folding, wing parameters, materials (areal density)
- **Web 3D / CAE Engineers**: Learn real-time physics rendering with Three.js, WebGL shaders, Flask REST API numerical integration

---

## 📚 Documentation Structure

| Document | Overview |
|:---|:---|
| [[Setup & Launch|Getting-Started]] | Prerequisites, Python environment setup, launch commands, environment variables |
| [[Architecture Design|Architecture]] | System architecture diagram, data flow, directory structure, tech stack |
| [[Development & Operations Guide|Development-Guide]] | Branch strategy, Git conventions, test (pytest/smoke_test) execution, CI pipeline |

---

## 🔗 Related Links

- [GitHub Repository](https://github.com/minetan1229/air_simulation)
- [REST API Specifications (project docs/api.md)](../docs/api.md)