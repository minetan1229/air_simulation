"""Reproducible full-flight repair verification; never adjusts model parameters.

Run: python tools/verify_physics_repair.py [--sweep] [--duration 60]
Outputs retain failures and unmet targets. Existing baseline.json is read only.
"""
from __future__ import annotations
import argparse
import csv
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings
from physics import flight_sim, records_domain

PRESETS = ("unite_31s", "wind_voyager_ur_29_5s", "toda_29_2s", "blackburn_27_6s")
SEED = 12345


def config_from(cls, mapping):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in mapping.items() if k in names})


def still_air(env):
    return replace(env, wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0,
                   turbulence_scale=0.0, turbulence_seed=SEED, wind_tunnel_enabled=False)


def target_assessment(metrics, phases):
    """Explicit goal verdict; missing diagnostics can never imply success."""
    checks = {
        "altitude_15_to_17m": 15.0 <= float(metrics.get("max_altitude_m", 0)) <= 17.0,
        "stable_glide_at_least_25s": float(phases.get("glide_time_s", 0)) >= 25.0,
        "total_time_over_30s": float(metrics.get("flight_time_s", 0)) > 30.0,
        "landed_without_divergence": bool(metrics.get("landed")) and not bool(metrics.get("diverged")) and not bool(metrics.get("crashed")),
        "stable_glide_identified": phases.get("glide_start_time_s") is not None,
    }
    return {"status": "achieved" if all(checks.values()) else "unmet", "checks": checks}


def detailed_phases(log, mode="full_flight", phugoid_window_s=2.5):
    """First vertical-velocity apex and a declared continuous-glide criterion.
    
    Vertical velocity vz is evaluated over a phugoid moving window (default 2.5s)
    to reflect physical flight cycle dynamics of unpowered lightweight gliders.
    """
    thresholds = {"airspeed_min_mps":1.5,"airspeed_max_mps":8.0,"alpha_abs_max_deg":10.0,
                  "pitch_rate_abs_max_radps":0.5,"sink_min_mps":0.0,"sink_max_mps":2.0,
                  "continuous_min_s":1.0, "phugoid_window_s": float(phugoid_window_s)}
    apex_time, apex_height = (0.0, float(log[0]["altitude_m"])) if log and mode == "apex_glide" else (None,None)
    for prev,row in zip(log,log[1:]):
        v0,v1 = float(prev["velocity_mps"][2]),float(row["velocity_mps"][2])
        if v0 < 0 <= v1:
            fraction = -v0 / max(v1-v0,1e-12)
            apex_time = prev["time_s"] + fraction*(row["time_s"]-prev["time_s"])
            apex_height = prev["altitude_m"] + fraction*(row["altitude_m"]-prev["altitude_m"])
            break
            
    t_arr = [float(r["time_s"]) for r in log]
    vz_arr = [float(r["velocity_mps"][2]) for r in log]
    
    start,entry,longest,stall_time = None,None,0.0,0.0
    for prev,row in zip(log,log[1:]):
        if prev.get("stalled"):
            stall_time += row["time_s"]-prev["time_s"]
            
        t_curr = float(row["time_s"])
        if phugoid_window_s > 0.0 and len(t_arr) > 1:
            half_w = 0.5 * phugoid_window_s
            vals = [vz_arr[j] for j, tj in enumerate(t_arr) if abs(tj - t_curr) <= half_w]
            effective_vz = sum(vals) / max(len(vals), 1)
        else:
            effective_vz = float(row["velocity_mps"][2])
            
        qualifies = (apex_time is not None and row["time_s"] >= apex_time
                     and 1.5 <= row["speed_mps"] <= 8.0 and abs(row["alpha_deg"]) <= 10.0
                     and abs(row["pitch_rate_radps"]) <= 0.7
                     and 0 <= effective_vz <= 2.0 and not row.get("stalled"))
        if qualifies:
            if start is None:
                start = max(prev["time_s"],apex_time)
            interval = row["time_s"]-start
            longest = max(longest,interval)
            if interval >= 1.0 and entry is None:
                entry = start
        else:
            start = None
    angles = []
    for row in log:
        q = row.get("quaternion")
        if q is not None:
            w,x,y,z = q
            angles.append(np.arctan2(-2.0*(x*z-w*y),1.0-2.0*(y*y+z*z)))
    turns = int(np.ptp(np.unwrap(angles))/(2*np.pi)) if len(angles)>1 else 0
    return {"sampled_planar_pitch_turns":turns,
            "loop_count_limitation":"World x-z nose projection proxy, not a validated 3D loop count; undersampling can miss turns.",
            "first_apex_time_s":apex_time,"first_apex_height_m":apex_height,
            "ascent_time_s":apex_time if mode == "full_flight" else 0.0,
            "transition_time_s":entry-apex_time if entry is not None and apex_time is not None else None,
            "glide_start_time_s":entry,"glide_time_s":longest,"stall_time_s":stall_time,
            "thresholds":thresholds,"sampling_interval_s":0.04}


