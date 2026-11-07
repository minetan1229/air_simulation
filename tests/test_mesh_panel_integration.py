"""Runtime boundary between mesh analysis and the flight aerodynamic model."""

from __future__ import annotations

from functools import wraps

from physics import aero_domain, flight_sim, mesh_domain
from physics.config_schema import AircraftConfig, EnvironmentConfig
from physics.flight_sim import FlightSimulator


def test_flight_dynamics_uses_coefficient_aero_without_calling_mesh_panels(monkeypatch) -> None:
    """Flight force evaluation calls aero_domain, while mesh panels stay offline."""
    aero_calls = 0
    evaluate_aero = aero_domain.evaluate_aero

    @wraps(evaluate_aero)
    def tracked_aero(*args, **kwargs):
        nonlocal aero_calls
        aero_calls += 1
        return evaluate_aero(*args, **kwargs)

    def unexpected_mesh_aero(*args, **kwargs):
        raise AssertionError("flight dynamics called the standalone mesh panel model")

    monkeypatch.setattr(aero_domain, "evaluate_aero", tracked_aero)
    monkeypatch.setattr(mesh_domain, "panel_aerodynamics", unexpected_mesh_aero)

    simulator = FlightSimulator(AircraftConfig(), EnvironmentConfig())
    simulator._compute_dynamics(flight_sim._pack(simulator.state), simulator.state.time_s)

    assert aero_calls > 0
