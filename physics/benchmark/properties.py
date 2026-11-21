"""Physical and aerodynamic properties extraction for v3 airframes."""

from __future__ import annotations

import math
from typing import Any, Optional, Tuple

from core.interfaces import AirframeGeometry, BehaviorDescriptor


from geometry.v3_origami.adapter import extract_airframe_geometry  # noqa: F401


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
