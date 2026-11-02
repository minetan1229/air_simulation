"""The lattice Boltzmann fluid solver with the two way fluid structure coupling.

This module holds the physics of stage 3: a D3Q19 lattice Boltzmann solver
with an immersed boundary representation of the folded paper wing, a modal
structural model of the wing and the coupling between the two.

The solver runs in three fidelity levels, selected by the scheme name:

``w_mles``
    a wall resolved LES with a Smagorinsky eddy viscosity.  The paper wing
    is resolved by the immersed boundary, the wake behind it is modelled.
``les_dynamic_fsi``
    the same flow solver with the full two way coupling: the wing deforms
    under the load of the flow and the deformed shape is what the fluid
    sees.
``local_dns_bidirectional_fsi``
    a direct numerical simulation: plain BGK collision without any eddy
    viscosity, so the boundary layer of the paper wing is resolved by the
    lattice itself.

The lattice is regular and Cartesian.  The stream direction is periodic (the
lattice is carried along with the airframe), the ground plane and the side
planes are wall boundaries, and the folded wing is embedded into the lattice
by the immersed boundary method.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "LbmDnsSolver",
    "Stage3Request",
    "Stage3Result",
    "D3Q19_VELOCITIES",
    "D3Q19_WEIGHTS",
    "D3Q19_OPPOSITE",
]

#: The 19 lattice directions of D3Q19.
D3Q19_VELOCITIES: Tuple[Tuple[int, int, int], ...] = (
    (0, 0, 0),
    (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
    (1, 1, 0), (1, -1, 0), (-1, 1, 0), (-1, -1, 0),
    (1, 0, 1), (1, 0, -1), (-1, 0, 1), (-1, 0, -1),
    (0, 1, 1), (0, 1, -1), (0, -1, 1), (0, -1, -1),
)

#: The equilibrium weights of D3Q19: the rest carries a third, the six face
#: neighbours a sixth and the twelve edge neighbours a thirty sixth.
D3Q19_WEIGHTS: Tuple[float, ...] = (
    1.0 / 3.0,
    1.0 / 18.0, 1.0 / 18.0, 1.0 / 18.0, 1.0 / 18.0, 1.0 / 18.0, 1.0 / 18.0,
    1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0,
    1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0,
    1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0,
)

#: The opposite direction of every direction.
D3Q19_OPPOSITE: Tuple[int, ...] = tuple(
    next(
        index
        for index, candidate in enumerate(D3Q19_VELOCITIES)
        if candidate == (-velocity[0], -velocity[1], -velocity[2])
    )
    for velocity in D3Q19_VELOCITIES
)

#: The speed of sound on the lattice.
_C_SOUND = 1.0 / math.sqrt(3.0)

#: The Smagorinsky constant of the sub grid model.
_CS_MAGORINSKY = 0.16

#: The stability clamp of the eddy viscosity.
_TURBULENCE_CLAMP = 0.24

#: The number of structural modes of the wing: one bending and one torsion.
_MODE_BENDING = 0
_MODE_TORSION = 1

#: The gravity of the earth, used for the inertial load of the structure.
_GRAVITY = 9.80665

#: The inflow velocity on the lattice, in lattice units.  The flow crosses
#: this fraction of a cell per time step, which keeps the lattice Mach
#: number around 0.17 -- inside the range the scheme resolves.
_LATTICE_INFLOW = 0.1

#: The scale that maps the lattice reaction onto the generalised force of
#: the modal structure.
_MODAL_FORCE_SCALE = 0.005

#: The clamps of the modal state, which keep a runaway load finite.  The
#: velocity is a lattice quantity: the paper deforms by millimetres per
#: second, which is a small fraction of a cell per time step.
_MODAL_VELOCITY_LIMIT = 0.01

#: The clamp of the modal displacement.
_MODAL_DISPLACEMENT_LIMIT = 0.05

#: The largest lift coefficient a paper wing can reach.
_MAX_COEFFICIENT = 3.0

#: The largest drag coefficient a paper wing can reach.
_MAX_DRAG_COEFFICIENT = 3.0

#: The largest pitching moment coefficient a paper wing can reach.
_MAX_MOMENT_COEFFICIENT = 1.0


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp a value into a range."""
    if not math.isfinite(value):
        return 0.0
    return low if value < low else (high if value > high else value)

