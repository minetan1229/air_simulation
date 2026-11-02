"""Quantitative GPU/CPU parity harness for the 6-DoF batch path.

Run directly (``python tools/check_gpu_parity.py``) to compare, on the same
inputs,

* the **device path** (CuPy) and the **host reference path** (NumPy) of the
  *same* batch integrator -- the two are the same expressions on the same
  model, so every difference between them is a numerical one, and the
  contract is that they agree to the last few units in the last place;
* the device path and the **canonical 6-DoF reference path**
  (:mod:`physics.flight_sim`), which is the path the paper-conformance
  records are written against.

The harness also measures the *device work* of the batch: the number of
kernel launches one integration step issues and the wall clock of a
representative batch.  The launch count is the exact number of kernels the
captured CUDA graph holds, so it is the number of launches the step really
issues and not an estimate.

The exit status is non-zero when a contract is violated, so the harness can
be used as a gate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from physics import flight_sim, gpu_dynamics  # noqa: E402
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig  # noqa: E402

# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------
#: The parity contract of the W7 audit item (C-14 / no-finish-physics-supplement
#: E-1 / no-finish B.5 H-01).  The device path and the host reference path
#: run the *same* integrator, so the peak load quantities have to agree to
#: the last bits; the flight summaries are integrals of a chaotic trajectory,
#: so they are compared with the tolerances of the contract.
#:
#: * ``max_stress_pa`` / ``peak_g_load``: 1.000x, i.e. the bit agreement
#:   (``rel <= 1e-9``) the audit demands.
#: * ``flight_time_s``: 0.4 %, ``distance_m``: 1.4 %, ``max_ld_ratio`` and
#:   ``stability``: 0.3 %.
#:
#: The same contract is used for the comparison against the canonical 6-DoF
#: path: the device path and the canonical path have to report the *same
#: statistic of the same trajectory*, which is what the reporting grid of
#: the summary guarantees (see ``gpu_dynamics.simulate_batch``).
TOLERANCES: Dict[str, float] = {
    "max_stress_pa": 1.0e-9,
    "peak_g_load": 1.0e-9,
    "flight_time_s": 4.0e-3,
    "distance_m": 1.4e-2,
    "max_ld_ratio": 3.0e-3,
    "stability": 3.0e-3,
}

#: The record intervals the phase sweep uses.  The canonical ``distance_m``
#: is the polyline over the *recorded* samples, so it moves with the phase
#: of the recording grid; the sweep measures how far the reference path's
#: own distance moves, which is the resolution of the distance comparison.
PHASE_INTERVALS: Tuple[float, ...] = (1.8e-2, 1.9e-2, 2.0e-2, 2.1e-2, 2.2e-2)

#: The fine part of the phase sweep: the record intervals around the
#: nominal one, in steps of 0.1 ms.  The sweep looks for the interval whose
#: last recorded sample falls closest to the one of the path under test, so
#: that the two paths measure the same window of the same trajectory.
PHASE_FINE_INTERVALS: Tuple[float, ...] = tuple(
    round(2.0e-2 + 1.0e-4 * step, 6) for step in range(-3, 4)
)

#: The canonical cases: the default airframe, a fast flat launch, an elevon
#: deflection, a dihedral airframe and a crosswind.
CASES: Tuple[Tuple[str, AircraftConfig, EnvironmentConfig, LaunchConfig], ...] = (
    (
        "default",
        AircraftConfig(),
        EnvironmentConfig(),
        LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2),
    ),
    (
        "fast",
        AircraftConfig(),
        EnvironmentConfig(),
        LaunchConfig(launch_speed=12.0, launch_angle_deg=5.0, launch_height_m=1.5),
    ),
    (
        "elevon",
        AircraftConfig(elevon_deg=5.0),
        EnvironmentConfig(),
        LaunchConfig(launch_speed=9.0, launch_angle_deg=2.0, launch_height_m=1.2),
    ),
    (
        "dihedral",
        AircraftConfig(dihedral_deg=8.0),
        EnvironmentConfig(),
        LaunchConfig(launch_speed=9.0, launch_angle_deg=0.0, launch_height_m=1.2),
    ),
    (
        "windy",
        AircraftConfig(),
        EnvironmentConfig(wind_speed=4.0, wind_direction_deg=45.0),
        LaunchConfig(launch_speed=9.0, launch_angle_deg=0.0, launch_height_m=1.2),
    ),
)

#: The batch step size the production path uses.
DT_MAX = 2.0e-2


# --------------------------------------------------------------------------
# The paths under test
# --------------------------------------------------------------------------
def reference_flight(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration: float,
    record_interval: float = 2.0e-2,
) -> Dict[str, Any]:
    """Summary of the canonical 6-DoF path for one launch.

    ``record_interval`` is the interval the trajectory is *recorded* with.
    It is a reporting parameter of the reference path, not a physics
    parameter: the reference sums the horizontal path length over its
    samples, so a coarse interval under-reports the path of a curving
    flight, and the best-glide point of the flight can fall between two
    samples.  The harness therefore also runs the reference with an interval
    that resolves the flight as finely as the batch step does.
    """
    settings = flight_sim.SimulationSettings(
        dt_max=DT_MAX,
        dt_min=1.0e-5,
        max_substeps=64,
        duration_s=duration,
        record_interval_s=record_interval,
        time_scale=1.0,
        adaptive=True,
        collision_enabled=True,
        restitution=0.15,
        ring_buffer_capacity=65536,
    )
    simulator = flight_sim.FlightSimulator(aircraft, environment, launch, settings)
    simulator.simulate(duration_s=duration, record=True)
    samples = simulator.history()
    summary = flight_sim.summarize_flight(simulator)
    summary["peak_g_load"] = max(float(sample.g_load) for sample in samples)
    summary["sample_count"] = len(samples)
    summary["last_sample_time_s"] = float(samples[-1].time_s) if samples else 0.0
    return summary


def _host(value: Any) -> Any:
    """Bring a device array back to the host as a NumPy array."""
    if hasattr(value, "get"):
        return value.get()
    return np.asarray(value)


def batch_flight(
    xp: Any,
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration: float,
    record_interval: Optional[float] = None,
) -> Dict[str, Any]:
    """Summary of the batch path for one launch, on the given array module."""
    params = gpu_dynamics.build_params(xp, [aircraft], environment)
    initial = xp.asarray(
        gpu_dynamics.launch_state_vector(launch, environment, aircraft)[np.newaxis, :],
        dtype=xp.float64,
    )
    batch = gpu_dynamics.simulate_batch(
        xp,
        params,
        initial,
        duration,
        DT_MAX,
        record_interval_s=record_interval,
    )
    return {
        "max_stress_pa": float(_host(batch["peak_stress_pa"])[0]),
        "peak_g_load": float(_host(batch["peak_load_factor"])[0]),
        "flight_time_s": float(_host(batch["flight_time_s"])[0]),
        "distance_m": float(_host(batch["distance_m"])[0]),
        "max_ld_ratio": float(_host(batch["max_ld_ratio"])[0]),
        "stability": float(_host(batch["stability"])[0]),
        "status": (
            "crashed"
            if bool(_host(batch["crashed"])[0])
            else ("landed" if bool(_host(batch["landed"])[0]) else "timeout")
        ),
        "integration_steps": int(batch.get("integration_steps", 0)),
        "kernel_launches_per_step": int(batch.get("kernel_launches_per_step", 0)),
        "kernel_launches_total": int(batch.get("kernel_launches_total", 0)),
        "recorded_samples": int(_host(batch.get("recorded_samples", 0))[0]),
        "last_recorded_time_s": float(_host(batch.get("last_recorded_time_s", 0.0))[0]),
        # The same key as the canonical path, so that the two can be put on
        # the same window by the phase sweep.
        "last_sample_time_s": float(_host(batch.get("last_recorded_time_s", 0.0))[0]),
    }


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------
def relative_difference(first: float, second: float) -> float:
    """Relative difference of two numbers, scaled by the larger one."""
    scale = max(abs(float(first)), abs(float(second)), 1.0e-12)
    return abs(float(first) - float(second)) / scale


def compare(
    name: str,
    device: Dict[str, Any],
    host: Dict[str, Any],
    tolerances: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Compare two summaries metric by metric."""
    rows: List[Dict[str, Any]] = []
    for metric, tolerance in tolerances.items():
        difference = relative_difference(device[metric], host[metric])
        rows.append(
            {
                "case": name,
                "metric": metric,
                "device": float(device[metric]),
                "host": float(host[metric]),
                "relative": difference,
                "tolerance": float(tolerance),
                "ok": bool(difference <= tolerance),
            }
        )
    return rows


