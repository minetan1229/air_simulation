"""Stage 4: Multi-seed, multi-azimuth robustness evaluation and asset export."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import FlightSimulator
from core.interfaces import AircraftCandidate, RobustnessReport
from physics.benchmark.benchmark import _build_aircraft_config


class RobustnessEvaluator:
    """Evaluates final selected aircraft candidates under realistic perturbations.

    Uses Monte Carlo sampling (LHS/fixed seed) for statistical robustness.
    """

    def __init__(
        self,
        duration_s: float = 6.0,
        n_samples: int = 16,
        seed: int = 2026,
        dryden_wind_directions_deg: Sequence[float] = (0.0, 90.0, 180.0, 270.0),
        dryden_seeds: Sequence[int] = (101, 202),
        dt_max: Optional[float] = None,
    ) -> None:
        self.duration_s = duration_s
        self.dt_max = dt_max
        self.n_samples = max(8, int(n_samples))
        self.seed = int(seed)
        self.dryden_wind_directions_deg = tuple(float(d) for d in dryden_wind_directions_deg)
        self.dryden_seeds = tuple(int(s) for s in dryden_seeds)

    def evaluate_candidate(self, candidate: AircraftCandidate) -> RobustnessReport:
        """Run Dryden turbulence, jitter, and high-pitch throw stress tests with Monte Carlo."""
        # 折り紙ジオメトリ由来の完全な AircraftConfig を構築（CG/慣性欠落を解消）
        if candidate.raw_genome is not None:
            from physics.origami_genome import genome_to_aircraft_config
            aircraft = genome_to_aircraft_config(
                candidate.raw_genome, paper_gsm=candidate.config.paper_gsm
            )
        else:
            geom = candidate.geometry
            cfg = candidate.config
            keel_height_m = geom.keel_height_m if geom else 0.015
            aircraft = AircraftConfig(
                mass_kg=geom.mass_kg if geom else 0.005,
                wing_area_m2=geom.wing_area_m2 if geom else 0.015,
                wingspan_m=geom.wingspan_m if geom else 0.18,
                aspect_ratio=geom.aspect_ratio if geom else 2.16,
                dihedral_deg=cfg.wing.dihedral_deg,
                elevon_deg=0.5 * (cfg.wing.elevon_left_deg + cfg.wing.elevon_right_deg),
                keel_height_m=keel_height_m,
            )

        rng = np.random.default_rng(self.seed)
        dryden_survivals, dryden_times = [], []
        dryden_runs: List[Dict[str, Any]] = []
        jitter_survivals, climb_recoveries = [], []
        jitter_runs: List[Dict[str, Any]] = []

        # 1. Dryden 乱流モンテカルロ（風向・風速・シードを分散） + 固定シナリオ
        total_dryden_scenarios = len(self.dryden_wind_directions_deg) * len(self.dryden_seeds)
        mc_dryden_count = max(0, self.n_samples - total_dryden_scenarios)

        # Fixed Dryden scenarios from master
        for w_dir in self.dryden_wind_directions_deg:
            for seed in self.dryden_seeds:
                turb_env = EnvironmentConfig(
                    wind_speed=2.0,
                    wind_direction_deg=w_dir,
                    gust_intensity=0.8,
                    surface_roughness_m=0.01,
                    turbulence_seed=seed,
                )
                turb_launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.5)
                try:
                    sim = FlightSimulator(aircraft=aircraft, environment=turb_env, launch=turb_launch)
                    sim.simulate(duration_s=self.duration_s)
                    m = sim.metrics()
                    ht = float(m.get("flight_time_s", 0.0) or 0.0)
                    ok = not bool(m.get("crashed", False) or m.get("diverged", False))
                except Exception:
                    ht = 0.0
                    ok = False

                if ok:
                    dryden_survivals.append(1.0)
                    dryden_times.append(ht)
                else:
                    dryden_survivals.append(0.0)
                    dryden_times.append(0.0)

                dryden_runs.append({
                    "wind_direction_deg": w_dir,
                    "turbulence_seed": seed,
                    "survived": ok,
                    "flight_time_s": ht,
                })

        # Monte Carlo Dryden scenarios
        for k in range(mc_dryden_count):
            turb_env = EnvironmentConfig(
                wind_speed=float(rng.uniform(1.0, 3.5)),
                wind_direction_deg=float(rng.uniform(0.0, 360.0)),
                gust_intensity=float(rng.uniform(0.5, 1.2)),
                turbulence_seed=int(self.seed * 1000 + k),
                surface_roughness_m=0.01,
            )
            turb_launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.5)
            try:
                sim = FlightSimulator(aircraft=aircraft, environment=turb_env, launch=turb_launch)
                sim.simulate(duration_s=self.duration_s)
                m = sim.metrics()
                ht = float(m.get("flight_time_s", 0.0) or 0.0)
                ok = not bool(m.get("crashed", False) or m.get("diverged", False))
            except Exception:
                ht = 0.0
                ok = False

            if ok:
                dryden_survivals.append(1.0)
                dryden_times.append(ht)
            else:
                dryden_survivals.append(0.0)
                dryden_times.append(0.0)

            dryden_runs.append({
                "wind_direction_deg": float(rng.uniform(0.0, 360.0)),
                "turbulence_seed": int(self.seed * 1000 + k),
                "survived": ok,
                "flight_time_s": ht,
            })

        # 2. 投擲バラツキモンテカルロ（対称ガウス擾乱）+ 固定シナリオ
        jitter_cases: Tuple[Tuple[float, float, float, float], ...] = (
            (2.0, 5.0, -3.0, 0.5),
            (2.0, -5.0, 3.0, 0.5),
            (-2.0, 5.0, 3.0, -0.5),
            (-2.0, -5.0, -3.0, -0.5),
        )

        # Fixed jitter cases
        calm_env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
        for pitch_deg, roll_deg, yaw_deg, q_rate in jitter_cases:
            jitter_launch = LaunchConfig(
                launch_speed=8.0,
                launch_angle_deg=pitch_deg,
                launch_roll_deg=roll_deg,
                launch_yaw_deg=yaw_deg,
                throw_pitch_rate_radps=q_rate,
                launch_height_m=1.5,
            )
            try:
                sim = FlightSimulator(aircraft=aircraft, environment=calm_env, launch=jitter_launch)
                sim.simulate(duration_s=self.duration_s)
                m = sim.metrics()
                ok = not bool(m.get("crashed", False) or m.get("diverged", False))
            except Exception:
                ok = False

            jitter_survivals.append(1.0 if ok else 0.0)
            jitter_runs.append({
                "pitch_deg": pitch_deg,
                "roll_deg": roll_deg,
                "yaw_deg": yaw_deg,
                "pitch_rate_radps": q_rate,
                "survived": ok,
            })

        # Monte Carlo jitter
        mc_jitter_count = max(0, self.n_samples // 4)
        for _ in range(mc_jitter_count):
            jitter_launch = LaunchConfig(
                launch_speed=float(np.clip(rng.normal(8.0, 1.0), 5.0, 12.0)),
                launch_angle_deg=float(rng.normal(0.0, 4.0)),
                launch_roll_deg=float(rng.normal(0.0, 8.0)),
                launch_yaw_deg=float(rng.normal(0.0, 6.0)),
                throw_pitch_rate_radps=float(rng.normal(0.0, 0.8)),
                launch_height_m=float(np.clip(rng.normal(1.5, 0.15), 1.0, 2.0)),
            )
            try:
                sim = FlightSimulator(aircraft=aircraft, environment=calm_env, launch=jitter_launch)
                sim.simulate(duration_s=self.duration_s)
                m = sim.metrics()
                ok = not bool(m.get("crashed", False) or m.get("diverged", False))
            except Exception:
                ok = False

            jitter_survivals.append(1.0 if ok else 0.0)

        # 3. 高ピッチ回復モンテカルロ（14〜24度）+ 固定シナリオ
        climb_launch = LaunchConfig(
            launch_speed=9.0,
            launch_angle_deg=18.0,
            launch_height_m=1.5,
        )
        climb_metrics: Dict[str, Any] = {}
        try:
            sim = FlightSimulator(aircraft=aircraft, environment=calm_env, launch=climb_launch)
            sim.simulate(duration_s=self.duration_s)
            m = sim.metrics()
            climb_metrics = m
            crashed = bool(m.get("crashed", False) or m.get("diverged", False))
            stalled = bool(m.get("stalled", False))
            climb_recoveries.append(1.0 if (not crashed and not stalled) else (0.5 if not crashed else 0.0))
        except Exception as exc:
            climb_recoveries.append(0.0)
            climb_metrics = {"error": f"{type(exc).__name__}: {exc}", "crashed": True}

        # Monte Carlo high-pitch
        mc_climb_count = max(0, self.n_samples // 4)
        for _ in range(mc_climb_count):
            climb_launch_mc = LaunchConfig(
                launch_speed=float(rng.uniform(7.5, 10.5)),
                launch_angle_deg=float(rng.uniform(14.0, 24.0)),
                launch_height_m=1.5,
            )
            try:
                sim = FlightSimulator(aircraft=aircraft, environment=calm_env, launch=climb_launch_mc)
                sim.simulate(duration_s=self.duration_s)
                m = sim.metrics()
                crashed = bool(m.get("crashed", False) or m.get("diverged", False))
                stalled = bool(m.get("stalled", False))
                climb_recoveries.append(1.0 if (not crashed and not stalled) else (0.25 if not crashed else 0.0))
            except Exception:
                climb_recoveries.append(0.0)

        dryden_rate = float(np.mean(dryden_survivals)) if dryden_survivals else 0.0
        jitter_rate = float(np.mean(jitter_survivals)) if jitter_survivals else 0.0
        recovery_rate = float(np.mean(climb_recoveries)) if climb_recoveries else 0.0
        # 下駄（0.2固定底上げ）を完全廃止
        overall = float(0.4 * dryden_rate + 0.3 * jitter_rate + 0.3 * recovery_rate)
        sorted_t = sorted(dryden_times)
        cvar_count = max(1, int(math.ceil(0.10 * len(sorted_t))))
        cvar10_hangtime = float(np.mean(sorted_t[:cvar_count])) if sorted_t else 0.0

        return RobustnessReport(
            candidate_id=candidate.candidate_id,
            dryden_survival_rate=dryden_rate,
            dryden_mean_hangtime_s=float(np.mean(dryden_times)) if dryden_times else 0.0,
            jitter_robustness_score=jitter_rate,
            high_pitch_recovery_rate=recovery_rate,
            overall_robustness_score=overall,
            details={
                "n_samples": self.n_samples,
                "keel_height_m": keel_height_m if 'keel_height_m' in locals() else (candidate.geometry.keel_height_m if candidate.geometry else 0.015),
                "dryden_cvar10_hangtime_s": cvar10_hangtime,
                "dryden_std_hangtime_s": float(np.std(dryden_times)) if dryden_times else 0.0,
                "dryden_runs": dryden_runs,
                "jitter_runs": jitter_runs,
                "climb_metrics": climb_metrics,
            },
        )

    def export_candidate_json(self, candidate: AircraftCandidate, output_path: str) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(candidate.to_dict(), f, indent=2, ensure_ascii=False)

    def export_candidate_obj(self, candidate: AircraftCandidate, output_path: str) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        positions: List[float] = []
        indices: List[int] = []

        if candidate.raw_genome is not None:
            try:
                from physics.origami_genome import step_meshes

                meshes = step_meshes(candidate.raw_genome)
                if meshes:
                    final_mesh = meshes[-1]
                    positions = list(final_mesh.get("positions", []))
                    indices = list(final_mesh.get("indices", []))
            except Exception:
                positions = []
                indices = []

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Wavefront OBJ file for {candidate.candidate_id}\n")
            f.write(f"# Step count: {candidate.step_count}\n")
            if positions and indices and len(positions) >= 9 and len(indices) >= 3:
                for i in range(0, len(positions), 3):
                    f.write(f"v {positions[i]:.5f} {positions[i+1]:.5f} {positions[i+2]:.5f}\n")
                for i in range(0, len(indices), 3):
                    f.write(f"f {indices[i]+1} {indices[i+1]+1} {indices[i+2]+1}\n")
            else:
                geom = candidate.geometry
                span = geom.wingspan_m if geom else 0.18
                chord = geom.chord_m if geom else 0.12
                keel = geom.keel_height_m if geom else 0.012
                half_span = 0.5 * span
                vertices = [
                    (0.0, chord, 0.0),
                    (-0.01, 0.0, 0.0),
                    (0.01, 0.0, 0.0),
                    (-half_span, 0.02, 0.01),
                    (half_span, 0.02, 0.01),
                    (0.0, 0.04, -keel),
                ]
                faces = [(1, 4, 2), (1, 3, 5), (1, 2, 6), (1, 6, 3)]
                for vx, vy, vz in vertices:
                    f.write(f"v {vx:.5f} {vy:.5f} {vz:.5f}\n")
                for f1, f2, f3 in faces:
                    f.write(f"f {f1} {f2} {f3}\n")
