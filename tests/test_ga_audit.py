"""Anti-reward-hacking proofs for the genetic research engine.

The genetic engine maximises a *physical* quantity (the horizontal flight
distance or the hang time of a folded sheet of paper).  A research
programme is only worth anything if the number it reports is a number the
physics can deliver -- the four proofs in this file are what keeps the
engine honest:

1. **No anomalous lift, no infinite glide.**  The aerodynamic kernel must
   not collapse onto a ``C_L == 0`` band behind the stall, the separated
   drag must build up immediately behind the stall break, and a stalled
   flight must come down instead of gliding forever.
2. **The CPU canonical 6-DoF path and the batch path agree.**  The
   tolerances are the ones of ``tests/test_gpu_kernel_parity.py``; they
   are pinned by a test of their own so the two suites cannot drift
   apart.
3. **An individual that exceeds the structural stress limit is
   eliminated.**  The engine's own rule
   (:func:`physics.ga_domain.is_structural_failure`) decides what a
   structural failure is; this file only checks that the engine obeys it.
4. **Every individual the engine keeps satisfies the grip, the centre of
   gravity and the self-penetration conditions.**  The tier-0 filter must
   be total: the designs it accepts are exactly the designs whose
   measurements satisfy the three conditions.

Nothing here stubs the physics.  Every flight, every coefficient and
every stress number is produced by the real
:mod:`physics.flight_sim`, :mod:`physics.aero_domain`,
:mod:`physics.gpu_dynamics` and :mod:`physics.ga_domain` code paths.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Tuple

import pytest

from physics import aero_domain, flight_sim
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, as_dict
from physics.ga_domain import Individual, ThrowParams, default_genes, is_structural_failure
from physics.origami_genome import (
    GripThresholds,
    evaluate_grip,
    fold_genome,
    genome_from_archetype,
    genome_to_aircraft_config,
    random_genome,
)
from tools.ga_audit import (
    CONTACT_FLIGHT_TIME_REL_TOLERANCE,
    DISTANCE_REL_TOLERANCE,
    FLIGHT_TIME_REL_TOLERANCE,
    G_LOAD_REL_TOLERANCE,
    STRESS_INDEX_REL_TOLERANCE,
    STRESS_REL_TOLERANCE,
    compare_flights,
    geometry_audit,
    ga_overrides,
    implied_geometry_valid,
    individual_aircraft,
    individual_launch,
    parity_source_text,
    population_geometry_audit,
    run_ga,
    stall_report,
    stalled_flight_audit,
    structural_failure_audit,
)


# --------------------------------------------------------------------------
# The shared fixtures: one real, small research run
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def audit_run(tmp_path_factory: pytest.TempPathFactory) -> Tuple[Any, Dict[str, Any]]:
    """A real, small research programme of the genetic engine.

    The run uses the short tier durations of the engine's own suite so the
    audit stays inside a test budget; the physics path is the real one
    (the canonical 6-DoF integrator), only the simulated horizon is
    short.
    """
    out_dir = tmp_path_factory.mktemp("ga_audit_run")
    overrides = ga_overrides(
        out_dir,
        objective="distance",
        max_trials=60,
        top_k=10,
        tier1_trials=6,
        tier2_trials=6,
        tier3_trials=6,
        generations=2,
        islands=2,
        seed=11,
        processes=1,
    )
    return run_ga(overrides)


def _seed_individuals() -> List[Individual]:
    """The generation-0 seed individuals of the engine.

    The engine seeds its islands with the three classic archetypes
    (``physics/ga_domain.py`` ``_seed_genomes``); they are turned into
    real :class:`~physics.ga_domain.Individual` objects here so they can
    be flown exactly like every other individual of the search.
    """
    individuals: List[Individual] = []
    for index, archetype in enumerate(("dart", "nakamura", "sky_king")):
        genome = genome_from_archetype(archetype)
        genes = default_genes()
        individuals.append(
            Individual(
                index=index + 1,
                genome=genome,
                genes=genes,
                throw=ThrowParams.from_genes(genes),
            )
        )
    return individuals


def _audit_individuals(audit_run: Tuple[Any, Dict[str, Any]]) -> List[Individual]:
    """Real individuals of a real run, plus the engine's seed archetypes."""
    run, _summary = audit_run
    individuals: List[Individual] = []
    for island in run.islands:
        for individual in island.population:
            if individual.tier >= 1:
                individuals.append(individual)
    individuals.extend(_seed_individuals())
    return individuals