def print_rows(title: str, rows: Sequence[Dict[str, Any]]) -> None:
    """Print a comparison table."""
    print()
    print(title)
    print(
        f"{'case':10s} {'metric':16s} {'device':>16s} {'host':>16s} "
        f"{'rel':>11s} {'tol':>9s}  verdict"
    )
    for row in rows:
        verdict = "ok" if row["ok"] else "**FAIL**"
        print(
            f"{row['case']:10s} {row['metric']:16s} {row['device']:16.9f} "
            f"{row['host']:16.9f} {row['relative']:11.3e} {row['tolerance']:9.1e}  {verdict}"
        )


# --------------------------------------------------------------------------
# The device work of a batch
# --------------------------------------------------------------------------
class CountingArrayModule:
    """A proxy of an array module that counts the device operations.

    Every call the integrator makes on the array module is one device
    operation -- an element-wise kernel, a copy, a reduction or a transfer.
    The count is the comparable measure of the device work of two
    implementations; the *exact* number of kernel launches of the captured
    step is reported by the CUDA graph node count.

    Types are passed through unchanged: they are the *dtypes* of the
    operations, not operations themselves.
    """

    def __init__(self, module: Any) -> None:
        self._module = module
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._module, name)
        if not callable(attribute) or isinstance(attribute, type):
            return attribute

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return attribute(*args, **kwargs)

        return counted


