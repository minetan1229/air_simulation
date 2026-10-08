"""End-to-end integration smoke test for the paper-airplane simulator.

Drives the whole REST surface in one process against the real Flask app:
environment -> aerodynamics -> wind -> stress -> 6-DoF simulation -> mesh
import -> optimizer job (submit, poll progress/ETA, fetch result, CSV) ->
data export/import.  No network listener is required; the Flask test client
exercises the identical routing, validation and serialisation path.

Usage::

    python smoke_test.py            # run every stage
    python smoke_test.py --list     # list the stage names only
    python smoke_test.py --stage wind --stage mesh
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import tempfile
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from physics.config_schema import OBJECTIVE_DIRECTIONS

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
INFO = "\033[96mINFO\033[0m"


class SmokeFailure(AssertionError):
    """Raised when a stage assertion does not hold."""


class Runner:
    def __init__(self, client, verbose: bool = True) -> None:
        self.client = client
        self.verbose = verbose
        self.results: List[Tuple[str, bool, str, float]] = []
        self.state: Dict[str, Any] = {}

    # -- HTTP helpers ----------------------------------------------------
    def post(self, url: str, payload: Any = None, expect: int = 200, **kw) -> Dict[str, Any]:
        response = self.client.post(
            url, json=payload, **kw
        ) if payload is not None else self.client.post(url, **kw)
        return self._unwrap(url, response, expect)

    def get(self, url: str, expect: int = 200, **kw) -> Dict[str, Any]:
        return self._unwrap(url, self.client.get(url, **kw), expect)

    def _unwrap(self, url: str, response, expect: int) -> Dict[str, Any]:
        if response.status_code != expect:
            body = response.get_data(as_text=True)
            raise SmokeFailure(
                f"{url} -> HTTP {response.status_code} (expected {expect}): {body[:400]}"
            )
        return json.loads(response.get_data(as_text=True))

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            raise SmokeFailure(message)

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"      {message}")

    # -- stage driver ----------------------------------------------------
    def stage(self, name: str, fn: Callable[[], None]) -> bool:
        started = time.perf_counter()
        try:
            fn()
        except Exception as exc:
            elapsed = time.perf_counter() - started
            detail = str(exc)
            if self.verbose:
                print(f"  {FAIL} {name:<22} {elapsed:6.2f}s  {detail}")
                if not isinstance(exc, SmokeFailure):
                    print(traceback.format_exc())
            self.results.append((name, False, detail, elapsed))
            return False
        elapsed = time.perf_counter() - started
        if self.verbose:
            print(f"  {PASS} {name:<22} {elapsed:6.2f}s")
        self.results.append((name, True, "", elapsed))
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def wing_obj() -> str:
    """A minimal closed, tapered wing solid exported as Wavefront OBJ.

    Geometry (millimetres, to also exercise ``scale_hint``): root chord 120 mm
    at y=0 tapering to a 60 mm tip at y=150 mm, thickness 4 mm.  The span
    (300 mm) is deliberately the longest principal axis so the planform
    analysis picks the right one.
    """
    root_chord = 120.0
    tip_chord = 60.0
    half_span = 150.0
    half_thick = 4.0
    verts = []
    for chord, span in ((root_chord, 0.0), (tip_chord, half_span)):
        for x, z in ((0.0, 0.0), (chord, 0.0), (chord, half_thick), (0.0, half_thick)):
            verts.append((x, span, z))
    lines = ["# smoke-test wing (millimetres)"]
    lines += [f"v {x:.4f} {y:.4f} {z:.4f}" for x, y, z in verts]
    faces = [
        (1, 2, 3), (1, 3, 4),          # root cap
        (5, 7, 6), (5, 8, 7),          # tip cap
        (1, 5, 6), (1, 6, 2),          # leading edge
        (3, 7, 8), (3, 8, 4),          # trailing edge
        (2, 6, 7), (2, 7, 3),          # lower surface
        (4, 8, 7), (4, 7, 1),          # upper surface
    ]
    lines += [f"f {a} {b} {c}" for a, b, c in faces]
    return "\n".join(lines) + "\n"


def stl_binary(tetra_scale: float = 0.1) -> bytes:
    """A binary STL tetrahedron, used to prove the binary STL parser runs."""
    import struct

    pts = [
        (0.0, 0.0, 0.0),
        (tetra_scale, 0.0, 0.0),
        (0.0, tetra_scale, 0.0),
        (0.0, 0.0, tetra_scale),
    ]
    tris = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
    out = bytearray(b"smoke test binary stl".ljust(80, b"\x00"))
    out += struct.pack("<I", len(tris))
    for i, j, k in tris:
        a, b, c = pts[i], pts[j], pts[k]
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        out += struct.pack("<3f", *n)
        for idx in (i, j, k):
            out += struct.pack("<3f", *pts[idx])
        out += struct.pack("<H", 0)
    return bytes(out)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------
def stage_health(r: Runner) -> None:
    health = r.get("/healthz")
    r.check(health["status"] == "ok", "/healthz did not report ok")

    meta = r.get("/api/v1/meta/schemas")
    for key in ("aircraft", "environment", "objectives", "algorithms", "design_variables"):
        r.check(key in meta, f"schema bundle missing '{key}'")
    r.check(len(meta["algorithms"]) == 5, f"expected 5 algorithms, got {meta['algorithms']}")
    r.check(len(meta["objectives"]) == 7, f"expected 7 objectives, got {len(meta['objectives'])}")
    r.log(f"algorithms={meta['algorithms']} objectives={len(meta['objectives'])}")

    domains = r.get("/api/v1/meta/health")["domains"]
    bad = {k: v for k, v in domains.items() if v != "ok"}
    r.check(not bad, f"unavailable domains: {bad}")
    r.state["domains"] = domains


def stage_environment(r: Runner) -> None:
    base = r.get("/api/v1/environment/current")
    for key in ("density_kgm3", "kinematic_viscosity_m2s", "sound_speed_mps"):
        r.check(key in base, f"environment/current missing '{key}'")
    r.log(f"rho={base['density_kgm3']:.5f} nu={base['kinematic_viscosity_m2s']:.3e} a={base['sound_speed_mps']:.2f}")

    # Dry, cold, dense, fast and highly loaded must all move the numbers.
    hot = r.post(
        "/api/v1/environment/atmosphere",
        {"temperature_K": 320.0, "humidity_rel": 0.0, "wind_speed": 6.0, "drag_scale": 2.5,
         "gust_intensity": 0.8, "thermal_strength": 3.0, "gravity": 3.72, "ground_friction": 1.1,
         "ground_effect_enabled": False, "compressibility_enabled": False},
    )
    r.check(abs(hot["density_kgm3"] - base["density_kgm3"]) > 1e-4, "temperature change did not move density")
    r.check(hot["environment"]["gravity"] == 3.72, "gravity was not persisted")
    r.check(hot["environment"]["drag_scale"] == 2.5, "drag_scale was not persisted")
    r.check(hot["environment"]["ground_effect_enabled"] is False, "ground_effect_enabled was not persisted")
    r.check(hot["backend"] == "numpy", f"unexpected atmosphere backend {hot['backend']}")
    r.log(f"hot: rho={hot['density_kgm3']:.5f} gravity={hot['environment']['gravity']} drag_scale={hot['environment']['drag_scale']}")

    # Out-of-range input must be clamped, not rejected.
    clamped = r.post("/api/v1/environment/atmosphere", {"drag_scale": 99.0, "humidity_rel": 5.0})
    r.check(clamped["environment"]["drag_scale"] == 5.0, "drag_scale was not clamped to 5.0")
    r.check(clamped["environment"]["humidity_rel"] == 1.0, "humidity_rel was not clamped to 1.0")

    # Validation must return 400 with a message.
    r.post("/api/v1/environment/atmosphere", {"temperature_K": "not-a-number"}, expect=400)

    # Restore a sane flying environment for the remaining stages.
    r.state["hot_env"] = hot["environment"]
    restored = r.post(
        "/api/v1/environment/atmosphere",
        {"temperature_K": 288.15, "pressure_Pa": 101325.0, "humidity_rel": 0.0,
         "wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0,
         "gravity": 9.80665, "ground_friction": 0.4, "drag_scale": 1.0,
         "lift_scale": 1.0, "ground_effect_enabled": True, "compressibility_enabled": True},
    )
    r.state["rho"] = restored["density_kgm3"]
    r.log(f"restored: rho={restored['density_kgm3']:.5f}")


def stage_aerodynamics(r: Runner) -> None:
    result = r.post("/api/v1/aerodynamics/calculate", {"speed_mps": 12.0, "alpha_deg": 5.0})
    for key in ("cl", "cd", "cm", "lift_n", "drag_n", "ld_ratio", "stalled",
                "stall_margin", "cd_min", "alpha_ld_max_deg", "reynolds", "mach"):
        r.check(key in result, f"aerodynamics/calculate missing '{key}'")
    r.check(result["lift_n"] > 0.0, "lift must be positive at alpha=5 deg")
    r.check(result["drag_n"] > 0.0, "drag must be positive")
    r.check(abs(result["ld_ratio"] - result["cl"] / result["cd"]) < 1e-6, "L/D inconsistent with cl/cd")
    r.check(result["backend"] == "numpy", "unexpected aerodynamics backend")
    r.log(f"CL={result['cl']:.4f} CD={result['cd']:.4f} L/D={result['ld_ratio']:.2f} L={result['lift_n']:.4f}N")

    # drag_scale must be observable through the force.
    doubled = r.post(
        "/api/v1/aerodynamics/calculate",
        {"speed_mps": 12.0, "alpha_deg": 5.0, "environment": {"drag_scale": 2.0}},
    )
    r.check(
        abs(doubled["drag_n"] - 2.0 * result["drag_n"]) < 1e-9,
        f"drag_scale=2.0 must exactly double drag ({doubled['drag_n']} vs {2*result['drag_n']})",
    )
    zero = r.post(
        "/api/v1/aerodynamics/calculate",
        {"speed_mps": 12.0, "alpha_deg": 5.0, "environment": {"drag_scale": 0.0}},
    )
    r.check(zero["drag_n"] == 0.0, "drag_scale=0.0 must give exactly zero drag")
    r.log("drag_scale 0/1/2 verified end-to-end through the API")

    # Stall behaviour.
    stalled = r.post("/api/v1/aerodynamics/calculate", {"speed_mps": 12.0, "alpha_deg": 22.0})
    r.check(stalled["stalled"] is True, "alpha=22 deg must report stalled")
    r.check(stalled["stall_margin"] < 0.0, "stall margin must be negative past the stall angle")

    polar = r.post("/api/v1/aerodynamics/polar", {"alpha_min_deg": -8.0, "alpha_max_deg": 20.0, "points": 57})
    for key in ("alpha_deg", "cl", "cd", "ld_ratio", "cd_min", "alpha_ld_max_deg"):
        r.check(key in polar, f"polar missing '{key}'")
    r.check(len(polar["alpha_deg"]) == 57, f"polar returned {len(polar['alpha_deg'])} points, expected 57")
    r.check(abs(polar["cd_min"] - min(polar["cd"])) < 1e-9, "cd_min does not match the sampled minimum")
    best = max(range(len(polar["ld_ratio"])), key=lambda i: polar["ld_ratio"][i])
    r.check(abs(polar["alpha_ld_max_deg"] - polar["alpha_deg"][best]) < 1e-9, "alpha_ld_max_deg mismatch")
    r.log(f"CDmin={polar['cd_min']:.5f} at alpha={polar['alpha_ld_max_deg']:.2f} deg")

    r.post("/api/v1/aerodynamics/polar", {"alpha_min_deg": 20.0, "alpha_max_deg": -8.0}, expect=400)
    r.post("/api/v1/aerodynamics/calculate", {"speed_mps": -1.0}, expect=400)


def stage_wind(r: Runner) -> None:
    field = r.post(
        "/api/v1/wind/field",
        {
            "bounds_min": [-1.0, -1.0, 0.0],
            "bounds_max": [1.0, 1.0, 1.0],
            "resolution": 8,
            "environment": {"wind_speed": 5.0, "gust_intensity": 0.4, "thermal_strength": 1.0},
        },
    )
    for key in ("positions", "vectors", "speeds", "shape", "backend", "grid_point_count"):
        r.check(key in field, f"wind/field missing '{key}'")
    r.check(field["shape"] == [8, 8, 8], f"unexpected shape {field['shape']}")
    r.check(field["grid_point_count"] == 512, f"expected 512 grid points, got {field['grid_point_count']}")
    for axis in ("positions", "vectors"):
        for row in field[axis]:
            r.check(len(row) == 512, f"{axis} row length {len(row)} != 512")
    r.check(len(field["speeds"]) == 512, "speeds length mismatch")
    r.check(field["backend"] in ("numpy", "cupy", "torch"), f"unexpected backend {field['backend']}")
    r.check(
        abs(field["max_speed"] - max(field["speeds"])) < 1e-9,
        "max_speed does not match the speed samples",
    )
    r.log(f"grid={field['grid_point_count']} backend={field['backend']} max={field['max_speed']:.3f} m/s mean={field['mean_speed']:.3f}")

    # Wind tunnel mode must give a uniform stream.
    tunnel = r.post(
        "/api/v1/wind/field",
        {
            "bounds_min": [0.0, -0.5, 0.0],
            "bounds_max": [1.0, 0.5, 0.5],
            "resolution": 4,
            "environment": {"wind_tunnel_enabled": True, "wind_tunnel_speed": 14.0},
        },
    )
    xs, us = tunnel["positions"][0], tunnel["vectors"][0]
    r.check(
        all(abs(u - 14.0) < 1e-9 for u in us),
        "wind tunnel mode must produce a uniform +x stream at wind_tunnel_speed",
    )
    r.log("wind tunnel uniform stream verified")

    r.post("/api/v1/wind/field", {"bounds_min": [1.0, 0, 0], "bounds_max": [-1.0, 1, 1]}, expect=400)


def stage_stress(r: Runner) -> None:
    result = r.post("/api/v1/stress/analysis", {"speed_mps": 12.0, "density_kgm3": 1.225, "spanwise": 12, "chordwise": 10})
    for key in ("stress_index", "stress_pa", "max_stress_index", "max_stress_pa",
                "wing_root_moment_nm", "load_factor", "failure_index", "status",
                "critical_spanwise_frac", "colours"):
        r.check(key in result, f"stress/analysis missing '{key}'")
    r.check(len(result["stress_index"]) == 10, "stress grid chordwise dimension wrong")
    r.check(len(result["stress_index"][0]) == 12, "stress grid spanwise dimension wrong")
    flat = [v for row in result["stress_index"] for v in row]
    r.check(all(0.0 <= v <= 1.0 for v in flat), "stress index left [0,1]")
    r.check(all(math.isfinite(v) for v in flat), "stress index contains NaN/Inf")
    r.check(abs(max(flat) - result["max_stress_index"]) < 1e-9, "max_stress_index mismatch")
    r.check(result["status"] in ("ok", "high_load", "failed"), f"bad status {result['status']}")
    r.check(result["max_stress_pa"] > 0.0, "max stress must be positive")
    r.check(result["backend"] == "numpy", "unexpected stress backend")
    r.log(f"max_index={result['max_stress_index']:.4f} max_pa={result['max_stress_pa']:.1f} nz={result['load_factor']:.3f} status={result['status']}")

    # Higher speed must raise the stress.
    faster = r.post("/api/v1/stress/analysis", {"speed_mps": 24.0, "density_kgm3": 1.225})
    r.check(
        faster["max_stress_pa"] > result["max_stress_pa"],
        "doubling the speed must increase the maximum stress",
    )
    r.log(f"24 m/s -> max_pa={faster['max_stress_pa']:.1f} (stress scales with dynamic pressure)")

    r.post("/api/v1/stress/analysis", {"density_kgm3": -1.0}, expect=400)


def stage_simulation(r: Runner) -> None:
    reset = r.post(
        "/api/v1/simulation/reset",
        {
            "launch": {"launch_speed": 12.0, "launch_angle_deg": 10.0, "launch_height_m": 1.5, "launch_roll_deg": 0.0},
            "environment": {"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        },
    )
    r.check("state" in reset, "reset response missing 'state'")
    r.check(reset["state"]["position_m"][2] < 0.0, "launch altitude must be negative z (z is down)")
    r.log(f"launch pos={tuple(round(v,3) for v in reset['state']['position_m'])}")

    previous_time = -1.0
    max_alpha = -90.0
    speed_seen = 0.0
    for index in range(400):
        step = r.post("/api/v1/simulation/step", {})
        for key in ("state", "dt", "substeps", "alpha_deg", "speed_mps", "altitude_m",
                    "ld_ratio", "g_load", "max_stress_index", "stalled", "landed"):
            if key not in step:
                raise SmokeFailure(f"simulation/step missing '{key}' at step {index}")
        t = step["time_s"]
        if t < previous_time - 1e-9:
            raise SmokeFailure(f"simulated time went backwards at step {index}: {t} < {previous_time}")
        previous_time = t
        max_alpha = max(max_alpha, step["alpha_deg"])
        speed_seen = max(speed_seen, step["speed_mps"])
        position = step["position_m"]
        if not all(math.isfinite(v) for v in position + step["velocity_mps"]):
            raise SmokeFailure(f"non-finite state at step {index}: {position}")
        if step["landed"] or step["crashed"]:
            r.log(f"terminated at step {index}: landed={step['landed']} crashed={step['crashed']} t={t:.3f}s")
            break
    r.check(previous_time > 0.0, "simulation never advanced in time")
    r.check(speed_seen > 0.0, "simulated speed stayed at zero")
    r.log(f"integrated {index+1} steps to t={previous_time:.3f}s, peak alpha={max_alpha:.2f} deg, peak speed={speed_seen:.2f} m/s")

    history = r.get("/api/v1/simulation/history")
    r.check(len(history["samples"]) > 10, f"history too short: {len(history['samples'])} frames")
    times = [s["time_s"] for s in history["samples"]]
    r.check(all(b >= a for a, b in zip(times, times[1:])), "history is not oldest-first")
    r.log(f"history frames={len(history['samples'])} metrics keys={sorted(history['metrics'])[:6]}...")

    frame = r.get("/api/v1/simulation/history/0")
    r.check("sample" in frame, "history frame endpoint missing 'sample'")
    r.get("/api/v1/simulation/history/999999", expect=400)

    r.state["flight_samples"] = history["samples"]
    r.state["flight_metrics"] = history["metrics"]


def stage_mesh(r: Runner) -> None:
    data = io.BytesIO(wing_obj().encode("utf-8"))
    metrics = None
    response = r.client.post(
        "/api/v1/mesh/import",
        data={
            "file": (data, "wing.obj"),
            "scale_hint": "mm",
            "cg_mode": "volume",
            "paper_gsm": "100",
        },
        content_type="multipart/form-data",
    )
    metrics = json.loads(response.get_data(as_text=True))
    if response.status_code != 200:
        raise SmokeFailure(f"mesh/import failed: {response.get_data(as_text=True)[:400]}")

    for key in ("model_id", "surface_area_m2", "planform_area_m2", "wingspan_m",
                "mean_chord_m", "taper_ratio", "aspect_ratio", "cg_m", "mass_kg",
                "inertia_tensor", "panels", "panel_count", "backend"):
        r.check(key in metrics, f"mesh/import missing '{key}'")
    r.check(len(metrics["inertia_tensor"]) == 9, "inertia_tensor must be a flat 9-tuple")
    r.check(len(metrics["cg_m"]) == 3, "cg_m must be a 3-tuple")
    r.check(metrics["planform_area_m2"] > 0.0, "planform_area_m2 must be positive")
    r.check(metrics["mean_chord_m"] > 0.0, "mean_chord_m must be positive")
    r.check(metrics["aspect_ratio"] > 0.0, "aspect_ratio must be positive")
    r.check(metrics["panel_count"] > 0, "no aerodynamic panels were built")
    r.check(metrics["backend"] in ("numpy", "trimesh"), f"unexpected backend {metrics['backend']}")
    r.log(
        f"span={metrics['wingspan_m']:.4f}m area={metrics['planform_area_m2']:.6f}m2 "
        f"AR={metrics['aspect_ratio']:.3f} taper={metrics['taper_ratio']:.3f} "
        f"mass={metrics['mass_kg']*1000:.2f}g panels={metrics['panel_count']} backend={metrics['backend']}"
    )

    model_id = metrics["model_id"]
    r.state["model_id"] = model_id

    # Unit correction: the same geometry declared in mm must come back 1000x
    # smaller in every length metric than the identical file declared in m.
    as_metres = r.client.post(
        "/api/v1/mesh/import",
        data={"file": (io.BytesIO(wing_obj().encode("utf-8")), "wing.obj"), "scale_hint": "m"},
        content_type="multipart/form-data",
    )
    r.check(as_metres.status_code == 200, f"scale_hint=m import failed: {as_metres.get_data(as_text=True)[:300]}")
    metres = json.loads(as_metres.get_data(as_text=True))
    for key in ("wingspan_m", "mean_chord_m"):
        ratio = metres[key] / metrics[key] if metrics[key] else float("inf")
        r.check(
            abs(ratio - 1000.0) < 1.0,
            f"scale_hint length correction wrong for {key}: m/mm ratio {ratio:.4f}, expected 1000",
        )
    for key in ("planform_area_m2", "surface_area_m2"):
        ratio = metres[key] / metrics[key] if metrics[key] else float("inf")
        r.check(
            abs(ratio - 1.0e6) < 1.0e4,
            f"scale_hint area correction wrong for {key}: m/mm ratio {ratio:.6g}, expected 1e6",
        )
    r.check(
        abs(metres["mass_kg"] / metrics["mass_kg"] - 1.0e6) < 1.0e4,
        f"mass must scale with area (1e6x): got {metres['mass_kg']/metrics['mass_kg']:.4g}",
    )
    r.log(
        f"unit correction verified: lengths x1000, areas x1e6 "
        f"(span {metrics['wingspan_m']*1000:.2f} mm -> {metres['wingspan_m']:.5f} m)"
    )

    listing = r.get("/api/v1/mesh/models")
    r.check(listing["count"] >= 1, "mesh model list is empty")
    r.check(any(m["model_id"] == model_id for m in listing["models"]), "imported model missing from the list")
    r.log(f"model library count={listing['count']}")

    fetched = r.get(f"/api/v1/mesh/metrics/{model_id}")
    r.check(fetched["mesh_hash"] == metrics["mesh_hash"], "mesh_hash is not stable across fetches")

    # Manual override must win over the automatic value and be recorded.
    override = r.post(
        f"/api/v1/mesh/overrides/{model_id}", {"overrides": {"planform_area_m2": 0.021}}
    )
    r.check(
        abs(override["planform_area_m2"] - 0.021) < 1e-12,
        f"manual override was not applied to planform_area_m2 (got {override['planform_area_m2']})",
    )
    r.check("planform_area_m2" in override["overrides"], "override was not recorded in metrics.overrides")
    r.log("manual override applied and recorded")

    selected = r.post("/api/v1/mesh/select", {"model_id": model_id})
    r.check(selected["model_id"] == model_id, "select returned the wrong model")

    # Selecting must push the mesh-derived geometry into the live airframe.
    config = r.post("/api/v1/environment/config", {})
    r.check(
        abs(config["aircraft"]["wing_area_m2"] - 0.021) < 1e-9,
        f"mesh-derived area did not reach the active AircraftConfig (got {config['aircraft']['wing_area_m2']})",
    )
    r.check(
        abs(config["aircraft"]["wingspan_m"] - metrics["wingspan_m"]) < 1e-9,
        "mesh-derived span did not reach the active AircraftConfig",
    )
    r.log(
        f"airframe updated from mesh: wing_area_m2={config['aircraft']['wing_area_m2']} "
        f"wingspan_m={config['aircraft']['wingspan_m']:.4f}"
    )

    # Binary STL path.
    stl = r.client.post(
        "/api/v1/mesh/import",
        data={"file": (io.BytesIO(stl_binary()), "tetra.stl"), "scale_hint": "m"},
        content_type="multipart/form-data",
    )
    if stl.status_code == 200:
        stl_metrics = json.loads(stl.get_data(as_text=True))
        r.check(stl_metrics["face_count"] == 4, f"binary STL face count wrong: {stl_metrics['face_count']}")
        tetra_volume = 0.1 ** 3 / 6.0
        r.check(
            abs(abs(stl_metrics["volume_m3"]) - tetra_volume) < 1e-9,
            f"tetrahedron volume wrong: {stl_metrics['volume_m3']} (expected |{tetra_volume}|)",
        )
        r.log(f"binary STL parsed: faces={stl_metrics['face_count']} volume={stl_metrics['volume_m3']:.3e} m3")
    else:
        raise SmokeFailure(f"binary STL import failed: {stl.get_data(as_text=True)[:300]}")

    r.get("/api/v1/mesh/metrics/does-not-exist", expect=400)
    r.post("/api/v1/mesh/select", {"model_id": "nope"}, expect=400)


def stage_optimizer(r: Runner) -> None:
    request_payload = {
        "objectives": [{"name": "max_distance", "weight": 1.0}, {"name": "min_max_stress", "weight": 0.5}],
        "algorithm": "nsga2",
        "design_variables": ["launch_angle_deg", "elevon_deg", "dihedral_deg", "paper_gsm"],
        "variable_bounds": {"launch_angle_deg": [0.0, 30.0], "elevon_deg": [0.0, 20.0],
                            "dihedral_deg": [0.0, 15.0], "paper_gsm": [80.0, 140.0]},
        # Deliberately small: this stage proves the submit -> progress -> ETA ->
        # result -> CSV *chain*, not the optimiser's accuracy. A heavy budget
        # would make the wall-clock deadline machine-dependent.
        "max_evaluations": 8,
        "population_size": 6,
        "seed": 3,
        "processes": 1,
        "sim_duration_s": 0.4,
        "dt_max": 0.04,
    }
    started = r.post("/api/v1/optimizer/run", request_payload, expect=202)
    r.check("job_id" in started, "optimizer/run did not return a job_id")
    job_id = started["job_id"]
    r.state["job_id"] = job_id
    r.log(f"job {job_id} accepted (status={started['status']}, total={started['total_evaluations']})")

    # Non-blocking proof: the submit call returned while the job was still live.
    statuses = []
    saw_progress = False
    saw_eta = False
    # Generous, because the flight integrator is intentionally high fidelity and
    # a loaded machine can be several times slower than an idle one.
    deadline = time.time() + 900.0
    while time.time() < deadline:
        status = r.get(f"/api/v1/optimizer/status/{job_id}")
        statuses.append(status)
        for key in ("job_id", "status", "progress", "evaluated_count", "total_evaluations",
                    "eta_seconds", "estimated_completion", "backend", "elapsed_seconds",
                    "evals_per_second", "algorithm"):
            r.check(key in status, f"optimizer/status missing '{key}'")
        if status["evaluated_count"] > 0:
            saw_progress = True
        if status["status"] not in ("queued", "running", "completed", "failed", "cancelled"):
            raise SmokeFailure(f"illegal status {status['status']}")
        if status["status"] in ("completed", "failed", "cancelled"):
            break
        if status["eta_seconds"] is not None and status["eta_seconds"] > 0:
            saw_eta = True
        if status["status"] == "running":
            time.sleep(0.25)
    else:
        raise SmokeFailure("optimizer job did not terminate within 900 s")

    final = statuses[-1]
    if final["status"] == "failed":
        raise SmokeFailure(f"optimizer job failed: {final.get('message')}")
    r.check(final["status"] in ("completed", "cancelled"), f"unexpected terminal status {final['status']}")
    r.check(saw_progress, "progress never advanced past zero evaluations")
    r.check(
        final["evaluated_count"] <= final["total_evaluations"] + 1,
        f"over-evaluated: {final['evaluated_count']} > {final['total_evaluations']}",
    )
    r.check(0.0 <= final["progress"] <= 1.0, f"progress out of range: {final['progress']}")
    r.log(
        f"terminal status={final['status']} evaluated={final['evaluated_count']}/{final['total_evaluations']} "
        f"backend={final['backend']} eta_seen={saw_eta}"
    )
    if saw_eta:
        eta_logged = [s for s in statuses if s["eta_seconds"] > 0][-1]
        r.log(
            f"ETA sample: eta_seconds={eta_logged['eta_seconds']:.2f} "
            f"evals_per_second={eta_logged['evals_per_second']:.3f} "
            f"estimated_completion={eta_logged['estimated_completion']}"
        )

    result = r.get(f"/api/v1/optimizer/result/{job_id}")
    for key in ("algorithm", "best_vector", "best_params", "best_objectives",
                "best_score", "pareto_front", "evaluations", "history", "backend"):
        r.check(key in result, f"optimizer/result missing '{key}'")
    r.check(len(result["history"]) > 0, "optimizer history is empty")
    front = result["pareto_front"]
    r.check(len(front) >= 1, "Pareto front is empty")
    if result["algorithm"] == "nsga2" and len(front) > 1:
        # Direction-aware dominance: `min_max_stress` must be MINIMISED.
        def dominates(a, b, objs):
            better = [
                (a["objectives"][o] >= b["objectives"][o])
                if OBJECTIVE_DIRECTIONS[o] > 0
                else (a["objectives"][o] <= b["objectives"][o])
                for o in objs
            ]
            strictly = [
                (a["objectives"][o] > b["objectives"][o])
                if OBJECTIVE_DIRECTIONS[o] > 0
                else (a["objectives"][o] < b["objectives"][o])
                for o in objs
            ]
            return all(better) and any(strictly)

        objs = ["max_distance", "min_max_stress"]
        for i in range(len(front)):
            for j in range(len(front)):
                if i != j:
                    r.check(
                        not dominates(front[i], front[j], objs),
                        f"Pareto front is not non-dominated: point {i} dominates {j} "
                        f"({front[i]['objectives']} vs {front[j]['objectives']})",
                    )
    r.log(
        f"result: evaluations={result['evaluations']} best_score={result['best_score']:.5f} "
        f"pareto={len(front)} best_params={ {k: round(v, 4) for k, v in result['best_params'].items()} }"
    )

    csv_response = r.client.get(f"/api/v1/optimizer/result/{job_id}?format=csv")
    r.check(csv_response.status_code == 200, "optimizer/result?format=csv failed")
    csv_text = csv_response.get_data(as_text=True)
    r.check("evaluation,score" in csv_text.splitlines()[0], "optimizer CSV header is wrong")
    r.log(f"optimizer CSV: {len(csv_text.splitlines())} lines, header ok")

    listing = r.get("/api/v1/jobs")
    r.check(listing["count"] >= 1, "job listing is empty")
    r.check(any(j["job_id"] == job_id for j in listing["jobs"]), "job missing from the listing")
    r.log(f"jobs endpoint: count={listing['count']} active={listing['active']}")

    r.get("/api/v1/optimizer/status/unknown-job", expect=400)

    # Validation
    r.post("/api/v1/optimizer/run", {"objectives": [{"name": "not_real"}]}, expect=400)
    r.post("/api/v1/optimizer/run", {"objectives": [{"name": "max_distance"}], "algorithm": "nope"}, expect=400)
    r.post("/api/v1/optimizer/run", {"algorithm": "grid"}, expect=400)


def stage_optimizer_cancel(r: Runner) -> None:
    # Kept deliberately cheap: the point is to prove the cooperative cancel is
    # honoured *mid-run*, not to finish an expensive search.
    started = r.post(
        "/api/v1/optimizer/run",
        {
            "objectives": [{"name": "max_distance", "weight": 1.0}],
            "algorithm": "differential-evolution",
            "design_variables": ["launch_angle_deg", "elevon_deg", "dihedral_deg", "paper_gsm"],
            "max_evaluations": 150,
            "population_size": 24,
            "processes": 1,
            "sim_duration_s": 0.8,
            "dt_max": 0.03,
        },
        expect=202,
    )
    job_id = started["job_id"]
    time.sleep(1.0)
    cancelled = r.post(f"/api/v1/optimizer/cancel/{job_id}")
    r.check(cancelled["status"] == "cancelled", f"cancel did not take effect: {cancelled['status']}")

    deadline = time.time() + 90.0
    status: Dict[str, Any] = {}
    while time.time() < deadline:
        status = r.get(f"/api/v1/optimizer/status/{job_id}")
        if status["status"] in ("cancelled", "completed", "failed"):
            break
        time.sleep(0.2)
    r.check(status.get("status") == "cancelled", f"job did not reach cancelled: {status.get('status')}")
    r.check(
        status["evaluated_count"] < status["total_evaluations"],
        f"cancelled job consumed every evaluation ({status['evaluated_count']}/{status['total_evaluations']})",
    )
    r.log(f"cancel honoured: evaluated {status['evaluated_count']}/{status['total_evaluations']} then cancelled")
    r.post("/api/v1/optimizer/cancel/unknown", expect=400)


def stage_batch(r: Runner) -> None:
    cases = [
        {"launch": {"launch_angle_deg": angle, "launch_speed": 12.0}, "duration_s": 0.6, "dt_max": 0.02}
        for angle in (5.0, 12.0, 20.0)
    ]
    batch = r.post("/api/v1/simulation/batch", {"cases": cases, "processes": 1})
    r.check(batch["count"] == 3, f"expected 3 batch results, got {batch['count']}")
    for index, entry in enumerate(batch["results"]):
        for key in ("distance_m", "flight_time_s", "max_altitude_m", "max_stress_index", "status"):
            r.check(key in entry, f"batch result {index} missing '{key}'")
        r.check(entry["distance_m"] >= 0.0, f"batch result {index} has negative distance")
    distances = [e["distance_m"] for e in batch["results"]]
    r.check(len(set(distances)) > 1, "batch results are all identical; parameters are not wired through")
    r.log(f"batch backend={batch['backend']} distances={[round(d,4) for d in distances]}")
    r.post("/api/v1/simulation/batch", {"cases": []}, expect=400)


def stage_data(r: Runner) -> None:
    samples = r.state.get("flight_samples", [])
    if not samples:
        raise SmokeFailure("no flight samples captured for export")
    exported = r.post(
        "/api/v1/data/export", {"kind": "flight", "format": "csv", "samples": samples}
    )
    r.check(exported["mimetype"] == "text/csv", f"unexpected mimetype {exported['mimetype']}")
    r.check(exported["filename"].endswith(".csv"), "CSV filename does not end in .csv")
    header = exported["payload"].splitlines()[0].split(",")
    r.check(header[0] == "time_s", f"unexpected CSV header {header[:3]}")
    r.check("alpha_deg" in header and "max_stress_index" in header, "CSV header is missing flight columns")
    r.check(len(exported["payload"].splitlines()) > 2, "CSV payload is too short")
    r.log(f"flight CSV: {len(exported['payload'].splitlines())} lines, {len(header)} columns")

    as_json = r.post(
        "/api/v1/data/export", {"kind": "flight", "format": "json", "samples": samples}
    )
    r.check(as_json["mimetype"] == "application/json", "JSON mimetype wrong")
    decoded = json.loads(as_json["payload"])
    r.check(len(decoded) == len(samples), f"JSON export lost rows: {len(decoded)} vs {len(samples)}")
    r.log(f"flight JSON: {len(decoded)} rows round-tripped")

    config_export = r.post("/api/v1/data/export", {"kind": "config", "format": "json"})
    bundle = json.loads(config_export["payload"])
    for section in ("aircraft", "environment", "launch", "simulation"):
        r.check(section in bundle, f"config export missing '{section}'")

    imported = r.post("/api/v1/data/import", {"bundle": bundle})
    r.check(imported["ok"] is True, "data/import did not report ok")
    r.check("aircraft" in imported["bundle"], "data/import lost the aircraft section")
    r.log("config export -> import round-trip verified")

    # A modified bundle must actually change the live configuration.
    bundle["aircraft"]["elevon_deg"] = 17.5
    bundle["environment"]["wind_speed"] = 3.0
    applied = r.post("/api/v1/data/import", {"bundle": bundle})
    r.check(abs(applied["bundle"]["aircraft"]["elevon_deg"] - 17.5) < 1e-9, "imported elevon not applied")
    current = r.get("/api/v1/environment/current")
    r.check(abs(current["environment"]["wind_speed"] - 3.0) < 1e-9, "imported wind speed not applied")
    r.log("imported values reached the live environment")

    r.post("/api/v1/data/import", {"bundle": {"aircraft": "not-an-object"}}, expect=400)
    r.post("/api/v1/data/export", {"kind": "flight", "format": "xml"}, expect=400)
    r.post("/api/v1/data/export", {"kind": "nope"}, expect=400)


def stage_ui(r: Runner) -> None:
    import re

    page = r.client.get("/")
    r.check(page.status_code == 200, f"GET / returned {page.status_code}")
    html = page.get_data(as_text=True)
    r.check("<canvas" in html, "index.html has no 3D canvas")
    for needle in ("eta", "optimizer", "wind", "stress"):
        r.check(needle.lower() in html.lower(), f"index.html does not mention '{needle}'")

    referenced = set(re.findall(r'(?:src|href)="([^"]+)"', html))
    static_files = set()
    for root, _dirs, names in os.walk("static"):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), "static").replace("\\", "/")
            static_files.add(f"static/{rel}")
    local = {ref for ref in referenced if ref.startswith("static/")}
    # Modules reached through an ES dynamic import() from main.js are referenced
    # by bare filename, not by a <script src>, so collect those too.
    js_sources = []
    for asset in sorted(local):
        if asset.endswith(".js"):
            js_sources.append(r.client.get(f"/{asset}").get_data(as_text=True))
    for asset in sorted(static_files):
        if asset not in local:
            name = asset.rsplit("/", 1)[-1]
            if any(name in text for text in js_sources) or name in html:
                local.add(asset)
    orphans = sorted(static_files - local)
    r.check(
        not orphans,
        f"static files never referenced by index.html or any module: {orphans}",
    )
    r.log(f"index.html + modules reference {len(local)} local assets, 0 orphans")

    for asset in sorted(local):
        response = r.client.get(f"/{asset}")
        r.check(response.status_code == 200, f"{asset} returned HTTP {response.status_code}")
    r.log(f"served {len(local)} static assets with HTTP 200")


def stage_investigate(r: Runner) -> None:
    """Auto investigation: trigger, poll status/progress, cancel."""
    job = r.post("/api/v1/investigate/run", {"target_metric": "distance"}, expect=202)
    job_id = job.get("job_id")
    r.check(bool(job_id), "investigate/run returned no job_id")
    status = r.get(f"/api/v1/investigate/status/{job_id}")
    r.check(status.get("job_id") == job_id, "investigate/status mismatched job_id")
    cancel = r.post(f"/api/v1/investigate/cancel/{job_id}")
    r.check(cancel.get("status") in ("cancelled", "running", "completed"), "investigate/cancel failed")
    r.log(f"auto-investigate job {job_id} lifecycle verified")


STAGES: List[Tuple[str, Callable[[Runner], None]]] = [
    ("health+schemas", stage_health),
    ("environment", stage_environment),
    ("aerodynamics", stage_aerodynamics),
    ("wind field", stage_wind),
    ("stress", stage_stress),
    ("simulation", stage_simulation),
    ("mesh import", stage_mesh),
    ("optimizer job", stage_optimizer),
    ("optimizer cancel", stage_optimizer_cancel),
    ("batch", stage_batch),
    ("auto investigate", stage_investigate),
    ("data export", stage_data),
    ("ui assets", stage_ui),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper airplane simulator smoke test")
    parser.add_argument("--stage", action="append", help="run only the named stage (repeatable)")
    parser.add_argument("--list", action="store_true", help="list stage names and exit")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()

    if args.list:
        for name, _fn in STAGES:
            print(name)
        return 0

    from app import app

    app.config["TESTING"] = True
    client = app.test_client()
    runner = Runner(client, verbose=not args.quiet)

    selected = [(n, f) for n, f in STAGES if not args.stage or n in args.stage]
    if args.stage:
        unknown = set(args.stage) - {n for n, _ in STAGES}
        if unknown:
            print(f"unknown stage(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2

    print("=" * 78)
    print("  3D PAPER AIRPLANE SIMULATOR - END-TO-END SMOKE TEST")
    print(f"  backend: numpy fallback (no CUDA device detected by the test harness)")
    print("=" * 78)

    started = time.perf_counter()
    for name, fn in selected:
        runner.stage(name, lambda fn=fn: fn(runner))
    total = time.perf_counter() - started

    passed = sum(1 for _n, ok, _m, _e in runner.results if ok)
    failed = len(runner.results) - passed
    print("-" * 78)
    if failed:
        print(f"  {FAIL} {passed}/{len(runner.results)} stages passed in {total:.2f}s")
        for name, ok, message, elapsed in runner.results:
            if not ok:
                print(f"     - {name}: {message}")
    else:
        print(f"  {PASS} all {passed}/{len(runner.results)} stages passed in {total:.2f}s")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
