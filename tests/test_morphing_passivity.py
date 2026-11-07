"""Issue #132: morphing on/off energy comparison guard.

The morph is an algebraic quasi-steady geometric change delta_eff(q) with no
elastic state; passivity is therefore documented as a limitation, not proven.
Guard ensures identical initial conditions with morph on/off both keep the
mechanical energy non-increasing in calm glide (no injection from the sweep).
"""
from __future__ import annotations

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings
from physics.flight_sim import FlightSimulator


def _run(compliance: float):
    aircraft = AircraftConfig(
        mass_kg=0.0035,
        wing_area_m2=0.018,
        wingspan_m=0.22,
        fidelity_mode="standard",
        elevon_deg=6.0,
        fold_trailing_deg=0.0,
        aeroelastic_compliance=compliance,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, ground_level_m=-50.0)
    launch = LaunchConfig(launch_speed=6.5, launch_angle_deg=2.0, launch_height_m=20.0)
    settings = SimulationSettings(dt_min=0.001, dt_max=0.01, record_interval_s=0.02, duration_s=1.0, collision_enabled=False)
    sim = FlightSimulator(aircraft, env, launch, settings)
    return sim.simulate(duration_s=1.0)


def test_morphing_on_off_both_non_increasing() -> None:
    off = _run(0.0)
    on = _run(1.0)
    assert off["energy_strictly_decreasing"]
    assert on["energy_strictly_decreasing"]