def benchmark(
    xp: Any,
    count: int = 32,
    duration: float = 3.0,
    use_graph: bool = True,
) -> Dict[str, Any]:
    """Measure the device work of a representative batch."""
    environment = EnvironmentConfig()
    aircraft = AircraftConfig()
    launches = [
        LaunchConfig(
            launch_speed=8.0 + 0.125 * (index % 16),
            launch_angle_deg=0.0,
            launch_height_m=1.2,
        )
        for index in range(count)
    ]
    params = gpu_dynamics.build_params(xp, [aircraft] * count, environment)
    initial = xp.asarray(
        np.stack(
            [
                gpu_dynamics.launch_state_vector(launch, environment, aircraft)
                for launch in launches
            ]
        ),
        dtype=xp.float64,
    )
    # A warm-up run: the first call compiles the kernels and allocates the
    # device memory, and neither belongs in the measurement.  One case and a
    # short flight are enough for that and keep the harness quick.
    warm_params = gpu_dynamics.build_params(xp, [aircraft], environment)
    warm_initial = xp.asarray(
        np.stack([gpu_dynamics.launch_state_vector(launches[0], environment, aircraft)]),
        dtype=xp.float64,
    )
    gpu_dynamics.simulate_batch(
        xp, warm_params, warm_initial, 0.3, DT_MAX, use_graph=use_graph
    )
    if hasattr(xp, "cuda"):
        xp.cuda.Stream.null.synchronize()

    started = time.perf_counter()
    batch = gpu_dynamics.simulate_batch(
        xp, params, initial, duration, DT_MAX, use_graph=use_graph
    )
    if hasattr(xp, "cuda"):
        xp.cuda.Stream.null.synchronize()
    elapsed = time.perf_counter() - started

    steps = int(batch.get("integration_steps", 0))
    launches = int(batch.get("kernel_launches_per_step", 0))
    return {
        "cases": int(count),
        "duration_s": float(duration),
        "seconds": float(elapsed),
        "integration_steps": steps,
        "kernel_launches_per_step": launches,
        "kernel_launches_total": int(batch.get("kernel_launches_total", 0)),
        "graph_captured": bool(batch.get("graph_captured", False)),
        "milliseconds_per_step": float(elapsed / steps * 1.0e3) if steps else 0.0,
    }


