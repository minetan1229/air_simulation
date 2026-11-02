"""Stage 2 of the evaluation pipeline: the quasi static fluid structure screen.

The stage asks a question the cheap geometry filter cannot answer: does the
wing *survive* the flight load, or does it buckle, flutter apart or lose its
pitch stability before the aeroplane ever leaves the hand?

The model is a cantilever wing panel of span ``b``, chord ``c`` and thickness
``t``.  The panel is the half wing, so ``b`` is the semi span of the
aeroplane, the distance from the fuselage to the tip:

*buckling*
    The flight load pulls the panel down in compression.  The classical line
    load of a plate that buckles is ``N_cr = k pi^2 D / b^2`` with the plate
    stiffness ``D = E t^3 / (12 (1 - nu^2))``.  The applied line load is the
    aerodynamic load ``q c`` plus the inertial part of the load factor, and
    the margin is the ratio of the two.

*elastic deformation*
    The panel is a cantilever, so the flight load bends it: the tip
    deflection is ``w = q_span b^4 / (8 E I)`` with ``I = c t^3 / 12``.  The
    linear beam theory behind the formula is only valid while the
    deflection stays small, which is exactly what the tip deflection limit
    of the screen watches: a panel that deflects more than a fraction of its
    span has folded onto itself and the case is lost anyway.

*pitch stability*
    The bend twists the panel (the tip turns nose down when the wing rises),
    the twist moves the aerodynamic centre of the wing and the changed lift
    moves the trim point of the aeroplane, so the static margin of the
    deformed wing is smaller than the static margin of the rigid one.  When
    the deformation destroys the static margin the aeroplane diverges in
    pitch and the individual is eliminated.

The screen is *vectorised*: a whole batch of load cases is evaluated with one
array operation on NumPy or CuPy, so the stage can be run on the GPU without
a single branch on a device value.  The host only assembles the reasons of
the finished numbers.

The material model follows the repository: the stiffness of the paper comes
from :func:`physics.stress_domain.effective_paper_stiffness`, which returns
the *normalised* (0..1) bending stiffness of the airframe and not a modulus
in pascals.  The structural model needs pascals, so the normalised value is
mapped onto the reference modulus of paper along the grain, scaled with the
grammage of the sheet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from ..config_schema import AircraftConfig
from ..stress_domain import (
    KAPPA_SIGMA,
    TE_FOLD_CHORD_REDUCTION,
    Z_SCALE_GAIN,
    Z_SCALE_OFFSET,
    effective_paper_stiffness,
    paper_allowable_stress,
    root_bending_moment_batch,
    section_modulus_batch,
)

__all__ = [
    "Stage2Case",
    "Stage2Report",
    "StaticFsiScreener",
    "PAPER_YOUNGS_MODULUS_PA",
    "PAPER_REFERENCE_STIFFNESS",
    "POISSON_RATIO",
    "BUCKLING_FACTOR",
    "MAX_TIP_DEFLECTION_FRACTION",
    "NOMINAL_INCIDENCE_DEG",
    "TWIST_EFFECTIVENESS",
]


#: The Young's modulus of paper along the grain, in pascals.
PAPER_YOUNGS_MODULUS_PA: float = 3.0e9

#: The normalised paper stiffness that corresponds to the reference modulus.
PAPER_REFERENCE_STIFFNESS: float = 0.35

#: The Poisson ratio of paper.
POISSON_RATIO: float = 0.3

#: The buckling factor of a panel whose root is built into the fuselage.
BUCKLING_FACTOR: float = 12.0

#: The largest tip deflection a wing may show, as a fraction of its span.
MAX_TIP_DEFLECTION_FRACTION: float = 0.15

#: The incidence the trim shift of the deformed wing is referred to.
NOMINAL_INCIDENCE_DEG: float = 5.0

#: The lift weighted mean of a linear twist distribution over the span.
TWIST_EFFECTIVENESS: float = 0.2122

#: The standard gravity used for the inertial part of the load.
STANDARD_GRAVITY: float = 9.80665

#: The density of the air used when a case carries a speed but no pressure.
SEA_LEVEL_DENSITY: float = 1.225


# --------------------------------------------------------------------------
# The data model
# --------------------------------------------------------------------------
@dataclass
class Stage2Case:
    """One load case the structural screen has to answer.

    ``span_m`` is the span of the *aeroplane*.  The panel the model describes
    is the half wing, the cantilever that runs from the fuselage to the tip,
    so its span is half of it.  ``chord_m`` is the streamwise length of the
    panel.  ``thickness_m`` is the effective thickness of the panel; zero
    means the thickness of a single sheet of the paper the airframe is made
    of, which is the prescribed model, while a folded wing carries the
    thickness of its stack and passes it explicitly.
    """

    index: int
    aircraft: AircraftConfig
    dynamic_pressure_pa: float
    load_factor: float
    speed_mps: float
    span_m: float = 0.0
    chord_m: float = 0.0
    thickness_m: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the load case."""
        return {
            "index": int(self.index),
            "dynamic_pressure_pa": float(self.dynamic_pressure_pa),
            "load_factor": float(self.load_factor),
            "speed_mps": float(self.speed_mps),
            "span_m": float(self.span_m),
            "chord_m": float(self.chord_m),
            "thickness_m": float(self.thickness_m),
        }