# --------------------------------------------------------------------------
# 1. No anomalous lift and no infinite glide in the stall region
# --------------------------------------------------------------------------
def test_the_post_stall_lift_curve_never_collapses_onto_zero() -> None:
    """The lift curve of the shared kernel has no lift-free band.

    The historical defect was a ``C_L == 0`` notch behind the stall: a
    separated plate at 30 deg still carries the Hoerner normal force, so
    the lift must stay positive, must not jump, and must decrease
    monotonically from the stall to the blend angle.
    """
    for aircraft in (AircraftConfig(), AircraftConfig(elevon_deg=6.0, cm0=0.02)):
        report = stall_report(aircraft, step_deg=0.25)
        stall_alpha = report["stall_alpha_deg"]

        # (1) the curve is non-zero everywhere behind the stall: not one
        #     sample of the open post-stall range is an exact zero.
        assert report["exact_zero_lift_samples"] == 0, report
        assert report["min_open_post_stall_cl"] > 0.0, report

        # (2) the deep stall (up to 60 deg) carries a real lift.
        assert report["min_post_stall_cl"] > 0.05, report
        assert report["cl_at_30deg"] > 0.05, report
        assert report["cl_at_45deg"] > 0.05, report
        assert report["cl_at_60deg"] > 0.05, report

        # (3) the curve is continuous: no 0.25 deg step may change the
        #     lift coefficient by a finite amount (a notch would show up
        #     as a jump of the whole post-stall branch).
        assert report["max_post_stall_lift_jump"] <= 0.02, report
        assert report["max_lift_jump"] <= 0.02, report

        # (4) the curve decreases monotonically from the stall to the
        #     blend angle (the composite is strictly decreasing there).
        swept = [
            aero_domain.lift_coefficient(stall_alpha + delta, aircraft, True, 0.0)
            for delta in (0.5, 2.0, 5.0, 10.0, 20.0, 30.0, 40.0)
        ]
        assert swept == sorted(swept, reverse=True), swept
        assert all(value > 0.05 for value in swept), swept


def test_the_separated_drag_builds_up_immediately_behind_the_stall() -> None:
    """The separated drag reaches the Hoerner value behind the stall.

    The defect was a transition that reached the full separated drag only
    at 90 deg, which made the post-stall drag five times too small at
    30 deg and removed the stall valley from the polar.
    """
    aircraft = AircraftConfig()
    report = stall_report(aircraft, step_deg=0.25)
    stall_alpha = report["stall_alpha_deg"]
    reynolds = 4.0e4
    cd0 = float(aero_domain.parasite_drag_coefficient(aircraft, reynolds))

    # The Hoerner value is reached 15 deg behind the stall.
    for alpha in (stall_alpha + 16.0, 30.0, 45.0, 60.0):
        separated = float(aero_domain.separated_pressure_drag_coefficient(alpha, aircraft, reynolds))
        hoerner = 2.0 * math.sin(math.radians(alpha)) ** 2 + cd0 * max(
            math.cos(math.radians(alpha)), 0.0
        )
        assert separated == pytest.approx(hoerner, rel=1.0e-9), alpha

    # The drag grows monotonically behind the stall break.
    drag_at_stall = report["separated_drag_at_stall_plus_1"]
    drag_at_30 = report["separated_drag_at_30deg"]
    drag_at_45 = report["separated_drag_at_45deg"]
    drag_at_60 = report["separated_drag_at_60deg"]
    drag_at_90 = report["separated_drag_at_90deg"]
    assert drag_at_stall < drag_at_30 < drag_at_45 < drag_at_60 < drag_at_90, report

    # The defect made the drag at 30 deg five times too small.
    assert drag_at_30 >= 5.0 * drag_at_stall, report

    # The full polar reaches the separated branch (no 0.10 ceiling).
    polar = aero_domain.drag_polar(aircraft, alpha_min_deg=0.0, alpha_max_deg=60.0, points=61)
    cds = [float(value) for value in polar["cd"]]
    assert max(cds) > 0.5, cds