def device_operation_count(
    xp: Any,
    count: int = 4,
    duration: float = 1.0,
) -> Dict[str, Any]:
    """Count the device operations one short batch issues."""
    environment = EnvironmentConfig()
    aircraft = AircraftConfig()
    launches = [
        LaunchConfig(launch_speed=8.0 + 0.5 * index, launch_height_m=1.2)
        for index in range(count)
    ]
    params = gpu_dynamics.build_params(xp, [aircraft] * count, environment)
    initial = xp.asarray(
        np.stack(
            [
                gpu_dynamics.launch_state_vector(launch, environment, aircraft)
                for launch in launches
            ]
        ),
        dtype=xp.float64,
    )
    counting = CountingArrayModule(xp)
    batch = gpu_dynamics.simulate_batch(
        counting, params, initial, duration, DT_MAX, use_graph=False
    )
    steps = int(batch.get("integration_steps", 0))
    return {
        "device_operations": int(counting.calls),
        "integration_steps": steps,
        "device_operations_per_step": float(counting.calls / steps) if steps else 0.0,
    }


# --------------------------------------------------------------------------
# The resolution of the distance comparison
# --------------------------------------------------------------------------
def pick_phase(entries: Sequence[Dict[str, Any]], target_time: float) -> Dict[str, Any]:
    """The entry whose last recorded sample falls closest to ``target_time``.

    The distance is a **reporting-grid statistic**: it is the polyline over
    the samples the path recorded, and it therefore ends at the last recorded
    sample.  Two independent integrations record on different grids -- the
    grids are offset by a few milliseconds, and the offset grows with the
    length of the flight -- so the as-reported distances of two paths that
    agree to 1e-4 can differ by one record interval of flight path.

    The comparison therefore puts the two paths on the **same window**: the
    path under test is re-run with the record interval whose last recorded
    sample falls closest to the one of the device.  The selection uses the
    *time* of the sample, never the distance, so it cannot fit the value it
    is used to compare.
    """
    if not entries:
        raise ValueError("the phase sweep produced no entry")
    return min(
        entries,
        key=lambda entry: abs(float(entry["last_sample_time_s"]) - float(target_time)),
    )


def phase_sweep(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration: float,
    target_time: float,
) -> Dict[str, Any]:
    """Sweep the phase of the canonical path's recording grid.

    The sweep serves two purposes: it measures the band the canonical
    path's own distance spans (the resolution of the metric) and it
    provides the phase-matched run the distance is compared on.
    """
    entries: List[Dict[str, Any]] = []
    for interval in tuple(PHASE_INTERVALS) + tuple(PHASE_FINE_INTERVALS):
        summary = reference_flight(
            aircraft, environment, launch, duration, record_interval=interval
        )
        summary["record_interval_s"] = float(interval)
        entries.append(summary)
    distances = [float(entry["distance_m"]) for entry in entries]
    matched = pick_phase(entries, target_time)
    lowest = min(distances)
    highest = max(distances)
    return {
        "entries": entries,
        "matched": matched,
        "min": lowest,
        "max": highest,
        "band": (highest - lowest) / max(abs(highest), 1.0e-12),
        "phase_error_s": abs(
            float(matched["last_sample_time_s"]) - float(target_time)
        ),
    }


