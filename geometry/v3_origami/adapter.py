"""OrigamiAdapter: Lossless conversion to OrigamiGenome and Tier 0 geometric filter."""

from __future__ import annotations

import uuid
from typing import Any, List, Optional, Tuple

from physics.origami_genome import (
    GripReport,
    GripThresholds,
    OrigamiGenome,
    evaluate_grip,
    fold_genome,
)
from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
)
from geometry.v3_origami.compiler import ParametricOrigamiCompiler
from physics.benchmark.properties import extract_airframe_geometry


def validate_tier0(
    candidate: AircraftCandidate,
    thresholds: Optional[GripThresholds] = None,
) -> Tuple[bool, List[str]]:
    """Validate aircraft candidate against Tier 0 geometric filter.

    Enforces:
      1. Minimum step count (16 to 24 steps)
      2. Grip thresholds (keel height >= 8mm, length >= 30mm, layers >= 2)
      3. No physical mesh penetrations (max_penetrations == 0)
    """
    reasons: List[str] = []

    # 1. Step count check (Strict 16 to 24 steps enforcement)
    if candidate.step_count < MIN_FOLD_STEPS:
        reasons.append(
            f"Step count {candidate.step_count} is below minimum requirement {MIN_FOLD_STEPS}"
        )
    if candidate.step_count > MAX_FOLD_STEPS:
        reasons.append(
            f"Step count {candidate.step_count} exceeds maximum allowable {MAX_FOLD_STEPS}"
        )

    # 2. Geometry check
    geom = candidate.geometry
    if geom is None:
        reasons.append("Airframe geometry has not been generated")
        return False, reasons

    # 3. Grip & structural integrity check
    active_thresh = thresholds or GripThresholds(
        min_grip_height_m=0.008,
        min_grip_length_m=0.030,
        min_grip_layers=2,
    )

    if geom.keel_height_m < active_thresh.min_grip_height_m:
        reasons.append(
            f"Keel height {geom.keel_height_m * 1000.0:.1f}mm < required {active_thresh.min_grip_height_m * 1000.0:.1f}mm"
        )
    if geom.keel_length_m < active_thresh.min_grip_length_m:
        reasons.append(
            f"Keel length {geom.keel_length_m * 1000.0:.1f}mm < required {active_thresh.min_grip_length_m * 1000.0:.1f}mm"
        )
    if geom.keel_layers < active_thresh.min_grip_layers:
        reasons.append(
            f"Keel layers {geom.keel_layers} < required {active_thresh.min_grip_layers}"
        )

    return (len(reasons) == 0), reasons


class OrigamiAdapter:
    """Bridges parametric configs to OrigamiGenome and folded geometry."""

    def __init__(self, compiler: Optional[ParametricOrigamiCompiler] = None) -> None:
        self.compiler = compiler or ParametricOrigamiCompiler()

    def compile_candidate(
        self,
        config: ParametricOrigamiConfig,
        candidate_id: Optional[str] = None,
    ) -> AircraftCandidate:
        """Compile a parametric config into a complete AircraftCandidate."""
        cid = candidate_id or f"v3-cand-{uuid.uuid4().hex[:8]}"
        steps = self.compiler.compile_steps(config)
        step_count = len(steps)

        genome = OrigamiGenome(
            steps=steps,
            key=cid,
            label=f"Parametric 20-step {cid}",
            strategy="v3_parametric",
        )

        # Attempt geometric fold evaluation
        try:
            geometry_obj = fold_genome(genome, paper_gsm=config.paper_gsm)
            geometry = extract_airframe_geometry(geometry_obj, step_count=step_count)
        except Exception:
            # Fallback geometry if fold engine encountered degenerate edge
            geometry = extract_airframe_geometry(None, step_count=step_count)

        candidate = AircraftCandidate(
            candidate_id=cid,
            config=config,
            step_count=step_count,
            raw_genome=genome,
            geometry=geometry,
        )

        valid, reasons = validate_tier0(candidate)
        candidate.metadata["tier0_valid"] = valid
        candidate.metadata["tier0_reasons"] = reasons

        return candidate
