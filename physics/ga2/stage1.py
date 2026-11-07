"""Stage 1 of the evaluation pipeline: the ultra large batch 6-DoF screen.

The stage has to screen 10 000 to 100 000 fold programmes per generation
within a couple of seconds, so three rules apply:

*no host round trip*
    Nothing inside the integration loop touches the host.  The wind of the
    indoor case (and the perturbation of the throw ensemble) is drawn on the
    device, the state lives in device memory and the summary is reduced on
    the device as well.  A ``.get()`` inside the loop would serialise the
    pipeline and cost more than the arithmetic.

*no data dependent control flow*
    The number of integration steps is fixed, the cases that landed or
    crashed are masked instead of removed, and the loop never branches on a
    device value.  This is what makes the step capturable.

*one captured step*
    The whole step -- the RK4 sweep, the contact detection, the summary
    accumulation -- is captured into a CUDA graph and replayed.  A step is
    about forty kernel launches; without the graph the launch overhead
    dominates the arithmetic by an order of magnitude.

The physics is the shared one: the aerodynamic coefficients come from
:mod:`physics.aero_kernel`, the structural model from
:mod:`physics.stress_domain` and the contact model from
:mod:`physics.gpu_dynamics`, so the screening sees the same aeroplane as the
6-DoF reference path.  The only difference is the time step: the screen
integrates with a fixed step (a graph cannot have a data dependent one) and
the survivors are re-simulated with the adaptive reference integrator.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .. import aero_kernel, gpu_dynamics
from ..config_schema import AircraftConfig, EnvironmentConfig, clamp
from ..stress_domain import peak_root_stress_pa

__all__ = [
    "Stage1Case",
    "Stage1Result",
    "BatchScreener",
    "build_screening_aircraft",
    "rank_correlation",
    "wind_rank_report",
    "PERTURBATION_SPEED_SIGMA",
    "PERTURBATION_ANGLE_SIGMA_RAD",
    "PERTURBATION_RATE_SIGMA_RADPS",
]

#: The relative spread of the launch speed inside the perturbation ensemble.
PERTURBATION_SPEED_SIGMA = 0.04

#: The spread of the launch elevation inside the ensemble (radians).
PERTURBATION_ANGLE_SIGMA_RAD = 0.035

#: The spread of the body rates inside the ensemble (rad/s).
PERTURBATION_RATE_SIGMA_RADPS = 0.6

#: The impact distance the reference model uses for the crash load factor.
_IMPACT_DISTANCE_M = 0.031

#: The capture mode that tolerates the asynchronous copies CuPy emits.
_CAPTURE_MODE_RELAXED = 2


def _contract3(xp: Any, rotation: Any, vector: Any) -> Any:
    """Contract an ``(N, 3, 3)`` matrix with an ``(N, 3)`` vector.

    The contraction is written out element by element instead of with
    ``einsum``: CuPy lowers ``einsum`` to cuBLAS, and a cuBLAS call cannot be
    captured into a CUDA graph.
    """
    v0 = vector[:, 0]
    v1 = vector[:, 1]
    v2 = vector[:, 2]
    return xp.stack(
        (
            rotation[:, 0, 0] * v0 + rotation[:, 0, 1] * v1 + rotation[:, 0, 2] * v2,
            rotation[:, 1, 0] * v0 + rotation[:, 1, 1] * v1 + rotation[:, 1, 2] * v2,
            rotation[:, 2, 0] * v0 + rotation[:, 2, 1] * v1 + rotation[:, 2, 2] * v2,
        ),
        axis=-1,
    )


@contextmanager
def _capture_safe_rotation() -> Any:
    """Use a capture safe rotation while a CUDA graph is captured.

    The shared kernel contracts the rotation with ``einsum``, which CuPy
    lowers to cuBLAS, and a cuBLAS call cannot be captured.  During the
    capture the contraction is therefore written out element by element.  The
    two forms differ by at most one unit in the last place, which the
    chaotic 4g dart of the project amplifies -- the shared kernel is *not*
    changed, only the captured graph uses the elementwise form.  The graph
    keeps the kernels it captured, so the patch can be taken back right
    after the capture.
    """
    original_body = gpu_dynamics.body_to_world_batch
    original_world = gpu_dynamics.world_to_body_batch

    def body_to_world(xp: Any, rotation: Any, vector_body: Any) -> Any:
        return _contract3(xp, rotation, vector_body)

    def world_to_body(xp: Any, rotation: Any, vector_world: Any) -> Any:
        return _contract3(xp, xp.swapaxes(rotation, 1, 2), vector_world)

    gpu_dynamics.body_to_world_batch = body_to_world
    gpu_dynamics.world_to_body_batch = world_to_body
    try:
        yield
    finally:
        gpu_dynamics.body_to_world_batch = original_body
        gpu_dynamics.world_to_body_batch = original_world


def build_screening_aircraft(
    record_or_genome: Any,
    paper_gsm: float = 100.0,
    base: Optional[AircraftConfig] = None,
) -> AircraftConfig:
    """Construct an AircraftConfig for Stage 1 screening faithfully preserving genome parameters.

    Ensures aerodynamic coefficients (cL0, cm0, cd0, cl_alpha, etc.), CG position / static margin,
    wingspan, wing area, mass, and planform moments of inertia from the genome and Stage 0 geometry
    screening are correctly reflected and never overwritten by unphysical hard-coded defaults.
    """
    import dataclasses

    config = dataclasses.replace(base) if base is not None else AircraftConfig()

    record: Dict[str, Any] = {}
    genome: Any = None
    if isinstance(record_or_genome, dict):
        record = record_or_genome
        genome = record.get("genome")
    else:
        genome = record_or_genome

    # Inherit from existing aircraft config in record if available
    if isinstance(record.get("aircraft"), AircraftConfig):
        config = dataclasses.replace(record["aircraft"])
    elif isinstance(record.get("aircraft"), dict):
        for k, v in record["aircraft"].items():
            if hasattr(config, k) and v is not None:
                setattr(config, k, v)

    # Extract aerodynamic & geometric properties from genome if present
    if genome is not None:
        if hasattr(genome, "aircraft") and isinstance(genome.aircraft, AircraftConfig):
            config = dataclasses.replace(genome.aircraft)
        meta = getattr(genome, "meta", {}) or {}
        for aero_key in (
            "cL0", "cm0", "cd0", "cl_alpha", "stall_alpha_deg", "elevon_deg",
            "dihedral_deg", "fold_trailing_deg", "step_trip_ratio", "oswald_efficiency"
        ):
            if aero_key in meta and meta[aero_key] is not None:
                setattr(config, aero_key, float(meta[aero_key]))

    # Extract geometry report from Stage 0 if present
    report = record.get("stage0") or {}
    area = float(report.get("wing_area_m2", 0.0)) or float(getattr(config, "wing_area_m2", 0.02)) or 0.02
    span = float(report.get("span_m", 0.0)) or float(getattr(config, "wingspan_m", 0.15)) or 0.15
    mass = float(report.get("mass_kg", 0.0)) or float(getattr(config, "mass_kg", 0.005)) or 0.005
    aspect = float(report.get("aspect_ratio", 0.0)) or (span * span / max(area, 1.0e-6))

    config.wing_area_m2 = max(area, 1.0e-6)
    config.wingspan_m = max(span, 1.0e-3)
    config.aspect_ratio = max(aspect, 1.0e-3)
    config.mass_kg = max(mass, 1.0e-9)
    config.sheet_area_m2 = max(area, 1.0e-6)
    config.paper_gsm = float(paper_gsm)

    # Chord & CG offset reflection
    chord = area / max(span, 1.0e-3)
    x_ac = 0.25 * chord
    if "cg" in report and isinstance(report["cg"], (tuple, list)) and len(report["cg"]) >= 1:
        cg_x = float(report["cg"][0])
        config.cg_offset_x = clamp(cg_x - x_ac, -0.5, 0.5)
    elif "static_margin_m" in report and report["static_margin_m"] is not None:
        # Static margin SM = x_np - x_cg  => x_cg - x_np = -SM
        config.cg_offset_x = clamp(-float(report["static_margin_m"]), -0.5, 0.5)

    # Physical planform moments of inertia: Ixx ~ 1/12 m b^2, Iyy ~ 1/12 m c^2, Izz ~ Ixx + Iyy
    ixx = mass * span * span / 12.0
    iyy = mass * chord * chord / 12.0
    izz = ixx + iyy
    config.roll_inertia_kgm2 = max(ixx, 1.0e-12)
    config.pitch_inertia_kgm2 = max(iyy, 1.0e-12)
    config.yaw_inertia_kgm2 = max(izz, 1.0e-12)
    return config


@dataclass
class Stage1Case:
    """One airframe that the screening stage has to fly."""

    index: int
    aircraft: AircraftConfig
    initial_state: Any
    duration_s: float = 4.0
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "duration_s": float(self.duration_s),
            "label": str(self.label),
        }


@dataclass
class Stage1Result:
    """The screening summary of one airframe."""

    index: int
    status: str
    flight_time_s: float
    distance_m: float
    max_ld_ratio: float
    stability: float
    peak_stress_pa: float
    peak_stress_index: float
    peak_load_factor: float
    peak_dynamic_pressure_pa: float
    stall_events: float
    cl: float = 0.0
    cd: float = 0.0
    coverage: float = 1.0
    repeats: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "status": str(self.status),
            "flight_time_s": float(self.flight_time_s),
            "distance_m": float(self.distance_m),
            "max_ld_ratio": float(self.max_ld_ratio),
            "stability": float(self.stability),
            "peak_stress_pa": float(self.peak_stress_pa),
            "peak_stress_index": float(self.peak_stress_index),
            "peak_load_factor": float(self.peak_load_factor),
            "peak_dynamic_pressure_pa": float(self.peak_dynamic_pressure_pa),
            "stall_events": float(self.stall_events),
            "cl": float(self.cl),
            "cd": float(self.cd),
            "coverage": float(self.coverage),
            "repeats": int(self.repeats),
        }


def _backend() -> Tuple[Any, str]:
    """Return the array module of the screen and its name."""
    try:
        import cupy as cp

        if int(cp.cuda.runtime.getDeviceCount()) > 0:
            return cp, "cupy"
    except Exception:  # pragma: no cover - depends on the host
        pass
    return np, "numpy"


class BatchScreener:
    """The batched 6-DoF screening stage.

    Stage 1 is an INTENTIONAL MEAN-WIND SCREENER (Issue #81): the spatial /
    vertical shear and time-varying gust field of :mod:`physics.wind_domain`
    is NOT sampled per stage here -- ``use_mean_wind_only=True`` makes every
    RK4 substep reuse the per-individual drawn mean wind.  The guarantee
    envelope is therefore a fast screen only; the variable-wind truth is
    re-evaluated downstream (reference integrator with
    ``sample_wind_stages`` in the runner, stage 2/3) and the rank swap is
    quantified by :func:`wind_rank_report` below, not assumed.
    """

    MEAN_WIND_SCREENER = True

    def __init__(
        self,
        xp: Any = None,
        env: Optional[EnvironmentConfig] = None,
        dt_max: float = 2.5e-3,
        duration_s: float = 4.0,
        use_graph: bool = True,
        seed: int = 20251021,
        check_interval: int = 64,
        max_steps: int = 2000,
    ) -> None:
        if xp is None:
            xp, _ = _backend()
        self.xp = xp
        self.backend = "cupy" if xp is not np else "numpy"
        self.env = env or EnvironmentConfig(wind_speed=0.0)
        self.dt_max = max(float(dt_max), 1.0e-5)
        self.duration_s = max(float(duration_s), 0.1)
        self.max_steps = max(1, int(max_steps))
        self.use_graph = bool(use_graph) and self.backend == "cupy"
        self.seed = int(seed)
        self.check_interval = max(1, int(check_interval))
        self._graphs: Dict[Tuple[int, int], Any] = {}
        self._graph_error: Optional[str] = None
        self._last_metrics: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """A JSON serialisable description of the screener."""
        return {
            "backend": self.backend,
            "graph": bool(self._graphs),
            "graph_error": self._graph_error,
            "dt_max_s": float(self.dt_max),
            "duration_s": float(self.duration_s),
            "seed": int(self.seed),
            **self._last_metrics,
        }

    def screen(
        self,
        cases: Sequence[Stage1Case],
        repeats: int = 1,
        perturbation: bool = True,
    ) -> List[Stage1Result]:
        """Fly every case and return the screening summary.

        ``repeats`` is the number of independent passes over the population
        (the perturbation ensemble of the specification).  The summary of a
        case aggregates the passes: the best lift to drag ratio is kept, the
        distance and the flight time are averaged and the worst status wins.
        """
        if not cases:
            return []
        xp = self.xp
        count = len(cases)
        duration = max(float(case.duration_s) for case in cases)
        states = np.stack(
            [np.asarray(case.initial_state, dtype=np.float64) for case in cases]
        )
        aircraft = [case.aircraft for case in cases]
        params = gpu_dynamics.build_params(
            xp,
            aircraft,
            self.env,
            wind_override=np.zeros((count, 3), dtype=np.float64),
        )

        generator = _generator(xp, self.seed)
        wind = _draw_wind(xp, generator, count, perturbation)
        params.wind = wind
        params.mean_wind = wind
        params.use_mean_wind_only = True

        device_states = xp.asarray(states)
        totals: Optional[Dict[str, Any]] = None
        passes = max(1, int(repeats))
        for repeat in range(passes):
            perturbed = _perturb(xp, device_states, generator, perturbation, repeat)
            summary = self._integrate(params, perturbed, wind, duration)
            totals = summary if totals is None else _merge(totals, summary, xp)
        assert totals is not None
        totals["flight_time"] = totals["flight_time"] / float(passes)
        totals["distance"] = totals["distance"] / float(passes)
        totals["coverage"] = xp.minimum(
            totals["time"] / max(duration * float(passes), 1.0e-9), 1.0
        )
        return self._finalise(cases, totals)

    def release(self) -> None:
        """Drop the captured graphs and give their memory back.

        The graph keeps the memory of the temporaries it captured alive.
        When a later stage allocates a lot of device memory (the flow solver
        does), that memory may be handed out again and the next replay of
        the graph would read the wrong addresses.  Releasing the graph
        before such a stage keeps both of them sound; the next generation
        captures it again, which costs a millisecond.
        """
        for graph in self._graphs.values():
            try:
                graph.reset()
            except Exception:  # pragma: no cover - depends on the driver
                pass
        self._graphs.clear()
        try:
            import cupy as cp

            cp.cuda.Stream.null.synchronize()
        except Exception:  # pragma: no cover - depends on the driver
            pass

    # ------------------------------------------------------------------
    # Integration
    # ------------------------------------------------------------------
    def _integrate(
        self,
        params: Any,
        states: Any,
        wind: Any,
        duration: float,
    ) -> Dict[str, Any]:
        xp = self.xp
        count = states.shape[0]
        y = xp.array(states, dtype=xp.float64, copy=True)
        acc = _new_accumulators(xp, count)
        # Nominal step count based on dt_max, scaled by safety factor (up to max_steps)
        # to ensure adaptive step size reduction (dt < dt_max) does not exhaust loop steps
        # before reaching duration (Issue #78). Early exit via _finished() prevents wasted compute.
        nominal_steps = int(math.ceil(duration / self.dt_max))
        steps = max(1, min(self.max_steps, nominal_steps * 3))

        launch_core = gpu_dynamics._evaluate_state(xp, params, y, wind)
        _seed_accumulators(xp, acc, launch_core, params)

        step = _make_step(xp, params, y, wind, acc, duration, self.dt_max)
        graph = self._capture(count, steps, step)
        if graph is not None:
            import cupy as cp

            stream = cp.cuda.Stream(non_blocking=True)
            for index in range(steps):
                graph.launch(stream)
                if (index + 1) % self.check_interval == 0 and _finished(xp, acc, duration):
                    break
            stream.synchronize()
        else:
            for index in range(steps):
                step()
                if (index + 1) % self.check_interval == 0 and _finished(xp, acc, duration):
                    break
        acc["stability"] = _stability(xp, params, acc)
        acc["state"] = y
        acc["steps"] = steps
        return acc

    def _capture(self, count: int, steps: int, step: Any) -> Any:
        """Capture the integration step into a CUDA graph."""
        if not self.use_graph:
            return None
        key = (int(count), int(steps))
        cached = self._graphs.get(key)
        if cached is not None:
            return cached
        try:
            import cupy as cp

            stream = cp.cuda.Stream(non_blocking=True)
            with _capture_safe_rotation():
                with stream:
                    stream.begin_capture(mode=_CAPTURE_MODE_RELAXED)
                    step()
                    graph = stream.end_capture()
            graph.launch(stream)
            stream.synchronize()
            self._graphs[key] = graph
            return graph
        except Exception as exc:  # pragma: no cover - depends on the driver
            self.use_graph = False
            self._graph_error = f"{type(exc).__name__}: {exc}"
            return None

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------
    def _finalise(
        self,
        cases: Sequence[Stage1Case],
        totals: Dict[str, Any],
    ) -> List[Stage1Result]:
        pull = _to_host(self.xp)
        landed = pull(totals["landed"])
        crashed = pull(totals["crashed"])
        flight_time = pull(totals["flight_time"])
        distance = pull(totals["distance"])
        max_ld = pull(totals["max_ld"])
        stability = pull(totals["stability"])
        peak_stress = pull(totals["peak_stress"])
        allowable = pull(totals["allowable"])
        peak_g = pull(totals["peak_g"])
        peak_q = pull(totals["peak_q"])
        stall_events = pull(totals["stall_events"])
        coverage = pull(totals["coverage"])
        trim_cl = pull(totals["trim_cl"])
        trim_cd = pull(totals["trim_cd"])
        results: List[Stage1Result] = []
        for position, case in enumerate(cases):
            if crashed[position]:
                status = "crashed"
            elif landed[position]:
                status = "landed"
            elif flight_time[position] > 0.0:
                status = "timeout"
            else:
                status = "degenerate"
            results.append(
                Stage1Result(
                    index=int(case.index),
                    status=status,
                    flight_time_s=float(flight_time[position]),
                    distance_m=float(distance[position]),
                    max_ld_ratio=float(max_ld[position]),
                    stability=float(stability[position]),
                    peak_stress_pa=float(peak_stress[position]),
                    peak_stress_index=(
                        float(peak_stress[position] / allowable[position])
                        if allowable[position] > 0.0
                        else 0.0
                    ),
                    peak_load_factor=float(peak_g[position]),
                    peak_dynamic_pressure_pa=float(peak_q[position]),
                    stall_events=float(stall_events[position]),
                    cl=float(trim_cl[position]),
                    cd=float(trim_cd[position]),
                    coverage=float(coverage[position]),
                )
            )
        self._last_metrics = {
            "cases": int(len(cases)),
            "steps": int(totals.get("steps", 0)),
        }
        return results


# --------------------------------------------------------------------------
# Device helpers
# --------------------------------------------------------------------------
def _generator(xp: Any, seed: int) -> Any:
    """A random generator of the array module."""
    if xp is np:
        return np.random.default_rng(int(seed))
    import cupy as cp

    return cp.random.default_rng(int(seed))


def _draw_wind(xp: Any, generator: Any, count: int, perturbation: bool) -> Any:
    """The wind of the batch, drawn on the device.

    The indoor case is windless; the perturbation ensemble adds a very small
    draught so that the ensemble is not a single point of the design space.
    """
    if not perturbation:
        return xp.zeros((count, 3), dtype=xp.float64)
    speed = generator.standard_normal(count) * 0.05
    angle = generator.uniform(0.0, 2.0 * math.pi, size=count)
    return xp.stack(
        (
            speed * xp.cos(angle),
            speed * xp.sin(angle),
            xp.zeros_like(speed),
        ),
        axis=-1,
    )


def _perturb(
    xp: Any,
    states: Any,
    generator: Any,
    perturbation: bool,
    repeat: int,
) -> Any:
    """Draw the perturbed launch conditions of the ensemble on the device.

    The perturbation acts on the launch speed, the launch elevation and the
    body rates -- the three quantities a human throw actually varies.  It is
    drawn with the device generator, so the whole ensemble stays in device
    memory.
    """
    out = xp.array(states, dtype=xp.float64, copy=True)
    if not perturbation:
        return out
    count = out.shape[0]
    scale = 1.0 + generator.standard_normal(count) * PERTURBATION_SPEED_SIGMA
    tilt = generator.standard_normal(count) * PERTURBATION_ANGLE_SIGMA_RAD
    rates = generator.standard_normal((count, 3)) * PERTURBATION_RATE_SIGMA_RADPS

    vx = out[:, 3] * scale
    vy = out[:, 4] * scale
    vz = out[:, 5] * scale
    cos_t = xp.cos(tilt)
    sin_t = xp.sin(tilt)
    out[:, 3] = vx * cos_t - vz * sin_t
    out[:, 5] = vx * sin_t + vz * cos_t
    out[:, 4] = vy
    out[:, 10:13] = out[:, 10:13] + rates
    return out


def _new_accumulators(xp: Any, count: int) -> Dict[str, Any]:
    """The persistent summary buffers of one integration."""
    return {
        "time": xp.zeros(count, dtype=xp.float64),
        "flight_time": xp.zeros(count, dtype=xp.float64),
        "distance": xp.zeros(count, dtype=xp.float64),
        "peak_q": xp.zeros(count, dtype=xp.float64),
        "peak_g": xp.zeros(count, dtype=xp.float64),
        "peak_stress": xp.zeros(count, dtype=xp.float64),
        "allowable": xp.ones(count, dtype=xp.float64),
        "max_ld": xp.zeros(count, dtype=xp.float64),
        "trim_alpha": xp.zeros(count, dtype=xp.float64),
        "trim_speed": xp.zeros(count, dtype=xp.float64),
        "trim_altitude": xp.zeros(count, dtype=xp.float64),
        "trim_cl": xp.zeros(count, dtype=xp.float64),
        "trim_cd": xp.zeros(count, dtype=xp.float64),
        "stall_events": xp.zeros(count, dtype=xp.float64),
        "stall_margin": xp.ones(count, dtype=xp.float64),
        "stability": xp.zeros(count, dtype=xp.float64),
        "landed": xp.zeros(count, dtype=bool),
        "crashed": xp.zeros(count, dtype=bool),
    }


def _seed_accumulators(
    xp: Any, acc: Dict[str, Any], core: Dict[str, Any], params: Any
) -> None:
    """Seed the summary with the launch state."""
    acc["allowable"] = params.allowable_pa
    acc["peak_q"][...] = xp.maximum(acc["peak_q"], core["dynamic_pressure"])
    acc["peak_g"][...] = xp.maximum(acc["peak_g"], core["g_load"])
    stress = _stress(xp, core, params)
    acc["peak_stress"][...] = xp.maximum(acc["peak_stress"], stress)
    fast = core["speed"] >= gpu_dynamics.SPEED_STAT_FLOOR_MPS
    candidate = xp.where(fast, xp.abs(core["ld_ratio"]), 0.0)
    better = candidate > acc["max_ld"]
    acc["max_ld"][...] = xp.where(better, candidate, acc["max_ld"])
    acc["trim_alpha"][...] = xp.where(better, core["alpha"], acc["trim_alpha"])
    acc["trim_speed"][...] = xp.where(better, core["speed"], acc["trim_speed"])
    acc["trim_altitude"][...] = xp.where(better, core["altitude"], acc["trim_altitude"])
    acc["trim_cl"][...] = xp.where(better, core["cl"], acc["trim_cl"])
    acc["trim_cd"][...] = xp.where(better, core["cd"], acc["trim_cd"])


def _stress(xp: Any, core: Dict[str, Any], params: Any) -> Any:
    """The wing-root bending stress at one state."""
    return peak_root_stress_pa(
        xp,
        load_factor=core["g_load"],
        mass_kg=params.mass,
        gravity=params.gravity,
        span_m=params.span,
        taper_ratio=params.taper_ratio,
        root_chord_m=params.root_chord_m,
        thickness_m=params.thickness_m,
        z_gain=params.z_gain,
        dihedral_rad=params.dihedral_rad,
        chord_scale=params.chord_scale,
        tip_factor=params.tip_factor,
    )


def _stability(xp: Any, params: Any, acc: Dict[str, Any]) -> Any:
    """The static longitudinal stability slope (dCm/dalpha) and trim residual.

    Evaluates the pitch stiffness (restoring slope dCm/dalpha < 0) around the
    best glide point, combined with the trim residual |Cm|.  Airframes with
    positive pitch slope (pitch divergence / instability) are heavily penalized,
    ensuring only statically stable gliders receive high stability scores.
    """
    air = gpu_dynamics.atmosphere_batch(
        xp, params.env, xp.maximum(acc["trim_altitude"], 0.0), params.chord
    )
    reynolds = acc["trim_speed"] * params.chord / xp.maximum(
        air["kinematic_viscosity_m2s"], 1.0e-12
    )
    mach = acc["trim_speed"] / xp.maximum(air["sound_speed_mps"], 1.0)
    alt = xp.maximum(acc["trim_altitude"], 0.0)
    density = air["density_kgm3"]
    trim_alpha = acc["trim_alpha"]

    coefficients = aero_kernel.evaluate_coefficients(
        xp,
        params.aero,
        alpha_deg=trim_alpha,
        beta_deg=0.0,
        p_rate=0.0,
        q_rate=0.0,
        r_rate=0.0,
        speed=acc["trim_speed"],
        reynolds=reynolds,
        mach=mach,
        ground_height_m=alt,
        density=density,
    )
    coeff_plus = aero_kernel.evaluate_coefficients(
        xp,
        params.aero,
        alpha_deg=trim_alpha + 1.0,
        beta_deg=0.0,
        p_rate=0.0,
        q_rate=0.0,
        r_rate=0.0,
        speed=acc["trim_speed"],
        reynolds=reynolds,
        mach=mach,
        ground_height_m=alt,
        density=density,
    )
    # dCm/dalpha per degree
    cm_alpha_eff = coeff_plus["cm"] - coefficients["cm"]
    # Static stability requires dCm/dalpha < 0. Typical healthy slope ~ -0.015 / deg.
    # Score 0 for unstable (>= 0), rising to 1 for healthy restoring slope.
    stable = cm_alpha_eff < 0.0
    slope_factor = xp.where(
        stable,
        xp.clip(-cm_alpha_eff / 0.015, 0.0, 1.0),
        xp.zeros_like(cm_alpha_eff),
    )
    trim_factor = 1.0 / (1.0 + xp.abs(coefficients["cm"]))
    return slope_factor * trim_factor

def _make_step(
    xp: Any,
    params: Any,
    y: Any,
    wind: Any,
    acc: Dict[str, Any],
    duration: float,
    dt_max: float,
) -> Any:
    """Build the integration step.

    The step is a pure function of the persistent buffers, so it can be
    captured into a CUDA graph and replayed without touching the host.
    """

    def step() -> None:
        active = (~acc["landed"]) & (~acc["crashed"]) & (acc["time"] < duration)
        core = gpu_dynamics._evaluate_state(xp, params, y, wind)
        size = gpu_dynamics._step_size(xp, params, y, core, dt_max)
        size = xp.minimum(size, xp.maximum(duration - acc["time"], 0.0))
        size = xp.where(active, size, 0.0)
        y_next = gpu_dynamics._rk4(xp, params, y, size, wind)
        core_next = gpu_dynamics._evaluate_state(xp, params, y_next, wind)

        # Every accumulator is updated *in place*.  A CUDA graph replays the
        # kernels it captured, so a summary that was bound to a new array
        # inside the step would only ever hold the value of the capture.
        travelled = xp.sqrt(
            (y_next[:, 0] - y[:, 0]) ** 2 + (y_next[:, 1] - y[:, 1]) ** 2
        )
        acc["distance"] += xp.where(active, travelled, 0.0)
        acc["time"] += size

        acc["peak_q"][...] = xp.maximum(acc["peak_q"], core_next["dynamic_pressure"])
        acc["peak_g"][...] = xp.maximum(acc["peak_g"], core_next["g_load"])
        stress = _stress(xp, core_next, params)
        worse = active & (stress > acc["peak_stress"])
        acc["peak_stress"][...] = xp.where(worse, stress, acc["peak_stress"])
        acc["stall_events"] += xp.where(active & core_next["stalled"], 1.0, 0.0)
        acc["stall_margin"][...] = xp.where(
            active,
            xp.minimum(acc["stall_margin"], core_next["stall_margin"]),
            acc["stall_margin"],
        )
        fast = core_next["speed"] >= gpu_dynamics.SPEED_STAT_FLOOR_MPS
        candidate = xp.where(fast, xp.abs(core_next["ld_ratio"]), 0.0)
        better = active & (candidate > acc["max_ld"])
        acc["max_ld"][...] = xp.where(better, candidate, acc["max_ld"])
        acc["trim_alpha"][...] = xp.where(better, core_next["alpha"], acc["trim_alpha"])
        acc["trim_speed"][...] = xp.where(better, core_next["speed"], acc["trim_speed"])
        acc["trim_altitude"][...] = xp.where(
            better, core_next["altitude"], acc["trim_altitude"]
        )
        acc["trim_cl"][...] = xp.where(better, core_next["cl"], acc["trim_cl"])
        acc["trim_cd"][...] = xp.where(better, core_next["cd"], acc["trim_cd"])
        acc["flight_time"][...] = xp.where(active, acc["time"], acc["flight_time"])

        stations = gpu_dynamics._ground_stations(xp, params, y_next)
        touching = stations["min_alt"] <= 0.0
        impact_speed = xp.maximum(y_next[:, 5], 0.0)
        impact_g = 1.0 + impact_speed * impact_speed / (
            2.0 * params.gravity * _IMPACT_DISTANCE_M
        )
        impact_stress = peak_root_stress_pa(
            xp,
            load_factor=impact_g,
            mass_kg=params.mass,
            gravity=params.gravity,
            span_m=params.span,
            taper_ratio=params.taper_ratio,
            root_chord_m=params.root_chord_m,
            thickness_m=params.thickness_m,
            z_gain=params.z_gain,
            dihedral_rad=params.dihedral_rad,
            chord_scale=params.chord_scale,
            tip_factor=params.tip_factor,
        )
        impact_index = impact_stress / params.allowable_pa
        failed = touching & (
            (impact_index >= 1.0)
            | (impact_speed > gpu_dynamics.CRASH_IMPACT_SPEED_MPS)
        )
        acc["landed"] |= touching & ~failed
        acc["crashed"] |= failed
        y[...] = xp.where(active[:, xp.newaxis], y_next, y)

    return step


def _finished(xp: Any, acc: Dict[str, Any], duration: float) -> bool:
    """True when every case has either landed, crashed or run out of time."""
    active = (~acc["landed"]) & (~acc["crashed"]) & (acc["time"] < duration)
    return not bool(xp.any(active))


def _to_host(xp: Any) -> Any:
    """A function that brings a device array back to the host."""
    if xp is np:
        return lambda array: np.asarray(array)

    def pull(array: Any) -> Any:
        return np.asarray(array.get())

    return pull


def _merge(first: Dict[str, Any], second: Dict[str, Any], xp: Any) -> Dict[str, Any]:
    """Merge the summaries of two ensemble passes."""
    merged = dict(first)
    merged["distance"] = first["distance"] + second["distance"]
    merged["flight_time"] = first["flight_time"] + second["flight_time"]
    merged["peak_q"] = xp.maximum(first["peak_q"], second["peak_q"])
    merged["peak_g"] = xp.maximum(first["peak_g"], second["peak_g"])
    merged["peak_stress"] = xp.maximum(first["peak_stress"], second["peak_stress"])
    merged["max_ld"] = xp.maximum(first["max_ld"], second["max_ld"])
    merged["stall_events"] = first["stall_events"] + second["stall_events"]
    merged["stall_margin"] = xp.minimum(first["stall_margin"], second["stall_margin"])
    merged["stability"] = xp.maximum(first["stability"], second["stability"])
    merged["landed"] = first["landed"] | second["landed"]
    merged["crashed"] = first["crashed"] | second["crashed"]
    merged["time"] = first["time"] + second["time"]
    better = second["max_ld"] > first["max_ld"]
    merged["trim_alpha"] = xp.where(better, second["trim_alpha"], first["trim_alpha"])
    merged["trim_speed"] = xp.where(better, second["trim_speed"], first["trim_speed"])
    merged["trim_altitude"] = xp.where(
        better, second["trim_altitude"], first["trim_altitude"]
    )
    merged["trim_cl"] = xp.where(better, second["trim_cl"], first["trim_cl"])
    merged["trim_cd"] = xp.where(better, second["trim_cd"], first["trim_cd"])
    return merged


def rank_correlation(stage1_values: Any, reference_values: Any) -> float:
    """Spearman rank correlation between Stage 1 and reference rankings."""
    left = list(stage1_values)
    right = list(reference_values)
    count = len(left)
    if count < 2 or len(right) != count:
        return 0.0
    order_l = sorted(range(count), key=lambda i: left[i])
    order_r = sorted(range(count), key=lambda i: right[i])
    ranks_l = [0.0] * count
    ranks_r = [0.0] * count
    for pos, idx in enumerate(order_l):
        ranks_l[idx] = float(pos)
    for pos, idx in enumerate(order_r):
        ranks_r[idx] = float(pos)
    mean_l = sum(ranks_l) / count
    mean_r = sum(ranks_r) / count
    num = sum((ranks_l[i] - mean_l) * (ranks_r[i] - mean_r) for i in range(count))
    den_l = math.sqrt(sum((v - mean_l) ** 2 for v in ranks_l))
    den_r = math.sqrt(sum((v - mean_r) ** 2 for v in ranks_r))
    if den_l <= 0.0 or den_r <= 0.0:
        return 0.0
    return num / (den_l * den_r)


def wind_rank_report(
    stage1_scores: Any,
    reference_scores: Any,
    top_k: int = 10,
) -> Dict[str, float]:
    """Quantify the mean-wind screening error of Stage 1 (Issue #81).

    Reports the Spearman rank correlation between Stage 1 (mean-wind only)
    and the variable-wind reference evaluation, plus the *miss rate*: the
    fraction of the reference top-k that fell outside the Stage 1 top-k.
    A miss rate of 0 means screening never lost a true elite; a large miss
    rate means the final decision must not rest on Stage 1 alone.
    """
    left = list(stage1_scores)
    right = list(reference_scores)
    count = len(left)
    if count < 2:
        return {"rank_correlation": 0.0, "miss_rate": 0.0, "top_k": 0.0}
    order_l = sorted(range(count), key=lambda i: left[i], reverse=True)
    order_r = sorted(range(count), key=lambda i: right[i], reverse=True)
    k = max(1, min(int(top_k), count))
    top_l = set(order_l[:k])
    top_r = order_r[:k]
    misses = sum(1 for idx in top_r if idx not in top_l)
    return {
        "rank_correlation": float(rank_correlation(left, right)),
        "miss_rate": float(misses / k),
        "top_k": float(k),
    }
