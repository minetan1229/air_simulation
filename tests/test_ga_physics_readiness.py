"""Audit tests for the P2 / P3 remediation of the physical engine.

``PHYSICS_PAPER_CONFORMANCE_AUDIT.md`` (the audit) lists the physics
break-downs that had to be closed *before* the genetic-algorithm phase starts,
because a GA attacks exactly the holes a simulator leaves open: it will find
the airframe whose "optimal" trajectory only exists because the physics is
wrong.

The audit is split into four priorities.  ``tests/test_paper_conformance_p0_p1.py``
covers P0 (model tags, stress standardisation, the Helmbold and wind-sign
conventions) and P1 (post-stall lift, separated drag, the Polhamus form).  This
module covers the remaining two:

P2 -- the aerodynamics, the collision and the inertia
    * the ground contact is an **impulse** model (CCD interpolation, restitution
      and friction) instead of a position clamp (H07 / H08 / H09, M-02..M-04),
    * the step-acceptance criterion is **relative** to the predicted change
      instead of an absolute threshold (H20),
    * there is only **one** aerodynamics implementation: the local copy was
      removed, so the simulator cannot silently run a different model
      (H14),
    * the adaptive step is derived from the **air-relative** speed and is
      bounded by the stiffness and the attitude limit (M-06 / M-07).

P3 -- the GPU path and the physical consistency of the batch evaluation
    * the batched rigid-body simulation agrees with the reference 6-DoF
      integrator (H01 / H02), instead of the 3-DoF surrogate that produced a
      1261x stress divergence,
    * the reduced paths are explicitly tagged ``reduced_surrogate`` and never
      claim to be physical (P0 model tag),
    * the stress allowable is a single physical standard in every path (H10),
    * the optimiser re-verifies its final candidates in the 6-DoF path before
      publishing them (H05 / P3-20).

The module also pins the **branch guard** of the ``genetic-project`` phase: the
main execution entry points must refuse to run on a protected branch.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics import aero_kernel, flight_sim, gpu_dynamics, parallel_engine, stress_domain  # noqa: E402
from physics.config_schema import (  # noqa: E402
    AircraftConfig,
    as_dict,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    StateVector,
    derived_properties,
)
from physics.optimizer_domain import revalidate_candidate_6dof  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _quiet_environment() -> EnvironmentConfig:
    """Still air: no gust, no thermal, no mean wind."""
    return EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)


def _drop(aircraft: AircraftConfig, height_m: float = 1.0, speed: float = 8.0) -> LaunchConfig:
    return LaunchConfig(launch_speed=speed, launch_angle_deg=0.0, launch_height_m=height_m)


def _station_altitudes(sim: flight_sim.FlightSimulator, state: StateVector) -> dict:
    return flight_sim.compute_ground_stations(
        state.position_m, state.quaternion, sim.environment, sim._derived
    )


# ---------------------------------------------------------------------------
# P2 (1) -- the ground contact is an impulse model
# ---------------------------------------------------------------------------
def test_ground_contact_never_lets_the_airframe_penetrate_the_ground() -> None:
    """No contact station may end up below the ground plane.

    The audit found the contact resolved by *clamping* the position, which
    lets the airframe sink into the floor and then teleport back out.  The
    impulse model resolves the impact on the deepest station and the airframe
    must stay on or above the surface.
    """
    aircraft = AircraftConfig()
    settings = SimulationSettings(dt_max=0.01, dt_min=1.0e-4, adaptive=True, max_substeps=64)
    sim = flight_sim.FlightSimulator(aircraft, _quiet_environment(), _drop(aircraft), settings)
    result = sim.simulate(duration_s=6.0)
    assert result["status"] in ("landed", "crashed")

    lowest = math.inf
    for sample in sim._history:
        stations = flight_sim.compute_ground_stations(
            (sample.x_m, sample.y_m, sample.z_m),
            (sample.qw, sample.qx, sample.qy, sample.qz),
            sim.environment,
            sim._derived,
        )
        lowest = min(lowest, float(stations["min_alt"]))
    assert lowest > -1.0e-6, f"the airframe penetrated the ground by {-lowest} m"


def test_ground_contact_dissipates_energy_and_never_creates_it() -> None:
    """A landing may only remove mechanical energy.

    The impulse model reverses the normal velocity with a restitution factor
    below one and damps the tangential velocity, so the total mechanical
    energy of the airframe can only decrease across the impact.  A contact
    that *gains* energy is the classic symptom of a position clamp.
    """
    aircraft = AircraftConfig()
    settings = SimulationSettings(dt_max=0.01, dt_min=1.0e-4, adaptive=True, max_substeps=64)
    sim = flight_sim.FlightSimulator(aircraft, _quiet_environment(), _drop(aircraft), settings)
    sim.simulate(duration_s=6.0)

    mass = float(sim._mass)
    gravity = 9.80665
    energy = []
    for sample in sim._history:
        speed = math.sqrt(sample.vx_mps ** 2 + sample.vy_mps ** 2 + sample.vz_mps ** 2)
        height = max(sample.altitude_m, 0.0)
        energy.append(0.5 * mass * speed * speed + mass * gravity * height)
    peak = max(energy)
    # The mechanical energy never exceeds the energy the airframe was launched
    # with (the launch condition is the global maximum of the trajectory).
    assert peak <= energy[0] * 1.0001 + 1.0e-9
    # And the final state carries strictly less energy than the launch.
    assert energy[-1] < energy[0]


def test_ground_contact_reverses_and_damps_the_normal_velocity() -> None:
    """The normal velocity is reflected with a restitution below one.

    The impact must not simply delete the vertical velocity (``vz = 0``) and
    must not leave it unchanged: the airframe has to bounce, with a smaller
    normal speed than it arrived with, and the tangential velocity has to be
    damped by the ground friction.
    """
    aircraft = AircraftConfig()
    settings = SimulationSettings(restitution=0.3, dt_max=0.01, dt_min=1.0e-4, adaptive=True)
    sim = flight_sim.FlightSimulator(aircraft, _quiet_environment(), _drop(aircraft), settings)

    # The airframe is five millimetres above the ground plane: the keel
    # station (15 mm deep) is below it, so the body contact is resolved, and
    # the airframe is descending, i.e. +z (down) velocity is positive.
    incoming = StateVector(
        time_s=1.0,
        position_m=(4.0, 0.0, -0.005),
        velocity_mps=(6.0, 0.0, 4.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    resolved = sim._apply_ground_contact(incoming)

    assert resolved.velocity_mps[2] < 0.0, "the normal velocity was not reflected"
    assert abs(resolved.velocity_mps[2]) < 4.0, "the normal velocity was not damped"
    assert abs(resolved.velocity_mps[0]) < 6.0, "the ground friction did not damp the slide"
    # The airframe bounces: it is neither landed nor crashed yet.
    assert not (resolved.landed and resolved.crashed)
    assert sim._bounce_count >= 1


def test_ground_contact_is_not_a_position_clamp() -> None:
    """The contact must not teleport the airframe out of the ground.

    A clamped contact moves the airframe by more than the free-flight
    displacement of the same interval; an impulse contact leaves the position
    on the free-flight path (only the velocity changes).
    """
    aircraft = AircraftConfig()
    settings = SimulationSettings(dt_max=0.005, dt_min=1.0e-4, adaptive=True, max_substeps=64)
    sim = flight_sim.FlightSimulator(aircraft, _quiet_environment(), _drop(aircraft, 0.6), settings)
    sim.simulate(duration_s=6.0)

    history = list(sim._history)
    for previous, current in zip(history, history[1:]):
        dt = current.time_s - previous.time_s
        if dt <= 0.0:
            continue
        speed = math.sqrt(previous.vx_mps ** 2 + previous.vy_mps ** 2 + previous.vz_mps ** 2)
        free_travel = speed * dt
        moved = math.sqrt(
            (current.x_m - previous.x_m) ** 2
            + (current.y_m - previous.y_m) ** 2
            + (current.z_m - previous.z_m) ** 2
        )
        # A clamp would have to move the airframe *upwards* by more than the
        # free-flight path allows.
        assert moved <= free_travel * 1.35 + 1.0e-6, (
            "the contact moved the airframe further than the free-flight path: "
            "the position is being clamped instead of impulsed"
        )


# ---------------------------------------------------------------------------
# P2 (2) -- the step acceptance criterion is relative
# ---------------------------------------------------------------------------
def _state_at_altitude(altitude_m: float, vz: float = -3.0) -> StateVector:
    return StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, -altitude_m),
        velocity_mps=(6.0, 0.0, vz),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )


def test_step_acceptance_scales_with_the_predicted_change() -> None:
    """A large change that the derivative predicted must be accepted.

    The absolute threshold of the old implementation (8 rad/s) rejected every
    step of a fast-tumbling airframe, so the integrator silently ran on
    halved steps and the result stopped depending on ``dt_max``.  The relative
    criterion accepts a change the local derivative predicted.
    """
    aircraft = AircraftConfig()
    settings = SimulationSettings(dt_max=0.01, dt_min=1.0e-5, adaptive=True, max_substeps=64)
    sim = flight_sim.FlightSimulator(aircraft, _quiet_environment(), _drop(aircraft), settings)
    previous = _state_at_altitude(50.0)

    # The derivative predicts a 40 rad/s change over the step: the candidate
    # must be accepted even though the jump is far above the old 8 rad/s
    # absolute threshold.
    sim._predicted_rate_jump = 40.0
    sim._predicted_speed_jump = 1.0
    candidate = StateVector(
        time_s=0.01,
        position_m=previous.position_m,
        velocity_mps=previous.velocity_mps,
        quaternion=previous.quaternion,
        angular_velocity_radps=(40.0, 0.0, 0.0),
        acceleration_mps2=previous.acceleration_mps2,
        angular_accel_radps2=previous.angular_accel_radps2,
        landed=False,
        crashed=False,
    )
    assert flight_sim.FlightSimulator._step_is_acceptable(sim, previous, candidate) is True


def test_step_acceptance_rejects_a_change_the_derivative_did_not_predict() -> None:
    """An unexplained jump must still be rejected."""
    aircraft = AircraftConfig()
    settings = SimulationSettings(dt_max=0.01, dt_min=1.0e-5, adaptive=True, max_substeps=64)
    sim = flight_sim.FlightSimulator(aircraft, _quiet_environment(), _drop(aircraft), settings)
    previous = _state_at_altitude(50.0)

    sim._predicted_rate_jump = 0.1
    sim._predicted_speed_jump = 0.1
    candidate = StateVector(
        time_s=0.01,
        position_m=previous.position_m,
        velocity_mps=previous.velocity_mps,
        quaternion=previous.quaternion,
        angular_velocity_radps=(500.0, 0.0, 0.0),
        acceleration_mps2=previous.acceleration_mps2,
        angular_accel_radps2=previous.angular_accel_radps2,
        landed=False,
        crashed=False,
    )
    assert flight_sim.FlightSimulator._step_is_acceptable(sim, previous, candidate) is False


# ---------------------------------------------------------------------------
# P2 (3) -- there is only one aerodynamics implementation
# ---------------------------------------------------------------------------
def test_the_local_aerodynamics_fallback_has_been_removed() -> None:
    """The second, divergent aerodynamics implementation must be gone."""
    assert not hasattr(flight_sim, "_local_aero"), (
        "physics.flight_sim still carries a local copy of the aerodynamics: the "
        "simulator can silently run a lift curve and a moment set that differ "
        "from physics.aero_domain"
    )
    source = (ROOT / "physics" / "flight_sim.py").read_text(encoding="utf-8")
    assert "def _local_aero(" not in source


def test_aero_forces_fails_loud_without_the_upstream_solver(monkeypatch) -> None:
    """Without ``aero_domain`` the 6-DoF path must raise, not fall back."""
    aircraft = AircraftConfig()
    environment = _quiet_environment()
    settings = SimulationSettings()
    sim = flight_sim.FlightSimulator(aircraft, environment, _drop(aircraft), settings)
    monkeypatch.setattr(flight_sim, "aero_module", lambda: None)
    with pytest.raises(RuntimeError, match="aero_domain"):
        flight_sim.aero_forces(
            aircraft, environment, 12.0, 4.0, 1.0, 0.035, 1.0e5, 1.2, (0.1, 0.2, 0.3)
        )


def test_the_reported_aero_backend_is_never_a_silent_fallback() -> None:
    """``active_backends`` must not advertise a numpy fallback any more."""
    backends = flight_sim.active_backends()
    assert backends["aero"] in ("upstream", "unavailable")
    assert backends["aero"] != "numpy"


# ---------------------------------------------------------------------------
# P2 (4) -- the adaptive step is derived from the air-relative speed
# ---------------------------------------------------------------------------
def test_the_adaptive_step_uses_the_air_relative_speed() -> None:
    """A head wind tightens the translational step limit.

    The geometric limit is ``TRAVEL_FRACTION_PER_STEP * chord / |v_rel|``.
    With the *ground* speed the limit would relax exactly when the
    aerodynamic stiffness is largest (a head wind), which is the bug the
    audit reported.
    """
    aircraft = AircraftConfig()
    settings = SimulationSettings(dt_max=0.02, dt_min=1.0e-5, adaptive=True, max_substeps=64)
    calm = _quiet_environment()
    head_wind = EnvironmentConfig(
        wind_speed=10.0,
        wind_direction_deg=0.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
    )

    still = flight_sim.FlightSimulator(aircraft, calm, _drop(aircraft, 5.0, 10.0), settings)
    windy = flight_sim.FlightSimulator(aircraft, head_wind, _drop(aircraft, 5.0, 10.0), settings)

    step_calm = still.derived_timestep()
    step_windy = windy.derived_timestep()
    assert math.isfinite(step_calm) and math.isfinite(step_windy)
    assert step_windy < step_calm, (
        "the head wind did not tighten the step: the limit is still built on the "
        "ground speed instead of the air-relative speed"
    )


def test_the_adaptive_step_stays_inside_the_stability_limits() -> None:
    """The step is bounded by ``dt_min`` / ``dt_max`` and by the attitude limit."""
    aircraft = AircraftConfig()
    settings = SimulationSettings(dt_max=0.02, dt_min=1.0e-4, adaptive=True, max_substeps=64)
    sim = flight_sim.FlightSimulator(aircraft, _quiet_environment(), _drop(aircraft), settings)
    step = sim.derived_timestep()
    assert settings.dt_min - 1.0e-12 <= step <= settings.dt_max + 1.0e-12


# ---------------------------------------------------------------------------
# P3 (1) -- the batched rigid-body simulation agrees with the reference
# ---------------------------------------------------------------------------
def test_the_batched_rigid_body_simulation_matches_the_reference_6dof() -> None:
    """The GPU 13-state integrator and the reference 6-DoF path must agree.

    The audit measured a 1261x stress divergence and a completely different
    state between the reduced GPU path and the reference integrator, because
    the GPU path integrated a 3-DoF point mass with a closed-form glide
    surrogate.  The batched rigid-body model must reproduce the reference
    trajectory.
    """
    aircraft = AircraftConfig()
    environment = _quiet_environment()
    launch = _drop(aircraft, 5.0, 10.0)
    duration = 0.4

    settings = SimulationSettings(dt_max=0.01, dt_min=1.0e-4, adaptive=True, max_substeps=64)
    reference = flight_sim.FlightSimulator(aircraft, environment, launch, settings)
    reference_result = reference.simulate(duration_s=duration)

    params = gpu_dynamics.build_params(np, [aircraft], environment)
    initial = gpu_dynamics.launch_state_vector(launch, environment, aircraft)
    batch = gpu_dynamics.simulate_batch(
        np, params, initial.reshape(1, -1), duration_s=duration, dt_max=0.01
    )

    assert float(batch["peak_dynamic_pressure_pa"][0]) == pytest.approx(
        float(reference_result["max_speed_mps"]) ** 2 * 0.5 * 1.225, rel=0.08
    )
    assert float(batch["peak_load_factor"][0]) == pytest.approx(
        float(reference_result["max_g_load"]), rel=0.35
    )
    assert float(batch["peak_stress_index"][0]) == pytest.approx(
        float(reference_result["max_stress_index"]), rel=0.35
    )


def test_the_batched_simulation_reports_the_shared_stress_allowable() -> None:
    """The batch path must normalise with the *shared* allowable.

    The audit found three different allowables (2200 Pa, 25 kPa, 120 kPa) in
    the code base, a spread of 54.5x.  The batch path has to use the one
    standard of :func:`physics.stress_domain.paper_allowable_stress`.
    """
    aircraft = AircraftConfig()
    environment = _quiet_environment()
    launch = _drop(aircraft, 5.0, 10.0)
    params = gpu_dynamics.build_params(np, [aircraft], environment)
    initial = gpu_dynamics.launch_state_vector(launch, environment, aircraft)
    batch = gpu_dynamics.simulate_batch(
        np, params, initial.reshape(1, -1), duration_s=0.2, dt_max=0.01
    )
    expected = float(
        stress_domain.paper_allowable_stress(
            paper_gsm=float(aircraft.paper_gsm),
            paper_stiffness=float(aircraft.paper_stiffness),
        )
    )
    assert float(batch["allowable_stress_pa"][0]) == pytest.approx(expected, rel=1.0e-9)


def test_the_analytical_surrogate_is_tagged_as_a_reduced_model() -> None:
    """A reduced path must never claim to be a physical evaluation."""
    row = parallel_engine.evaluate_flight_surrogate(
        aircraft={}, environment={}, launch={}, duration_s=0.1
    )
    assert row["model"] == parallel_engine.REDUCED_MODEL_TAG
    assert row["approximate"] is True
    assert row["physics_verification_eligible"] is False


def test_the_gpu_batch_path_is_tagged_as_the_rigid_body_model() -> None:
    """The batch path must no longer masquerade as a surrogate.

    The audit found the GPU path evaluating a 3-DoF point mass with a
    closed-form glide surrogate while reporting itself like the reference
    integrator.  The batched rigid-body model replaced it, so the tag has to
    follow.

    The batch path has two implementations: the vectorised CUDA integrator
    (``rigid_body_6dof_batch``) and the CPU fallback, which runs the
    reference integrator itself (``6dof_rk4``).  Both are 6-DoF models, so
    the family tag is the same and only the detail differs - a runner
    without a GPU (the CI matrix) must not fail this test.
    """
    rows = parallel_engine.gpu_batch_evaluate_cases(
        [{"aircraft": {}, "environment": {}, "launch": {}, "duration_s": 0.1}] * 2
    )
    assert len(rows) == 2
    expected_detail = (
        "rigid_body_6dof_batch"
        if rows[0].get("backend") == "cupy"
        else "6dof_rk4"
    )
    for row in rows:
        assert row["model"] == parallel_engine.RIGID_BODY_MODEL_TAG
        assert row["model_detail"] == expected_detail


def test_the_gpu_batch_path_agrees_with_the_reference_6dof() -> None:
    """The batched evaluation must reproduce the reference 6-DoF result.

    This is the P3 requirement in its strongest form: the batch path and the
    reference integrator describe the *same* flight, so the stress index, the
    load factor and the distance have to agree.  The audit measured a 1261x
    stress divergence and a completely different state between the two.
    """
    aircraft = AircraftConfig()
    environment = _quiet_environment()
    launch = _drop(aircraft, 5.0, 10.0)
    case = {
        "aircraft": as_dict(aircraft),
        "environment": as_dict(environment),
        "launch": as_dict(launch),
        "duration_s": 0.4,
        "dt_max": 0.02,
    }
    reference = parallel_engine.evaluate_case(case)
    batch = parallel_engine.gpu_batch_evaluate_cases([case])[0]

    assert float(batch["max_stress_index"]) == pytest.approx(
        float(reference["max_stress_index"]), rel=0.20, abs=1.0e-3
    )
    assert float(batch["max_stress_pa"]) == pytest.approx(
        float(reference["max_stress_pa"]), rel=0.20, abs=1.0
    )
    assert float(batch["distance_m"]) == pytest.approx(
        float(reference["distance_m"]), rel=0.20, abs=0.05
    )


def test_the_reference_evaluation_is_tagged_as_the_rigid_body_model() -> None:
    """The 6-DoF path must claim the rigid-body model, not the surrogate."""
    row = parallel_engine.evaluate_case(
        {"aircraft": {}, "environment": {}, "launch": {}, "duration_s": 0.2}
    )
    assert row["model"] == parallel_engine.RIGID_BODY_MODEL_TAG
    assert row["approximate"] is False


# ---------------------------------------------------------------------------
# P3 (2) -- the optimiser re-verifies its final candidates
# ---------------------------------------------------------------------------
def test_the_optimizer_can_revalidate_a_candidate_in_the_6dof_path() -> None:
    """The re-verification must report the rigid-body model and a real flight."""
    from physics.config_schema import OptimizerConfig

    config = OptimizerConfig()
    variables = config.resolved_variables()
    vector = [
        float(getattr(config.aircraft, variable.name))
        if hasattr(config.aircraft, variable.name)
        else float(getattr(config.launch, variable.name))
        for variable in variables
    ]
    check = revalidate_candidate_6dof(vector, config, ["max_distance", "max_ld_ratio"])
    assert check["model"] == parallel_engine.RIGID_BODY_MODEL_TAG
    assert check["verified"] is True
    assert check["status"] in ("landed", "crashed", "stalled", "flying", "time-limit", "ok")
    assert math.isfinite(float(check["distance_m"]))
    assert set(check["objectives"]) == {"max_distance", "max_ld_ratio"}


def test_the_optimizer_payload_exposes_the_verification_fields() -> None:
    """The published payload must carry the verification bookkeeping."""
    from physics.optimizer_domain import RESULT_KEYS

    assert "verified_candidates" in RESULT_KEYS
    assert "best_verified" in RESULT_KEYS


# ---------------------------------------------------------------------------
# The branch guard of the genetic-project phase
# ---------------------------------------------------------------------------
def test_the_entry_points_refuse_to_run_on_a_protected_branch(monkeypatch, capsys) -> None:
    """``app`` and ``smoke_test`` must abort when the branch is protected.

    The two entry points have **different** contracts, and both of them are
    pinned here:

    * ``app.main()`` **raises** ``SystemExit(2)`` -- the server must not
      start on a protected branch.
    * ``smoke_test.main()`` **returns** 2 -- it reports the violation on
      stderr and its ``__main__`` block turns that return value into the
      process exit code.  It does not raise.

    The guard is patched at the level each entry point reads it from.
    ``app.py`` resolved ``assert_branch_allowed`` at import time, so the
    name is a module attribute of ``app``.  ``smoke_test.py`` imports it
    *inside* ``main()``; a function local ``from X import Y`` creates a
    local name that shadows any module attribute, so the smoke test never
    reads a module attribute of ``smoke_test`` -- it reads the attribute
    of the **source** module at call time.  Patching
    ``smoke_test.assert_branch_allowed`` therefore installs an attribute
    the smoke test never looks at.  Both patches are active at the same
    time, because they resolve two different bindings.
    """
    import app as app_module
    import smoke_test

    from tools import branch_guard
    from tools.branch_guard import BranchViolation

    # The policy is read from the module, not hard coded: the phase branch
    # and the protected branches are the ones the guard really uses.
    assert branch_guard.EXPECTED_BRANCH == "genetic-project"
    assert branch_guard.PROTECTED_BRANCHES, "the guard must name the protected branches"
    protected = branch_guard.PROTECTED_BRANCHES[0]

    def refuse(*_args, **_kwargs):
        raise BranchViolation(f"branch '{protected}' is protected")

    # ``app`` resolved the guard at import time ...
    monkeypatch.setattr(app_module, "assert_branch_allowed", refuse)
    # ... the smoke test resolves it from the source module at call time.
    monkeypatch.setattr("tools.branch_guard.assert_branch_allowed", refuse)

    # ------------------------------------------------------------------
    # app.main(): the server refuses to start
    # ------------------------------------------------------------------
    def no_server(*_args, **_kwargs):
        raise AssertionError("the server must not start on a protected branch")

    def no_domain(*_args, **_kwargs):
        raise AssertionError("the GPU warmup must not start on a protected branch")

    monkeypatch.setattr(app_module.app, "run", no_server)
    monkeypatch.setattr(app_module, "domain", no_domain)
    # The entry points parse the command line: give them a clean one, so
    # the guard is what is under test and not the argument parser.
    monkeypatch.setattr(sys, "argv", ["app"])

    with pytest.raises(SystemExit) as app_exit:
        app_module.main()
    assert app_exit.value.code == 2, "the app must exit with the code 2"

    # ------------------------------------------------------------------
    # smoke_test.main(): the stages are skipped and the code is returned
    # ------------------------------------------------------------------
    def no_stage(*_args, **_kwargs):
        raise AssertionError("no smoke test stage may run on a protected branch")

    monkeypatch.setattr(
        smoke_test,
        "STAGES",
        [(name, no_stage) for name, _fn in smoke_test.STAGES],
    )
    monkeypatch.setattr(sys, "argv", ["smoke_test"])

    capsys.readouterr()  # drop whatever the imports printed
    exit_code = smoke_test.main()
    captured = capsys.readouterr()

    assert exit_code == 2, "the smoke test must report the violation with the code 2"
    assert "branch guard" in captured.err, "the smoke test must report the violation"
    assert protected in captured.err, "the violation must name the protected branch"
    assert captured.out == "", "the smoke test must not print a single stage line"


def test_the_branch_guard_refuses_the_protected_branches(monkeypatch) -> None:
    """The policy of the guard itself: the protected branches are refused.

    The test pins the policy of :mod:`tools.branch_guard` instead of a hard
    coded branch name: the expected branch and the protected list are read
    from the module, every protected branch must raise a
    :class:`BranchViolation`, and the branch the phase is carried out on
    must not.
    """
    from tools import branch_guard
    from tools.branch_guard import BranchViolation

    assert branch_guard.EXPECTED_BRANCH == "genetic-project"
    assert branch_guard.PROTECTED_BRANCHES == ("master", "main")

    # The strict policy, whatever the environment of the runner says (a CI
    # runner downgrades the severity to a warning).
    monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, "error")

    for protected in branch_guard.PROTECTED_BRANCHES:
        monkeypatch.setattr(
            branch_guard,
            "current_branch",
            lambda repo_root=None, _name=protected: _name,
        )
        status = branch_guard.guard_status()
        assert status["protected"] is True
        assert status["severity"] == "error"
        assert status["expected_branch"] == branch_guard.EXPECTED_BRANCH
        with pytest.raises(BranchViolation):
            branch_guard.assert_branch_allowed()

    # The branch of the phase itself is not a violation.
    monkeypatch.setattr(
        branch_guard,
        "current_branch",
        lambda repo_root=None: branch_guard.EXPECTED_BRANCH,
    )
    status = branch_guard.guard_status()
    assert status["protected"] is False
    assert status["severity"] == "none"
    assert status["ok"] is True
    branch_guard.assert_branch_allowed()  # must not raise


def test_the_kernel_and_the_domain_agree_on_the_coefficients() -> None:
    """The shared kernel is the single source of the aerodynamic truth.

    The scalar 6-DoF path and the vectorised path must produce the same
    coefficients, otherwise the GPU evaluation and the reference evaluation
    describe two different airframes.
    """
    aircraft = AircraftConfig()
    environment = _quiet_environment()
    params = aero_kernel.params_from_config(aircraft, environment)
    for alpha in (-45.0, -14.0, 0.0, 8.0, 20.0, 45.0):
        for beta in (-30.0, 0.0, 25.0):
            vector = aero_kernel.evaluate_coefficients(
                np,
                params,
                alpha_deg=alpha,
                beta_deg=beta,
                speed=12.0,
                reynolds=1.0e5,
                mach=0.035,
                ground_height_m=1.0,
                density=1.225,
            )
            scalar = flight_sim.aero_forces(
                aircraft, environment, 12.0, alpha, beta, 0.035, 1.0e5, 1.0, (0.0, 0.0, 0.0)
            )
            assert float(vector["cl"]) == pytest.approx(scalar.cl, rel=1.0e-12, abs=1.0e-14)
            assert float(vector["cd"]) == pytest.approx(scalar.cd, rel=1.0e-12, abs=1.0e-14)
