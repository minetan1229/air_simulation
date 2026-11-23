"""Stage 4: Multi-seed, multi-azimuth robustness evaluation and asset export."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from core.interfaces import AircraftCandidate, RobustnessReport
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import FlightSimulator


class RobustnessEvaluator:
    """Evaluates final selected aircraft candidates under multi-scenario perturbations."""

    def __init__(
        self,
        duration_s: float = 6.0,
        dryden_wind_directions_deg: Sequence[float] = (0.0, 90.0, 180.0, 270.0),
        dryden_seeds: Sequence[int] = (101, 202),
    ) -> None:
        self.duration_s = float(duration_s)
        self.dryden_wind_directions_deg = tuple(float(d) for d in dryden_wind_directions_deg)
        self.dryden_seeds = tuple(int(s) for s in dryden_seeds)

    def evaluate_candidate(self, candidate: AircraftCandidate) -> RobustnessReport:
        """Run multi-azimuth/multi-seed Dryden turbulence, symmetric jitter, and high-pitch tests."""
        geom = candidate.geometry
        cfg = candidate.config

        mass_kg = geom.mass_kg if geom else 0.005
        wing_area = geom.wing_area_m2 if geom else 0.015
        wingspan = geom.wingspan_m if geom else 0.18
        aspect_ratio = geom.aspect_ratio if geom else 2.16
        keel_height_m = geom.keel_height_m if geom else 0.015
        dihedral_deg = cfg.wing.dihedral_deg
        elevon_deg = 0.5 * (cfg.wing.elevon_left_deg + cfg.wing.elevon_right_deg)

        aircraft = AircraftConfig(
            mass_kg=mass_kg,
            wing_area_m2=wing_area,
            wingspan_m=wingspan,
            aspect_ratio=aspect_ratio,
            dihedral_deg=dihedral_deg,
            elevon_deg=elevon_deg,
            keel_height_m=keel_height_m,
        )

        turb_launch = LaunchConfig(
            launch_speed=8.0,
            launch_angle_deg=0.0,
            launch_height_m=1.5,
        )

        dryden_runs: List[Dict[str, Any]] = []
        dryden_survived_count = 0
        dryden_hangtimes: List[float] = []

        for w_dir in self.dryden_wind_directions_deg:
            for seed in self.dryden_seeds:
                turb_env = EnvironmentConfig(
                    wind_speed=2.0,
                    wind_direction_deg=w_dir,
                    gust_intensity=0.8,
                    surface_roughness_m=0.01,
                    turbulence_seed=seed,
                )
                try:
                    turb_sim = FlightSimulator(
                        aircraft=aircraft,
                        environment=turb_env,
                        launch=turb_launch,
                    )
                    turb_sim.simulate(duration_s=self.duration_s)
                    run_metrics = turb_sim.metrics()
                    ht = float(run_metrics.get("flight_time_s", 0.0) or 0.0)
                    survived = not bool(
                        run_metrics.get("crashed", False) or run_metrics.get("diverged", False)
                    )
                except Exception:
                    ht = 0.0
                    survived = False

                if survived:
                    dryden_survived_count += 1
                    dryden_hangtimes.append(ht)

                dryden_runs.append(
                    {
                        "wind_direction_deg": w_dir,
                        "turbulence_seed": seed,
                        "survived": survived,
                        "flight_time_s": ht,
                    }
                )

        total_dryden = max(1, len(dryden_runs))
        dryden_survival_rate = dryden_survived_count / total_dryden
        dryden_mean_hangtime = sum(dryden_hangtimes) / total_dryden if dryden_hangtimes else 0.0

        jitter_env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
        jitter_cases: Tuple[Tuple[float, float, float, float], ...] = (
            (2.0, 5.0, -3.0, 0.5),
            (2.0, -5.0, 3.0, 0.5),
            (-2.0, 5.0, 3.0, -0.5),
            (-2.0, -5.0, -3.0, -0.5),
        )
        jitter_runs: List[Dict[str, Any]] = []
        jitter_survived_count = 0

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
                jitter_sim = FlightSimulator(
                    aircraft=aircraft,
                    environment=jitter_env,
                    launch=jitter_launch,
                )
                jitter_sim.simulate(duration_s=self.duration_s)
                jm = jitter_sim.metrics()
                survived = not bool(jm.get("crashed", False) or jm.get("diverged", False))
            except Exception:
                survived = False

            if survived:
                jitter_survived_count += 1
            jitter_runs.append(
                {
                    "pitch_deg": pitch_deg,
                    "roll_deg": roll_deg,
                    "yaw_deg": yaw_deg,
                    "pitch_rate_radps": q_rate,
                    "survived": survived,
                }
            )

        jitter_score = jitter_survived_count / max(1, len(jitter_cases))

        climb_launch = LaunchConfig(
            launch_speed=9.0,
            launch_angle_deg=18.0,
            launch_height_m=1.5,
        )
        climb_metrics: Dict[str, Any] = {}
        try:
            climb_sim = FlightSimulator(
                aircraft=aircraft,
                environment=jitter_env,
                launch=climb_launch,
            )
            climb_sim.simulate(duration_s=self.duration_s)
            climb_metrics = climb_sim.metrics()
            stalled = bool(climb_metrics.get("stalled", False))
            crashed = bool(climb_metrics.get("crashed", False) or climb_metrics.get("diverged", False))
            high_pitch_recovered = (not crashed) and (not stalled)
            recovery_rate = 1.0 if high_pitch_recovered else (0.5 if not crashed else 0.0)
        except Exception as exc:
            recovery_rate = 0.0
            climb_metrics = {"error": f"{type(exc).__name__}: {exc}", "crashed": True}

        overall = float(
            0.4 * dryden_survival_rate + 0.3 * jitter_score + 0.3 * recovery_rate
        )

        return RobustnessReport(
            candidate_id=candidate.candidate_id,
            dryden_survival_rate=dryden_survival_rate,
            dryden_mean_hangtime_s=dryden_mean_hangtime,
            jitter_robustness_score=jitter_score,
            high_pitch_recovery_rate=recovery_rate,
            overall_robustness_score=overall,
            details={
                "keel_height_m": keel_height_m,
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