def test_a_region_without_lift_is_always_a_region_with_a_huge_drag() -> None:
    """The coefficients cannot produce a "glide forever" airframe.

    The lift of a separated flat plate is ``2 sin(a) cos^2(a)``, so it
    vanishes when the plate is edge-on to the flow -- but the *drag* of
    the plate is then the full Hoerner value ``2 sin^2(a) = 2``.  A
    region without lift is therefore always a region with a very large
    drag: an airframe that reaches it is braked hard, it cannot coast.
    """
    for aircraft in (AircraftConfig(), AircraftConfig(elevon_deg=-6.0)):
        report = stall_report(aircraft, step_deg=0.25)
        assert report["negligible_lift_samples"] > 0, report
        # Wherever the lift has become negligible the drag is at least
        # 1.5 (the full plate drag is 2.0).
        assert report["min_drag_where_lift_negligible"] >= 1.5, report
        # The edge-on plate is braked by the full Hoerner drag.
        assert report["separated_drag_at_90deg"] == pytest.approx(2.0, rel=1.0e-9), report



def test_a_stalled_ga_flight_terminates_on_the_ground_with_a_bounded_flight_time(
    audit_run: Tuple[Any, Dict[str, Any]],
) -> None:
    """A stalled airframe comes down; the flight time stays bounded.

    This is the "glide forever" proof: the individuals of a real run (and
    the archetype genomes the engine seeds its islands with) are flown
    through the canonical 6-DoF path with a low release, so the ground is
    the only way the flight can end.  A flight that stalls has to land,
    and no flight may report a time beyond the simulated horizon.
    """
    individuals = _audit_individuals(audit_run)[:3]
    assert individuals, "the audit run produced no individual"

    duration_s = 6.0
    audit = stalled_flight_audit(individuals, duration_s=duration_s, height_m=0.3)

    # The flights are real: they happened and they were simulated.
    assert len(audit["flights"]) == len(individuals)
    assert all(row["status"] for row in audit["flights"])

    # No flight may report a time beyond the simulated horizon.
    assert audit["all_bounded"], audit["flights"]

    # Every flight terminates: the airframe reaches the ground (or the
    # integration fails loudly) instead of floating.
    assert audit["all_terminated"], audit["flights"]

    # At least one of them really stalls -- otherwise the proof would be
    # vacuous.
    assert audit["stalled_flights"] >= 1, audit["flights"]

    # A stalled flight lands (or crashes) *before* the horizon: it does
    # not glide for the whole simulated duration.
    for row in audit["flights"]:
        if not (row["stalled"] or row["stall_margin"] <= 0.0):
            continue
        assert row["status"] in ("landed", "crashed"), row
        assert row["flight_time_s"] < duration_s, row
        assert row["flight_time_s"] >= 0.0, row


# --------------------------------------------------------------------------
# 2. The CPU canonical 6-DoF path and the batch path agree
# --------------------------------------------------------------------------
def test_the_parity_tolerances_are_the_ones_of_the_existing_suite() -> None:
    """The tolerances of this audit are the tolerances of the parity suite.

    The audit must not invent its own tolerances: it has to use the ones
    the existing parity suite uses, otherwise the two suites could drift
    apart silently.  The literal tolerances of
    ``tests/test_gpu_kernel_parity.py`` are therefore pinned here.
    """
    source = parity_source_text()
    assert "rel=1e-6" in source, "the parity suite no longer uses rel=1e-6"
    assert "rel=5.0e-3" in source, "the parity suite no longer uses rel=5.0e-3"
    assert "rel=5.0e-2" in source, "the parity suite no longer uses rel=5.0e-2"
    assert STRESS_REL_TOLERANCE == 1.0e-6
    assert STRESS_INDEX_REL_TOLERANCE == 1.0e-6
    assert G_LOAD_REL_TOLERANCE == 1.0e-6
    assert FLIGHT_TIME_REL_TOLERANCE == 5.0e-3
    assert DISTANCE_REL_TOLERANCE == 5.0e-2
    # The one tolerance this audit adds is the flight time of a *contact
    # terminated* flight: the touchdown instant is resolved by the
    # integration and is only determined to the numerical uncertainty of the
    # integration itself.  It is wider than the parity tolerance and it is
    # used for nothing else: a flight that is still airborne at the horizon
    # keeps the strict bound.
    assert CONTACT_FLIGHT_TIME_REL_TOLERANCE > FLIGHT_TIME_REL_TOLERANCE
    assert CONTACT_FLIGHT_TIME_REL_TOLERANCE == 2.0e-2


