"""Stage 3 solvers: coupled LBM (D3Q19) aerodynamic solver with immersed-boundary
two-way fluid-structure coupling and a modal structural model.

The module provides three aerodynamic fidelity levels selected through
``Stage3Request`` / ``LbmDnsSolver``:

``w_mles``
    Weakly compressible wall-resolved LES with a Smagorinsky eddy viscosity.
``les_dynamic_fsi``
    LES with a dynamic (Leslie) Smagorinsky model and the full two-way
    immersed-boundary coupling (solid deformation feeds back into the fluid).
``local_dns_bidirectional_fsi``
    Local direct numerical simulation (plain BGK, no eddy viscosity) with a
    bidirectional fluid-structure coupling.

All solvers are lattice Boltzmann codes on a regular Cartesian lattice with
periodic boundaries in the stream direction (x) and immersed-boundary
representations of the folded paper structure.  The lattice cell count is
derived from ``grid_cells`` using an approximate 2:1:1 span:chord:thickness
aspect ratio.

Public API
----------
``STAGE3_MODES``
    Tuple of the supported aerodynamic schemes.
``Stage3Request``
    Immutable request description (genome, aircraft configuration, launch
    state and lattice budget).
``Stage3Result``
    Result container with aerodynamic coefficients and stability margins.
``LbmDnsSolver``
    Solver object binding an array module (``cupy`` or ``numpy``) to a scheme.
``run_stage3``
    Sequential evaluation of a batch of requests.
``run_stage3_parallel``
    Multi-GPU evaluation, one worker process per visible GPU.
"""

from __future__ import annotations

import math
import multiprocessing as mp
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "STAGE3_MODES",
    "Stage3Request",
    "Stage3Result",
    "LbmDnsSolver",
    "run_stage3",
    "run_stage3_parallel",
]

STAGE3_MODES: Tuple[str, ...] = ("w_mles", "les_dynamic_fsi", "local_dns_bidirectional_fsi")

_SOUND_SPEED_LATTICE = 0.1
_VISCOSITY_LATTICE = 0.0009
_SMAGORINSKY_CONSTANT = 0.16
_DYNAMIC_SMAGORINSKY_CLAMP = 0.24
_GROUND_CLEARANCE_CELLS = 2.0
_INFLOW_MARGIN_CELLS = 3.0
_OUTFLOW_MARGIN_CELLS = 6.0
_MOMENTUM_EXCHANGE_FACTOR = 1.0
_FLUTTER_MARGIN_SCALE = 0.25
_MAX_GRID_CELLS = 4_000_000
_MIN_GRID_CELLS = 1024


from .lbm import LbmDnsSolver, Stage3Request, Stage3Result


def _lattice_shape(grid_cells: int) -> Tuple[int, int, int]:
    """Return ``(nx, ny, nz)`` with an approximately 2:1:1 span:chord:thickness ratio."""
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


def run_stage3(requests: Sequence[Stage3Request], profile: Any, xp: Any = None) -> List[Stage3Result]:
    """Evaluate ``requests`` sequentially with the solver configured by ``profile``.

    Parameters
    ----------
    requests:
        Sequence of stage 3 requests to evaluate.
    profile:
        Scaling profile exposing ``effective_grid_cells()`` and
        ``stage3_scheme``.
    xp:
        Optional array module override (``cupy`` or ``numpy``).  When ``None``
        the module is selected automatically.

    Returns
    -------
    list of :class:`Stage3Result`
    """
    module = _resolve_array_module(xp)
    scheme = _resolve_scheme(profile)
    grid_cells = _resolve_grid_cells(profile, requests)
    results: List[Stage3Result] = []
    for request in requests:
        solver = LbmDnsSolver(module, grid_cells, scheme)
        results.append(solver.solve(request))
    return results