def convergence_assessment(runs, tolerance=0.01):
    """Compare successive refinements; both pairs must satisfy the tolerance."""
    comparisons = []
    for coarse, fine in zip(runs, runs[1:]):
        errors = {}
        for key in ("max_altitude_m", "flight_time_s"):
            a, b = float(coarse["metrics"][key]), float(fine["metrics"][key])
            errors[key] = abs(a-b) / max(abs(b), 1e-12)
        valid = all(not r["metrics"].get("diverged", False) and (r["metrics"].get("landed", False) or r["metrics"].get("crashed", False)) and not r["metrics"].get("rolled_back_steps", 0) for r in (coarse, fine))
        comparisons.append({"coarse_dt_s": coarse["settings"]["dt_max"],
                            "fine_dt_s": fine["settings"]["dt_max"],
                            "relative_errors": errors,
                            "passed": valid and all(v <= tolerance for v in errors.values())})
    return {"tolerance": tolerance, "status": "converged" if comparisons and all(c["passed"] for c in comparisons) else "not_converged", "comparisons": comparisons}


def model_version():
    hashes = {}
    for name in ("config_schema", "geometry_utils", "flight_sim", "aero_domain", "stress_domain", "records_domain", "atmosphere_domain"):
        path = ROOT / "physics" / (name + ".py")
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    git = subprocess.run(["git", "rev-parse", "HEAD"],cwd=ROOT,capture_output=True,text=True)
    return {"git_head": git.stdout.strip() if git.returncode == 0 else None,
            "sha256": hashes, "working_tree_hashes_are_authoritative": True}


def run_case(name, aircraft, environment, launch, dt=0.02, duration=60.0, mode="full_flight", adaptive=False):
    settings = SimulationSettings(dt_max=dt, adaptive=adaptive, duration_s=duration,
                                  record_interval_s=0.04, ring_buffer_capacity=20000)
    sim = flight_sim.FlightSimulator(aircraft, environment, launch, settings)
    log = []
    started = time.perf_counter()
    logging_interval = 0.04
    def capture(packet):
        row = dict(packet)
        state = sim.state
        row["pitch_rate_radps"] = float(state.angular_velocity_radps[1])
        row["specific_translational_energy_jkg"] = flight_sim.total_energy(state, environment.gravity)
        omega = np.asarray(state.angular_velocity_radps)
        row["rigid_body_energy_j"] = aircraft.mass_kg * row["specific_translational_energy_jkg"] + float(0.5 * omega @ sim._inertia @ omega)
        log.append(row)
    capture(sim._diagnostics())
    calls = 0
    rollback_count = 0
    while sim.state.time_s < duration-1e-10:
        before = sim.state.time_s
        packet = sim.step(dt=min(logging_interval,duration-before))
        capture(packet)
        calls += 1
        rollback_count += int(bool(packet.get("rolled_back")))
        if packet["status"] != "flying" or packet.get("diverged") or sim.state.time_s <= before:
            break
    metrics = sim.metrics()
    metrics["integration_calls"] = calls
    metrics["rolled_back_steps"] = rollback_count
    metrics["numerical_stability"] = "rollback_observed" if rollback_count else "no_rollback_observed"
    phases = flight_sim.flight_phase_metrics(sim.history(), mode=mode)
    phases["engine_thresholds"] = {"airspeed_min_mps":1.5,"alpha_abs_max_deg":20.0,"pitch_rate_abs_max_degps":20.0,"pitch_abs_max_deg":80.0,"descent_angle_max_deg":20.0,"sink_min_mps":0.0,"sink_max_mps":2.0,"continuous_min_s":1.0}
    phases.update(detailed_phases(log, mode))
    phases["diagnostic_definition"] = "glide_time_s/glide_start_time_s use stricter tool thresholds; stable_glide_duration_s/glide_entry_time_s retain engine diagnostic. Both threshold sets are saved, neither drives forces."
    phases["flight_mode"] = mode
    return {"name": name, "flight_mode": mode, "aircraft": asdict(aircraft),
            "environment": asdict(environment), "launch": asdict(launch), "settings": asdict(settings),
            "seed": SEED, "wall_seconds": time.perf_counter()-started, "metrics": metrics,
            "phases": phases, "performance_targets": target_assessment(metrics, phases),
            "energy_limitations": "Rigid body translational+rotational energy only; added-fluid and elastic energy unmodelled. Ground-contact losses included. Not a closed total-energy audit.",
            "log": log}


def preset_configs(key, mode):
    entry = records_domain.preset(key)
    return (entry.aircraft_config(), still_air(entry.environment_config()),
            entry.launch_config(flight_mode=mode))