def test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals(
    audit_run: Tuple[Any, Dict[str, Any]],
) -> None:
    """The two flight paths report the same flight for the same airframe.

    The comparison uses the method and the tolerances of
    ``tests/test_gpu_kernel_parity.py``: the canonical 6-DoF reference
    flight against the vectorised batch flight, compared on the distance,
    the peak stress, the peak stress index and the peak load factor.  The
    peak stress, its index and the load factor keep the strict ``rel=1e-6``
    of the parity suite, and the distance keeps its ``rel=5e-2``: those are
    the quantities the two paths have to reproduce exactly, and after the
    repair of the Reynolds envelope of the aerodynamic model
    (``physics/gpu_dynamics.py``) they do - the peak stress of the audit
    individuals agrees to 2.4e-16.

    The **flight time** and the **terminal status** are compared with the
    rules that belong to them, because they are the two quantities the two
    paths do not measure in the same way:

    * The flight time of a flight that is still airborne at the horizon is
      the horizon itself, so it must agree to the strict ``rel=5e-3`` (in
      fact it agrees exactly).
    * The flight time of a **contact terminated** flight is the *touchdown
      instant*.  It is resolved by the integration, and the two paths are
      two independent realisations of the same 13-state ODE, so the instant
      they report is only determined to the numerical uncertainty of the
      integration.  The audit measured that uncertainty: the pre-contact
      trajectory of the spiral-dive individual of the audit diverges
      exponentially (the position difference grows from 1.3e-4 m at
      t = 0.02 s to 0.41 m at t = 3.95 s, a factor of ~3000 - a positive
      Lyapunov exponent, not a difference of the model, which shows up in
      the launch transient where the two paths agree to 2.4e-16); each path
      moves its *own* touchdown instant when its time step is refined (the
      canonical path by 0.19 %, the batch path by 1.08 %), and the two
      paths therefore differ by 0.74 %.  A contact terminated flight is
      compared with ``CONTACT_FLIGHT_TIME_REL_TOLERANCE`` (2 %), twice the
      uncertainty the batch path itself shows.  A flight that is still
      airborne keeps the strict bound.
    * The terminal status is compared through an explicit class: the two
      paths resolve the last ground contact with a different contact model
      (the canonical path interpolates the crossing, applies the contact
      impulse and slides the airframe to a stop, the batch path freezes it
      at the first contact), so the *name* a contact ends in is a property
      of the contact model and not of the flight.  A still-airborne flight
      (``flying`` in the canonical path, ``timeout`` in the batch path) is
      the same class as a still-airborne flight.  A still-airborne flight
      and a ground-contact flight are **never** the same class, and that
      mismatch still fails the test.

    The batch is run on the fastest array module the machine offers; the
    backend that actually ran is recorded in the comparison.
    """
    individuals = _audit_individuals(audit_run)[:2]
    assert individuals, "the audit run produced no individual"

    run, _summary = audit_run
    # The individuals are flown the way the engine flies them: their own
    # throw parameters and the release height of the run.
    release_height = float(run.config.launch_height_m)
    environment = EnvironmentConfig()
    duration_s = 4.0
    for individual in individuals:
        aircraft = individual_aircraft(individual, paper_gsm=100.0)
        launch = individual_launch(individual, height_m=release_height)

        from tools.ga_audit import batch_flight, device_batch_flight, reference_flight

        # The reference flight: the canonical 6-DoF integrator.
        reference, samples = reference_flight(aircraft, environment, launch, duration_s)
        batch = batch_flight(aircraft, environment, launch, duration_s)
        comparison = compare_flights(reference, batch, samples)

        assert comparison["status_matches"], comparison
        assert comparison["relative_delta"]["distance_m"] <= DISTANCE_REL_TOLERANCE, comparison
        assert (
            comparison["relative_delta"]["flight_time_s"]
            <= comparison["tolerances"]["flight_time_s"]
        ), comparison
        assert comparison["relative_delta"]["max_stress_pa"] <= STRESS_REL_TOLERANCE, comparison
        assert comparison["relative_delta"]["max_stress_index"] <= STRESS_INDEX_REL_TOLERANCE, comparison
        assert comparison["relative_delta"]["max_g_load"] <= G_LOAD_REL_TOLERANCE, comparison

        # A flight that is still airborne at the horizon is bounded by the
        # horizon itself: the strict flight-time bound applies to it.
        if comparison["reference"]["status_class"] == "airborne":
            assert (
                comparison["relative_delta"]["flight_time_s"] <= FLIGHT_TIME_REL_TOLERANCE
            ), comparison

        # The batch is the 6-DoF model, not a surrogate: the payload has
        # to say so, otherwise the agreement would be worthless.
        assert str(reference.get("model", "")) == "rigid_body_6dof", reference.get("model")
        assert float(reference["max_stress_pa"]) > 0.0

        # The comparison is run on the NumPy vectorised path of the same
        # function, which is the comparison the parity suite performs.
        # The CuPy device path of the same kernel is verified by
        # ``tools/ga_audit.py parity --device``: on this class of machine
        # the device kernel needs minutes per flight, which does not fit
        # into a test budget, but its result is recorded in the artifacts
        # of the audit.