@dataclass
class Stage2Report:
    """The outcome of the structural screen for one load case."""

    index: int
    feasible: bool
    buckling_margin: float
    max_tip_deflection_m: float
    pitch_stability_margin: float
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the report."""
        return {
            "index": int(self.index),
            "feasible": bool(self.feasible),
            "buckling_margin": float(self.buckling_margin),
            "max_tip_deflection_m": float(self.max_tip_deflection_m),
            "pitch_stability_margin": float(self.pitch_stability_margin),
            "reasons": list(self.reasons),
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _backend() -> Tuple[Any, str]:
    """Return the array module of the screen and its name."""
    try:
        import cupy as cp

        if int(cp.cuda.runtime.getDeviceCount()) > 0:
            return cp, "cupy"
    except Exception:  # pragma: no cover - depends on the host
        pass
    return np, "numpy"


def _to_host(xp: Any, array: Any) -> np.ndarray:
    """Bring a device array back to the host.

    CuPy owns its own transfer routine; NumPy arrays are already on the host.
    The device array is never handed to ``numpy.asarray`` directly, because
    that raises on a CuPy array.
    """
    transfer = getattr(xp, "asnumpy", None)
    if transfer is not None:
        return np.asarray(transfer(array))
    return np.asarray(array)


def _finite(value: Any, default: float = 0.0) -> float:
    """Coerce a value to a finite float."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return out


