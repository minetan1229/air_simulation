"""Stage 4: Real-world robustness evaluation and asset export.

Tests top elite aircraft under Dryden turbulence, throw jitter, and high-pitch
launches, and generates standalone .obj 3D mesh and JSON summary files.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import FlightSimulator
from core.interfaces import AircraftCandidate, RobustnessReport
from physics.benchmark.benchmark import _build_aircraft_config


class RobustnessEvaluator:
    """Evaluates final selected aircraft candidates under realistic perturbations.
    
    Uses Monte Carlo sampling (LHS/fixed seed) for statistical robustness.
    """

    def __init__(self, duration_s: float = 6.0, n_samples: int = 16, seed: int = 2026) -> None:
        self.duration_s = duration_s
        self.n_samples = max(8, int(n_samples))
        self.seed = int(seed)

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
            aircraft = AircraftConfig(
                mass_kg=geom.mass_kg if geom else 0.005,
                wing_area_m2=geom.wing_area_m2 if geom else 0.015,
                wingspan_m=geom.wingspan_m if geom else 0.18,
                aspect_ratio=geom.aspect_ratio if geom else 2.16,
                dihedral_deg=cfg.wing.dihedral_deg,
                elevon_deg=0.5 * (cfg.wing.elevon_left_deg + cfg.wing.elevon_right_deg),
            )

        rng = np.random.default_rng(self.seed)
        dryden_survivals, dryden_times = [], []
        jitter_survivals, climb_recoveries = [], []

        for k in range(self.n_samples):
            # 1. Dryden 乱流モンテカルロ（風向・風速・シードを分散）
            turb_env = EnvironmentConfig(
                wind_speed=float(rng.uniform(1.0, 3.5)),
                wind_direction_deg=float(rng.uniform(0.0, 360.0)),
                gust_intensity=float(rng.uniform(0.5, 1.2)),
                turbulence_seed=int(self.seed * 100 + k),
                surface_roughness_m=0.01,
            )
            turb_launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.5)
            try:
                sim = FlightSimulator(aircraft=aircraft, environment=turb_env, launch=turb_launch)
                sim.simulate(duration_s=self.duration_s)
                m = sim.metrics()
                ok = not bool(m.get("crashed", False) or m.get("diverged", False))
                dryden_survivals.append(1.0 if ok else 0.0)
                dryden_times.append(float(m.get("flight_time_s", 0.0) or 0.0) if ok else 0.0)
            except Exception:
                dryden_survivals.append(0.0)
                dryden_times.append(0.0)

            # 2. 投擲バラツキモンテカルロ（対称ガウス擾乱）
            jitter_launch = LaunchConfig(
                launch_speed=float(np.clip(rng.normal(8.0, 1.0), 5.0, 12.0)),
                launch_angle_deg=float(rng.normal(0.0, 4.0)),
                launch_roll_deg=float(rng.normal(0.0, 8.0)),
                launch_yaw_deg=float(rng.normal(0.0, 6.0)),
                throw_pitch_rate_radps=float(rng.normal(0.0, 0.8)),
                launch_height_m=float(np.clip(rng.normal(1.5, 0.15), 1.0, 2.0)),
            )
            calm_env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
            try:
                sim = FlightSimulator(aircraft=aircraft, environment=calm_env, launch=jitter_launch)
                sim.simulate(duration_s=self.duration_s)
                m = sim.metrics()
                ok = not bool(m.get("crashed", False) or m.get("diverged", False))
                jitter_survivals.append(1.0 if ok else 0.0)
            except Exception:
                jitter_survivals.append(0.0)

            # 3. 高ピッチ回復モンテカルロ（14〜24度）
            climb_launch = LaunchConfig(
                launch_speed=float(rng.uniform(7.5, 10.5)),
                launch_angle_deg=float(rng.uniform(14.0, 24.0)),
                launch_height_m=1.5,
            )
            try:
                sim = FlightSimulator(aircraft=aircraft, environment=calm_env, launch=climb_launch)
                sim.simulate(duration_s=self.duration_s)
                m = sim.metrics()
                crashed = bool(m.get("crashed", False) or m.get("diverged", False))
                stalled = bool(m.get("stalled", False))
                climb_recoveries.append(1.0 if (not crashed and not stalled) else (0.25 if not crashed else 0.0))
            except Exception:
                climb_recoveries.append(0.0)

        dryden_rate = float(np.mean(dryden_survivals))
        jitter_rate = float(np.mean(jitter_survivals))
        recovery_rate = float(np.mean(climb_recoveries))
        # 下駄（0.2固定底上げ）を完全廃止
        overall = float(0.4 * dryden_rate + 0.3 * jitter_rate + 0.3 * recovery_rate)
        sorted_t = sorted(dryden_times)
        cvar_count = max(1, int(math.ceil(0.10 * len(sorted_t))))
        cvar10_hangtime = float(np.mean(sorted_t[:cvar_count]))

        return RobustnessReport(
            candidate_id=candidate.candidate_id,
            dryden_survival_rate=dryden_rate,
            dryden_mean_hangtime_s=float(np.mean(dryden_times)),
            jitter_robustness_score=jitter_rate,
            high_pitch_recovery_rate=recovery_rate,
            overall_robustness_score=overall,
            details={
                "n_samples": self.n_samples,
                "dryden_cvar10_hangtime_s": cvar10_hangtime,
                "dryden_std_hangtime_s": float(np.std(dryden_times)),
            },
        )

    def export_candidate_json(self, candidate: AircraftCandidate, output_path: str) -> None:
        """Export candidate metadata, config, and metrics to JSON."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(candidate.to_dict(), f, indent=2, ensure_ascii=False)

    def export_candidate_obj(self, candidate: AircraftCandidate, output_path: str) -> None:
        """Export candidate 3D geometric mesh to Wavefront OBJ format."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        positions: List[float] = []
        indices: List[int] = []

        # Attempt to extract actual folded 3D mesh from raw_genome
        if candidate.raw_genome is not None:
            try:
                from physics.origami_genome import step_meshes
                meshes = step_meshes(candidate.raw_genome)
                if meshes:
                    final_mesh = meshes[-1]
                    positions = list(final_mesh.get("positions", []))
                    indices = list(final_mesh.get("indices", []))
            except Exception:
                pass

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Wavefront OBJ file for {candidate.candidate_id}\n")
            f.write(f"# Step count: {candidate.step_count}\n")

            if positions and indices and len(positions) >= 9 and len(indices) >= 3:
                # Real 3D origami vertex mesh
                for i in range(0, len(positions), 3):
                    f.write(f"v {positions[i]:.5f} {positions[i+1]:.5f} {positions[i+2]:.5f}\n")
                for i in range(0, len(indices), 3):
                    f.write(f"f {indices[i]+1} {indices[i+1]+1} {indices[i+2]+1}\n")
            else:
                # Fallback clean triangle mesh if mesh generation unavailable
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
                faces = [
                    (1, 4, 2),
                    (1, 3, 5),
                    (1, 2, 6),
                    (1, 6, 3),
                ]
                for vx, vy, vz in vertices:
                    f.write(f"v {vx:.5f} {vy:.5f} {vz:.5f}\n")
                for f1, f2, f3 in faces:
                    f.write(f"f {f1} {f2} {f3}\n")