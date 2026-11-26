"""OrigamiAdapter: Lossless conversion to OrigamiGenome and Tier 0 geometric filter."""

from __future__ import annotations

import math
import uuid
from dataclasses import replace
from typing import Any, List, Optional, Tuple

from geometry.origami_genome import (
    GripReport,
    GripThresholds,
    OrigamiGenome,
    adjacent_panel_pairs,
    detect_penetrations,
    evaluate_grip,
    fold_genome,
    _separated_polygons,
)
from geometry.v3_origami.compiler import validate_crease_pattern_physics
from geometry.v3_origami.adapter import (
    _compute_true_silhouette_area_m2,
    detect_strict_self_intersections,
)
from v3.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
)
from v3.origami.compiler import ParametricOrigamiCompiler
from v3.physics.properties import extract_airframe_geometry


def validate_tier0(
    candidate: AircraftCandidate,
    thresholds: Optional[GripThresholds] = None,
) -> Tuple[bool, List[str]]:
    """Validate aircraft candidate against Tier 0 geometric filter.

    Enforces:
      1. Minimum step count (16 to 24 steps)
      2. Grip thresholds (keel height >= 8mm, length >= 30mm, layers >= 2)
      3. No physical mesh penetrations (max_penetrations == 0)
      4. Fold validity and physical dimension sanity
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

    if not getattr(geom, "fold_valid", False):
        reasons.append("Geometry fold_valid is False")

    # 3. Grip & structural integrity check
    active_thresh = thresholds or GripThresholds(
        min_grip_height_m=0.008,
        min_grip_length_m=0.030,
        min_grip_layers=2,
        max_penetrations=0,
    )

    def _invalid_nonneg(value: Any) -> bool:
        if value is None or isinstance(value, bool):
            return True
        try:
            f = float(value)
        except (TypeError, ValueError):
            return True
        if not math.isfinite(f) or f < 0.0:
            return True
        return False

    if _invalid_nonneg(geom.keel_height_m):
        reasons.append(
            f"Invalid keel height value: {geom.keel_height_m!r}"
        )
    if _invalid_nonneg(geom.keel_length_m):
        reasons.append(
            f"Invalid keel length value: {geom.keel_length_m!r}"
        )
    if _invalid_nonneg(geom.keel_layers):
        reasons.append(
            f"Invalid keel layers value: {geom.keel_layers!r}"
        )

    if not _invalid_nonneg(geom.keel_height_m) and geom.keel_height_m < active_thresh.min_grip_height_m:
        reasons.append(
            f"Keel height {geom.keel_height_m * 1000.0:.1f}mm < required {active_thresh.min_grip_height_m * 1000.0:.1f}mm"
        )
    if not _invalid_nonneg(geom.keel_length_m) and geom.keel_length_m < active_thresh.min_grip_length_m:
        reasons.append(
            f"Keel length {geom.keel_length_m * 1000.0:.1f}mm < required {active_thresh.min_grip_length_m * 1000.0:.1f}mm"
        )
    if not _invalid_nonneg(geom.keel_layers) and geom.keel_layers < active_thresh.min_grip_layers:
        reasons.append(
            f"Keel layers {geom.keel_layers} < required {active_thresh.min_grip_layers}"
        )

    # 4. Penetrations check
    pen_raw = getattr(geom, "penetrations", None)
    pen_count: Optional[int] = None
    pen_invalid = False
    if pen_raw is None or isinstance(pen_raw, bool):
        pen_invalid = True
    else:
        try:
            pen_f = float(pen_raw)
        except (TypeError, ValueError):
            pen_invalid = True
            pen_f = None
        if pen_f is not None:
            if not math.isfinite(pen_f) or pen_f < 0.0 or pen_f != int(pen_f):
                pen_invalid = True
            else:
                pen_count = int(pen_f)
    if pen_invalid:
        reasons.append(
            f"Invalid penetrations value: {pen_raw!r}"
        )
    elif pen_count is not None and pen_count > active_thresh.max_penetrations:
        reasons.append(
            f"Self-intersection detected: {pen_count} penetration(s) > max allowed {active_thresh.max_penetrations}"
        )

    # 5. Thickness limit and flat foldable
    if not bool(getattr(geom, "thickness_limit_ok", True)):
        reasons.append("Paper thickness bend-radius limit (Gallivan bound) exceeded")
    if not bool(getattr(geom, "flat_foldable_ok", True)):
        reasons.append("Flat-foldability theorem violation (Kawasaki/Maekawa)")

    # 6. Physical dimension sanity (finite, positive where required)
    try:
        dims = {
            "mass_kg": geom.mass_kg,
            "wing_area_m2": geom.wing_area_m2,
            "planform_area_m2": geom.planform_area_m2,
            "wingspan_m": geom.wingspan_m,
            "chord_m": geom.chord_m,
            "aspect_ratio": geom.aspect_ratio,
        }
        dims_ok = True
        for name, val in dims.items():
            if val is None or isinstance(val, bool):
                dims_ok = False
                reasons.append(f"Invalid {name} value: {val!r}")
                continue
            try:
                fv = float(val)
            except (TypeError, ValueError):
                dims_ok = False
                reasons.append(f"Invalid {name} value: {val!r}")
                continue
            if not math.isfinite(fv):
                dims_ok = False
                reasons.append(f"Non-finite {name} value: {val!r}")
        if dims_ok:
            if not (float(dims["mass_kg"]) > 0.0):
                reasons.append("Degenerate mass_kg (must be > 0)")
            if not (float(dims["wing_area_m2"]) > 1.0e-5):
                reasons.append("Degenerate wing_area_m2 (must be > 1.0e-5)")
            if not (float(dims["wingspan_m"]) > 0.0):
                reasons.append("Degenerate wingspan_m (must be > 0)")
            if not (float(dims["chord_m"]) > 0.0):
                reasons.append("Degenerate chord_m (must be > 0)")
            if not (float(dims["aspect_ratio"]) > 0.0):
                reasons.append("Degenerate aspect_ratio (must be > 0)")
            if not (float(dims["planform_area_m2"]) > 1.0e-5):
                reasons.append("Degenerate planform_area_m2 (must be > 1.0e-5)")
    except (TypeError, ValueError, AttributeError):
        reasons.append("Non-finite or degenerate physical geometry dimensions")

    # 7. Re-check penetrations via detect_penetrations if raw_genome available
    raw_genome = getattr(candidate, "raw_genome", None)
    if raw_genome is not None:
        genome = raw_genome
        try:
            if hasattr(genome, "faces") and genome.faces is not None:
                if isinstance(getattr(getattr(genome, "pattern", None), "faces", None), (list, tuple)):
                    adj = adjacent_panel_pairs(genome.pattern)
                    sep = _separated_polygons(genome)
                    hits = detect_penetrations(genome.faces, adjacency=adj, polygons=sep)
                else:
                    hits = detect_penetrations(genome.faces)
            else:
                paper_gsm_val = float(getattr(candidate.config, "paper_gsm", 100.0))
                geometry_obj = fold_genome(genome, paper_gsm=paper_gsm_val)
                adj = adjacent_panel_pairs(geometry_obj.pattern)
                sep = _separated_polygons(geometry_obj)
                hits = detect_penetrations(geometry_obj.faces, adjacency=adj, polygons=sep)
            if len(hits) > active_thresh.max_penetrations:
                reasons.append(
                    f"Re-check penetrations {len(hits)} > max allowed {active_thresh.max_penetrations}"
                )
        except Exception as e:
            reasons.append(f"Penetration re-check failed: {e}")

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

        raw_nose_fold_count = config.nose.fold_count
        try:
            _fc_f = float(raw_nose_fold_count)
        except (TypeError, ValueError):
            _fc_f = float("nan")
        if math.isfinite(_fc_f):
            clamped_nose_fold_count = int(max(3.0, min(6.0, _fc_f)))
        else:
            clamped_nose_fold_count = 6

        raw_nose_length_ratio = config.nose.nose_length_ratio
        try:
            _nlr_f = float(raw_nose_length_ratio)
        except (TypeError, ValueError):
            _nlr_f = float("nan")
        if math.isfinite(_nlr_f):
            clamped_nose_length_ratio = max(0.20, min(0.35, _nlr_f))
        else:
            clamped_nose_length_ratio = 0.20

        foldability = validate_crease_pattern_physics(
            steps=steps,
            paper_gsm=config.paper_gsm,
            nose_fold_count=clamped_nose_fold_count,
            nose_length_ratio=clamped_nose_length_ratio,
        )

        fold_failed = False
        fold_error = ""
        geometry_obj = None

        try:
            geometry_obj = fold_genome(genome, paper_gsm=config.paper_gsm)
        except Exception as exc:
            fold_failed = True
            fold_error = str(exc)

        candidate = AircraftCandidate(
            candidate_id=cid,
            config=config,
            step_count=step_count,
            raw_genome=genome,
            geometry=None,
        )

        candidate.metadata["foldability_reasons"] = list(foldability.reasons)

        if fold_failed or geometry_obj is None:
            candidate.metadata["fold_failed"] = True
            candidate.metadata["fold_error"] = fold_error
            candidate.metadata["tier0_valid"] = False
            candidate.metadata["tier0_reasons"] = [f"Fold engine failed: {fold_error}"]
            return candidate

        base_geom = extract_airframe_geometry(geometry_obj, step_count=step_count)

        faces = geometry_obj.faces
        adj = adjacent_panel_pairs(geometry_obj.pattern)

        try:
            planform_area = _compute_true_silhouette_area_m2(faces)
        except Exception:
            planform_area = max(
                1.0e-5, min(base_geom.wing_area_m2, base_geom.wingspan_m * base_geom.chord_m * 0.65)
            )

        nose_tip_x = max(
            (float(pt[0]) for f in faces for pt in f.poly3d),
            default=0.1485,
        )

        penetrations = len(detect_strict_self_intersections(faces, adjacency=adj))

        geometry = replace(
            base_geom,
            planform_area_m2=planform_area,
            planform_aspect_ratio=(
                base_geom.wingspan_m**2 / planform_area if planform_area > 0 else base_geom.aspect_ratio
            ),
            nose_tip_x_m=nose_tip_x,
            penetrations=penetrations,
            fold_valid=True,
            thickness_limit_ok=foldability.thickness_limit_ok,
            flat_foldable_ok=foldability.flat_foldable_ok,
        )

        candidate.geometry = geometry

        valid, reasons = validate_tier0(candidate)
        candidate.metadata["tier0_valid"] = valid
        candidate.metadata["tier0_reasons"] = reasons

        return candidate