def _raw(value: Any) -> float:
    """Coerce a value to a float, keeping a non finite value non finite.

    The screen has to tell a degenerate load case from a usable one, so the
    raw value travels to the device and the finiteness is decided there.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _young_modulus_pa(effective_stiffness: float, paper_gsm: float) -> float:
    """The Young's modulus of the paper of an airframe, in pascals.

    :func:`physics.stress_domain.effective_paper_stiffness` returns the
    normalised bending stiffness of the airframe (zero to one, corrected for
    the grain of the paper), not a modulus in pascals.  The plate model needs
    pascals, so the normalised value is mapped onto the reference modulus of
    paper along the grain and scaled with the grammage of the sheet.
    """
    ratio = max(_finite(effective_stiffness, 0.0), 0.0) / PAPER_REFERENCE_STIFFNESS
    grammage = max(_finite(paper_gsm, 0.0), 0.0) / 100.0
    return PAPER_YOUNGS_MODULUS_PA * ratio * grammage


def _sheet_thickness_m(paper_gsm: float) -> float:
    """The thickness of a single sheet of paper, in metres.

    ``t = gsm * 1e-3 / 800`` is the thickness of a sheet with a bulk density
    of 800 kg/m3, the density of the paper this repository models.
    """
    return max(_finite(paper_gsm, 0.0), 0.0) * 1.0e-3 / 800.0


# --------------------------------------------------------------------------
# The screener
# --------------------------------------------------------------------------
class StaticFsiScreener:
    """The quasi static structural screen of stage 2.

    The screener evaluates a whole batch of load cases with one array
    operation on the array module it was built with, so the same code runs
    on the CPU and on the GPU.
    """

    def __init__(self, xp: Any = None, paper_gsm: float = 100.0) -> None:
        """Build the screener.

        ``xp`` is the array module the batch is evaluated with; it defaults
        to CuPy when the host has a usable device and to NumPy otherwise.
        ``paper_gsm`` is the grammage of the paper of the load cases that do
        not carry a thickness of their own.
        """
        if xp is None:
            xp, _name = _backend()
        self.xp = xp
        self.backend = "cupy" if getattr(xp, "__name__", "numpy") == "cupy" else "numpy"
        self.paper_gsm = max(_finite(paper_gsm, 100.0), 0.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def screen(self, cases: Sequence[Stage2Case]) -> List[Stage2Report]:
        """Screen a batch of load cases and report one result per case.

        The whole batch is evaluated with a single array operation; the
        numbers come back to the host once and the reasons are assembled from
        them.  A degenerate case (a zero span or chord, a non finite load)
        is reported as infeasible with a reason, it never raises.
        """
        if not cases:
            return []
        xp = self.xp

        span: List[float] = []
        chord: List[float] = []
        thickness: List[float] = []
        pressure: List[float] = []
        load_factor: List[float] = []
        modulus: List[float] = []
        static_margin: List[float] = []
        aspect: List[float] = []
        areal_density: List[float] = []
        mass: List[float] = []
        root_chord: List[float] = []
        taper: List[float] = []
        chord_scale: List[float] = []
        z_gain: List[float] = []
        allowable: List[float] = []

        for case in cases:
            aircraft = case.aircraft
            grammage = _raw(getattr(aircraft, "paper_gsm", 0.0))
            if not math.isfinite(grammage) or grammage <= 0.0:
                grammage = self.paper_gsm
            panel = _raw(case.thickness_m)
            if math.isfinite(panel) and panel <= 0.0:
                panel = _sheet_thickness_m(grammage)
            dynamic_pressure = _raw(case.dynamic_pressure_pa)
            speed = _raw(case.speed_mps)
            if math.isfinite(dynamic_pressure) and dynamic_pressure <= 0.0:
                if math.isfinite(speed) and speed > 0.0:
                    dynamic_pressure = 0.5 * SEA_LEVEL_DENSITY * speed * speed
                else:
                    dynamic_pressure = 0.0
            try:
                effective = effective_paper_stiffness(aircraft)
            except Exception:  # pragma: no cover - defensive
                effective = 0.0
            span.append(_raw(case.span_m))
            chord.append(_raw(case.chord_m))
            thickness.append(panel)
            pressure.append(dynamic_pressure)
            load_factor.append(_raw(case.load_factor))
            modulus.append(_young_modulus_pa(effective, grammage))
            static_margin.append(_raw(getattr(aircraft, "static_margin", 0.0)))
            aspect.append(self._aspect_ratio(aircraft, case))
            areal_density.append(grammage * 1.0e-3)
            mass.append(_raw(getattr(aircraft, "mass_kg", 0.0)))
            taper.append(_raw(getattr(aircraft, "taper_ratio", 0.7)))
            area = _raw(getattr(aircraft, "wing_area_m2", 0.0))
            semispan = _raw(getattr(aircraft, "wingspan_m", 0.0))
            taper_safe = min(max(_finite(getattr(aircraft, "taper_ratio", 0.7), 0.7), 0.05), 1.0)
            root_chord.append(
                2.0 * area / max(semispan * (1.0 + taper_safe), 1.0e-6)
            )
            trailing = _raw(getattr(aircraft, "fold_trailing_deg", 0.0))
            chord_scale.append(1.0 - TE_FOLD_CHORD_REDUCTION * abs(math.sin(math.radians(trailing))))
            z_gain.append(Z_SCALE_OFFSET + Z_SCALE_GAIN * max(effective, 0.0))
            allowable.append(paper_allowable_stress(grammage, effective))

        b = xp.asarray(span, dtype=xp.float64)
        c = xp.asarray(chord, dtype=xp.float64)
        t = xp.asarray(thickness, dtype=xp.float64)
        q = xp.asarray(pressure, dtype=xp.float64)
        nz = xp.asarray(load_factor, dtype=xp.float64)
        e_mod = xp.asarray(modulus, dtype=xp.float64)
        sm_fraction = xp.asarray(static_margin, dtype=xp.float64)
        ar = xp.asarray(aspect, dtype=xp.float64)
        sigma = xp.asarray(areal_density, dtype=xp.float64)
        mass_array = xp.asarray(mass, dtype=xp.float64)
        root_chord_array = xp.asarray(root_chord, dtype=xp.float64)
        taper_array = xp.asarray(taper, dtype=xp.float64)
        chord_scale_array = xp.asarray(chord_scale, dtype=xp.float64)
        z_gain_array = xp.asarray(z_gain, dtype=xp.float64)
        allowable_array = xp.asarray(allowable, dtype=xp.float64)

        finite = (
            xp.isfinite(b)
            & xp.isfinite(c)
            & xp.isfinite(t)
            & xp.isfinite(q)
            & xp.isfinite(nz)
            & xp.isfinite(e_mod)
            & xp.isfinite(sm_fraction)
            & xp.isfinite(ar)
            & xp.isfinite(mass_array)
            & xp.isfinite(root_chord_array)
            & xp.isfinite(taper_array)
            & xp.isfinite(allowable_array)
        )
        positive = (
            (b > 0.0)
            & (c > 0.0)
            & (t > 0.0)
            & (e_mod > 0.0)
            & (root_chord_array > 0.0)
            & (mass_array > 0.0)
        )
        usable = finite & positive

        b_safe = xp.where(usable, b, 1.0)
        c_safe = xp.where(usable, c, 1.0)
        t_safe = xp.where(usable, t, 1.0e-4)
        q_safe = xp.where(usable, q, 0.0)
        nz_safe = xp.where(usable, nz, 1.0)
        e_safe = xp.where(usable, e_mod, 1.0)
        ar_safe = xp.where(usable & (ar > 0.05), ar, 0.5)
        sm_safe = xp.where(usable, sm_fraction, 0.0)
        mass_safe = xp.where(usable, mass_array, 1.0e-6)
        root_chord_safe = xp.where(usable, root_chord_array, 1.0)
        scale_safe = xp.where(usable, chord_scale_array, 1.0)
        z_safe = xp.where(usable, z_gain_array, 1.0)

        panel_span = 0.5 * b_safe

        # The buckling of the compressed ply at the root: the root section
        # is a plate of the thickness of one sheet, and the critical stress
        # The root section of the folded body carries the moment of the
        # flight load.  The moment and the section modulus are the ones of
        # the shared structural model, so the stress the screen reports is
        # the stress the flight path reports for the same load case.
        moment = root_bending_moment_batch(
            xp, nz_safe, mass_safe, STANDARD_GRAVITY, b_safe, taper_array
        )
        section = section_modulus_batch(
            xp, root_chord_safe * scale_safe, t_safe, z_safe
        )
        root_stress = KAPPA_SIGMA * moment / section
        stress_margin = xp.where(root_stress > 0.0, allowable_array / root_stress, 0.0)

        # The buckling of the compressed ply at the root: the root section
        # is a plate of the thickness of one sheet, and the critical stress
        # of that plate decides whether the paper buckles (creases) there.
        critical_stress = (
            BUCKLING_FACTOR
            * math.pi
            * math.pi
            * e_safe
            * t_safe
            * t_safe
            / (
                12.0
                * (1.0 - POISSON_RATIO * POISSON_RATIO)
                * root_chord_safe
                * root_chord_safe
            )
        )
        buckling_margin = xp.where(
            root_stress > 0.0, critical_stress / xp.maximum(root_stress, 1.0e-30), 0.0
        )

        # The skin of a paper wing is a tensioned membrane: the flight load
        # pulls it taut and the tension stiffens the panel by orders of
        # magnitude.  The deflection follows from that tensioned plate.
        line_load = q_safe * c_safe
        tension = line_load
        plate_stiffness = e_safe * t_safe * t_safe * t_safe / (
            12.0 * (1.0 - POISSON_RATIO * POISSON_RATIO)
        )
        effective_stiffness = plate_stiffness + tension * panel_span * panel_span / (
            math.pi * math.pi
        )
        deflection = tension * panel_span**4 / (8.0 * effective_stiffness)
        tip_deflection = xp.abs(deflection)

        # The elastic deflection twists the wing; the twist moves the centre
        # of pressure towards the trailing edge and eats the static margin.
        twist = tip_deflection / xp.maximum(panel_span, 1.0e-6)
        margin_m = sm_safe * c_safe
        cg_shift = twist * margin_m
        deformed_margin = margin_m - cg_shift

        deflection_limit = MAX_TIP_DEFLECTION_FRACTION * panel_span
        margin = xp.where(usable, xp.minimum(stress_margin, buckling_margin), 0.0)
        tip_deflection = xp.where(usable, tip_deflection, 0.0)
        deformed_margin = xp.where(usable, deformed_margin, 0.0)
        feasible = (
            usable
            & (stress_margin > 1.0)
            & (buckling_margin > 1.0)
            & (deformed_margin > 0.0)
            & (tip_deflection <= deflection_limit)
        )

        margin_host = _to_host(xp, margin)
        tip_host = _to_host(xp, tip_deflection)
        pitch_host = _to_host(xp, deformed_margin)
        feasible_host = _to_host(xp, feasible)
        usable_host = _to_host(xp, usable)
        finite_host = _to_host(xp, finite)
        stress_host = _to_host(xp, stress_margin)
        buckle_host = _to_host(xp, buckling_margin)

        reports: List[Stage2Report] = []
        for position, case in enumerate(cases):
            reasons: List[str] = []
            if not bool(finite_host[position]):
                reasons.append("non finite load case")
            elif not bool(usable_host[position]):
                reasons.append("degenerate panel geometry")
            buckling = float(margin_host[position])
            tip = float(tip_host[position])
            pitch = float(pitch_host[position])
            if bool(usable_host[position]):
                if float(stress_host[position]) <= 1.0:
                    reasons.append("root section exceeds the allowable stress")
                if float(buckle_host[position]) <= 1.0:
                    reasons.append("wing panel buckles under the load")
                if pitch <= 0.0:
                    reasons.append("pitch stability destroyed by the deflection")
                if tip > MAX_TIP_DEFLECTION_FRACTION * 0.5 * float(span[position]):
                    reasons.append("tip deflection too large")
            reports.append(
                Stage2Report(
                    index=int(case.index),
                    feasible=bool(feasible_host[position]),
                    buckling_margin=buckling,
                    max_tip_deflection_m=tip,
                    pitch_stability_margin=pitch,
                    reasons=reasons,
                )
            )
        return reports

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _aspect_ratio(self, aircraft: AircraftConfig, case: Stage2Case) -> float:
        """The aspect ratio of the aeroplane the elastic twist acts on.

        The centre of pressure of the whole aeroplane moves, not the centre
        of pressure of one panel, so the aspect ratio of the aeroplane is the
        one the shift is computed with.  The panel aspect ratio is the
        fallback when the airframe does not describe a wing.
        """
        aspect = _finite(getattr(aircraft, "aspect_ratio", 0.0), 0.0)
        if aspect > 0.0:
            return aspect
        span = _finite(getattr(aircraft, "wingspan_m", 0.0), 0.0)
        area = _finite(getattr(aircraft, "wing_area_m2", 0.0), 0.0)
        if span > 0.0 and area > 0.0:
            return (span * span) / area
        panel_span = _finite(case.span_m, 0.0)
        panel_chord = _finite(case.chord_m, 0.0)
        if panel_span > 0.0 and panel_chord > 0.0:
            return panel_span / panel_chord
        return 0.5
