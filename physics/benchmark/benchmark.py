"""Deterministic horizontal flight benchmark for paper aeroplanes.

Strictly locks launch conditions to horizontal zero-pitch, zero-angular-rates,
zero-wind at 1.5 m altitude. Evaluates at low (6.0 m/s) and mid (10.0 m/s) speeds.
Uses existing FlightSimulator metrics and flight_phase_metrics to enforce
steady gliding and reject zoom-climb/ground-bounce hacks.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    derived_properties,
)
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

    mass_kg = float(getattr(geom, "mass_kg", 0.005)) if geom else 0.005
    raw_area = getattr(geom, "planform_area_m2", None)
    if raw_area is None and geom is not None:
        raw_area = getattr(geom, "wing_area_m2", 0.015)
    try:
        wing_area = float(raw_area) if raw_area is not None else 0.015
    except (TypeError, ValueError):
        wing_area = 0.015

    try:
        wingspan = float(getattr(geom, "wingspan_m", 0.18)) if geom else 0.18
    except (TypeError, ValueError):
        wingspan = 0.18

    raw_ar = getattr(geom, "planform_aspect_ratio", None)
    if raw_ar is None and geom is not None:
        raw_ar = getattr(geom, "aspect_ratio", 2.16)
    try:
        aspect_ratio = float(raw_ar) if raw_ar is not None else 2.16
    except (TypeError, ValueError):
        aspect_ratio = 2.16

    dihedral_deg = cfg.wing.dihedral_deg
    elevon_deg = 0.5 * (cfg.wing.elevon_left_deg + cfg.wing.elevon_right_deg)

    cg_offset_x = 0.0
    static_margin = 0.12
    if geom:
        try:
            chord = max(0.01, float(getattr(geom, "chord_m", 0.12)))
            nose_x = float(getattr(geom, "nose_tip_x_m", 0.1485))
            cg_m = getattr(geom, "cg_m", (0.08, 0.0, 0.005))
            x_from_nose = nose_x - float(cg_m[0])
            nominal_ac_from_nose = 0.35 * chord
            raw_offset = x_from_nose - nominal_ac_from_nose
            cg_offset_x = max(-0.05, min(0.05, float(raw_offset)))
            static_margin = max(-0.20, min(0.30, float(-cg_offset_x / chord)))
        except (TypeError, ValueError, AttributeError, IndexError):
            pass

    ac = AircraftConfig(
        mass_kg=mass_kg,
        wing_area_m2=wing_area,
        wingspan_m=wingspan,
        aspect_ratio=aspect_ratio,
        dihedral_deg=dihedral_deg,
        elevon_deg=elevon_deg,
        cg_offset_x=cg_offset_x,
        static_margin=static_margin,
        low_re_enabled=True,
    )

    props = derived_properties(ac)
    ac.roll_inertia_kgm2 = props.get("roll_inertia_kgm2", ac.roll_inertia_kgm2)
    ac.pitch_inertia_kgm2 = props.get("pitch_inertia_kgm2", ac.pitch_inertia_kgm2)
    ac.yaw_inertia_kgm2 = props.get("yaw_inertia_kgm2", ac.yaw_inertia_kgm2)
    return ac


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

    sim_metrics: dict = {}
    samples: list = []
    stable_duration = 0.0
    try:
        result = sim.simulate(duration_s=duration_s)
        sim_metrics = sim.metrics() or {}
        samples = sim.history()
        phase_metrics = flight_phase_metrics(samples, phugoid_window_s=2.5)
        stable_duration = float(phase_metrics.get("stable_glide_duration_s", 0.0) or 0.0)
    except Exception:
        pass

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

        # Composite score using harmonic mean to strictly penalize failure in either speed,
        # with soft penalty when one speed succeeds to avoid optimizer cliff
        if s_low > 1.0e-4 and s_mid > 1.0e-4:
            composite = 2.0 * (s_low * s_mid) / (s_low + s_mid)
            valid = True
            rejection_reason = ""
        elif s_low > 1.0e-4 or s_mid > 1.0e-4:
            composite = 0.20 * max(s_low, s_mid)
            valid = False
            rejection_reason = (
                f"Failed benchmark at one speed: low_score={s_low:.3f}, mid_score={s_mid:.3f}"
            )
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
