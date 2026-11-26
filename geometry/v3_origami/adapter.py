"""OrigamiAdapter: Lossless conversion to OrigamiGenome and strict Tier 0 filter."""

from __future__ import annotations

import math
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.interfaces import (
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    AircraftCandidate,
    AirframeGeometry,
    ParametricOrigamiConfig,
)
from geometry.origami_domain import FoldedFace, Vec3
from geometry.origami_genome import (
    GripThresholds,
    OrigamiGenome,
    _point_in_polygon3d,
    _separated_polygons,
    adjacent_panel_pairs,
    detect_penetrations,
    evaluate_grip,
    fold_genome,
)
from geometry.v3_origami.compiler import (
    ParametricOrigamiCompiler,
    validate_crease_pattern_physics,
)


def _segment_pierces_polygon_3d(
    p0: Vec3,
    p1: Vec3,
    polygon: Sequence[Vec3],
    normal: Vec3,
    eps: float = 1.0e-6,
) -> bool:
    """Return True if segment (p0, p1) strictly pierces the interior of planar polygon."""
    if len(polygon) < 3:
        return False
    origin = polygon[0]
    d0 = (
        normal[0] * (p0[0] - origin[0])
        + normal[1] * (p0[1] - origin[1])
        + normal[2] * (p0[2] - origin[2])
    )
    d1 = (
        normal[0] * (p1[0] - origin[0])
        + normal[1] * (p1[1] - origin[1])
        + normal[2] * (p1[2] - origin[2])
    )
    if (d0 > eps and d1 > eps) or (d0 < -eps and d1 < -eps):
        return False
    denom = d0 - d1
    if abs(denom) <= 1.0e-12:
        return False
    t = d0 / denom
    if t <= 1.0e-4 or t >= 1.0 - 1.0e-4:
        return False
    hit_pt: Vec3 = (
        p0[0] + t * (p1[0] - p0[0]),
        p0[1] + t * (p1[1] - p0[1]),
        p0[2] + t * (p1[2] - p0[2]),
    )
    return _point_in_polygon3d(hit_pt, polygon, normal, eps)


def detect_strict_self_intersections(
    faces: Sequence[FoldedFace],
    adjacency: Optional[Iterable[Tuple[int, int]]] = None,
    tolerance: float = 1.0e-6,
    polygons: Optional[Dict[int, List[Vec3]]] = None,
) -> List[Dict[str, Any]]:
    """Detect both vertex-containment and 3D edge-piercing-face (X-crossing) penetrations."""
    hits = detect_penetrations(
        faces,
        adjacency=adjacency,
        tolerance=tolerance,
        polygons=polygons,
    )
    seen_pairs = {
        (min(int(h["face_a"]), int(h["face_b"])), max(int(h["face_a"]), int(h["face_b"])))
        for h in hits
    }
    adjacent = {
        (min(int(a), int(b)), max(int(a), int(b)))
        for a, b in (adjacency or ())
    }

    count = len(faces)
    for i in range(count):
        fi = faces[i]
        pi = list(fi.poly3d)
        if len(pi) < 3:
            continue
        for j in range(i + 1, count):
            fj = faces[j]
            pair_key = (min(fi.fid, fj.fid), max(fi.fid, fj.fid))
            if pair_key in adjacent or pair_key in seen_pairs:
                continue
            pj = list(fj.poly3d)
            if len(pj) < 3:
                continue

            pierced = False
            for k in range(len(pi)):
                if _segment_pierces_polygon_3d(pi[k], pi[(k + 1) % len(pi)], pj, fj.normal, tolerance):
                    pierced = True
                    break
            if not pierced:
                for k in range(len(pj)):
                    if _segment_pierces_polygon_3d(pj[k], pj[(k + 1) % len(pj)], pi, fi.normal, tolerance):
                        pierced = True
                        break

            if pierced:
                seen_pairs.add(pair_key)
                hits.append(
                    {
                        "face_a": int(fi.fid),
                        "face_b": int(fj.fid),
                        "group_a": fi.group,
                        "group_b": fj.group,
                        "type": "edge_piercing_face",
                    }
                )
    return hits


