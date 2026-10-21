"""Regression tests for the shared physics kernel.

These tests pin the properties the audit found broken:

* the vectorised GPU path and the 6-DoF reference path must evaluate the
  **same** airframe (the audit measured a 1261x disagreement in the peak
  wing-root stress);
* the aerodynamic coefficients must have a single implementation, so the GPU
  path cannot drift into a look-alike model;
* the allowable stress must have a single definition;
* the post-stall branch must neither collapse onto an exact ``C_L == 0``
  band nor produce a drag five times too small.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from physics import aero_domain, aero_kernel, flight_sim, gpu_dynamics, stress_domain
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig


# --------------------------------------------------------------------------
# 1. The kernel and the scalar domain are the same model
# --------------------------------------------------------------------------
ALPHAS = (-180.0, -90.0, -60.0, -45.0, -30.0, -20.0, -14.0, -5.0, 0.0, 5.0,
          14.0, 20.0, 30.0, 41.0, 45.0, 60.0, 90.0, 135.0, 180.0)
BETAS = (-90.0, -20.0, 0.0, 20.0, 90.0)
SPEEDS = (0.0, 0.5, 2.9, 3.1, 8.0, 20.0)


def _configurations():
    base = AircraftConfig()
    env = EnvironmentConfig()
    return [
        (base, env),
        (AircraftConfig(elevon_deg=6.0, cm0=0.02, cm_alpha=-0.8), env),
        (AircraftConfig(aileron_deg=4.0, rudder_deg=-3.0, dihedral_deg=6.0), env),
        (AircraftConfig(polhamus_kv=0.4), env),
        (AircraftConfig(stall_alpha_deg=9.0, cl_max=0.7, taper_ratio=0.2), env),
        (AircraftConfig(step_trip_ratio=0.05), env),
        (base, EnvironmentConfig(lift_scale=0.8, drag_scale=1.2)),
        (base, EnvironmentConfig(ground_effect_enabled=False, compressibility_enabled=False)),
    ]


def test_kernel_matches_scalar_domain_bit_for_bit():
    """The vectorised kernel reproduces the scalar domain exactly."""
    worst = 0.0
    for cfg, env in _configurations():
        params = aero_kernel.params_from_config(cfg, env)
        chord = float(stress_domain.derived_properties(cfg)["ref_length_m"])
        for alpha in ALPHAS:
            for beta in BETAS:
                for speed in SPEEDS:
                    air = aero_domain.atmosphere_state(env, speed, reference_chord_m=chord)
                    reynolds = speed * chord / float(air["kinematic_viscosity_m2s"])
                    mach = speed / float(air["sound_speed_mps"])
                    scalar = aero_domain.evaluate_aero(
                        cfg, env, speed, alpha, beta, mach, reynolds, 1.0, (0.3, -0.2, 0.15)
                    )
                    vector = aero_kernel.evaluate_coefficients(
                        np,
                        params,
                        alpha_deg=alpha,
                        beta_deg=beta,
                        p_rate=0.3,
                        q_rate=-0.2,
                        r_rate=0.15,
                        speed=speed,
                        reynolds=reynolds,
                        mach=mach,
                        ground_height_m=1.0,
                        density=float(air["density_kgm3"]),
                    )
                    for key, expected in (
                        ("cl", scalar.cl),
                        ("cd", scalar.cd),
                        ("cy", scalar.cy),
                        ("cm", scalar.cm),
                        ("lift_n", scalar.lift_n),
                        ("drag_n", scalar.drag_n),
                        ("side_n", scalar.side_n),
                        ("pitching_nm", scalar.pitching_moment_nm),
                        ("rolling_nm", scalar.rolling_moment_nm),
                        ("yawing_nm", scalar.yawing_moment_nm),
                    ):
                        got = float(np.asarray(vector[key]))
                        scale = max(abs(float(expected)), 1.0e-9)
                        worst = max(worst, abs(got - float(expected)) / scale)
    assert worst < 1.0e-12, f"kernel and domain disagree by {worst:.3e}"


def test_helmbold_is_single_implementation():
    """The lift slope has one definition, shared with the GPU path."""
    aspect = 2.667
    taper = 0.7
    expected = 2.0 * math.pi * aspect / (
        2.0 + math.sqrt(aspect * aspect * (1.0 + taper) + 4.0 * aspect / math.pi)
    )
    assert aero_domain.helmbold_cl_alpha(aspect, taper) == pytest.approx(expected, rel=1e-15)
    assert aero_kernel.helmbold_cl_alpha(aspect, taper) == pytest.approx(expected, rel=1e-15)


# --------------------------------------------------------------------------
# 2. The structural model is shared
# --------------------------------------------------------------------------
def test_vectorised_peak_stress_reproduces_the_stress_field():
    """The scalar reduction of the stress field is exact for the 6-DoF grid."""
    env = EnvironmentConfig()
    for cfg, load in (
        (AircraftConfig(), 1.0),
        (AircraftConfig(), 6.0),
        (AircraftConfig(fold_tip_deg=25.0), 3.0),
        (AircraftConfig(fold_trailing_deg=30.0), 3.0),
        (AircraftConfig(paper_gsm=160.0, taper_ratio=0.3), 2.0),
    ):
        derived = stress_domain.derived_properties(cfg)
        field = stress_domain.stress_field(
            cfg=cfg,
            dynamic_pressure_pa=30.0,
            load_factor=load,
            spanwise=3,
            chordwise=2,
            backend="numpy",
            gravity=env.gravity,
        )
        thickness = stress_domain._sheet_thickness(cfg.paper_gsm)
        z_gain = stress_domain.Z_SCALE_OFFSET + stress_domain.Z_SCALE_GAIN * stress_domain.effective_paper_stiffness(cfg)
        peak = float(
            stress_domain.peak_root_stress_pa(
                np,
                load_factor=load,
                mass_kg=derived["mass_kg"],
                gravity=env.gravity,
                span_m=derived["ref_span_m"],
                taper_ratio=derived["taper_ratio"],
                root_chord_m=stress_domain.root_chord_batch(
                    np, derived["ref_area_m2"], derived["ref_span_m"], derived["taper_ratio"]
                ),
                thickness_m=thickness,
                z_gain=z_gain,
                chord_scale=stress_domain.te_fold_chord_scale(np, cfg.fold_trailing_deg),
                tip_factor=abs(math.sin(math.radians(cfg.fold_tip_deg))),
            )
        )
        assert peak == pytest.approx(float(field["max_stress_pa"]), rel=1e-12)


def test_allowable_stress_has_a_single_definition():
    """No path may normalise the stress against its own constant."""
    import pathlib

    from physics import parallel_engine

    source = pathlib.Path(parallel_engine.__file__).read_text(encoding="utf-8")
    assert "_STRESS_LIMIT_PA" not in source
    # The heat map and the flight payload share the same allowable: the
    # default airframe allows 2200 Pa, and the index of the allowable stress
    # is exactly 1 against it.
    cfg = AircraftConfig()
    expected = stress_domain.paper_allowable_stress(
        cfg.paper_gsm, stress_domain.effective_paper_stiffness(cfg)
    )
    assert expected == pytest.approx(2200.0, rel=1e-6)
    assert stress_domain.failure_index(expected, expected) == pytest.approx(1.0, rel=1e-9)
    assert stress_domain.failure_index(expected / 2.0, expected) == pytest.approx(0.5, rel=1e-9)


# --------------------------------------------------------------------------
# 3. The GPU path and the 6-DoF path agree
# --------------------------------------------------------------------------
def _reference_flight(aircraft, env, launch, duration=6.0):
    settings = flight_sim.SimulationSettings(
        dt_max=2.0e-2,
        dt_min=1.0e-5,
        max_substeps=64,
        duration_s=duration,
        record_interval_s=2.0e-2,
        time_scale=1.0,
        adaptive=True,
        collision_enabled=True,
        restitution=0.15,
        ring_buffer_capacity=4096,
    )
    simulator = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    simulator.simulate(duration_s=duration, record=True)
    samples = simulator.history()
    return flight_sim.summarize_flight(simulator), samples


@pytest.mark.parametrize(
    "name,aircraft,env,launch",
    [
        ("default", AircraftConfig(), EnvironmentConfig(),
         LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)),
        ("fast", AircraftConfig(), EnvironmentConfig(),
         LaunchConfig(launch_speed=12.0, launch_angle_deg=5.0, launch_height_m=1.5)),
        ("elevon", AircraftConfig(elevon_deg=5.0), EnvironmentConfig(),
         LaunchConfig(launch_speed=9.0, launch_angle_deg=2.0, launch_height_m=1.2)),
        ("dihedral", AircraftConfig(dihedral_deg=8.0), EnvironmentConfig(),
         LaunchConfig(launch_speed=9.0, launch_angle_deg=0.0, launch_height_m=1.2)),
        ("windy", AircraftConfig(),
         EnvironmentConfig(wind_speed=4.0, wind_direction_deg=45.0),
         LaunchConfig(launch_speed=9.0, launch_angle_deg=0.0, launch_height_m=1.2)),
    ],
)
def test_gpu_batch_reproduces_the_6dof_flight(name, aircraft, env, launch):
    """The batch and the reference path must report the same flight.

    The audit measured a 1261x disagreement in the peak wing-root stress and
    a different flight status for the very same launch.
    """
    reference, samples = _reference_flight(aircraft, env, launch)
    params = gpu_dynamics.build_params(np, [aircraft], env)
    initial = gpu_dynamics.launch_state_vector(launch, env, aircraft)[np.newaxis, :]
    batch = gpu_dynamics.simulate_batch(np, params, initial, 6.0, 2.0e-2)

    gpu_stress = float(np.asarray(batch["peak_stress_pa"])[0])
    ref_stress = float(reference["max_stress_pa"])
    assert gpu_stress == pytest.approx(ref_stress, rel=1e-6), (
        f"{name}: peak stress {gpu_stress} != {ref_stress}"
    )
    assert float(np.asarray(batch["peak_stress_index"])[0]) == pytest.approx(
        float(reference["max_stress_index"]), rel=1e-6
    )
    ref_g = max(float(sample.g_load) for sample in samples)
    assert float(np.asarray(batch["peak_load_factor"])[0]) == pytest.approx(
        ref_g, rel=1e-6
    )
    assert float(np.asarray(batch["flight_time_s"])[0]) == pytest.approx(
        float(reference["flight_time_s"]), rel=5.0e-3
    )
    assert float(np.asarray(batch["distance_m"])[0]) == pytest.approx(
        float(reference["distance_m"]), rel=5.0e-2
    )
    landed = bool(np.asarray(batch["landed"])[0])
    crashed = bool(np.asarray(batch["crashed"])[0])
    assert ("crashed" if crashed else ("landed" if landed else "timeout")) == reference["status"]


def test_gpu_batch_reports_the_rigid_body_model():
    """The batch is a 6-DoF result, not a surrogate."""
    from physics.parallel_engine import evaluate_cases_on_device

    cases = [
        {
            "aircraft": AircraftConfig(),
            "environment": EnvironmentConfig(),
            "launch": LaunchConfig(launch_speed=8.0, launch_height_m=1.2),
            "duration_s": 6.0,
        }
    ]
    result = evaluate_cases_on_device(cases, np)[0]
    assert result["model"] == "rigid_body_6dof"
    assert result["approximate"] is False
    assert result["physics_verification_eligible"] is True


# --------------------------------------------------------------------------
# 3a. The public payload carries the parity contract
# --------------------------------------------------------------------------
def _default_parity_case(duration: float = 6.0) -> dict:
    """The launch of the default airframe, as the public API receives it."""
    return {
        "aircraft": AircraftConfig(),
        "environment": EnvironmentConfig(),
        "launch": LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2),
        "duration_s": duration,
    }


def test_the_device_payload_publishes_the_peak_stress_without_quantisation():
    """The published peak stress must not be rounded to two decimals.

    The payload builder used to publish the peak stress as
    ``round(stress_pa, 2)`` and its index as ``round(stress_index, 6)``.
    The quantisation existed for JSON tidiness and it cost the parity
    contract: through the public payload the peak stress could only agree
    with the 6-DoF reference to ~1e-5 relative instead of the 1e-6 the
    contract demands.

    The payload builder is backend agnostic -- it takes the array module as
    an argument -- so the regression is pinned with NumPy as well: a host
    without a CUDA device still proves that the published payload carries
    the device value unchanged, and that value is the one the 6-DoF
    reference agrees with.
    """
    from physics.parallel_engine import evaluate_cases_on_device

    aircraft = AircraftConfig()
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)
    duration = 6.0

    reference, _samples = _reference_flight(aircraft, env, launch, duration=duration)
    params = gpu_dynamics.build_params(np, [aircraft], env)
    initial = gpu_dynamics.launch_state_vector(launch, env, aircraft)[np.newaxis, :]
    device = gpu_dynamics.simulate_batch(np, params, initial, duration, 2.0e-2)
    device_stress = float(np.asarray(device["peak_stress_pa"])[0])
    device_index = float(np.asarray(device["peak_stress_index"])[0])

    payload = evaluate_cases_on_device([_default_parity_case(duration)], np)[0]

    # The payload carries the device value unchanged: no quantisation.
    assert float(payload["max_stress_pa"]) == device_stress
    assert float(payload["max_stress_index"]) == device_index
    # And that value honours the established rel=1e-6 parity contract
    # against the 6-DoF reference -- the same comparison and the same
    # tolerance as ``test_gpu_batch_reproduces_the_6dof_flight``.
    assert float(payload["max_stress_pa"]) == pytest.approx(
        float(reference["max_stress_pa"]), rel=1e-6
    ), f"peak stress {payload['max_stress_pa']} != {reference['max_stress_pa']}"
    assert float(payload["max_stress_index"]) == pytest.approx(
        float(reference["max_stress_index"]), rel=1e-6
    ), f"stress index {payload['max_stress_index']} != {reference['max_stress_index']}"


def test_the_public_batch_payload_agrees_with_the_reference_on_the_peak_stress():
    """The public ``gpu_batch_evaluate_cases`` payload must honour rel=1e-6.

    On a CUDA host the public wrapper takes the device path, and the device
    path is where the quantisation lived.  On a host without a device the
    wrapper falls back to the CPU reference path, which never quantised the
    stress and therefore satisfies the contract as well; the regression
    above pins the shared payload builder itself.
    """
    from physics.parallel_engine import gpu_batch_evaluate_cases

    aircraft = AircraftConfig()
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)
    duration = 6.0

    reference, _samples = _reference_flight(aircraft, env, launch, duration=duration)
    payload = gpu_batch_evaluate_cases([_default_parity_case(duration)])[0]

    assert float(payload["max_stress_pa"]) == pytest.approx(
        float(reference["max_stress_pa"]), rel=1e-6
    ), f"peak stress {payload['max_stress_pa']} != {reference['max_stress_pa']}"
    assert float(payload["max_stress_index"]) == pytest.approx(
        float(reference["max_stress_index"]), rel=1e-6
    ), f"stress index {payload['max_stress_index']} != {reference['max_stress_index']}"


# --------------------------------------------------------------------------
# 3b. The trailing-edge fold is the same model in both paths
# --------------------------------------------------------------------------
def test_trailing_edge_fold_bundle_carries_the_fold_angle():
    """The batch bundle must carry the fold angle of the airframe.

    ``39bb795`` rebuilt the batch bundle from an explicit list of
    :class:`~physics.aero_kernel.AeroParams` fields and the list did not
    contain ``fold_trailing_deg``: the batch path therefore evaluated an
    airframe with a *flat* trailing edge while the reference path evaluated
    the folded one.  The two paths were not comparing the same airplane.
    """
    env = EnvironmentConfig()
    for angle in (-30.0, 0.0, 15.0, 45.0):
        params = gpu_dynamics.build_params(np, [AircraftConfig(fold_trailing_deg=angle)], env)
        assert float(np.asarray(params.aero.fold_trailing_deg)[0]) == pytest.approx(angle)


def test_trailing_edge_fold_contribution_is_identical_in_both_paths():
    """Both paths must derive the fold increments from the same kernel call."""
    env = EnvironmentConfig()
    cfg = AircraftConfig(fold_trailing_deg=30.0)
    params = gpu_dynamics.build_params(np, [cfg], env)

    scalar = aero_kernel.evaluate_coefficients(
        aero_kernel.ScalarMath,
        aero_kernel.params_from_aircraft(cfg),
        alpha_deg=4.0,
        speed=10.0,
    )
    batch = aero_kernel.evaluate_coefficients(
        np, params.aero, alpha_deg=4.0, speed=10.0
    )
    for key in ("te_fold_dcl", "te_fold_dcm", "te_fold_dcd", "cl", "cd", "cm"):
        assert float(np.asarray(batch[key])[0]) == pytest.approx(
            float(scalar[key]), rel=1e-12
        ), key
    # The increments are the ones the conformance audit prescribes.
    assert float(np.asarray(batch["te_fold_dcl"])[0]) == pytest.approx(0.45, rel=1e-12)
    assert float(np.asarray(batch["te_fold_dcm"])[0]) == pytest.approx(-0.36, rel=1e-12)


def test_trailing_edge_fold_changes_the_batch_flight():
    """A folded trailing edge must change the flight the batch reports.

    The batch used to ignore the fold entirely, so an optimisation run on a
    CUDA host scored a different airplane than the reference path.
    """
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=9.0, launch_angle_deg=2.0, launch_height_m=1.2)

    def batch_flight(fold_deg: float):
        cfg = AircraftConfig(fold_trailing_deg=fold_deg)
        params = gpu_dynamics.build_params(np, [cfg], env)
        initial = gpu_dynamics.launch_state_vector(launch, env, cfg)[np.newaxis, :]
        return gpu_dynamics.simulate_batch(np, params, initial, 6.0, 2.0e-2)

    flat = batch_flight(0.0)
    folded = batch_flight(30.0)
    assert float(np.asarray(folded["distance_m"])[0]) != pytest.approx(
        float(np.asarray(flat["distance_m"])[0]), rel=1e-9
    )
    assert float(np.asarray(folded["peak_stress_pa"])[0]) != pytest.approx(
        float(np.asarray(flat["peak_stress_pa"])[0]), rel=1e-9
    )


# --------------------------------------------------------------------------
# 3c. The NaN / Inf defence of the shared kernel
# --------------------------------------------------------------------------
def test_a_non_finite_configuration_value_cannot_poison_the_batch_coefficients():
    """A NaN / inf in a configuration field must be replaced by its default.

    ``39bb795`` removed the per-parameter guards of
    :func:`physics.aero_kernel.evaluate_coefficients` and left the guarantee
    to the parameter bundle alone.  The bundle is the single construction
    point of every path, so the guarantee itself is complete -- but a defence
    without a test is a defence that can be removed again, which is exactly
    what ``39bb795`` did to the per-parameter guards.

    The failure mode is severe: a single NaN makes every comparison of the
    batch ``False``, so a broken design would be scored as a normal one by the
    optimiser instead of being reported as infeasible.
    """
    env = EnvironmentConfig()
    cfg = AircraftConfig(cm0=float("nan"), cd0=float("inf"), cL0=float("nan"))

    # The configuration object keeps the raw values; the *kernel* is the
    # layer that has to sanitise them.
    assert math.isnan(cfg.cm0)
    assert math.isinf(cfg.cd0)

    params = aero_kernel.params_from_aircraft(cfg)
    assert params.cm0 == 0.0
    assert params.cL0 == 0.0
    assert params.cd0 == pytest.approx(0.02)

    bundle = gpu_dynamics.build_params(np, [cfg], env)
    assert np.all(np.isfinite(np.asarray(bundle.aero.cm0)))
    assert np.all(np.isfinite(np.asarray(bundle.aero.cd0)))
    assert np.all(np.isfinite(np.asarray(bundle.aero.cL0)))

    out = aero_kernel.evaluate_coefficients(
        np, bundle.aero, alpha_deg=4.0, speed=10.0
    )
    for key in (
        "cl",
        "cd",
        "cm",
        "cy",
        "lift_n",
        "drag_n",
        "side_n",
        "pitching_nm",
        "rolling_nm",
        "yawing_nm",
    ):
        assert math.isfinite(float(np.asarray(out[key])[0])), key


def test_a_non_finite_environment_scale_cannot_poison_the_batch_coefficients():
    """The environment factors are sanitised on the same path as the airframe.

    A NaN in ``lift_scale`` / ``drag_scale`` used to reach the coefficients of
    every case of the batch, because only the reference path replaced it.
    """
    env = EnvironmentConfig(lift_scale=float("nan"), drag_scale=float("inf"))
    lift_scale, drag_scale = aero_kernel.environment_scales(env)
    assert lift_scale == 1.0
    assert drag_scale == 1.0

    bundle = gpu_dynamics.build_params(np, [AircraftConfig()], env)
    assert np.all(np.isfinite(np.asarray(bundle.aero.lift_scale)))
    assert np.all(np.isfinite(np.asarray(bundle.aero.drag_scale)))


def test_a_failed_evaluation_keeps_its_failure_status_in_the_batch_fallback():
    """A broken evaluation must not be relabelled as a benign timeout.

    ``optimizer_domain.is_infeasible_flight`` decides from the ``status``
    alone whether a design is infeasible, and an infeasible design must never
    enter the Pareto front: it used to look like a legitimate 0 m flight.  The
    batch path therefore has to forward the failure status unchanged --
    relabelling ``"error"`` as ``"timeout"`` (or ``"failed"`` as ``"crashed"``)
    hides the failure and lets the broken design into the front.
    """
    from physics import parallel_engine
    from physics.optimizer_domain import is_infeasible_flight

    case = {
        "aircraft": {"wingspan_m": "not-a-number"},
        "environment": {},
        "launch": {},
        "duration_s": 0.2,
    }
    reference = parallel_engine.evaluate_case(case)
    assert reference["status"] == "error"

    rows = parallel_engine.gpu_batch_evaluate_cases([case], force_cpu=True)
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert rows[0]["landed"] is False
    assert is_infeasible_flight(rows[0])


# --------------------------------------------------------------------------
# 4. The post-stall branch
# --------------------------------------------------------------------------
def test_post_stall_lift_has_no_zero_band():
    """A separated plate still carries lift; the curve never collapses to 0."""
    cfg = AircraftConfig()
    cl_values = [
        aero_domain.lift_coefficient(alpha, cfg, True, 0.1) for alpha in (20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 60.0)
    ]
    assert all(value > 0.05 for value in cl_values), cl_values
    # The curve is monotone down from the stall to the blend angle.
    stall = float(aero_domain.effective_stall_alpha_deg(cfg))
    swept = [
        aero_domain.lift_coefficient(stall + delta, cfg, True, 0.1)
        for delta in (0.5, 2.0, 5.0, 10.0, 20.0)
    ]
    assert swept == sorted(swept, reverse=True), swept


def test_post_stall_drag_follows_hoerner():
    """The separated drag is the Hoerner form, not five times too small.

    The separated branch is joined to the attached polar by a C1 smoothstep
    that reaches the full Hoerner value 15 deg past the stall, so the check
    is made where the blend is complete.
    """
    cfg = AircraftConfig()
    reynolds = 4.0e4
    cd0 = aero_domain.parasite_drag_coefficient(cfg, reynolds)
    stall = float(aero_domain.effective_stall_alpha_deg(cfg))
    for alpha in (stall + 16.0, 30.0, 45.0, 60.0):
        separated = aero_domain.separated_pressure_drag_coefficient(alpha, cfg, reynolds)
        hoerner = 2.0 * math.sin(math.radians(alpha)) ** 2 + cd0 * max(math.cos(math.radians(alpha)), 0.0)
        assert separated == pytest.approx(hoerner, rel=1e-9), alpha


def test_post_stall_drag_builds_up_behind_the_stall():
    """The separated drag grows immediately behind the stall break.

    The former transition (zero at the stall, full only at 90 deg) made the
    post-stall drag five times too small at 30 deg.
    """
    cfg = AircraftConfig()
    reynolds = 4.0e4
    stall = float(aero_domain.effective_stall_alpha_deg(cfg))
    values = [
        aero_domain.separated_pressure_drag_coefficient(alpha, cfg, reynolds)
        for alpha in (stall + 1.0, stall + 5.0, 30.0, 45.0)
    ]
    assert values == sorted(values), values
    assert values[-1] > 5.0 * values[0], values


def test_post_stall_drag_is_reachable_in_the_polar():
    """The full polar reaches the separated drag, not a 0.10 ceiling."""
    cfg = AircraftConfig()
    polar = aero_domain.drag_polar(cfg, alpha_min_deg=0.0, alpha_max_deg=60.0, points=31)
    values = [float(value) for value in polar["cd"]]
    assert max(values) > 0.5, values