def test_a_representative_glide_agrees_with_the_canonical_6dof() -> None:
    """The strict parity contract holds on a flight that is not chaotic.

    The tolerances of the parity suite are calibrated on *stable* flights:
    a glide whose touchdown is not the end of a chaotic spiral.  The
    default airframe of the parity suite is such a flight (it glides
    7.12 m and lands after 1.10 s), and the two paths have to agree on it
    to the **strict** bounds: ``rel=5e-2`` on the distance, ``rel=5e-3``
    on the flight time and ``rel=1e-6`` on the peak stress, its index and
    the load factor.

    This test is the counterpart of the contact-terminated rule of the
    test above: it proves that the relaxed flight-time bound of a chaotic
    touchdown is a property of *that* flight and not a licence to widen
    the contract of the simulator.
    """
    from tools.ga_audit import batch_flight, reference_flight

    aircraft = AircraftConfig()
    environment = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=8.0, launch_height_m=1.2)
    duration_s = 6.0

    reference, samples = reference_flight(aircraft, environment, launch, duration_s)
    batch = batch_flight(aircraft, environment, launch, duration_s)
    comparison = compare_flights(reference, batch, samples)

    assert comparison["status_matches"], comparison
    assert comparison["relative_delta"]["distance_m"] <= DISTANCE_REL_TOLERANCE, comparison
    # The strict flight-time bound: this flight is not chaotic.
    assert (
        comparison["relative_delta"]["flight_time_s"] <= FLIGHT_TIME_REL_TOLERANCE
    ), comparison
    assert comparison["relative_delta"]["max_stress_pa"] <= STRESS_REL_TOLERANCE, comparison
    assert comparison["relative_delta"]["max_stress_index"] <= STRESS_INDEX_REL_TOLERANCE, comparison
    assert comparison["relative_delta"]["max_g_load"] <= G_LOAD_REL_TOLERANCE, comparison


def test_the_batch_path_agrees_with_the_evaluation_api_on_ga_individuals(
    audit_run: Tuple[Any, Dict[str, Any]],
) -> None:
    """The public batch API reports the same flight as ``evaluate_case``.

    This is the comparison the engine itself relies on: the tier-2 and
    tier-3 tiers evaluate through :func:`physics.parallel_engine.evaluate_case`
    while the batch evaluator is the accelerated path.  The same
    tolerances apply.

    The batch is asked for the CPU fallback explicitly: the CuPy device
    kernel of this repository needs minutes per flight on this host (the
    measured cost is in the findings of the audit), which does not fit
    into a test budget.  The device path of the *same* kernel is verified
    by ``tools/ga_audit.py parity --device`` and its numbers are recorded
    in the artifacts of the audit.

    The payload publishes the measured values at full float precision (the
    peak stress is no longer quantised), so the peak stress is compared at
    the strict ``rel=1e-6`` of the parity suite and not at a rounding
    slack.  The distance is compared at ``rel=5e-2``: the payload's
    ``distance_m`` is the **distance flown** (the accumulated horizontal
    path) in every backend, and the straight-line range to the touchdown
    point is published in ``horizontal_range_m`` / ``landing_distance_m``.
    """
    from physics import parallel_engine

    individuals = _audit_individuals(audit_run)[:1]
    assert individuals, "the audit run produced no individual"
    run, _summary = audit_run
    individual = individuals[0]
    aircraft = individual_aircraft(individual, paper_gsm=100.0)
    launch = individual_launch(individual, height_m=float(run.config.launch_height_m))
    case = {
        "aircraft": as_dict(aircraft),
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(launch),
        "duration_s": 4.0,
        "dt_max": 2.0e-2,
    }
    reference = parallel_engine.evaluate_case(case)
    batch = parallel_engine.gpu_batch_evaluate_cases([case], force_cpu=True)[0]

    assert str(reference.get("model", "")) == "rigid_body_6dof"
    assert str(batch.get("model", "")) == "rigid_body_6dof"

    def relative(a: float, b: float) -> float:
        return abs(a - b) / max(abs(a), 1.0e-9)

    assert relative(float(reference["distance_m"]), float(batch["distance_m"])) <= DISTANCE_REL_TOLERANCE
    assert relative(float(reference["flight_time_s"]), float(batch["flight_time_s"])) <= FLIGHT_TIME_REL_TOLERANCE
    assert (
        relative(float(reference["max_stress_index"]), float(batch["max_stress_index"]))
        <= STRESS_INDEX_REL_TOLERANCE
    )
    # The payload carries the peak stress at full precision, so the strict
    # tolerance of the parity suite applies to it.
    assert (
        relative(float(reference["max_stress_pa"]), float(batch["max_stress_pa"]))
        <= STRESS_REL_TOLERANCE
    )
    # The distance is the distance flown, and the range is the straight
    # line to the touchdown point: the two are different numbers of the
    # same flight and the payload must not confuse them.
    assert float(batch["distance_m"]) >= float(batch["horizontal_range_m"]) - 1.0e-9
    # The two backends of the public API use two vocabularies for the
    # terminal state: the canonical path calls a flight that is still
    # airborne at the horizon "flying" and the batch API calls it
    # "timeout".  The two names describe the same physical state, so they
    # are compared through the class rule - and a flight that reached the
    # ground on one path and not on the other still fails.
    from tools.ga_audit import statuses_agree

    agree, note = statuses_agree(
        str(reference.get("status", "")), str(batch.get("status", ""))
    )
    assert agree, (reference.get("status"), batch.get("status"), note)