def run_stage3_parallel(requests: Sequence[Stage3Request], profile: Any) -> List[Stage3Result]:
    """Evaluate ``requests`` using one worker process per visible GPU.

    Each worker receives ``CUDA_VISIBLE_DEVICES=<i>`` and processes a shard of
    the request queue, returning the results through a result queue.  The
    function falls back to :func:`run_stage3` when a single GPU is available
    or when multiprocessing is unavailable.
    """
    request_list = list(requests)
    if not request_list:
        return []
    device_count = _resolve_device_count(profile)
    if device_count <= 1:
        return run_stage3(request_list, profile, xp=None)
    try:
        context = mp.get_context("spawn")
    except ValueError:
        return run_stage3(request_list, profile, xp=None)
    try:
        return _run_stage3_multiprocess(context, request_list, profile, device_count)
    except (OSError, RuntimeError, ValueError):
        return run_stage3(request_list, profile, xp=None)


def _run_stage3_multiprocess(
    context: Any,
    request_list: List[Stage3Request],
    profile: Any,
    device_count: int,
) -> List[Stage3Result]:
    """Spawn one worker per GPU and collect the results.

    The individuals are put on one shared queue and the workers pull them
    one by one: the assignment of an individual to a GPU is decided when it
    is picked up, not in advance, so a worker that finishes early takes the
    next individual while the others are still busy.  This is the task
    parallel strategy of the specification -- one individual per GPU, no
    communication between the GPUs at all.
    """
    task_queue: Any = context.Queue()
    result_queue: Any = context.Queue()
    workers = []
    for index in range(device_count):
        process = context.Process(
            target=_stage3_worker,
            args=(index, profile, task_queue, result_queue),
            daemon=False,
        )
        process.start()
        workers.append(process)
    for request in request_list:
        task_queue.put(request)
    for _ in workers:
        task_queue.put(None)
    task_queue.close()
    collected: Dict[int, Stage3Result] = {}
    for _ in range(len(request_list)):
        item = result_queue.get()
        collected[int(item.index)] = item
    result_queue.close()
    for process in workers:
        process.join(timeout=600.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10.0)
    return [collected[key] for key in sorted(collected)]


def _stage3_worker(
    device_index: int,
    profile: Any,
    task_queue: Any,
    result_queue: Any,
) -> None:
    """Worker entry point: pull individuals from the queue and solve them.

    The worker sees exactly one GPU: ``CUDA_VISIBLE_DEVICES`` is set before
    CuPy is imported, so the process allocates its lattices on that device
    only and never talks to the other GPUs.
    """
    os.environ["CUDA_VISIBLE_DEVICES"] = str(device_index)
    module = _resolve_array_module(None)
    scheme = _resolve_scheme(profile)
    grid_cells = _resolve_grid_cells(profile, ())
    try:
        while True:
            try:
                payload = task_queue.get(timeout=1.0)
            except Exception:
                break
            if payload is None:
                break
            solver = LbmDnsSolver(module, grid_cells, scheme)
            result_queue.put(solver.solve(payload))
    finally:
        try:
            task_queue.close()
        except Exception:
            pass


def _resolve_array_module(xp: Any) -> Any:
    """Return the array module to use, preferring CuPy when it is importable."""
    if xp is not None:
        return xp
    try:  # pragma: no cover - depends on the runtime environment
        import cupy  # type: ignore[import-not-found]

        if cupy.cuda.runtime.getDeviceCount() > 0:
            return cupy
    except Exception:
        pass
    return np


def _resolve_scheme(profile: Any) -> str:
    """Return a valid scheme name for the given scaling profile."""
    scheme = getattr(profile, "stage3_scheme", None)
    if not isinstance(scheme, str) or scheme not in STAGE3_MODES:
        return STAGE3_MODES[0]
    return scheme


def _resolve_grid_cells(profile: Any, requests: Sequence[Stage3Request]) -> int:
    """Return the effective lattice budget, honouring the request override."""
    grid_cells = None
    effective = getattr(profile, "effective_grid_cells", None)
    if callable(effective):
        try:
            grid_cells = int(effective())
        except Exception:
            grid_cells = None
    if grid_cells is None or grid_cells <= 0:
        grid_cells = 32768
    for request in requests:
        requested = int(getattr(request, "grid_cells", 0) or 0)
        if requested > 0:
            grid_cells = requested
            break
    return int(grid_cells)


def _resolve_device_count(profile: Any) -> int:
    """Return the number of GPUs usable for stage 3."""
    for attribute in ("stage3_workers_available", "device_count"):
        value = getattr(profile, attribute, None)
        if value is None:
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    return 1
