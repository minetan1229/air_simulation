"""Core package — canonical types, configuration schemas, and utilities.

This package contains the foundational data contracts and utilities that
all other packages depend on. It has no dependencies on physics, geometry,
optimization, or infrastructure packages.

Modules
-------
config_schema
    Dataclasses for aircraft, environment, launch, simulation settings.
geometry_utils
    Quaternion algebra, Euler conversions, rotation matrices.
payload_contract
    Simulation step payload structure and normalization.
error_domain
    Structured error logging and audit diagnostics.
interfaces
    Abstract interfaces, protocols, and benchmark contracts.
"""

from __future__ import annotations

__all__ = [
    "config_schema",
    "geometry_utils",
    "payload_contract",
    "error_domain",
    "interfaces",
]