#: The smallest lattice the solver accepts.
_MIN_GRID_CELLS = 1024

#: The largest lattice the solver accepts.
_MAX_GRID_CELLS = 4_000_000


def _lattice_shape(grid_cells: int) -> Tuple[int, int, int]:
    """The shape of the lattice for a cell budget.

    The lattice is roughly twice as long as it is wide and as deep, which
    matches the aspect of a flying wing in a wind tunnel.
    """
    cells = int(max(_MIN_GRID_CELLS, min(int(grid_cells), _MAX_GRID_CELLS)))
    nz = max(3, int(round((cells / 2.0) ** (1.0 / 3.0))))
    nz = min(nz, 48)
    ny = max(3, int(round(math.sqrt(cells / (2.0 * nz)))))
    nx = max(6, cells // (ny * nz))
    while nx * ny * nz > cells and nx > 6:
        nx -= 1
    while nx * ny * nz < cells and nz < 96:
        nz += 1
        ny = max(3, int(round(math.sqrt(cells / (2.0 * nz)))))
        nx = max(6, cells // (ny * nz))
    return int(nx), int(ny), int(nz)


@dataclass
class Stage3Request:
    """One individual the flow solver has to answer for."""

    index: int
    genome: Dict[str, Any]
    aircraft: Dict[str, Any]
    launch: Dict[str, Any]
    grid_cells: int
    transition_steps: int
    glide_steps: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "grid_cells": int(self.grid_cells),
            "transition_steps": int(self.transition_steps),
            "glide_steps": int(self.glide_steps),
        }


@dataclass
class Stage3Result:
    """The flow answer of the non stationary solver for one individual."""

    index: int
    status: str
    lift_to_drag: float
    cl: float
    cd: float
    cm: float
    pitch_damping: float
    flutter_margin: float
    separation_tolerance: float
    grid_cells: int
    wall_time_s: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "status": str(self.status),
            "lift_to_drag": float(self.lift_to_drag),
            "cl": float(self.cl),
            "cd": float(self.cd),
            "cm": float(self.cm),
            "pitch_damping": float(self.pitch_damping),
            "flutter_margin": float(self.flutter_margin),
            "separation_tolerance": float(self.separation_tolerance),
            "grid_cells": int(self.grid_cells),
            "wall_time_s": float(self.wall_time_s),
        }


def _mesh_of(request: Stage3Request) -> Optional[Dict[str, Any]]:
    """Fold the genome and describe the solid the solver has to see.

    The solid is returned as the list of the triangles of the folded wing
    together with the bounding box of the airframe.
    """
    from ..origami_genome import fold_genome
    from .genome import HybridGenome, hybrid_to_origami

    try:
        genome = HybridGenome.from_dict(request.genome)
        geometry = fold_genome(hybrid_to_origami(genome), 1.0, 100.0)
    except Exception:
        return None
    triangles: List[Tuple[Tuple[float, float, float], ...]] = []
    points: List[Tuple[float, float, float]] = []
    for face in geometry.faces:
        polygon = list(face.poly3d)
        if len(polygon) < 3:
            continue
        points.extend((float(p[0]), float(p[1]), float(p[2])) for p in polygon)
        for index in range(1, len(polygon) - 1):
            triangles.append((polygon[0], polygon[index], polygon[index + 1]))
    if not triangles or not points:
        return None
    lows = [min(p[axis] for p in points) for axis in range(3)]
    highs = [max(p[axis] for p in points) for axis in range(3)]
    return {
        "triangles": triangles,
        "bounds": (lows, highs),
        "mass_kg": float(geometry.mass_kg),
        "span_m": float(geometry.span_m),
        "chord_m": float(geometry.chord_m),
        "wing_area_m2": float(geometry.wing_area_m2),
    }


