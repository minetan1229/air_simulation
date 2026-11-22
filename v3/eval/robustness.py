"""Stage 4: Real-world robustness evaluation and asset export.

Tests top elite aircraft under Dryden turbulence, throw jitter, and high-pitch
launches, and generates standalone .obj 3D mesh and JSON summary files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import FlightSimulator
from v3.interfaces import AircraftCandidate, RobustnessReport


class RobustnessEvaluator:
    """Evaluates final selected aircraft candidates under realistic perturbations."""

    def __init__(self, duration_s: float = 6.0) -> None:
        self.duration_s = duration_s

    def evaluate_candidate(self, candidate: AircraftCandidate) -> RobustnessReport:
        """Run Dryden turbulence, jitter, and high-pitch throw stress tests."""
        geom = candidate.geometry
        cfg = candidate.config

        mass_kg = geom.mass_kg if geom else 0.005
        wing_area = geom.wing_area_m2 if geom else 0.015
        wingspan = geom.wingspan_m if geom else 0.18
        aspect_ratio = geom.aspect_ratio if geom else 2.16
        dihedral_deg = cfg.wing.dihedral_deg
        elevon_deg = 0.5 * (cfg.wing.elevon_left_deg + cfg.wing.elevon_right_deg)

        # Derive CG offset and static margin from folded geometry if available
        cg_offset_x = 0.0
        static_margin = 0.12
        if geom:
            chord = max(0.01, float(geom.chord_m))
            x_from_nose = 0.1485 - float(geom.cg_m[0])
            nominal_ac_from_nose = 0.35 * chord
            raw_offset = x_from_nose - nominal_ac_from_nose
            cg_offset_x = max(-0.05, min(0.05, float(raw_offset)))
            static_margin = max(-0.20, min(0.30, float(-cg_offset_x / chord)))

        aircraft = AircraftConfig(
            mass_kg=mass_kg,
            wing_area_m2=wing_area,
            wingspan_m=wingspan,
            aspect_ratio=aspect_ratio,
            dihedral_deg=dihedral_deg,
            elevon_deg=elevon_deg,
            cg_offset_x=cg_offset_x,
            static_margin=static_margin,
        )

        # 1. Dryden Turbulence Test
        turb_env = EnvironmentConfig(
            wind_speed=2.0,
            gust_intensity=0.8,
            surface_roughness_m=0.01,
        )
        turb_launch = LaunchConfig(
            launch_speed=8.0,
            launch_angle_deg=0.0,
            launch_height_m=1.5,
        )
        turb_sim = FlightSimulator(aircraft=aircraft, environment=turb_env, launch=turb_launch)
        turb_sim.simulate(duration_s=self.duration_s)
        turb_metrics = turb_sim.metrics()
        dryden_hangtime = float(turb_metrics.get("flight_time_s", 0.0) or 0.0)
        dryden_survived = not bool(turb_metrics.get("crashed", False) or turb_metrics.get("diverged", False))

        # 2. Throw Jitter Test (+/- 5 deg roll & yaw perturbation)
        jitter_launch = LaunchConfig(
            launch_speed=8.0,
            launch_angle_deg=2.0,
            launch_roll_deg=5.0,
            launch_yaw_deg=-3.0,
            throw_pitch_rate_radps=0.5,
            launch_height_m=1.5,
        )
        jitter_env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
        jitter_sim = FlightSimulator(aircraft=aircraft, environment=jitter_env, launch=jitter_launch)
        jitter_sim.simulate(duration_s=self.duration_s)
        jitter_metrics = jitter_sim.metrics()
        jitter_survived = not bool(jitter_metrics.get("crashed", False) or jitter_metrics.get("diverged", False))
        jitter_score = 1.0 if jitter_survived else 0.2

        # 3. High-Pitch Launch Test (18 deg climb test)
        climb_launch = LaunchConfig(
            launch_speed=9.0,
            launch_angle_deg=18.0,
            launch_height_m=1.5,
        )
        climb_sim = FlightSimulator(aircraft=aircraft, environment=jitter_env, launch=climb_launch)
        climb_sim.simulate(duration_s=self.duration_s)
        climb_metrics = climb_sim.metrics()
        stalled = bool(climb_metrics.get("stalled", False))
        high_pitch_recovered = (not bool(climb_metrics.get("crashed", False))) and (not stalled)
        recovery_rate = 1.0 if high_pitch_recovered else (0.5 if not bool(climb_metrics.get("crashed", False)) else 0.0)

        overall = float(
            0.4 * (1.0 if dryden_survived else 0.2)
            + 0.3 * jitter_score
            + 0.3 * recovery_rate
        )

        return RobustnessReport(
            candidate_id=candidate.candidate_id,
            dryden_survival_rate=1.0 if dryden_survived else 0.0,
            dryden_mean_hangtime_s=dryden_hangtime,
            jitter_robustness_score=jitter_score,
            high_pitch_recovery_rate=recovery_rate,
            overall_robustness_score=overall,
            details={
                "dryden_metrics": turb_metrics,
                "jitter_metrics": jitter_metrics,
                "climb_metrics": climb_metrics,
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