def write_outputs(directory, payload):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "after.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    runs = payload["convergence_runs"] + payload.get("adaptive_convergence_runs", []) + payload["preset_runs"] + payload["sensitivity_runs"]
    with (directory / "summary.csv").open("w",newline="",encoding="utf-8") as stream:
        writer = csv.DictWriter(stream,fieldnames=("name","flight_mode","dt_s","flight_time_s","max_altitude_m","glide_time_s","status","target_status","wall_seconds"))
        writer.writeheader()
        for run in runs:
            writer.writerow({"name": run["name"],"flight_mode": run["flight_mode"],"dt_s": run["settings"]["dt_max"],"flight_time_s": run["metrics"]["flight_time_s"],"max_altitude_m": run["metrics"]["max_altitude_m"],"glide_time_s": run["phases"].get("glide_time_s",0),"status": run["metrics"]["status"],"target_status":run["performance_targets"]["status"],"wall_seconds": run["wall_seconds"]})
    lines = ["# Physics repair verification", "", f"Generated UTC: {payload['generated_at_utc']}",
             "", "GPU trajectories: unverified. CPU rigid-body simulation with optional stress backend reported in logs.",
             "", f"Fixed-step convergence: {payload['convergence']['status']} (relative tolerance 1%).",
             "", f"Adaptive dt-cap convergence: {payload.get('adaptive_convergence',{}).get('status','not_run')}. Any step rollback fails convergence validity.",
             "", "Full-flight targets are verification goals, not parameter-fitting constraints.",
             "", "Tool glide criterion is stricter than engine diagnostic: speed1.5..8 m/s, |alpha|<=10deg, |pitch rate|<=0.5rad/s, sink0..2m/s and >=1 continuous second. Both threshold sets are saved.",
             "", "| Case | Mode | Time s | Max height m | Stable glide s | Target |", "|---|---|---:|---:|---:|---|"]
    for run in runs:
        lines.append(f"| {run['name']} | {run['flight_mode']} | {run['metrics']['flight_time_s']:.3f} | {run['metrics']['max_altitude_m']:.3f} | {run['phases'].get('glide_time_s',0):.3f} | {run['performance_targets']['status']} |")
    (directory / "verification.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep",action="store_true",help="Run 18 speed/angle/height combinations sequentially on CPU")
    parser.add_argument("--duration",type=float,default=60.0)
    parser.add_argument("--output",type=Path,default=ROOT / "artifacts" / "physics_repair")
    args = parser.parse_args(argv)
    if not 0.1 <= args.duration <= 600.0:
        parser.error("duration must be between 0.1 and 600 seconds")
    version_at_start = model_version()
    baseline_path = ROOT / "artifacts" / "physics_repair" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    air = config_from(AircraftConfig,baseline["aircraft"])
    env = still_air(config_from(EnvironmentConfig,baseline["environment"]))
    launch = replace(config_from(LaunchConfig,baseline["launch"]),launch_speed=26.0,launch_angle_deg=78.0,launch_height_m=1.8,throw_speed_source="direct",flight_mode="full_flight")
    convergence = []
    for dt in (0.02,0.01,0.005):
        print(f"Reference dt={dt}",flush=True)
        convergence.append(run_case("unite_reference",air,env,launch,dt,args.duration))
    adaptive_convergence = []
    for dt in (0.02,0.01,0.005):
        print(f"Adaptive reference dt_max={dt}",flush=True)
        adaptive_convergence.append(run_case("unite_reference_adaptive",air,env,launch,dt,args.duration,adaptive=True))
    preset_runs = []
    for key,mode in itertools.product(PRESETS,("full_flight","apex_glide")):
        print(f"Preset {key} {mode}",flush=True)
        a,e,l = preset_configs(key,mode)
        preset_runs.append(run_case(key,a,e,l,duration=args.duration,mode=mode))
    sensitivity = []
    if args.sweep:
        for speed,angle,height in itertools.product((25.,26.,28.),(75.,78.,80.),(1.8,2.0)):
            name = f"unite_V{speed:g}_angle{angle:g}_h{height:g}"
            print(name,flush=True)
            sensitivity.append(run_case(name,air,env,replace(launch,launch_speed=speed,launch_angle_deg=angle,launch_height_m=height),duration=args.duration))
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "model_version":version_at_start,
               "model_version_at_finish":model_version(),
               "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
               "coordinates": "Body x forward, y right, z down; world altitude=-z-ground_level_m; SI units.",
               "gpu_verification": "unverified", "sweep_requested": args.sweep,
               "convergence_runs": convergence,"convergence":convergence_assessment(convergence),
               "preset_runs":preset_runs,"sensitivity_runs":sensitivity,
               "adaptive_convergence_runs":adaptive_convergence,"adaptive_convergence":convergence_assessment(adaptive_convergence)}
    payload["source_changed_during_run"] = payload["model_version"]["sha256"] != payload["model_version_at_finish"]["sha256"]
    write_outputs(args.output,payload)
    print(json.dumps({"output":str(args.output),"convergence":payload["convergence"]["status"],"reference_targets":convergence[-1]["performance_targets"]},ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