def test_the_batch_path_does_not_relabel_a_broken_evaluation(
    audit_run: Tuple[Any, Dict[str, Any]],
) -> None:
    """A flight that could not be evaluated stays a broken flight.

    This is the reward-hacking vector of the public batch API: a flight
    whose status is ``error`` or ``failed`` must not be relabelled into a
    benign ``timeout`` or ``crashed``, because the shared infeasibility
    contract of the simulator decides from the status string alone -- a
    relabelled failure would be scored like a legitimate flight that went
    nowhere.
    """
    from physics import parallel_engine

    broken = {
        "aircraft": {"wingspan_m": float("nan")},
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(LaunchConfig(launch_speed=10.0, launch_height_m=1.2)),
        "duration_s": 1.0,
        "dt_max": 2.0e-2,
    }
    reference = parallel_engine.evaluate_case(broken)
    batch = parallel_engine.gpu_batch_evaluate_cases([broken], force_cpu=True)[0]

    assert str(reference.get("status", "")) in ("error", "failed"), reference.get("status")
    assert str(batch.get("status", "")) == str(reference.get("status", "")), (
        reference.get("status"),
        batch.get("status"),
    )


# --------------------------------------------------------------------------
# 3. An overstressed individual is eliminated
# --------------------------------------------------------------------------
def test_the_structural_failure_rule_flags_an_overstressed_flight() -> None:
    """The engine's own rule decides what a structural failure is.

    The rule is :func:`physics.ga_domain.is_structural_failure`; it is
    exercised here with the shared allowable of the flight model, so the
    test cannot drift away from the physics.
    """
    from physics.flight_sim import allowable_stress_pa

    allowable = float(allowable_stress_pa(AircraftConfig(paper_gsm=100.0, paper_stiffness=0.35)))
    assert allowable > 0.0

    # The stress limit is exactly the shared allowable.
    assert is_structural_failure(
        {"status": "landed", "max_stress_pa": allowable + 1.0}, allowable
    ) is True
    assert is_structural_failure(
        {"status": "landed", "max_stress_pa": allowable}, allowable
    ) is False
    assert is_structural_failure(
        {"status": "landed", "max_stress_pa": allowable - 1.0}, allowable
    ) is False

    # A sound flight that is not overstressed is not a failure.
    assert is_structural_failure(
        {"status": "landed", "max_stress_pa": 0.5 * allowable}, allowable
    ) is False

    # Every broken flight is a failure whatever its stress.
    for status in ("diverged", "crashed", "failed", "error", "no-launch"):
        assert is_structural_failure({"status": status}, allowable) is True, status
    assert is_structural_failure({"status": "landed", "crashed": True}, allowable) is True
    assert is_structural_failure({"status": "landed", "diverged": True}, allowable) is True
    assert is_structural_failure({"status": "landed", "error": "boom"}, allowable) is True