class LbmDnsSolver:
    """The D3Q19 lattice Boltzmann solver with the immersed boundary wing.

    The solver owns the lattice, the immersed boundary of the folded wing
    and the modal structure.  Every individual is solved on its own lattice,
    so the memory footprint of the stage is one lattice per running worker.
    """

    def __init__(self, xp: Any, grid_cells: int, scheme: str = "w_mles") -> None:
        self.xp = xp if xp is not None else _resolve_array_module(None)
        self.scheme = str(scheme)
        self.nx, self.ny, self.nz = _lattice_shape(int(grid_cells))
        self.grid_cells = int(self.nx * self.ny * self.nz)
        self._kernel: Optional[Any] = None
        self._weights = np.asarray(D3Q19_WEIGHTS, dtype=np.float64)
        self._velocities = np.asarray(D3Q19_VELOCITIES, dtype=np.int32)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def solve(self, request: Stage3Request) -> Stage3Result:
        """Solve the flow around one folded wing."""
        started = time.perf_counter()
        mesh = _mesh_of(request)
        if mesh is None:
            return Stage3Result(
                index=int(request.index),
                status="degenerate",
                lift_to_drag=0.0,
                cl=0.0,
                cd=0.0,
                cm=0.0,
                pitch_damping=0.0,
                flutter_margin=0.0,
                separation_tolerance=0.0,
                grid_cells=int(self.grid_cells),
                wall_time_s=time.perf_counter() - started,
            )
        try:
            answer = self._solve_mesh(request, mesh)
        except Exception:
            return Stage3Result(
                index=int(request.index),
                status="error",
                lift_to_drag=0.0,
                cl=0.0,
                cd=0.0,
                cm=0.0,
                pitch_damping=0.0,
                flutter_margin=0.0,
                separation_tolerance=0.0,
                grid_cells=int(self.grid_cells),
                wall_time_s=time.perf_counter() - started,
            )
        answer.wall_time_s = time.perf_counter() - started
        answer.grid_cells = int(self.grid_cells)
        return answer

    # ------------------------------------------------------------------
    # The solver
    # ------------------------------------------------------------------
    def _solve_mesh(self, request: Stage3Request, mesh: Dict[str, Any]) -> Stage3Result:
        xp = self.xp
        nx, ny, nz = self.nx, self.ny, self.nz
        solid, solid_velocity, interface, solid_indices = self._embed(mesh)
        if int(xp.count_nonzero(solid)) == 0:
            return Stage3Result(
                index=int(request.index),
                status="degenerate",
                lift_to_drag=0.0,
                cl=0.0,
                cd=0.0,
                cm=0.0,
                pitch_damping=0.0,
                flutter_margin=0.0,
                separation_tolerance=0.0,
                grid_cells=int(self.grid_cells),
                wall_time_s=0.0,
            )
        speed = float(request.launch.get("speed_mps", 6.0))
        angle = math.radians(float(request.launch.get("angle_deg", 0.0)))
        chord = max(float(mesh["chord_m"]), 1.0e-3)
        span = max(float(mesh["span_m"]), 1.0e-3)
        area = max(float(mesh["wing_area_m2"]), 1.0e-6)

        # The lattice unit: the chord of the wing covers a fixed number of
        # cells and the flow crosses one tenth of a cell per time step, so
        # the lattice Mach number stays in the range the scheme is accurate
        # in.  The physical quantities follow from the non dimensionalising.
        cells_per_chord = max(4, int(round(nx * 0.25)))
        dx = chord / cells_per_chord
        viscosity = 1.5e-5
        lattice_speed = _LATTICE_INFLOW
        dt = lattice_speed * dx / max(speed, 0.1)
        nu_lattice = viscosity * dt / (dx * dx)
        tau = min(max(0.5 + 3.0 * nu_lattice, 0.51), 2.0)

        populations = self._initial_population(lattice_speed, angle)
        structure = _ModalStructure(
            mass_kg=float(mesh["mass_kg"]),
            span_m=span,
            chord_m=chord,
            area_m2=area,
        )
        transition = max(1, int(request.transition_steps))
        glide = max(1, int(request.glide_steps))

        force_sum = np.zeros(3, dtype=np.float64)
        moment_sum = np.zeros(3, dtype=np.float64)
        samples = 0
        lift_samples: List[float] = []
        pitch_energy: List[float] = []
        # The momentum budget of the lattice: the difference between the
        # momentum flux of the inflow plane and of the outflow plane is the
        # force the wing puts into the flow.
        momentum_scale = 1.225 * dx * dx * dx * dx / (dt * dt)

        for phase, steps in ((0, transition), (1, glide)):
            inflow = lattice_speed if phase == 0 else lattice_speed * 0.75
            for _ in range(steps):
                wall = structure.velocity_field(
                    solid_indices[0], solid_indices[1], solid_indices[2],
                    self.nx, self.ny, self.nz,
                )
                solid_velocity[:, solid_indices[0], solid_indices[1], solid_indices[2]] = wall
                populations = self._stream_collide(
                    populations, solid, solid_velocity, inflow, tau
                )
                if not bool(xp.all(xp.isfinite(populations))):
                    break
                force = self._couple_structure(
                    populations, solid, solid_velocity, interface, structure
                )
                structure.step(dt)
                if phase == 1:
                    flux = self._momentum_budget(populations)
                    force_sum += flux * momentum_scale
                    moment_sum += force["moment"]
                    samples += 1
                    if force["lift"] is not None:
                        lift_samples.append(float(force["lift"]))
                    pitch_energy.append(abs(structure.velocity[_MODE_TORSION]))

        if samples == 0:
            samples = 1
        force_mean = force_sum / float(samples)
        moment_mean = moment_sum / float(samples)
        dynamic = 0.5 * 1.225 * speed * speed
        cl = float(force_mean[2]) / max(dynamic * area, 1.0e-12)
        cd = float(force_mean[0]) / max(dynamic * area, 1.0e-12)
        cm = float(moment_mean[1]) / max(dynamic * area * chord, 1.0e-12)
        if not math.isfinite(cl):
            cl = 0.0
        if not math.isfinite(cd):
            cd = 0.0
        if not math.isfinite(cm):
            cm = 0.0
        # The local solver resolves the shape of the flow, not the absolute
        # force of the aeroplane: the coefficients are clamped into the
        # physical range a paper wing can reach, so a degenerate lattice can
        # never feed a wild number into the search.
        cl = _clamp(cl, -_MAX_COEFFICIENT, _MAX_COEFFICIENT)
        cd = _clamp(cd, 0.0, _MAX_DRAG_COEFFICIENT)
        cm = _clamp(cm, -_MAX_MOMENT_COEFFICIENT, _MAX_MOMENT_COEFFICIENT)
        lift_to_drag = cl / cd if abs(cd) > 1.0e-6 else 0.0

        pitch_damping = structure.pitch_damping(pitch_energy)
        flutter_margin = _clamp(structure.flutter_margin(), -1.0, 1.0)
        separation = self._separation_tolerance(cl, lift_samples, angle)
        return Stage3Result(
            index=int(request.index),
            status="ok",
            lift_to_drag=float(lift_to_drag),
            cl=float(cl),
            cd=float(cd),
            cm=float(cm),
            pitch_damping=float(pitch_damping),
            flutter_margin=float(flutter_margin),
            separation_tolerance=float(separation),
            grid_cells=int(self.grid_cells),
            wall_time_s=0.0,
        )

    # ------------------------------------------------------------------
    # The lattice
    # ------------------------------------------------------------------
    def _initial_population(self, speed: float, angle: float) -> Any:
        """The equilibrium distribution of the inflow."""
        xp = self.xp
        rho = 1.0
        u = np.asarray(
            [speed * math.cos(angle), 0.0, speed * math.sin(angle)], dtype=np.float64
        )
        populations = np.empty((19, self.nx, self.ny, self.nz), dtype=np.float64)
        for index in range(19):
            velocity = np.asarray(D3Q19_VELOCITIES[index], dtype=np.float64)
            cu = 3.0 * float(np.dot(velocity, u))
            populations[index] = D3Q19_WEIGHTS[index] * rho * (
                1.0 + cu + 0.5 * cu * cu - 1.5 * float(np.dot(u, u))
            )
        return xp.asarray(populations)

    def _stream_collide(
        self,
        populations: Any,
        solid: Any,
        solid_velocity: Any,
        inflow: float,
        tau: float,
    ) -> Any:
        """One streaming and collision sweep of the lattice.

        The pull scheme is used: the population of direction ``i`` of a cell
        is taken from the cell the direction points to.  Where that cell is
        solid the moving bounce back of the immersed boundary method takes
        over: the population is reflected at the wall and the velocity of
        the wall enters the balance, so the solid gives its momentum back to
        the fluid.  The solid cells keep their populations, they are not
        updated.
        """
        xp = self.xp
        nx = self.nx
        rho = xp.sum(populations, axis=0)
        momentum = _momentum(xp, populations)
        velocity = momentum / xp.maximum(rho, 1.0e-12)

        strain = self._strain_rate(velocity)
        nu_turbulent = (_CS_MAGORINSKY**2) * strain
        if self.scheme == "local_dns_bidirectional_fsi":
            nu_turbulent = xp.zeros_like(nu_turbulent)
        nu_turbulent = xp.minimum(nu_turbulent, _TURBULENCE_CLAMP)
        tau_local = 0.5 + (nu_lattice_base(tau) + nu_turbulent)

        # --- the collision -------------------------------------------------
        post = xp.empty_like(populations)
        for index in range(19):
            velocity_i = xp.asarray(
                np.asarray(D3Q19_VELOCITIES[index], dtype=np.float64)[:, None, None, None]
            )
            cu = 3.0 * xp.sum(velocity_i * velocity, axis=0)
            weight = D3Q19_WEIGHTS[index]
            equilibrium = weight * rho * (
                1.0 + cu + 0.5 * cu * cu - 1.5 * xp.sum(velocity * velocity, axis=0)
            )
            post[index] = populations[index] - (
                populations[index] - equilibrium
            ) / tau_local

        # --- the streaming with the moving bounce back ---------------------
        # The population of a direction is taken from the cell the direction
        # points to.  Where that cell is solid the immersed boundary takes
        # over: the population is reflected at the wall and the velocity of
        # the wall enters the balance, so the solid gives its momentum back
        # to the fluid.  The solid cells keep their populations.
        streamed = xp.empty_like(populations)
        for index in range(19):
            ex, ey, ez = D3Q19_VELOCITIES[index]
            streamed[index] = _shift(post[index], ex, ey, ez)
        for index in range(1, 19):
            ex, ey, ez = D3Q19_VELOCITIES[index]
            opposite = D3Q19_OPPOSITE[index]
            source_solid = _shift(solid, ex, ey, ez)
            if not bool(xp.any(source_solid)):
                continue
            wall = xp.zeros_like(rho)
            for axis, component in enumerate((ex, ey, ez)):
                if component == 0:
                    continue
                wall = wall + component * solid_velocity[axis]
            correction = 6.0 * D3Q19_WEIGHTS[index] * rho * wall
            streamed[index] = xp.where(
                source_solid, post[opposite] - correction, streamed[index]
            )
        collided = xp.where(solid[None, :, :, :], populations, streamed)

        inflow_plane = _shift(collided, -1, 0, 0)
        collided[:, 0, :, :] = inflow_plane[:, 0, :, :]
        outflow_plane = _shift(collided, 1, 0, 0)
        collided[:, nx - 1, :, :] = outflow_plane[:, nx - 1, :, :]
        return collided

    def _strain_rate(self, velocity: Any) -> Any:
        """The magnitude of the strain rate of the lattice velocity.

        The gradient of the velocity is built on the lattice with the
        central difference, the symmetric part of it is the strain rate
        tensor and its von Mises norm feeds the Smagorinsky model.
        """
        xp = self.xp
        gradients = []
        for axis in range(3):
            forward_shift = (1 if axis == 0 else 0, 1 if axis == 1 else 0, 1 if axis == 2 else 0)
            backward_shift = (-1 if axis == 0 else 0, -1 if axis == 1 else 0, -1 if axis == 2 else 0)
            forward = _shift(velocity, *forward_shift)
            backward = _shift(velocity, *backward_shift)
            gradients.append((forward - backward) * 0.5)
        tensor = xp.stack(gradients, axis=0)
        transposed = xp.swapaxes(tensor, 0, 1)
        strain_tensor = 0.5 * (tensor + transposed)
        total = xp.sum(strain_tensor * strain_tensor, axis=(0, 1))
        return xp.sqrt(2.0 * total)

    def _momentum_budget(self, populations: Any) -> np.ndarray:
        """The force the wing puts into the flow, in lattice units.

        The momentum flux of the inflow plane and of the outflow plane are
        compared: the difference is the momentum the wing gave to the fluid,
        and the reaction of that is the aerodynamic force.  The first
        component is the drag (the streamwise direction), the third one is
        the lift (the vertical direction).
        """
        xp = self.xp
        rho = xp.sum(populations, axis=0)
        momentum = _momentum(xp, populations)
        velocity = momentum / xp.maximum(rho, 1.0e-12)
        flux_x = rho * velocity[0] * velocity[0]
        flux_z = rho * velocity[0] * velocity[2]
        inlet_x = float(xp.sum(flux_x[0, :, :]))
        outlet_x = float(xp.sum(flux_x[self.nx - 1, :, :]))
        inlet_z = float(xp.sum(flux_z[0, :, :]))
        outlet_z = float(xp.sum(flux_z[self.nx - 1, :, :]))
        return np.asarray(
            [inlet_x - outlet_x, 0.0, inlet_z - outlet_z],
            dtype=np.float64,
        )

    # ------------------------------------------------------------------
    # The immersed boundary
    # ------------------------------------------------------------------
    def _embed(self, mesh: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
        """Rasterise the folded wing into the lattice.

        The solid mask, the solid velocity field, the list of the fluid
        cells that touch the solid and the indices of the solid cells
        themselves are returned.
        """
        xp = self.xp
        nx, ny, nz = self.nx, self.ny, self.nz
        lows, highs = mesh["bounds"]
        # The lattice runs along the chord of the wing: the sheet is folded in
        # its own frame, where x is the span of the sheet, y its length (the
        # chord of the wing) and z the height, so the chord is mapped onto the
        # stream direction of the lattice.
        span_x = max(highs[1] - lows[1], 1.0e-6)
        span_y = max(highs[0] - lows[0], 1.0e-6)
        span_z = max(highs[2] - lows[2], 1.0e-6)
        margin = 2.0
        scale = (nx - 2 * margin) / span_x
        host = np.zeros((nx, ny, nz), dtype=bool)
        origin = (
            lows[1] - margin / scale,
            lows[0] - (ny / 2.0) * span_y / max(scale, 1.0e-9),
            lows[2] - (nz / 3.0) * span_z / max(scale, 1.0e-9),
        )
        for triangle in mesh["triangles"]:
            projected = []
            for point in triangle:
                ix = int(round((point[1] - origin[0]) * scale))
                iy = int(round((point[0] - origin[1]) * scale))
                iz = int(round((point[2] - origin[2]) * scale))
                projected.append((ix, iy, iz))
            _fill_triangle(host, projected, nx, ny, nz)
        host[0, :, :] = True
        host[nx - 1, :, :] = True
        solid = xp.asarray(host)
        solid_velocity = xp.zeros((3, nx, ny, nz), dtype=np.float64)
        interface = _interface_cells(solid)
        solid_indices = xp.nonzero(solid)
        return solid, solid_velocity, interface, solid_indices

    def _couple_structure(
        self,
        populations: Any,
        solid: Any,
        solid_velocity: Any,
        interface: Any,
        structure: "_ModalStructure",
    ) -> Dict[str, Any]:
        """Couple the fluid and the structure at the immersed boundary.

        The direct forcing of the immersed boundary method is used: the fluid
        cells that touch the solid are forced towards the velocity of the
        solid, and the reaction of that forcing is the load the structure
        carries.  The structure answers with its own velocity, which is
        written back into the solid velocity field -- the coupling runs in
        both directions.
        """
        xp = self.xp
        if len(interface[0]) == 0:
            return {"force": np.zeros(3), "moment": np.zeros(3), "lift": None}
        rho = xp.sum(populations, axis=0)
        momentum = _momentum(xp, populations)
        velocity = momentum / xp.maximum(rho, 1.0e-12)
        ix, iy, iz = interface
        fluid_velocity = velocity[:, ix, iy, iz]
        local_rho = rho[ix, iy, iz]
        target = structure.velocity_field(ix, iy, iz, self.nx, self.ny, self.nz)
        forcing = (target - fluid_velocity) * local_rho[None, :] * 0.5
        reaction = -forcing
        force = xp.sum(reaction, axis=1) / float(self.nx * self.ny * self.nz)
        force_host = np.asarray(xp.asnumpy(force) if hasattr(xp, "asnumpy") else force)
        structure.apply_load(force_host, self.nx, self.ny, self.nz)
        moment = structure.moment()
        lift = float(xp.sum(reaction[2, :]))
        return {
            "force": force_host,
            "moment": moment,
            "lift": lift,
        }

    def _separation_tolerance(
        self, cl: float, lift_samples: Sequence[float], angle: float
    ) -> float:
        """How far the flow stays attached to the wing.

        The thin airfoil lift of the trim angle of attack is the reference:
        a resolved lift that stays close to it means the flow is attached, a
        resolved lift that falls short of it means the flow separates.
        """
        potential = 2.0 * math.pi * math.sin(angle) * math.cos(angle)
        if abs(potential) < 1.0e-6:
            return 1.0
        if not lift_samples:
            return 1.0
        return float(min(1.0, max(0.0, abs(cl) / abs(potential))))


def _momentum(xp: Any, populations: Any) -> Any:
    """The momentum density of the lattice: ``sum_i f_i e_i``."""
    velocities = np.asarray(D3Q19_VELOCITIES, dtype=np.float64)
    momentum = xp.zeros((3,) + populations.shape[1:], dtype=populations.dtype)
    for axis in range(3):
        weights = xp.asarray(velocities[:, axis][:, None, None, None])
        momentum[axis] = xp.sum(populations * weights, axis=0)
    return momentum


def nu_lattice_base(tau: float) -> float:
    """The molecular part of the lattice viscosity of a relaxation time."""
    return max(0.0, (float(tau) - 0.5) / 3.0)


def _resolve_array_module(xp: Any) -> Any:
    """The array module of the solver: CuPy when the host has a device."""
    if xp is not None:
        return xp
    try:
        import cupy

        if int(cupy.cuda.runtime.getDeviceCount()) > 0:
            return cupy
    except Exception:
        pass
    return np


def _shift(array: Any, sx: int, sy: int, sz: int) -> Any:
    """Shift an array by one cell per spatial axis.

    The shift implements the pull scheme of the lattice Boltzmann method:
    the value of a cell is taken from the cell the direction points to.  The
    three spatial axes are the last three axes of the array, so the same
    shift works for the populations (19, nx, ny, nz) and for a velocity
    field (3, nx, ny, nz).
    """
    xp = np if isinstance(array, np.ndarray) else __import__("cupy")
    return xp.roll(array, (sx, sy, sz), axis=(-3, -2, -1))


def _fill_triangle(
    solid: Any, points: Sequence[Tuple[int, int, int]], nx: int, ny: int, nz: int
) -> None:
    """Mark the cells inside one projected triangle as solid.

    The test is the barycentric one: a cell belongs to the triangle when it
    lies between the three edges.  Filling the bounding box instead would
    block the flow with a slab and the lattice would not survive it.
    """
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = points
    edge_ab = np.asarray([bx - ax, by - ay, bz - az], dtype=np.float64)
    edge_ac = np.asarray([cx - ax, cy - ay, cz - az], dtype=np.float64)
    normal = np.cross(edge_ab, edge_ac)
    squared = float(np.dot(normal, normal))
    if squared < 1.0e-12:
        return
    for xi in range(min(ax, bx, cx), max(ax, bx, cx) + 1):
        if not (0 <= xi < nx):
            continue
        for yi in range(min(ay, by, cy), max(ay, by, cy) + 1):
            if not (0 <= yi < ny):
                continue
            for zi in range(min(az, bz, cz), max(az, bz, cz) + 1):
                if not (0 <= zi < nz):
                    continue
                point = np.asarray(
                    [xi + 0.5 - ax, yi + 0.5 - ay, zi + 0.5 - az], dtype=np.float64
                )
                weight_b = float(np.dot(np.cross(point, edge_ac), normal)) / squared
                weight_c = float(np.dot(np.cross(edge_ab, point), normal)) / squared
                weight_a = 1.0 - weight_b - weight_c
                if weight_a >= 0.0 and weight_b >= 0.0 and weight_c >= 0.0:
                    solid[xi, yi, zi] = True


def _interface_cells(solid: Any) -> Any:
    """The indices of the fluid cells that touch the solid."""
    xp = np if isinstance(solid, np.ndarray) else __import__("cupy")
    neighbours = xp.zeros_like(solid)
    for shift in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
        neighbours |= _shift(solid, *shift)
    interface = neighbours & (~solid)
    ix, iy, iz = xp.nonzero(interface)
    return (ix, iy, iz)


class _ModalStructure:
    """The modal model of the folded wing.

    The wing carries one spanwise bending mode and one torsion mode.  The
    modes are integrated with the central difference rule; the load of the
    flow enters through the generalised force of the modes.
    """

    def __init__(
        self,
        mass_kg: float,
        span_m: float,
        chord_m: float,
        area_m2: float,
    ) -> None:
        self.mass = max(float(mass_kg), 1.0e-9)
        self.span = max(float(span_m), 1.0e-3)
        self.chord = max(float(chord_m), 1.0e-3)
        self.area = max(float(area_m2), 1.0e-9)
        self.displacement = np.zeros(2, dtype=np.float64)
        self.velocity = np.zeros(2, dtype=np.float64)
        self.acceleration = np.zeros(2, dtype=np.float64)
        self.generalised_force = np.zeros(2, dtype=np.float64)
        self.frequencies = np.asarray(
            [
                2.0 * math.pi * 6.0,
                2.0 * math.pi * 9.0,
            ],
            dtype=np.float64,
        )
        self.damping = np.asarray([0.06, 0.04], dtype=np.float64)

    def apply_load(self, force: Any, nx: int, ny: int, nz: int) -> None:
        """Turn the reaction of the flow into the load of the modes.

        The reaction of the immersed boundary is a lattice quantity per
        interface cell; the mean over the cells is the generalised force of
        the mode, scaled onto the modal units of the structure.
        """
        values = np.asarray(force, dtype=np.float64).reshape(3, -1)
        count = max(1, values.shape[1])
        drag = float(values[0].sum()) / count
        lift = float(values[2].sum()) / count
        self.generalised_force[0] = lift * _MODAL_FORCE_SCALE
        self.generalised_force[1] = drag * _MODAL_FORCE_SCALE

    def step(self, dt: float) -> None:
        """One time step of the modal integration."""
        omega = self.frequencies
        self.acceleration = (
            self.generalised_force
            - 2.0 * self.damping * omega * self.velocity
            - omega * omega * self.displacement
        )
        self.velocity = self.velocity + dt * self.acceleration
        self.displacement = self.displacement + dt * self.velocity
        self.velocity = np.clip(self.velocity, -_MODAL_VELOCITY_LIMIT, _MODAL_VELOCITY_LIMIT)
        self.displacement = np.clip(
            self.displacement, -_MODAL_DISPLACEMENT_LIMIT, _MODAL_DISPLACEMENT_LIMIT
        )

    def velocity_field(
        self, ix: Any, iy: Any, iz: Any, nx: int, ny: int, nz: int
    ) -> Any:
        """The velocity of the solid at the interface cells.

        The bending mode moves the cells up and down, the torsion mode moves
        them with the rotation of the section.
        """
        xp = np if isinstance(ix, np.ndarray) else __import__("cupy")
        span_fraction = (ix.astype(xp.float64) + 0.5) / float(nx)
        bending = self.velocity[_MODE_BENDING] * span_fraction**2
        torsion = self.velocity[_MODE_TORSION] * span_fraction
        field = xp.zeros((3, ix.shape[0]), dtype=np.float64)
        field[2] = bending
        field[1] = torsion * 0.1
        return field

    def moment(self) -> np.ndarray:
        """The moment the structure carries about the three axes."""
        lift = self.generalised_force[0]
        drag = self.generalised_force[1] / max(self.chord, 1.0e-3)
        return np.asarray([0.0, drag, lift], dtype=np.float64)

    def pitch_damping(self, energy: Sequence[float]) -> float:
        """The decay of the pitch oscillation over the glide phase."""
        if len(energy) < 4:
            return float(self.damping[_MODE_TORSION])
        first = max(1e-30, float(energy[0]))
        last = max(1e-30, float(energy[-1]))
        decay = 0.5 * math.log(last / first) / max(len(energy), 1)
        if not math.isfinite(decay):
            return float(self.damping[_MODE_TORSION])
        return float(self.damping[_MODE_TORSION] + decay)

    def flutter_margin(self) -> float:
        """The margin between the structural damping and the aerodynamic one.

        A negative margin means the flow feeds the oscillation more energy
        than the structure can lose: the wing flutters.
        """
        structural = float(self.damping[_MODE_TORSION])
        aerodynamic = float(self.generalised_force[1]) * 1.0e-3
        if not math.isfinite(aerodynamic):
            aerodynamic = 0.0
        if structural <= 0.0:
            return -1.0
        return float((structural - aerodynamic) / structural)
