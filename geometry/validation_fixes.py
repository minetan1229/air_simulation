"""Corrected geometric validation functions for the origami pipeline.

This module provides fixed implementations for:
1. Kawasaki/Maekawa theorem validation for flat-foldable vertices
2. Accurate 3D polygon-polygon intersection detection (including cross-penetration and coplanar overlap)
3. Correct layer depth calculation (fixing 180-degree flip sign bug)
4. Area-weighted 3D centroid calculation
5. FRD coordinate system inertia tensor transformation
6. Orthotropic stiffness using Classical Lamination Theory (4th-order compliance tensor)
7. Inextensible elastica cantilever tip deflection
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import numpy as np

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except Exception:
    HAS_SHAPELY = False

Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]


# ---------------------------------------------------------------------------
# 1. Kawasaki & Maekawa theorem validation (flat-foldable interior vertex)
# ---------------------------------------------------------------------------
def validate_flat_foldable_vertex(
    sector_angles_rad: Sequence[float],
    assignments: Sequence[int],  # +1: Mountain, -1: Valley
    tol_rad: float = 1.0e-3,
) -> Tuple[bool, List[str]]:
    """Validate flat-foldability of an interior vertex.

    Checks:
    - Developability: sum of sector angles = 2π
    - Kawasaki theorem: sum of even-indexed angles = sum of odd-indexed angles = π
    - Maekawa theorem: |M - V| = 2
    """
    errors: List[str] = []
    n = len(sector_angles_rad)
    if n < 4 or n % 2 != 0:
        errors.append(f"flat-foldable interior vertex requires even degree >= 4, got {n}")
        return False, errors
    if len(assignments) != n:
        errors.append("assignments length must match sector_angles_rad")
        return False, errors

    total_angle = sum(sector_angles_rad)
    if abs(total_angle - 2.0 * math.pi) > tol_rad:
        errors.append(f"non-developable vertex: sum(theta)={math.degrees(total_angle):.2f} deg != 360")

    # Kawasaki: sum of alternating angles = π (180 degrees)
    # For flat-foldable vertex with even number of creases:
    # sum(theta_0, theta_2, ...) = sum(theta_1, theta_3, ...) = π
    even_sum = sum(sector_angles_rad[i] for i in range(0, n, 2))
    odd_sum = sum(sector_angles_rad[i] for i in range(1, n, 2))
    if abs(even_sum - math.pi) > tol_rad or abs(odd_sum - math.pi) > tol_rad:
        errors.append(f"Kawasaki violation: even sum={math.degrees(even_sum):.2f}, odd sum={math.degrees(odd_sum):.2f}, expected 180 each")

    mv_diff = abs(sum(1 if a > 0 else -1 for a in assignments))
    if mv_diff != 2:
        errors.append(f"Maekawa violation: |M - V|={mv_diff} != 2")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# 2. Accurate 3D polygon-polygon intersection detection
# ---------------------------------------------------------------------------
def _triangulate_convex(poly: Sequence[Vec3]) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Triangulate a convex polygon (fan triangulation from vertex 0)."""
    pts = [np.asarray(p, dtype=np.float64) for p in poly]
    return [(pts[0], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1)]


def _segment_triangle_intersect(
    p0: np.ndarray, p1: np.ndarray,
    v0: np.ndarray, v1: np.ndarray, v2: np.ndarray,
    eps: float = 1.0e-8,
) -> bool:
    """Möller–Trumbore ray-triangle intersection for segment (p0,p1) vs triangle (v0,v1,v2).

    Returns True iff the segment strictly pierces the triangle interior
    (not at edges/vertices, which are legitimate crease contacts).
    """
    d = p1 - p0
    e1 = v1 - v0
    e2 = v2 - v0
    h = np.cross(d, e2)
    det = float(np.dot(e1, h))
    if abs(det) <= eps:
        return False
    inv_det = 1.0 / det
    s = p0 - v0
    u = float(np.dot(s, h)) * inv_det
    if u <= eps or u >= 1.0 - eps:
        return False
    q = np.cross(s, e1)
    v = float(np.dot(d, q)) * inv_det
    if v <= eps or u + v >= 1.0 - eps:
        return False
    t = float(np.dot(e2, q)) * inv_det
    return eps < t < 1.0 - eps