def _compute_true_silhouette_area_m2(
    faces: Sequence[FoldedFace],
    cell_m: float = 1.5e-3,
) -> float:
    """Compute non-overlapping horizontal (x-z) silhouette planform area via scanline."""
    if not faces:
        return 0.0
    xs: List[float] = []
    zs: List[float] = []
    polys: List[List[Tuple[float, float]]] = []
    for face in faces:
        if len(face.poly3d) < 3:
            continue
        p2 = [(float(pt[0]), float(pt[2])) for pt in face.poly3d]
        polys.append(p2)
        for px, pz in p2:
            xs.append(px)
            zs.append(pz)

    if not polys:
        return 0.0

    min_x, max_x = min(xs), max(xs)
    min_z, max_z = min(zs), max(zs)
    nx = max(1, min(400, int(math.ceil((max_x - min_x) / cell_m))))
    nz = max(1, min(400, int(math.ceil((max_z - min_z) / cell_m))))
    dx = max((max_x - min_x) / nx, 1.0e-6)
    dz = max((max_z - min_z) / nz, 1.0e-6)

    covered_cells = 0
    for row in range(nz):
        z_line = min_z + (row + 0.5) * dz
        intervals: List[Tuple[float, float]] = []
        for poly in polys:
            crossings: List[float] = []
            m = len(poly)
            for k in range(m):
                ax, az = poly[k]
                bx, bz = poly[(k + 1) % m]
                if (az <= z_line < bz) or (bz <= z_line < az):
                    t = (z_line - az) / (bz - az)
                    crossings.append(ax + t * (bx - ax))
            if len(crossings) >= 2:
                crossings.sort()
                for idx in range(0, len(crossings) - 1, 2):
                    intervals.append((crossings[idx], crossings[idx + 1]))

        if not intervals:
            continue

        intervals.sort()
        merged_lo, merged_hi = intervals[0]
        row_len = 0.0
        for lo, hi in intervals[1:]:
            if lo <= merged_hi:
                merged_hi = max(merged_hi, hi)
            else:
                row_len += max(0.0, merged_hi - merged_lo)
                merged_lo, merged_hi = lo, hi
        row_len += max(0.0, merged_hi - merged_lo)
        covered_cells += int(round(row_len / dx))

    return max(1.0e-5, covered_cells * dx * dz)


def extract_airframe_geometry(
    geometry_obj: Any,
    step_count: int = 20,
    default_mass_kg: float = 0.005,
    thickness_limit_ok: bool = True,
    flat_foldable_ok: bool = True,
) -> AirframeGeometry:
    """Extract physical dimensions, true silhouette planform, and penetration counts."""
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
            fold_valid=False,
            thickness_limit_ok=thickness_limit_ok,
            flat_foldable_ok=flat_foldable_ok,
            nose_tip_x_m=0.1485,
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
        raise ValueError(f"Unexpected geometry_obj type: {type(geometry_obj)}")

    nose_tip_x = 0.1485
    penetrations_count = 0
    planform_area = max(1.0e-5, float(getattr(geometry_obj, "planform_area_m2", 0.0)))
    fold_valid = True
    keel_height = 0.0
    keel_length = 0.0
    keel_layers = 0

    if hasattr(geometry_obj, "faces") and hasattr(geometry_obj, "pattern"):
        faces = geometry_obj.faces
        if faces:
            nose_tip_x = max(float(pt[0]) for f in faces for pt in f.poly3d)
            planform_area = _compute_true_silhouette_area_m2(faces)
            adj = adjacent_panel_pairs(geometry_obj.pattern)
            sep_polys = _separated_polygons(geometry_obj)
            strict_hits = detect_strict_self_intersections(faces, adjacency=adj, polygons=sep_polys)
            penetrations_count = len(strict_hits)
            try:
                report = evaluate_grip(geometry_obj, penetrations=strict_hits)
                keel_height = float(report.grip_height_m)
                keel_length = float(report.grip_length_m)
                keel_layers = int(report.grip_layers)
            except Exception:
                keel_height = 0.0
                keel_length = 0.0
                keel_layers = 0
                fold_valid = False
        else:
            keel_height = 0.0
            keel_length = 0.0
            keel_layers = 0
            fold_valid = False

    if planform_area <= 1.0e-5:
        planform_area = max(1.0e-5, min(wing_area, span * chord * 0.65))
    safe_area = max(1.0e-5, wing_area)
    aspect_ratio = (span * span) / safe_area
    planform_ar = (span * span) / planform_area

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
        penetrations=penetrations_count,
        fold_valid=fold_valid,
        thickness_limit_ok=thickness_limit_ok,
        flat_foldable_ok=flat_foldable_ok,
        nose_tip_x_m=nose_tip_x,
    )


