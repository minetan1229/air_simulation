# REST API Reference (API Specifications)

This document describes all endpoints, request/response formats, and error handling for the Flask REST API provided by this system.

> **Interactive Specification**: After starting the server, you can try cURL / Python / JavaScript request examples via [Swagger UI](http://127.0.0.1:5000/docs) (`GET /docs`). The machine-readable specification is available from [`GET /api/openapi.json`](http://127.0.0.1:5000/api/openapi.json) (OpenAPI 3.0.3, auto-generated from `api/openapi.py`). This Markdown is a human-readable summary; the canonical source is the runtime specification.

---

## 1. Common Specifications & Error Handling

- **Base URL**: `http://localhost:5000`
- **Data Format**: Both requests and responses use `application/json` (except file uploads)
- **Backend Identifier**: Successful responses always include the compute backend used (`"backend": "cupy"` / `"torch"` / `"numpy"`).
- **Error Responses**:
  Client-side bad requests return `HTTP 400`, internal errors return `HTTP 500` with a detailed message.
  ```json
  {
    "error": "Error description message",
    "status": 400,
    "timestamp": "2026-10-17T13:20:00Z"
  }
  ```

---

## 2. API Endpoint List

| Method | Endpoint | Description |
|:---|:---|:---|
| `POST` | `/api/v1/aerodynamics/calculate` | Calculate lift, drag, moment, and stall judgment for specified conditions |
| `POST` | `/api/v1/aerodynamics/polar` | Get drag polar data via angle-of-attack sweep |
| `POST` | `/api/v1/environment/atmosphere` | Set atmosphere/environment parameters and get atmospheric properties (density, kinematic viscosity, etc.) |
| `GET`  | `/api/v1/environment/current` | Query current atmosphere/environment parameters |
| `POST` | `/api/v1/wind/field` | Compute wind velocity vector field on specified 3D spatial grid (GPU/CPU) |
| `POST` | `/api/v1/stress/analysis` | Compute wing dynamic pressure, G-forces, and vertex stress heatmap |
| `POST` | `/api/v1/simulation/step` | Single-step state update of 6DoF equations of motion (variable $\Delta t$ support) |
| `POST` | `/api/v1/simulation/reset` | Simulation initialization |
| `POST` | `/api/v1/simulation/batch` | Execute multiple parameter sweeps / parallel flight simulations |
| `POST` | `/api/v1/mesh/import` | Upload 3D model (STL/OBJ/GLB) and automatic physics analysis |
| `GET`  | `/api/v1/mesh/models` | Query list of uploaded models |
| `POST` | `/api/v1/mesh/select` | Select 3D model for simulation |
| `GET`  | `/api/v1/mesh/metrics/<model_id>` | Query detailed physics properties of specified model (center of mass, inertia tensor, etc.) |
| `POST` | `/api/v1/optimizer/run` | Asynchronously start optimization job (immediately returns `job_id`) |
| `GET`  | `/api/v1/optimizer/status/<job_id>` | Query optimization job progress, evaluation rate, estimated completion time (ETA) |
| `POST` | `/api/v1/optimizer/cancel/<job_id>` | Cancel running optimization job |
| `GET`  | `/api/v1/optimizer/result/<job_id>` | Get optimization job results (Pareto front, optimal values) |
| `GET`  | `/api/v1/jobs` | Query list of all registered jobs and their statuses |
| `POST` | `/api/v1/data/export` | Export flight log data in CSV / JSON format |
| `POST` | `/api/v1/data/import` | Import preset configuration |

---

## 3. Major Endpoint Details

### 3.1 Lift & Drag Calculation
`POST /api/v1/aerodynamics/calculate`

**Request Example:**
```json
{
  "alpha_deg": 4.5,
  "velocity_mps": 6.2,
  "density": 1.225,
  "wing_span": 0.28,
  "wing_area": 0.024,
  "aspect_ratio": 3.27,
  "gsm": 80.0
}
```

**Response Example:**
```json
{
  "lift_N": 0.0892,
  "drag_N": 0.0124,
  "lift_coeff": 0.324,
  "drag_coeff": 0.045,
  "is_stalled": false,
  "reynolds_number": 34500,
  "ld_ratio": 7.19,
  "backend": "numpy"
}
```

---

### 3.2 3D Wind Field Vector Calculation
`POST /api/v1/wind/field`

**Request Example:**
```json
{
  "bounds": {
    "x_min": -10, "x_max": 10,
    "y_min": -10, "y_max": 10,
    "z_min": 0, "z_max": 15
  },
  "resolution": [20, 20, 15],
  "mean_wind": [2.0, 0.0, 0.0],
  "turbulence_intensity": 0.15
}
```

**Response Example:**
```json
{
  "grid_shape": [20, 20, 15, 3],
  "vectors": [...],
  "backend": "cupy"
}
```

---

### 3.3 Asynchronous Optimization Job Start
`POST /api/v1/optimizer/run`

**Request Example:**
```json
{
  "algorithm": "nsga2",
  "objectives": ["max_distance", "min_max_stress"],
  "population_size": 24,
  "generations": 10
}
```

**Response Example (immediate return):**
```json
{
  "job_id": "8f3b2d1c-e7a9-4a2b-923f-1d84f04c64b1",
  "status": "queued",
  "message": "Optimization job successfully queued"
}
```

---

### 3.4 Optimization Job Status Query
`GET /api/v1/optimizer/status/<job_id>`

**Response Example:**
```json
{
  "job_id": "8f3b2d1c-e7a9-4a2b-923f-1d84f04c64b1",
  "status": "running",
  "progress": 0.45,
  "evaluated_count": 108,
  "total_evaluations": 240,
  "evals_per_second": 12.4,
  "eta_seconds": 10.6,
  "estimated_completion": "2026-10-17T13:20:15Z"
}
```

---

## 4. Headless Execution (Without Web UI)

```bash
# Python standalone (no server required)
python examples/quick_simulation.py --duration 6.0

# REST path (after server start)
curl -X POST http://127.0.0.1:5000/api/v1/simulation/launch \
  -H 'Content-Type: application/json' \
  -d '{"launch":{"launch_speed":12.0,"launch_angle_deg":12.0}}'

# When authentication is required (if API_KEY is set)
curl -X POST http://127.0.0.1:5000/api/v1/optimizer/run \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"algorithm":"nsga2","objectives":["max_distance"],"population_size":24,"generations":10}'
```

See [examples/quick_simulation.py](../examples/quick_simulation.py) for details.

---

## 5. Related Documents

- [Quick Start (docs/getting-started.md)](./getting-started.md)
- [System Architecture (docs/architecture.md)](./architecture.md)
- [User Guide (docs/usage.md)](./usage.md)
- [Development & Testing Guide (docs/development.md)](./development.md)

---

[Back to README](../README.md)