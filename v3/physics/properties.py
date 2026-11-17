"""Physical and aerodynamic properties extraction for v3 airframes."""

from __future__ import annotations

import math
from typing import Any, Optional, Tuple

from v3.interfaces import AirframeGeometry, BehaviorDescriptor


def extract_airframe_geometry(
    geometry_obj: Any,
    step_count: int = 20,
    default_mass_kg: float = 0.005,
) -> AirframeGeometry:
    """Extract physical dimensions and mass properties from folded genome geometry.

    Safely extracts properties whether geometry_obj is a GenomeGeometry instance
    or a dict representation.
    """
    if geometry_obj is None:
        # Fallback conservative geometry
        return AirframeGeometry(
            mass_kg=default_mass_kg,
            wing_area_m2=0.015,
            wingspan_m=0.18,
            chord_m=0.12,
            aspect_ratio=2.16,
            cg_m=(0.08, 0.0, 0.005),
            keel_height_m=0.012,
            keel_length_m=0.060,
            keel_layers=4,
            step_count=step_count,
        )

    # Extract fields if GenomeGeometry object or dict
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

    # Approximate keel dimensions based on sheet proportions and step count
    keel_height = max(0.008, chord * 0.10)
    keel_length = max(0.030, chord * 0.50)
    keel_layers = max(2, total_layers)

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
    )


def compute_behavior_descriptor(
    geometry: AirframeGeometry,
    static_stability: float = 0.5,
) -> BehaviorDescriptor:
    """Compute the 3D behavior descriptor [wing_loading, stability, aspect_ratio]."""
    wing_loading = geometry.mass_kg / max(1.0e-5, geometry.wing_area_m2)
    # Clamp stability into [0, 1]
    clamped_stability = max(0.0, min(1.0, float(static_stability)))

    return BehaviorDescriptor(
        wing_loading_kg_m2=wing_loading,
        static_stability=clamped_stability,
        aspect_ratio=geometry.aspect_ratio,
    )