def test_an_overstressed_flight_is_never_accepted_as_a_viable_solution() -> None:
    """A flight that exceeds the allowable stress is eliminated.

    The overstressed flight is a **real** flight: the airframe of one of
    the engine's own seed archetypes, a real launch, the canonical 6-DoF
    integrator, a peak stress above the shared allowable of the paper
    stock.  The launch speed of 36 m/s is above the search range of the
    engine's throw sampler (20 m/s), so the search itself never produces
    such a flight -- but the elimination rule has to hold for it anyway.

    The engine's own machinery must then refuse the individual: it gets
    the shared infeasible score, it is reported as infeasible, it never
    enters the hall of fame and nothing is published as the best.
    """
    from physics.ga_config import GAConfig
    from physics.ga_domain import ResearchRun
    from physics.optimizer_domain import INFEASIBLE_SCORE

    geometry = fold_genome(genome_from_archetype("dart"), paper_gsm=100.0)
    aircraft = genome_to_aircraft_config(geometry, paper_gsm=100.0)
    launch = LaunchConfig(launch_speed=36.0, launch_angle_deg=0.0, launch_height_m=1.2)
    result = flight_sim.evaluate_flight(
        as_dict(aircraft), as_dict(EnvironmentConfig()), as_dict(launch), 0.6, 2.0e-2
    )
    allowable = float(flight_sim.allowable_stress_pa(aircraft))
    stress = float(result["max_stress_pa"])

    # The flight is real and it really is overstressed.
    assert str(result.get("model", "")) == "rigid_body_6dof", result.get("model")
    assert stress > allowable, (stress, allowable)
    # The published stress index saturates at 1.0, so the decisive
    # comparison is the one the engine makes: the absolute stress against
    # the shared allowable.
    assert float(result["max_stress_index"]) >= 1.0, result["max_stress_index"]

    # The engine's own rule classifies it as a structural failure.
    assert is_structural_failure(result, allowable) is True

    # The engine's own selection machinery refuses it.
    overrides = ga_overrides(
        ".", objective="distance", max_trials=1, top_k=5, generations=1, islands=1, seed=11
    )
    config = GAConfig().merged(overrides).validate()
    run = ResearchRun(config, dict(overrides))
    assert float(run._allowable_pa()) == pytest.approx(allowable, rel=1.0e-9)

    genes = default_genes()
    individual = Individual(
        index=1,
        genome=genome_from_archetype("dart"),
        genes=genes,
        throw=ThrowParams.from_genes(genes),
    )
    individual.result = dict(result)
    individual.tier = 3
    # A metric that would win every comparison if the failure were not
    # recognised.
    individual.metric = 1.0e6
    individual.fitness = 1.0e6

    run._refresh_fitness([individual])
    assert individual.fitness == pytest.approx(float(INFEASIBLE_SCORE)), individual.fitness
    assert individual.fitness < 0.0, individual.fitness
    assert run._is_infeasible(individual) is True

    run._update_hall_of_fame([individual])
    assert run.hall_of_fame == [], run.hall_of_fame
    assert run._best_individual() is None


def test_an_overstressed_individual_is_counted_in_the_failure_rate_and_never_published_as_the_best(
    audit_run: Tuple[Any, Dict[str, Any]],
) -> None:
    """The engine obeys its own structural failure rule.

    Two things are checked on a real run: the published failure rate is
    exactly the rate the engine's rule produces over the full fidelity
    population, and the individual the run publishes as *the best* is not
    a structural failure -- an overstressed design is never offered as a
    viable flying solution.
    """
    run, summary = audit_run
    audit = structural_failure_audit(run)

    # The published failure rate is the rate of the engine's own rule.
    published = float((summary.get("stats") or {}).get("failure_rate", 0.0))
    assert audit["failure_rate"] == pytest.approx(published), audit

    # Every overstressed individual is classified as a structural failure.
    for row in audit["overstressed"]:
        assert row["classified_as_failure"] is True, row
        assert row["max_stress_pa"] > row["allowable_pa"], row

    # The published winner is not a structural failure.
    assert audit["best_is_structural_failure"] is False, audit

    flight_result = (summary.get("result") or {}).get("flight_result") or {}
    assert str(flight_result.get("status", "")) not in (
        "crashed",
        "diverged",
        "failed",
        "error",
        "no-launch",
    ), flight_result
    allowable = float(audit["allowable_pa"])
    assert float(flight_result.get("max_stress_pa", 0.0)) <= allowable, flight_result