def _coplanar_polys_strict_overlap(
    poly_a: Sequence[Vec3], poly_b: Sequence[Vec3], normal: np.ndarray, eps: float = 1.0e-7
) -> bool:
    """Check strict area overlap of two coplanar convex polygons (SAT).

    Edge contacts (shared crease) are excluded by requiring positive overlap
    on all separating axes with epsilon margin.
    """
    n = normal / max(float(np.linalg.norm(normal)), 1.0e-12)
    # Build orthonormal basis in the plane
    helper = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(n, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    a2 = np.array([[float(np.dot(p, e1)), float(np.dot(p, e2))] for p in poly_a])
    b2 = np.array([[float(np.dot(p, e1)), float(np.dot(p, e2))] for p in poly_b])
    for poly in (a2, b2):
        m = len(poly)
        for i in range(m):
            edge = poly[(i + 1) % m] - poly[i]
            axis = np.array([-edge[1], edge[0]])
            norm = float(np.linalg.norm(axis))
            if norm <= 1.0e-12:
                continue
            axis /= norm
            pa = a2 @ axis
            pb = b2 @ axis
            if float(pa.max()) <= float(pb.min()) + eps or float(pb.max()) <= float(pa.min()) + eps:
                return False
    return True


def panels_intersect_3d(
    poly_a: Sequence[Vec3], normal_a: Vec3,
    poly_b: Sequence[Vec3], normal_b: Vec3,
    eps: float = 1.0e-6,
) -> bool:
    """Detect 3D penetration between two panels.

    Catches:
    - Non-coplanar cross-penetration (edge piercing triangle)
    - Coplanar strict area overlap (+ cross overlap, not just edge contact)
    """
    na = np.asarray(normal_a, dtype=np.float64)
    nb = np.asarray(normal_b, dtype=np.float64)

    # 1. Coplanar check
    if abs(float(np.dot(na, nb))) > 0.9999:
        plane_dist = abs(float(np.dot(np.asarray(poly_b[0]) - np.asarray(poly_a[0]), na)))
        if plane_dist <= eps:
            return _coplanar_polys_strict_overlap(poly_a, poly_b, na, eps=eps)
        return False

    # 2. Non-coplanar 3D penetration: segment vs triangle (both directions)
    tris_a = _triangulate_convex(poly_a)
    tris_b = _triangulate_convex(poly_b)
    pts_a = [np.asarray(p, dtype=np.float64) for p in poly_a]
    pts_b = [np.asarray(p, dtype=np.float64) for p in poly_b]

    for i in range(len(pts_a)):
        p0, p1 = pts_a[i], pts_a[(i + 1) % len(pts_a)]
        if any(_segment_triangle_intersect(p0, p1, v0, v1, v2) for v0, v1, v2 in tris_b):
            return True
    for i in range(len(pts_b)):
        p0, p1 = pts_b[i], pts_b[(i + 1) % len(pts_b)]
        if any(_segment_triangle_intersect(p0, p1, v0, v1, v2) for v0, v1, v2 in tris_a):
            return True
    return False


# ---------------------------------------------------------------------------
# 3. Layer depth sign fix & area-weighted CG / FRD inertia transform
# ---------------------------------------------------------------------------
def cluster_signed_depth(centroid: Vec3, lead_centroid: Vec3, ref_normal: Vec3) -> float:
    """Correct signed depth for layer assignment (origami_domain.assign_layers).

    Does NOT flip sign based on face.normal dot ref_normal. The depth is the
    projection of (centroid - lead_centroid) onto the cluster reference normal.
    A face that has been folded 180° (normal = -ref) but sits physically
    above the reference face will have positive depth and be assigned to
    a higher layer index.
    """
    return (
        (centroid[0] - lead_centroid[0]) * ref_normal[0]
        + (centroid[1] - lead_centroid[1]) * ref_normal[1]
        + (centroid[2] - lead_centroid[2]) * ref_normal[2]
    )


def area_weighted_centroid_3d(poly: Sequence[Vec3]) -> Tuple[Vec3, float]:
    """Compute exact area-weighted 3D centroid and area of a planar polygon.

    Uses triangle fan from vertex 0, weighting each triangle's centroid by its area.
    """
    if len(poly) < 3:
        return (0.0, 0.0, 0.0), 0.0
    p0 = np.asarray(poly[0], dtype=np.float64)
    total_a = 0.0
    moment = np.zeros(3, dtype=np.float64)
    for k in range(1, len(poly) - 1):
        p1 = np.asarray(poly[k], dtype=np.float64)
        p2 = np.asarray(poly[k + 1], dtype=np.float64)
        cross = np.cross(p1 - p0, p2 - p0)
        area = 0.5 * float(np.linalg.norm(cross))
        if area > 1.0e-15:
            total_a += area
            moment += area * ((p0 + p1 + p2) / 3.0)
    if total_a <= 1.0e-15:
        return (0.0, 0.0, 0.0), 0.0
    c = moment / total_a
    return (float(c[0]), float(c[1]), float(c[2])), total_a


def origami_to_frd_inertia(tensor_orig: Sequence[Sequence[float]]) -> Dict[str, float]:
    """Transform inertia tensor from origami frame to FRD frame.

    Origami frame: x=forward (nose), y=up, z=span (right wing)
    FRD frame:     x=forward, y=right, z=down

    Rotation: R = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
    I_frd = R @ I_orig @ R.T
    """
    I = np.asarray(tensor_orig, dtype=np.float64)
    R = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]], dtype=np.float64)
    I_frd = R @ I @ R.T
    return {
        "roll_inertia_kgm2": max(float(I_frd[0, 0]), 1.0e-12),   # I_xx (unchanged)
        "pitch_inertia_kgm2": max(float(I_frd[1, 1]), 1.0e-12),  # I_yy = old I_zz
        "yaw_inertia_kgm2": max(float(I_frd[2, 2]), 1.0e-12),    # I_zz = old I_yy
        "inertia_xy_kgm2": float(I_frd[0, 1]),
        "inertia_xz_kgm2": float(I_frd[0, 2]),
        "inertia_yz_kgm2": float(I_frd[1, 2]),
    }


