"""Aeroelastic deformation, unsteady lag, and dynamic stall hysteresis states.

Manages 1st-order aeroelastic deformation lags (dihedral and twist), unsteady circulation (Wagner/Theodorsen),
and low-Reynolds laminar separation bubble (LSB) dynamic stall hysteresis.
"""

from __future__ import annotations

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
        return cls(
            aero_dihedral_lag=float(data.get("_aero_dihedral_lag", data.get("aero_dihedral_lag", 0.0))),
            aero_twist_lag=float(data.get("_aero_twist_lag", data.get("aero_twist_lag", 0.0))),
            unsteady_cl_lag=float(data.get("_unsteady_cl_lag", data.get("unsteady_cl_lag", 0.0))),
            prev_alpha_deg=float(data.get("_prev_alpha_deg", data.get("prev_alpha_deg", 0.0))),
            alpha_dot_deg_s=float(data.get("_alpha_dot_deg_s", data.get("alpha_dot_deg_s", 0.0))),
            lsb_shift_deg=float(data.get("_lsb_shift_deg", data.get("lsb_shift_deg", 0.0))),
            is_stalled_prev=bool(data.get("_is_stalled_prev", data.get("is_stalled_prev", False))),
            last_dt=float(data.get("_last_dt", data.get("last_dt", 0.01))),
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
        if "_aero_dihedral_lag" in lag_states or "aero_dihedral_lag" in lag_states:
            self.state.aero_dihedral_lag = float(lag_states.get("_aero_dihedral_lag", lag_states.get("aero_dihedral_lag", self.state.aero_dihedral_lag)))
        if "_aero_twist_lag" in lag_states or "aero_twist_lag" in lag_states:
            self.state.aero_twist_lag = float(lag_states.get("_aero_twist_lag", lag_states.get("aero_twist_lag", self.state.aero_twist_lag)))
        if "_prev_alpha_deg" in lag_states or "prev_alpha_deg" in lag_states:
            self.state.prev_alpha_deg = float(lag_states.get("_prev_alpha_deg", lag_states.get("prev_alpha_deg", self.state.prev_alpha_deg)))
        if "_alpha_dot_deg_s" in lag_states or "alpha_dot_deg_s" in lag_states:
            self.state.alpha_dot_deg_s = float(lag_states.get("_alpha_dot_deg_s", lag_states.get("alpha_dot_deg_s", self.state.alpha_dot_deg_s)))
        if "_is_stalled_prev" in lag_states or "is_stalled_prev" in lag_states:
            self.state.is_stalled_prev = bool(lag_states.get("_is_stalled_prev", lag_states.get("is_stalled_prev", self.state.is_stalled_prev)))
        if "_lsb_shift_deg" in lag_states or "lsb_shift_deg" in lag_states:
            self.state.lsb_shift_deg = float(lag_states.get("_lsb_shift_deg", lag_states.get("lsb_shift_deg", self.state.lsb_shift_deg)))
        if "_unsteady_cl_lag" in lag_states or "unsteady_cl_lag" in lag_states:
            self.state.unsteady_cl_lag = float(lag_states.get("_unsteady_cl_lag", lag_states.get("unsteady_cl_lag", self.state.unsteady_cl_lag)))
        if "_last_dt" in lag_states or "last_dt" in lag_states:
            self.state.last_dt = float(lag_states.get("_last_dt", lag_states.get("last_dt", self.state.last_dt)))