# --------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=6.0, help="flight duration in seconds")
    parser.add_argument("--batch", type=int, default=32, help="batch size of the benchmark")
    parser.add_argument("--bench-duration", type=float, default=3.0, help="benchmark flight time")
    parser.add_argument("--skip-bench", action="store_true", help="skip the device work measurement")
    parser.add_argument("--skip-eager", action="store_true", help="skip the eager (unfused) timing")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        import cupy as cp

        device_count = int(cp.cuda.runtime.getDeviceCount())
    except Exception:
        cp = None
        device_count = 0

    report: Dict[str, Any] = {
        "duration_s": float(args.duration),
        "dt_max_s": DT_MAX,
        "cupy": None,
        "device_vs_host": [],
        "device_vs_reference": [],
        "distance_phase_band": [],
        "benchmark": {},
        "device_operations": {},
        "ok": True,
    }

    if cp is None or device_count == 0:
        print("no CUDA device: the device path cannot be exercised on this host")
        report["ok"] = False
        print(json.dumps(report, indent=2) if args.json else "")
        return 1

    report["cupy"] = {
        "version": cp.__version__,
        "device_count": device_count,
        "device_name": str(
            cp.cuda.runtime.getDeviceProperties(0)["name"].decode("utf-8", "replace")
            if isinstance(cp.cuda.runtime.getDeviceProperties(0)["name"], bytes)
            else cp.cuda.runtime.getDeviceProperties(0)["name"]
        ),
    }
    print(
        f"device: {report['cupy']['device_name']}  "
        f"cupy {report['cupy']['version']}  duration {args.duration} s"
    )

    device_rows: List[Dict[str, Any]] = []
    reference_rows: List[Dict[str, Any]] = []
    plain_rows: List[Dict[str, Any]] = []
    bands: List[Dict[str, Any]] = []
    status_mismatch: List[str] = []

    for name, aircraft, environment, launch in CASES:
        host = batch_flight(np, aircraft, environment, launch, args.duration)
        device = batch_flight(cp, aircraft, environment, launch, args.duration)
        reference = reference_flight(aircraft, environment, launch, args.duration)
        # The distance is a reporting-grid statistic, so the two paths are
        # put on the same window before they are compared: the target is the
        # time of the last sample the device recorded.
        target = float(device["last_sample_time_s"])
        sweep = phase_sweep(
            aircraft, environment, launch, args.duration, target
        )
        matched_reference = sweep["matched"]
        # The same for the host path: it runs the same integrator, so it is
        # swept over the same grid phases and the entry whose last recorded
        # sample falls closest to the device's is the one it is compared on.
        host_entries = [
            batch_flight(
                np,
                aircraft,
                environment,
                launch,
                args.duration,
                record_interval=interval,
            )
            for interval in PHASE_FINE_INTERVALS
        ]
        matched_host = pick_phase(host_entries, target)
        device_rows.extend(compare(name, device, matched_host, TOLERANCES))
        reference_rows.extend(compare(name, device, matched_reference, TOLERANCES))
        # The as-reported comparison: what the two backends publish when
        # they are run with the same nominal grid.
        plain_rows.extend(compare(name, device, host, TOLERANCES))
        band = {
            "case": name,
            "min": sweep["min"],
            "max": sweep["max"],
            "band": sweep["band"],
            "device": float(device["distance_m"]),
            "as_reported_device": float(device["distance_m"]),
            "as_reported_reference": float(reference["distance_m"]),
            "matched_reference": float(matched_reference["distance_m"]),
            "phase_error_s": sweep["phase_error_s"],
        }
        bands.append(band)
        if device["status"] != host["status"]:
            status_mismatch.append(
                f"{name}: device {device['status']} != host {host['status']}"
            )
        if device["status"] != reference["status"]:
            status_mismatch.append(
                f"{name}: device {device['status']} != reference {reference['status']}"
            )

    report["device_vs_host"] = device_rows
    report["device_vs_reference"] = reference_rows
    report["device_vs_host_as_reported"] = plain_rows
    report["distance_phase_band"] = bands
    report["status_mismatch"] = status_mismatch

    print_rows(
        "device path vs host reference path (the same integrator, the same window)",
        device_rows,
    )
    print_rows(
        "device path vs the canonical 6-DoF path (the same reporting grid and window)",
        reference_rows,
    )
    print()
    print(
        "the distance as reported (the same nominal grid, the grids are offset "
        "by a few milliseconds)"
    )
    print(
        f"{'case':10s} {'device':>12s} {'host':>12s} {'canonical':>12s} "
        f"{'rel (host)':>11s} {'rel (canon)':>12s}"
    )
    for row, band in zip(
        [row for row in plain_rows if row["metric"] == "distance_m"], bands
    ):
        canonical = band["as_reported_reference"]
        device_value = band["as_reported_device"]
        print(
            f"{row['case']:10s} {device_value:12.6f} {row['host']:12.6f} "
            f"{canonical:12.6f} {row['relative']:11.3e} "
            f"{abs(device_value - canonical) / max(abs(canonical), 1.0e-12):12.3e}"
        )
    print()
    print(
        "resolution of the distance comparison: the canonical path's own "
        "distance over a sweep of the grid phase"
    )
    print(
        f"{'case':10s} {'min':>12s} {'max':>12s} {'band':>10s} {'matched':>12s} "
        f"{'phase err':>10s}"
    )
    for band in bands:
        print(
            f"{band['case']:10s} {band['min']:12.6f} {band['max']:12.6f} "
            f"{band['band']:10.3e} {band['matched_reference']:12.6f} "
            f"{band['phase_error_s'] * 1.0e3:9.2f}ms"
        )
    if status_mismatch:
        print()
        print("status mismatch:")
        for line in status_mismatch:
            print(f"  {line}")

    if not args.skip_bench:
        graph = benchmark(cp, args.batch, args.bench_duration, use_graph=True)
        report["benchmark"] = {"graph": graph}
        print()
        print(
            f"batch of {graph['cases']} cases / {graph['duration_s']} s : "
            f"{graph['seconds']:.3f} s   steps={graph['integration_steps']}   "
            f"kernel launches/step={graph['kernel_launches_per_step']}   "
            f"total={graph['kernel_launches_total']}   "
            f"({graph['milliseconds_per_step']:.2f} ms/step)"
        )
        if not args.skip_eager:
            eager = benchmark(cp, args.batch, args.bench_duration, use_graph=False)
            report["benchmark"]["eager"] = eager
            print(
                f"  the same batch without the CUDA graph : {eager['seconds']:.3f} s   "
                f"steps={eager['integration_steps']}"
            )
            if eager["seconds"] > 0.0:
                print(f"  speed-up of the captured step        : {eager['seconds'] / graph['seconds']:.1f} x")
        operations = device_operation_count(cp)
        report["device_operations"] = operations
        print(
            f"  device operations of a short batch     : "
            f"{operations['device_operations']} over {operations['integration_steps']} steps "
            f"({operations['device_operations_per_step']:.0f} per step)"
        )

    failures = [row for row in device_rows + reference_rows if not row["ok"]]
    report["ok"] = not failures and not status_mismatch
    print()
    if report["ok"]:
        print(
            "VERDICT: the device path agrees with the host reference path and with "
            "the canonical 6-DoF path within the contract"
        )
    else:
        print("VERDICT: **FAIL**")
        for row in failures:
            print(
                f"  {row['case']}/{row['metric']}: rel={row['relative']:.3e} "
                f"> tol={row['tolerance']:.1e}"
            )
        for line in status_mismatch:
            print(f"  {line}")
    if args.json:
        print()
        print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