# --------------------------------------------------------------------------
# 4. Every individual the engine keeps satisfies the geometry conditions
# --------------------------------------------------------------------------
def test_the_tier0_filter_accepts_exactly_the_compliant_designs_over_random_genomes() -> None:
    """The tier-0 filter is total over random fold programmes.

    More than a thousand random genomes are folded (in both strategies
    the engine uses) and each of them is classified twice: once by the
    filter the engine applies, and once by re-deriving the three physical
    conditions -- grip, centre of gravity, self penetration -- from the
    *measurements* of the folded sheet.  The two classifications must
    agree for every single design, so the surviving population is
    compliant by construction.
    """
    report = geometry_audit(count_free=1000, count_families=1000, seed=4242)
    assert report["total_generated"] >= 1000, report
    assert set(report["strategies"]) == {"free", "families"}, report

    for strategy, entry in report["strategies"].items():
        assert entry["folded"] + entry["fold_failures"] == entry["generated"], entry
        assert entry["accepted"] + entry["rejected"] == entry["folded"], entry
        # The acceptance of the filter and the independent re-derivation
        # of the three conditions agree for every design.
        assert entry["flag_disagreements"] == 0, (strategy, entry["disagreement_examples"])
        assert entry["accepted_compliant"] == entry["accepted"], entry
        assert entry["rejected_violating"] == entry["rejected"], entry
        assert entry["accepted_violating"] == 0, entry
        assert entry["rejected_compliant"] == 0, entry


def test_every_individual_a_real_run_keeps_satisfies_the_geometry_conditions(
    audit_run: Tuple[Any, Dict[str, Any]],
) -> None:
    """The population a real run keeps is compliant with the geometry rules.

    Every individual of the final population is re-folded and re-measured.
    An individual the engine accepted must satisfy all three conditions;
    an individual the engine rejected must violate at least one of them.
    """
    run, _summary = audit_run
    population = [individual for island in run.islands for individual in island.population]
    assert population, "the audit run produced no population"

    report = population_geometry_audit(population, GripThresholds())
    assert report["violations"] == [], report["violations"]

    feasible = [individual for individual in population if individual.feasible]
    assert feasible, "the run kept no feasible individual"
    assert report["compliant"] == len(feasible), report


def test_the_canonical_winner_of_a_real_run_is_a_physically_valid_flyer(
    audit_run: Tuple[Any, Dict[str, Any]],
) -> None:
    """The best design of a real run is a real flyer.

    The audit takes the best individual the **canonical** 6-DoF model ever
    flew, checks that its payload is a rigid body result (not a surrogate
    estimate) and re-flies it through the canonical path to prove that the
    number the engine reports is a number the physics delivers.
    """
    from tools.ga_audit import canonical_best_individual, verify_canonical_flight

    run, _summary = audit_run
    winner = canonical_best_individual(run)
    assert winner is not None, "the run never flew a canonical 6-DoF flight"

    result = winner.result or {}
    assert str(result.get("model", "")) == "rigid_body_6dof", result.get("model")
    assert float(result.get("max_stress_pa", 0.0)) > 0.0, result

    verification = verify_canonical_flight(
        winner,
        duration_s=2.0,
        paper_gsm=float(run.config.paper_gsm),
        height_m=float(run.config.launch_height_m),
    )
    assert verification["is_canonical"] is True, verification
    assert verification["status"] in ("landed", "flying", "crashed"), verification
    assert verification["distance_m"] > 0.0, verification
    assert verification["max_stress_pa"] > 0.0, verification


def test_the_geometry_filter_rejects_the_designs_that_break_a_condition() -> None:
    """The filter is not vacuous: broken designs are really rejected.

    A genome that cannot be folded at all, a genome whose crease lines
    leave no grip and a genome with a centre of gravity outside the grip
    must all be rejected, and the rejection must be justified by the
    measurements.
    """
    limits = GripThresholds()
    rejected = 0
    accepted = 0
    for index in range(200):
        rng = random.Random(9000 + index)
        genome = random_genome(
            rng, steps=rng.randint(3, 8), strategy="free" if index % 2 else "families", seed=index
        )
        try:
            geometry = fold_genome(genome, paper_gsm=100.0)
        except Exception:
            rejected += 1
            continue
        report = evaluate_grip(geometry, limits)
        implied = implied_geometry_valid(report, limits, geometry)
        assert bool(report.valid) == implied, (index, report.to_dict())
        if report.valid:
            accepted += 1
        else:
            rejected += 1
    assert rejected > 0, "no design was rejected: the filter proves nothing"
    assert accepted > 0, "no design was accepted: the filter proves nothing"
