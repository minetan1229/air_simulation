"""Deterministic horizontal flight benchmark for paper aeroplanes.

Strictly locks launch conditions to horizontal zero-pitch, zero-angular-rates,
zero-wind at 1.5 m altitude. Evaluates at low (6.0 m/s) and mid (10.0 m/s) speeds.
Uses existing FlightSimulator metrics and flight_phase_metrics to enforce
steady gliding and reject zoom-climb/ground-bounce hacks.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings
from physics.flight_sim import FlightSimulator, flight_phase_metrics
from core.interfaces import (
    AircraftCandidate,
    BENCHMARK_SPEEDS,
    EvaluationMetric,
    FlightBenchmarkResult,
    LAUNCH_HEIGHT_M,
    LAUNCH_PITCH_DEG,
    LAUNCH_PITCH_RATE_DEG_S,
    LAUNCH_ROLL_DEG,
    LAUNCH_ROLL_RATE_DEG_S,
    LAUNCH_YAW_DEG,
    LAUNCH_YAW_RATE_DEG_S,
    SPEED_LOW_MPS,
    SPEED_MID_MPS,
    WIND_SPEED_MPS,
)


def _build_fixed_launch_config(speed_mps: float) -> LaunchConfig:
    """Build immutable horizontal launch configuration."""
    return LaunchConfig(
        launch_speed=float(speed_mps),
        launch_angle_deg=LAUNCH_PITCH_DEG,
        launch_roll_deg=LAUNCH_ROLL_DEG,
        launch_yaw_deg=LAUNCH_YAW_DEG,
        launch_height_m=LAUNCH_HEIGHT_M,
        throw_pitch_rate_radps=0.0,
        throw_wrist_spin_radps=0.0,
        throw_yaw_rate_radps=0.0,
    )


def _build_environment_config() -> EnvironmentConfig:
    """Build quiescent windless environment."""
    return EnvironmentConfig(
        wind_speed=WIND_SPEED_MPS,
        gust_intensity=0.0,
        surface_roughness_m=0.001,
    )


def _build_aircraft_config(candidate: AircraftCandidate) -> AircraftConfig:
    """Convert candidate geometry & config into AircraftConfig for FlightSimulator."""
    geom = candidate.geometry
    cfg = candidate.config

    mass_kg = geom.mass_kg if geom else 0.005
    wing_area = geom.wing_area_m2 if geom else 0.015
    wingspan = geom.wingspan_m if geom else 0.18
    aspect_ratio = geom.aspect_ratio if geom else 2.16
    dihedral_deg = cfg.wing.dihedral_deg
    elevon_deg = 0.5 * (cfg.wing.elevon_left_deg + cfg.wing.elevon_right_deg)

    # Origami-derived properties
    cg_offset_x = 0.0
    static_margin = 0.12
    roll_inertia = 0.0
    pitch_inertia = 0.0
    yaw_inertia = 0.0
    low_re_enabled = True

    if geom:
        # CG offset: extract x from cg_m (NED: x forward, so cg_m[0] is forward position)
        # Positive cg_offset_x means aft CG; nose-heavy = negative offset
        cg_offset_x = -float(geom.cg_m[0]) if len(geom.cg_m) >= 1 else 0.0
        # Static margin from geometry if available, else default
        static_margin = float(getattr(geom, "static_margin", 0.12))
        # Inertia from geometry
        roll_inertia = float(getattr(geom, "roll_inertia_kgm2", 0.0))
        pitch_inertia = float(getattr(geom, "pitch_inertia_kgm2", 0.0))
        yaw_inertia = float(getattr(geom, "yaw_inertia_kgm2", 0.0))

    return AircraftConfig(
        mass_kg=mass_kg,
        wing_area_m2=wing_area,
        wingspan_m=wingspan,
        aspect_ratio=aspect_ratio,
        dihedral_deg=dihedral_deg,
        elevon_deg=elevon_deg,
        low_re_enabled=low_re_enabled,
        cg_offset_x=cg_offset_x,
        static_margin=static_margin,
        roll_inertia_kgm2=roll_inertia,
        pitch_inertia_kgm2=pitch_inertia,
        yaw_inertia_kgm2=yaw_inertia,
    )


def evaluate_horizontal_glide(
    aircraft_config: AircraftConfig,
    speed_mps: float,
    duration_s: float = 8.0,
    dt_max: float = 0.02,
) -> EvaluationMetric:
    """Run a single deterministic horizontal launch simulation and extract metrics."""
    launch = _build_fixed_launch_config(speed_mps)
    env = _build_environment_config()
    settings = SimulationSettings(dt_max=dt_max, duration_s=duration_s, adaptive=True)

    sim = FlightSimulator(
        aircraft=aircraft_config,
        environment=env,
        launch=launch,
        settings=settings,
    )

    result = sim.simulate(duration_s=duration_s)
    sim_metrics = sim.metrics()

    # Extract flight history samples for phase analysis
    samples = []
    try:
        samples = sim.history()
        phase_metrics = flight_phase_metrics(samples, phugoid_window_s=2.5)
        stable_duration = float(phase_metrics.get("stable_glide_duration_s", 0.0) or 0.0)
    except Exception:
        stable_duration = 0.0

    flight_time = float(sim_metrics.get("flight_time_s", 0.0) or 0.0)
    distance_m = float(sim_metrics.get("distance_m", 0.0) or 0.0)
    max_alt = float(sim_metrics.get("max_altitude_m", 0.0) or 0.0)
    max_ld = float(sim_metrics.get("max_ld_ratio", 0.0) or 0.0)
    crashed = bool(sim_metrics.get("crashed", False))
    diverged = bool(sim_metrics.get("diverged", False))
    landed = bool(sim_metrics.get("landed", False))
    status = str(sim_metrics.get("status", "unknown"))

    steady_glide_fraction = (
        stable_duration / max(1.0e-3, flight_time) if flight_time > 0.0 else 0.0
    )
    steady_glide_fraction = max(0.0, min(1.0, steady_glide_fraction))

    # Compute longitudinal static stability metric based on pitch stability and attitude damping
    # Pitch restoring stability is positive when pitch attitude oscillations remain bounded
    if crashed or diverged:
        stability = 0.0
    else:
        # Calculate pitch attitude variance or RMS pitch rate from samples if available
        if samples and len(samples) >= 2:
            q_rates = [
                abs(math.radians(s2.pitch_deg - s1.pitch_deg) / max(1.0e-4, s2.time_s - s1.time_s))
                for s1, s2 in zip(samples[:-1], samples[1:])
            ]
        elif samples:
            q_rates = [abs(float(getattr(s, "q_rate_radps", 0.0))) for s in samples]
        else:
            q_rates = []
        mean_q = sum(q_rates) / max(1, len(q_rates)) if q_rates else 0.0
        # Aerodynamic static margin contribution (nominal positive static margin ~0.10)
        sm = float(getattr(aircraft_config, "static_margin", 0.12))
        sm_factor = max(0.0, min(1.0, sm / 0.15)) if sm > 0.0 else 0.0
        damping_factor = 1.0 / (1.0 + mean_q * 2.0)
        excess_alt = max(0.0, max_alt - LAUNCH_HEIGHT_M * 1.25)
        climb_penalty = 1.0 / (1.0 + 3.0 * excess_alt * excess_alt)
        stability = float(0.35 * sm_factor + 0.35 * damping_factor + 0.30 * climb_penalty)

    return EvaluationMetric(
        speed_mps=float(speed_mps),
        flight_time_s=flight_time,
        max_ld_ratio=max_ld,
        steady_glide_fraction=steady_glide_fraction,
        stability=stability,
        distance_m=distance_m,
        max_altitude_m=max_alt,
        landed=landed,
        crashed=crashed,
        diverged=diverged,
        status=status,
    )


class DeterministicFlightBenchmark:
    """Benchmark runner executing low-speed and mid-speed deterministic launches."""

    def __init__(self, duration_s: float = 8.0, dt_max: float = 0.02) -> None:
        self.duration_s = duration_s
        self.dt_max = dt_max

    def evaluate_candidate(self, candidate: AircraftCandidate) -> FlightBenchmarkResult:
        """Evaluate a candidate at both 6.0 m/s and 10.0 m/s."""
        aircraft_cfg = _build_aircraft_config(candidate)

        low_metric = evaluate_horizontal_glide(
            aircraft_cfg,
            speed_mps=SPEED_LOW_MPS,
            duration_s=self.duration_s,
            dt_max=self.dt_max,
        )

        mid_metric = evaluate_horizontal_glide(
            aircraft_cfg,
            speed_mps=SPEED_MID_MPS,
            duration_s=self.duration_s,
            dt_max=self.dt_max,
        )

        s_low = low_metric.deterministic_score
        s_mid = mid_metric.deterministic_score

        # Composite score using harmonic mean to strictly penalize failure in either speed
        if s_low > 1.0e-4 and s_mid > 1.0e-4:
            composite = 2.0 * (s_low * s_mid) / (s_low + s_mid)
            valid = True
            rejection_reason = ""
        else:
            composite = 0.0
            valid = False
            rejection_reason = (
                f"Failed benchmark: low_score={s_low:.3f}, mid_score={s_mid:.3f}"
            )

        res = FlightBenchmarkResult(
            low_speed=low_metric,
            mid_speed=mid_metric,
            composite_score=composite,
            valid=valid,
            rejection_reason=rejection_reason,
        )
        candidate.benchmark = res
        return res