# ---------------------------------------------------------------------------
# 4. Orthotropic stiffness via Classical Lamination Theory (4th-order)
# ---------------------------------------------------------------------------
def orthotropic_spanwise_modulus(
    e_md_pa: float,
    anisotropy_ratio: float,
    grain_angle_deg: float,
    nu_md_cd: float = 0.25,
) -> float:
    """Compute effective Young's modulus in the wing spanwise direction.

    Uses the full 4th-order compliance tensor transformation for orthotropic
    materials (Classical Lamination Theory):
        1/E(φ) = cos⁴φ/E_MD + (1/G_12 - 2ν_12/E_MD) sin²φ cos²φ + sin⁴φ/E_CD

    Args:
        e_md_pa: Young's modulus in machine direction (MD) [Pa]
        anisotropy_ratio: E_MD / E_CD (typically 1.5–2.2)
        grain_angle_deg: Angle of MD relative to aircraft x-axis (forward).
                         0° = MD along fuselage, 90° = MD along span.
        nu_md_cd: Poisson's ratio ν_MD,CD (typical ~0.25)

    Returns:
        Effective Young's modulus in the spanwise (bending) direction [Pa].

    Note: Wing bending stress acts along the span (z-axis in origami frame).
          The angle φ in the formula is the angle between the span direction
          and the material MD direction. If grain_angle_deg=0 (MD along x),
          then φ = 90° for spanwise bending.
    """
    e_cd = e_md_pa / max(anisotropy_ratio, 1.0)
    # Poisson's ratio in the transverse direction
    nu_cd_md = nu_md_cd * (e_cd / e_md_pa)
    # In-plane shear modulus approximation (Campbell)
    g_12 = math.sqrt(e_md_pa * e_cd) / (2.0 * (1.0 + math.sqrt(max(nu_md_cd * nu_cd_md, 0.0))))

    # Spanwise direction is +90° from fuselage forward (x-axis)
    phi = math.radians(grain_angle_deg + 90.0)
    c, s = math.cos(phi), math.sin(phi)
    inv_e = (c**4) / e_md_pa + (1.0 / g_12 - 2.0 * nu_md_cd / e_md_pa) * (s**2) * (c**2) + (s**4) / e_cd
    return 1.0 / max(inv_e, 1.0e-30)