def validate_tier0(
    candidate: AircraftCandidate,
    thresholds: Optional[GripThresholds] = None,
) -> Tuple[bool, List[str]]:
    """Validate aircraft candidate against strict Tier 0 geometric & physics filter."""
    reasons: List[str] = []
    if candidate.step_count < MIN_FOLD_STEPS:
        reasons.append(
            f"Step count {candidate.step_count} is below minimum requirement {MIN_FOLD_STEPS}"
        )
    if candidate.step_count > MAX_FOLD_STEPS:
        reasons.append(
            f"Step count {candidate.step_count} exceeds maximum allowable {MAX_FOLD_STEPS}"
        )

    geom = candidate.geometry
    if geom is None:
        reasons.append("Airframe geometry has not been generated")
        return False, reasons

    if not geom.fold_valid:
        reasons.append("Folded solid marked invalid")

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
    if not bool(getattr(geom, "thickness_limit_ok", True)):
        reasons.append("Paper thickness bend-radius limit (Gallivan bound) exceeded")
    if not bool(getattr(geom, "flat_foldable_ok", True)):
        reasons.append("Flat-foldability theorem violation (Kawasaki/Maekawa)")

    try:
        dims = {
            "mass_kg": geom.mass_kg,
            "wing_area_m2": geom.wing_area_m2,
            "planform_area_m2": geom.planform_area_m2,
            "wingspan_m": geom.wingspan_m,
            "chord_m": geom.chord_m,
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
            if not (float(dims["wing_area_m2"]) > 0.0):
                reasons.append("Degenerate wing_area_m2 (must be > 0)")
            if not (float(dims["wingspan_m"]) > 0.0):
                reasons.append("Degenerate wingspan_m (must be > 0)")
            if not (float(dims["chord_m"]) > 0.0):
                reasons.append("Degenerate chord_m (must be > 0)")
            if not (float(dims["planform_area_m2"]) > 1.0e-5):
                reasons.append("Degenerate planform_area_m2 (must be > 1.0e-5)")
    except (TypeError, ValueError, AttributeError):
        reasons.append("Non-finite or degenerate physical geometry dimensions")

    # 4. Mesh penetration check (self-intersection)
    # Use the penetration count from the grip report or detect_penetrations if available
    if candidate.raw_genome is not None:
        try:
            genome = candidate.raw_genome
            if hasattr(genome, "faces") and genome.faces is not None:
                if isinstance(getattr(getattr(genome, "pattern", None), "faces", None), (list, tuple)):
                    adj = adjacent_panel_pairs(genome.pattern)
                    sep = _separated_polygons(genome)
                    hits = detect_penetrations(genome.faces, adjacency=adj, polygons=sep)
                else:
                    hits = detect_penetrations(genome.faces)
                if len(hits) > 0:
                    reasons.append(
                        f"Mesh self-penetration detected: {len(hits)} face pairs intersect"
                    )
            else:
                geometry_obj = fold_genome(genome, paper_gsm=getattr(candidate.config, "paper_gsm", 100.0))
                report = evaluate_grip(geometry_obj)
                p_raw = getattr(report, "penetrations", None)
                p_invalid = False
                p_count: Optional[int] = None
                if p_raw is None or isinstance(p_raw, bool):
                    p_invalid = True
                else:
                    try:
                        p_f = float(p_raw)
                    except (TypeError, ValueError):
                        p_invalid = True
                        p_f = None
                    if p_f is not None:
                        if not math.isfinite(p_f) or p_f < 0.0 or p_f != int(p_f):
                            p_invalid = True
                        else:
                            p_count = int(p_f)
                if p_invalid:
                    reasons.append(
                        f"Invalid penetrations from grip report: {p_raw!r}"
                    )
                elif p_count is not None and p_count > 0:
                    reasons.append(
                        f"Mesh self-penetration detected: {p_count} face pairs intersect"
                    )
        except Exception:
            reasons.append("Penetration re-validation failed during mesh evaluation")

    return (len(reasons) == 0), reasons


class OrigamiAdapter:
    """Bridges parametric configs to OrigamiGenome and strictly audited folded geometry."""

    def __init__(self, compiler: Optional[ParametricOrigamiCompiler] = None) -> None:
        self.compiler = compiler or ParametricOrigamiCompiler()

    def compile_candidate(
        self,
        config: ParametricOrigamiConfig,
        candidate_id: Optional[str] = None,
        raise_on_failure: bool = False,
        include_macro: bool = True,
        **kwargs: Any,
    ) -> AircraftCandidate:
        """Compile a parametric config into a strictly validated AircraftCandidate."""
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

        geometry: Optional[AirframeGeometry] = None
        fold_failed = False
        fold_error = None

        try:
            geometry_obj = fold_genome(genome, paper_gsm=config.paper_gsm)
            geometry = extract_airframe_geometry(
                geometry_obj,
                step_count=step_count,
                thickness_limit_ok=foldability.thickness_limit_ok,
                flat_foldable_ok=foldability.flat_foldable_ok,
            )
        except Exception as e:
            if raise_on_failure:
                raise RuntimeError(f"Fold evaluation failed for {cid}: {e}") from e
            fold_failed = True
            fold_error = str(e)
            geometry = None

        candidate = AircraftCandidate(
            candidate_id=cid,
            config=config,
            step_count=step_count,
            raw_genome=genome,
            geometry=geometry,
        )
        candidate.metadata["foldability_reasons"] = list(foldability.reasons)
        if fold_failed:
            candidate.metadata["fold_failed"] = True
            candidate.metadata["fold_error"] = fold_error

        valid, reasons = validate_tier0(candidate)
        if fold_failed:
            valid = False
            reasons.append(f"Fold engine failed for {cid}: {fold_error}")
        candidate.metadata["tier0_valid"] = valid
        candidate.metadata["tier0_reasons"] = reasons

        try:
            from .kernel_25d import compile_macro_origami
            macro_steps, _ = compile_macro_origami(config)
            candidate.metadata["macro_step_count"] = len(macro_steps)
            candidate.metadata["macro_steps"] = [
                {
                    "step_index": s.step_index,
                    "name": s.name,
                    "fold_type": s.fold_type,
                    "angle_deg": s.angle_deg,
                    "mountain": s.mountain,
                    "desc": s.desc,
                }
                for s in macro_steps
            ]
        except Exception:
            pass

        return candidate

    def validate_tier0(
        self,
        candidate: AircraftCandidate,
        thresholds: Optional[GripThresholds] = None,
    ) -> Tuple[bool, List[str]]:
        """Validate candidate against Tier 0 filters."""
        return validate_tier0(candidate, thresholds=thresholds)
