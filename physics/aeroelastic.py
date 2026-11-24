"""Aeroelastic deformation, unsteady lag, and dynamic stall hysteresis states.

Manages 1st-order aeroelastic deformation lags (dihedral and twist), unsteady circulation (Wagner/Theodorsen),
and low-Reynolds laminar separation bubble (LSB) dynamic stall hysteresis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AeroelasticLagState:
    """Snapshot of internal unsteady and deformation lag states."""
    aero_dihedral_lag: float = 0.0
    aero_twist_lag: float = 0.0
    unsteady_cl_lag: float = 0.0
    prev_alpha_deg: float = 0.0
    alpha_dot_deg_s: float = 0.0
    lsb_shift_deg: float = 0.0
    is_stalled_prev: bool = False
    last_dt: float = 0.01

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_aero_dihedral_lag": float(self.aero_dihedral_lag),
            "_aero_twist_lag": float(self.aero_twist_lag),
            "_unsteady_cl_lag": float(self.unsteady_cl_lag),
            "_prev_alpha_deg": float(self.prev_alpha_deg),
            "_alpha_dot_deg_s": float(self.alpha_dot_deg_s),
            "_lsb_shift_deg": float(self.lsb_shift_deg),
            "_is_stalled_prev": bool(self.is_stalled_prev),
            "_last_dt": float(self.last_dt),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AeroelasticLagState:
        def _get_float(key1: str, key2: str, default: float) -> float:
            val = data.get(key1, data.get(key2, default))
            if val is None:
                return default
            try:
                f = float(val)
                return f if math.isfinite(f) else default
            except (ValueError, TypeError):
                return default

        def _get_bool(key1: str, key2: str, default: bool) -> bool:
            val = data.get(key1, data.get(key2, default))
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes")
            return bool(val)

        last_dt = _get_float("_last_dt", "last_dt", 0.01)
        if last_dt <= 0.0:
            last_dt = 0.01

        return cls(
            aero_dihedral_lag=_get_float("_aero_dihedral_lag", "aero_dihedral_lag", 0.0),
            aero_twist_lag=_get_float("_aero_twist_lag", "aero_twist_lag", 0.0),
            unsteady_cl_lag=_get_float("_unsteady_cl_lag", "unsteady_cl_lag", 0.0),
            prev_alpha_deg=_get_float("_prev_alpha_deg", "prev_alpha_deg", 0.0),
            alpha_dot_deg_s=_get_float("_alpha_dot_deg_s", "alpha_dot_deg_s", 0.0),
            lsb_shift_deg=_get_float("_lsb_shift_deg", "lsb_shift_deg", 0.0),
            is_stalled_prev=_get_bool("_is_stalled_prev", "is_stalled_prev", False),
            last_dt=last_dt,
        )


class AeroelasticManager:
    """Orchestrates aeroelastic deformation and unsteady aerodynamic lag states."""

    def __init__(self) -> None:
        self.state = AeroelasticLagState()

    def reset(self) -> None:
        self.state = AeroelasticLagState()

    def snapshot(self, last_dt: Optional[float] = None) -> Dict[str, Any]:
        snap = self.state.to_dict()
        if last_dt is not None:
            snap["_last_dt"] = float(last_dt)
        return snap

    def restore(self, snap: Optional[Dict[str, Any]]) -> None:
        if not snap:
            return
        self.state = AeroelasticLagState.from_dict(snap)

    def apply_states(self, lag_states: Optional[Dict[str, Any]]) -> None:
        if not lag_states:
            return

        def _get_float(key1: str, key2: str, current: float) -> float:
            val = lag_states.get(key1, lag_states.get(key2, current))
            if val is None:
                return current
            try:
                f = float(val)
                return f if math.isfinite(f) else current
            except (ValueError, TypeError):
                return current

        def _get_bool(key1: str, key2: str, current: bool) -> bool:
            val = lag_states.get(key1, lag_states.get(key2, current))
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes")
            return bool(val)

        if "_aero_dihedral_lag" in lag_states or "aero_dihedral_lag" in lag_states:
            self.state.aero_dihedral_lag = _get_float("_aero_dihedral_lag", "aero_dihedral_lag", self.state.aero_dihedral_lag)
        if "_aero_twist_lag" in lag_states or "aero_twist_lag" in lag_states:
            self.state.aero_twist_lag = _get_float("_aero_twist_lag", "aero_twist_lag", self.state.aero_twist_lag)
        if "_prev_alpha_deg" in lag_states or "prev_alpha_deg" in lag_states:
            self.state.prev_alpha_deg = _get_float("_prev_alpha_deg", "prev_alpha_deg", self.state.prev_alpha_deg)
        if "_alpha_dot_deg_s" in lag_states or "alpha_dot_deg_s" in lag_states:
            self.state.alpha_dot_deg_s = _get_float("_alpha_dot_deg_s", "alpha_dot_deg_s", self.state.alpha_dot_deg_s)
        if "_is_stalled_prev" in lag_states or "is_stalled_prev" in lag_states:
            self.state.is_stalled_prev = _get_bool("_is_stalled_prev", "is_stalled_prev", self.state.is_stalled_prev)
        if "_lsb_shift_deg" in lag_states or "lsb_shift_deg" in lag_states:
            self.state.lsb_shift_deg = _get_float("_lsb_shift_deg", "lsb_shift_deg", self.state.lsb_shift_deg)
        if "_unsteady_cl_lag" in lag_states or "unsteady_cl_lag" in lag_states:
            self.state.unsteady_cl_lag = _get_float("_unsteady_cl_lag", "unsteady_cl_lag", self.state.unsteady_cl_lag)
        if "_last_dt" in lag_states or "last_dt" in lag_states:
            dt = _get_float("_last_dt", "last_dt", self.state.last_dt)
            if dt > 0.0:
                self.state.last_dt = dt