# ---------------------------------------------------------------------------
# 5. Inextensible elastica cantilever tip deflection
# ---------------------------------------------------------------------------
def elastica_cantilever_tip(
    semispan_m: float,
    uniform_line_load_npm: float,
    ei_nm2: float,
    n_steps: int = 80,
) -> Tuple[float, float, float]:
    """Compute tip deflection of an inextensible elastica cantilever.

    Boundary conditions:
    - Root (s=0): θ=0 (clamped)
    - Tip (s=L): M(L)=0, N_x(L)=0 (free end, no axial force)

    The elastica equations ensure inextensibility: sqrt(z_tip² + y_tip²) ≤ L

    Args:
        semispan_m: Half-span length L [m]
        uniform_line_load_npm: Distributed load w [N/m] (lift per unit span)
        ei_nm2: Bending stiffness EI [N·m²]
        n_steps: Integration steps (default 80)

    Returns:
        (z_tip, y_tip, theta_tip_rad) where z is along the original span axis
        and y is the out-of-plane deflection. z_tip < L due to foreshortening.
    """
    L = max(semispan_m, 1.0e-6)
    ei = max(ei_nm2, 1.0e-15)
    ds = L / n_steps
    theta = 0.0
    z = 0.0
    y = 0.0
    for i in range(n_steps):
        s_mid = (i + 0.5) * ds
        # Bending moment for uniform load on cantilever: M(s) = 0.5 * w * (L - s)²
        moment = 0.5 * uniform_line_load_npm * (L - s_mid) ** 2
        kappa = moment / ei
        # Midpoint integration for better accuracy
        theta_mid = min(theta + 0.5 * kappa * ds, math.pi * 0.499)
        z += math.cos(theta_mid) * ds
        y += math.sin(theta_mid) * ds
        theta = min(theta + kappa * ds, math.pi * 0.499)
    return z, y, theta


# ---------------------------------------------------------------------------
# 6. Planform area with overlap removal (silhouette projection)
# ---------------------------------------------------------------------------
def planform_area_silhouette(faces: Sequence[Any]) -> float:
    """Compute true silhouette (projected) planform area by union of XZ projections.

    Uses shapely if available for exact polygon union, otherwise falls back to
    a grid-based approximation. This avoids double-counting overlapping layers.
    """
    if HAS_SHAPELY:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        polys = []
        for face in faces:
            if len(face.poly3d) < 3:
                continue
            # Project to XZ plane
            coords = [(p[0], p[2]) for p in face.poly3d]
            poly = Polygon(coords)
            if poly.is_valid and poly.area > 1.0e-12:
                polys.append(poly)
        if not polys:
            return 1.0e-9
        union = unary_union(polys)
        return max(float(union.area), 1.0e-9)
    else:
        return _planform_area_grid(faces)


def _planform_area_grid(faces: Sequence[Any], grid_size: int = 400) -> float:
    """Grid-based silhouette area approximation (fallback without shapely)."""
    if not faces:
        return 1.0e-9
    # Find bounds
    min_x = min(p[0] for f in faces for p in f.poly3d)
    max_x = max(p[0] for f in faces for p in f.poly3d)
    min_z = min(p[2] for f in faces for p in f.poly3d)
    max_z = max(p[2] for f in faces for p in f.poly3d)
    if max_x <= min_x or max_z <= min_z:
        return 1.0e-9
    dx = (max_x - min_x) / grid_size
    dz = (max_z - min_z) / grid_size
    # Track covered cells to avoid double-counting overlapping layers
    covered = [[False] * grid_size for _ in range(grid_size)]
    count = 0
    for face in faces:
        if len(face.poly3d) < 3:
            continue
        poly_2d = [(p[0], p[2]) for p in face.poly3d]
        for ix in range(grid_size):
            x = min_x + (ix + 0.5) * dx
            for iz in range(grid_size):
                if covered[iz][ix]:
                    continue
                z = min_z + (iz + 0.5) * dz
                if _point_in_polygon_2d(x, z, poly_2d):
                    covered[iz][ix] = True
                    count += 1
    return count * dx * dz


def _point_in_polygon_2d(x: float, y: float, poly: Sequence[Tuple[float, float]]) -> bool:
    """Even-odd rule point-in-polygon test."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            inside = not inside
    return inside

