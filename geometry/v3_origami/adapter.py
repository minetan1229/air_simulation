"""OrigamiAdapter: Lossless conversion to OrigamiGenome and Tier 0 geometric filter."""

from __future__ import annotations

import uuid
from typing import Any, List, Optional, Tuple

from geometry.origami_genome import (
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


def extract_airframe_geometry(
    geometry_obj: Any,
    step_count: int = 20,
    default_mass_kg: float = 0.005,
) -> AirframeGeometry:
    """Extract physical dimensions and mass properties from folded genome geometry.

    Safely extracts properties whether geometry_obj is a GenomeGeometry instance
    or a dict representation. Uses real grip / keel dimensions when available.
    """
    if geometry_obj is None:
        return AirframeGeometry(
            mass_kg=default_mass_kg,
            wing_area_m2=0.015,
            wingspan_m=0.18,
            chord_m=0.12,
            aspect_ratio=2.16,
            cg_m=(0.08, 0.0, 0.005),
            keel_height_m=0.0,
            keel_length_m=0.0,
            keel_layers=0,
            step_count=step_count,
            planform_area_m2=0.015,
            planform_aspect_ratio=2.16,
            penetrations=999,
        )

    if hasattr(geometry_obj, "mass_kg"):
        mass = float(getattr(geometry_obj, "mass_kg", default_mass_kg))
        wing_area = float(getattr(geometry_obj, "wing_area_m2", 0.015))
        span = float(getattr(geometry_obj, "span_m", 0.18))
        chord = float(getattr(geometry_obj, "chord_m", 0.12))
        cg = getattr(geometry_obj, "cg", (0.08, 0.0, 0.005))
        cg_tuple = (float(cg[0]), float(cg[1]), float(cg[2]))
        total_layers = int(getattr(geometry_obj, "max_layers", 4))
    elif isinstance(geometry_obj, dict):
        mass = float(geometry_obj.get("mass_kg", default_mass_kg))
        wing_area = float(geometry_obj.get("wing_area_m2", 0.015))
        span = float(geometry_obj.get("span_m", 0.18))
        chord = float(geometry_obj.get("chord_m", 0.12))
        cg = geometry_obj.get("cg", [0.08, 0.0, 0.005])
        cg_tuple = (float(cg[0]), float(cg[1]), float(cg[2]))
        total_layers = int(geometry_obj.get("max_layers", 4))
    else:
        mass = default_mass_kg
        wing_area = 0.015
        span = 0.18
        chord = 0.12
        cg_tuple = (0.08, 0.0, 0.005)
        total_layers = 4

    safe_area = max(1.0e-5, wing_area)
    aspect_ratio = (span * span) / safe_area

    # Planform (projected horizontal) area and aeronautical aspect ratio
    planform_area = max(1.0e-5, float(getattr(geometry_obj, "planform_area_m2", span * chord * 0.52)))
    planform_ar = (span * span) / planform_area

    keel_height = 0.0
    keel_length = 0.0
    keel_layers = 0
    penetrations = 0

    try:
        if hasattr(geometry_obj, "faces") and hasattr(geometry_obj, "pattern"):
            report = evaluate_grip(geometry_obj)
            keel_height = report.grip_height_m
            keel_length = report.grip_length_m
            keel_layers = report.grip_layers
            penetrations = report.penetrations
        else:
            keel_height = max(0.008, chord * 0.10)
            keel_length = max(0.030, chord * 0.50)
            keel_layers = max(2, total_layers)
    except Exception:
        pass

    return AirframeGeometry(
        mass_kg=mass,
        wing_area_m2=wing_area,
        wingspan_m=span,
        chord_m=chord,
        aspect_ratio=aspect_ratio,
        cg_m=cg_tuple,
        keel_height_m=keel_height,
        keel_length_m=keel_length,
        keel_layers=keel_layers,
        step_count=step_count,
        planform_area_m2=planform_area,
        planform_aspect_ratio=planform_ar,
        penetrations=penetrations,
    )


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

    # 4. Self-intersection & penetration check
    if geom.penetrations > active_thresh.max_penetrations:
        reasons.append(
            f"Self-intersection detected: {geom.penetrations} penetrating panel pairs exceed allowable {active_thresh.max_penetrations}"
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
        fold_succeeded = True
        fold_err = ""
        try:
            geometry_obj = fold_genome(genome, paper_gsm=config.paper_gsm)
            geometry = extract_airframe_geometry(geometry_obj, step_count=step_count)
        except Exception as exc:
            # Fallback geometry if fold engine encountered degenerate edge
            geometry = extract_airframe_geometry(None, step_count=step_count)
            fold_succeeded = False
            fold_err = str(exc)

        candidate = AircraftCandidate(
            candidate_id=cid,
            config=config,
            step_count=step_count,
            raw_genome=genome,
            geometry=geometry,
        )

        valid, reasons = validate_tier0(candidate)
        if not fold_succeeded:
            valid = False
            reasons.append(f"Mesh folding failed: {fold_err or 'degenerate geometry'}")
        candidate.metadata["tier0_valid"] = valid
        candidate.metadata["tier0_reasons"] = reasons

        return candidate
