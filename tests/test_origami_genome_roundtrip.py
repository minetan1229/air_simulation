"""Regression tests for the round trip of a fold programme through JSON.

A genome leaves the server as JSON and comes back as JSON: the API route
rebuilds it with :func:`physics.origami_genome.genome_from_dict` and the GA
engine restores a checkpoint the same way.  The round trip therefore has to
be lossless.

The defect these tests guard against: :meth:`FoldStep.to_dict` writes the
fold sense as the *string* ``"mountain"`` / ``"valley"`` and the parser read
it back with ``bool(raw.get("fold_type"))``.  ``bool("valley")`` is
``True``, so every valley fold came back as a mountain fold.  The paper was
folded the other way round, the aeroplane that came back was a different
aeroplane than the one that was sent, and a GA checkpoint resumed with
flipped folds -- which silently changes the physics of the resumed run.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics import origami_genome as og  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def mixed_genome() -> og.OrigamiGenome:
    """A programme with one mountain and two valley folds, fixed by hand."""
    return og.OrigamiGenome(
        steps=[
            og.FoldStep(
                step_index=1,
                crease_line=((0.5, 0.0), (0.5, 1.0)),
                fold_angle_deg=40.0,
                symmetry=og.SYMMETRY_SYMMETRIC,
                set_group="g1",
                mountain=True,
                label="keel fold",
            ),
            og.FoldStep(
                step_index=2,
                crease_line=((0.0, 0.7), (1.0, 0.7)),
                fold_angle_deg=-25.0,
                symmetry=og.SYMMETRY_LEFT,
                set_group="g2",
                mountain=False,
                label="flap fold",
            ),
            og.FoldStep(
                step_index=3,
                crease_line=((0.15, 0.15), (0.85, 0.85)),
                fold_angle_deg=-120.0,
                symmetry=og.SYMMETRY_RIGHT,
                set_group="g3",
                mountain=False,
                label="diagonal fold",
            ),
        ],
        sheet=list(og.SHEET_OUTLINE),
        key="test",
        label="test programme",
        seed=7,
        strategy="custom",
    )


def families_genome() -> og.OrigamiGenome:
    """The programme of the defect report: the seed 7 family genome."""
    return og.random_genome(random.Random(7), steps=8, strategy="families", seed=7)


def assert_sense_is_consistent(step: og.FoldStep) -> None:
    """The documented invariant: the sign of the angle carries the sense."""
    if step.angle_magnitude_deg > 0.0:
        assert (step.fold_angle_deg > 0.0) == step.mountain, step


# ---------------------------------------------------------------------------
# 1. the round trip is lossless
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("genome", [mixed_genome(), families_genome()], ids=["handmade", "families-seed-7"])
def test_the_round_trip_keeps_every_fold_sense(genome: og.OrigamiGenome) -> None:
    """Every fold has to come back with the same sense, angle and crease."""
    senses = {step.mountain for step in genome.steps}
    assert senses == {True, False}, "the test genome needs a mountain and a valley fold"

    payload = og.genome_payload(genome)
    restored = og.genome_from_dict(payload)

    assert len(restored.steps) == len(genome.steps)
    for original, copy in zip(genome.steps, restored.steps):
        assert copy.crease_line == original.crease_line
        assert copy.angle_magnitude_deg == pytest.approx(
            original.angle_magnitude_deg, rel=0.0, abs=1.0e-12
        )
        assert copy.mountain == original.mountain
        assert copy.fold_angle_deg == pytest.approx(
            original.fold_angle_deg, rel=1.0e-12, abs=1.0e-12
        )
        assert_sense_is_consistent(copy)


def test_the_round_trip_of_the_bare_genome_keeps_every_fold_sense() -> None:
    """The genome dictionary on its own (what a client posts) round trips too."""
    genome = mixed_genome()
    restored = og.genome_from_dict(genome.to_dict())
    assert len(restored.steps) == len(genome.steps)
    assert [step.mountain for step in restored.steps] == [
        step.mountain for step in genome.steps
    ]


def test_the_sign_of_the_angle_follows_the_resolved_sense() -> None:
    """A step whose sense contradicts its sign is repaired, not flipped twice.

    The sense comes from ``fold_type``; the angle keeps its magnitude and
    gets the sign of that sense.  The magnitude is never changed.
    """
    payload = {
        "steps": [
            {
                "step_index": 1,
                "crease_line": [[0.0, 0.0], [1.0, 1.0]],
                "fold_angle_deg": -30.0,
                "fold_type": "mountain",
            }
        ]
    }
    restored = og.genome_from_dict(payload)
    step = restored.steps[0]
    assert step.mountain is True
    assert step.fold_angle_deg == pytest.approx(30.0, rel=0.0, abs=1.0e-12)
    assert step.angle_magnitude_deg == pytest.approx(30.0, rel=0.0, abs=1.0e-12)


def test_the_fold_type_names_are_parsed_and_the_japanese_ones_too() -> None:
    """The names of the sense are parsed; an unknown name is an error."""
    def step_with(fold_type: object) -> og.FoldStep:
        payload = {
            "steps": [
                {
                    "step_index": 1,
                    "crease_line": [[0.0, 0.0], [1.0, 1.0]],
                    "fold_angle_deg": 20.0,
                    "fold_type": fold_type,
                }
            ]
        }
        return og.genome_from_dict(payload).steps[0]

    assert step_with("mountain").mountain is True
    assert step_with("Mountain").mountain is True
    assert step_with("valley").mountain is False
    assert step_with("VALLEY").mountain is False
    assert step_with("山").mountain is True
    assert step_with("谷").mountain is False
    # A boolean is accepted as the sense itself.
    assert step_with(True).mountain is True
    assert step_with(False).mountain is False


def test_a_missing_fold_type_falls_back_to_the_boolean_and_to_the_sign() -> None:
    """Without a ``fold_type`` the other fields decide the sense."""
    payload = {
        "steps": [
            {
                "step_index": 1,
                "crease_line": [[0.0, 0.0], [1.0, 1.0]],
                "fold_angle_deg": 20.0,
                "mountain": False,
            }
        ]
    }
    assert og.genome_from_dict(payload).steps[0].mountain is False

    payload["steps"][0].pop("mountain")
    assert og.genome_from_dict(payload).steps[0].mountain is True  # angle >= 0

    payload["steps"][0]["fold_angle_deg"] = -20.0
    assert og.genome_from_dict(payload).steps[0].mountain is False  # angle < 0


# ---------------------------------------------------------------------------
# 2. the geometry survives the round trip
# ---------------------------------------------------------------------------
def test_the_round_trip_folds_into_the_same_solid() -> None:
    """The re-folded paper has to be the same solid: same bounds, same CG."""
    genome = families_genome()
    restored = og.genome_from_dict(og.genome_payload(genome))

    original = og.fold_genome(genome)
    copy = og.fold_genome(restored)

    lo, hi = original.bounds()
    copy_lo, copy_hi = copy.bounds()
    assert copy_lo == pytest.approx(lo, rel=0.0, abs=1.0e-12)
    assert copy_hi == pytest.approx(hi, rel=0.0, abs=1.0e-12)
    assert copy.cg == pytest.approx(original.cg, rel=0.0, abs=1.0e-12)
    assert copy.mass_kg == pytest.approx(original.mass_kg, rel=1.0e-12)
    assert math.isclose(copy.total_area_m2, original.total_area_m2, rel_tol=1.0e-12)


def test_the_fold_sense_is_geometry() -> None:
    """The sense of a fold is geometry: flipping it changes the solid.

    This is the discriminating check of the defect: with the sense lost, the
    restored genome would fold into the *flipped* solid instead of the
    original one.
    """
    genome = families_genome()
    restored = og.genome_from_dict(og.genome_payload(genome))
    flipped = og.OrigamiGenome(
        steps=[
            replace(
                step,
                mountain=not step.mountain,
                fold_angle_deg=-step.fold_angle_deg,
            )
            for step in genome.steps
        ],
        sheet=list(genome.sheet),
        key=genome.key,
        label=genome.label,
        seed=genome.seed,
        strategy=genome.strategy,
    )

    original = og.fold_genome(genome)
    assert og.fold_genome(restored).cg == pytest.approx(original.cg, rel=0.0, abs=1.0e-12)
    # The flipped programme is a different aeroplane: the restored genome is
    # therefore not the flipped one.
    assert og.fold_genome(flipped).cg != pytest.approx(original.cg, rel=0.0, abs=1.0e-12)


# ---------------------------------------------------------------------------
# 3. an unknown fold type is rejected
# ---------------------------------------------------------------------------
def test_an_unknown_fold_type_is_rejected() -> None:
    """An unknown sense must not be read as a mountain fold."""
    payload = {
        "steps": [
            {
                "step_index": 1,
                "crease_line": [[0.0, 0.0], [1.0, 1.0]],
                "fold_angle_deg": -30.0,
                "fold_type": "sideways",
            }
        ]
    }
    with pytest.raises(ValueError) as excinfo:
        og.genome_from_dict(payload)
    message = str(excinfo.value)
    assert "sideways" in message
    assert "mountain" in message and "valley" in message


def test_a_fold_type_that_is_neither_a_name_nor_a_boolean_is_rejected() -> None:
    """A malformed field is an error, not a silent mountain fold."""
    payload = {
        "steps": [
            {
                "step_index": 1,
                "crease_line": [[0.0, 0.0], [1.0, 1.0]],
                "fold_angle_deg": -30.0,
                "fold_type": 3,
            }
        ]
    }
    with pytest.raises(ValueError):
        og.genome_from_dict(payload)


# ---------------------------------------------------------------------------
# 4. the GA checkpoint restores the valley folds
# ---------------------------------------------------------------------------
def test_a_checkpoint_restores_the_valley_folds() -> None:
    """A checkpointed candidate has to resume with the folds it was saved with.

    :meth:`physics.ga_domain.Individual.from_dict` rebuilds the genome with
    :func:`physics.origami_genome.genome_from_dict`, so a checkpoint that
    contains valley folds used to resume with every fold flipped.
    """
    from physics import ga_domain

    genome = families_genome()
    individual = ga_domain.Individual(
        index=3,
        genome=genome,
        genes={"speed_mps": 11.0},
        throw=ga_domain.ThrowParams(speed_mps=11.0),
    )

    restored = ga_domain.Individual.from_dict(individual.to_dict())

    assert len(restored.genome.steps) == len(genome.steps)
    for original, copy in zip(genome.steps, restored.genome.steps):
        assert copy.mountain == original.mountain
        assert copy.angle_magnitude_deg == pytest.approx(
            original.angle_magnitude_deg, rel=0.0, abs=1.0e-12
        )
        assert copy.fold_angle_deg == pytest.approx(
            original.fold_angle_deg, rel=1.0e-12, abs=1.0e-12
        )
        assert_sense_is_consistent(copy)

    # The resumed candidate folds into the same aeroplane as the saved one.
    assert og.fold_genome(restored.genome).cg == pytest.approx(
        og.fold_genome(genome).cg, rel=0.0, abs=1.0e-12
    )
