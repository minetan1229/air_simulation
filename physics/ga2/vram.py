"""A fixed VRAM pool for the second generation search.

The search has to run for months on one consumer card.  The usual way of
serving the scratch space of a solver -- allocate an array whenever a stage
needs one and free it afterwards -- is exactly what destroys such a run: the
caching allocator grows into holes, the driver has to compact the device and
one day a stage cannot be served with a contiguous block any more, so the
process dies with an out of memory error after 4000 healthy generations.

:class:`VramPool` removes the problem instead of managing it.  The pool owns
*one* buffer, allocates it once and carves the named workspaces out of it.
The size of the buffer comes from the VRAM of the device and the fraction of
the specification, or from an explicit request of the caller.  After
:meth:`VramPool.reserve` the generation loop performs **no** device
allocation at all: :meth:`VramPool.workspace` returns a view into the buffer.

The pool works without a CUDA device as well -- the buffer is then a NumPy
array and every code path is the same, so the pool can be exercised on a
machine without a GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "VramPoolPlan",
    "VramPool",
    "DEFAULT_POOL_FRACTION",
    "WORKSPACE_ALIGNMENT",
]


#: The fraction of the VRAM of the device the pool may occupy.  The rest is
#: left to the driver so that a second worker can start on the same card.
DEFAULT_POOL_FRACTION = 0.85

#: The alignment of a workspace inside the pool buffer, in bytes.  A cache
#: line keeps the strided access of the solver from splitting a transaction
#: over two memory pages.
WORKSPACE_ALIGNMENT = 64


def _align_up(value: int, alignment: int) -> int:
    """Round ``value`` up to the next multiple of ``alignment``."""
    amount = int(alignment)
    if amount <= 1:
        return int(value)
    remainder = int(value) % amount
    if remainder == 0:
        return int(value)
    return int(value) + (amount - remainder)


def _import_cupy() -> Optional[Any]:
    """Return the CuPy module, or ``None`` when the host has no CuPy."""
    try:
        import cupy
    except Exception:  # pragma: no cover - depends on the host
        return None
    return cupy


@dataclass
class VramPoolPlan:
    """The memory plan of one pool.

    ``total_bytes``
        the VRAM of the device (the real number of the driver when CuPy is
        available, the declared size of the constructor otherwise).
    ``budget_bytes``
        the part of the device the pool may occupy: ``fraction * total``.
    ``reserved_bytes``
        the bytes the pool has already taken out of the budget.
    ``free_bytes``
        the bytes that are still available for the pool, i.e. what is left
        of the budget and what the driver still reports as free.
    """

    device_index: int
    total_bytes: int
    budget_bytes: int
    reserved_bytes: int
    free_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the plan."""
        return {
            "device_index": int(self.device_index),
            "total_bytes": int(self.total_bytes),
            "budget_bytes": int(self.budget_bytes),
            "reserved_bytes": int(self.reserved_bytes),
            "free_bytes": int(self.free_bytes),
        }


class VramPool:
    """One pre-allocated buffer that serves every scratch array of a run.

    The pool is created with the index of the device it belongs to, the
    fraction of the VRAM it may use and -- for the hosts without a GPU -- the
    size of the device.  The buffer itself is allocated by
    :meth:`reserve`; until then the pool only knows the plan.

    Every workspace is a *view* into that buffer: no memory is allocated
    when a workspace is requested, which is what keeps the generation loop
    free of allocations.  The layout of a workspace is fixed once it has
    been requested, so a workspace that is asked for a second time with
    another shape is a programming error and raises a :class:`ValueError`.
    """

    def __init__(
        self,
        device_index: int = 0,
        fraction: float = DEFAULT_POOL_FRACTION,
        total_bytes: int = 0,
        reserve_bytes: int = 0,
    ) -> None:
        """Create the pool of one device.

        ``device_index``
            the CUDA device the pool belongs to; it is honoured with
            ``cupy.cuda.Device(device_index)``.
        ``fraction``
            the part of the VRAM of the device the pool may occupy.
        ``total_bytes``
            the size of the device for the hosts without a CUDA device; on a
            host with a GPU the real size of the device is used instead.
        ``reserve_bytes``
            the size of the buffer; zero means "the whole budget".
        """
        share = float(fraction)
        if not (0.0 < share <= 1.0):
            raise ValueError("'fraction' must be within (0, 1]")
        if int(device_index) < 0:
            raise ValueError("'device_index' must not be negative")
        if int(total_bytes) < 0:
            raise ValueError("'total_bytes' must not be negative")
        if int(reserve_bytes) < 0:
            raise ValueError("'reserve_bytes' must not be negative")
        self._device_index = int(device_index)
        self._fraction = share
        self._declared_total_bytes = int(total_bytes)
        self._requested_reserve_bytes = int(reserve_bytes)
        self._cupy = _import_cupy()
        self._backend = "numpy"
        self._device: Any = None
        self._buffer: Any = None
        self._reserved_bytes = 0
        self._offset = 0
        self._workspaces: Dict[str, Dict[str, Any]] = {}
        self._device_total_bytes = 0
        self._device_free_bytes = 0
        self._pool_error: Optional[str] = None
        self._select_backend()

    # ------------------------------------------------------------------
    # The backend
    # ------------------------------------------------------------------
    def _select_backend(self) -> None:
        """Pick CuPy or NumPy and bind the pool to its device."""
        cupy = self._cupy
        if cupy is None:
            return
        try:
            count = int(cupy.cuda.runtime.getDeviceCount())
        except Exception:  # pragma: no cover - depends on the driver
            return
        if count <= 0:
            return
        if self._device_index >= count:
            raise ValueError(
                f"device index {self._device_index} is out of range; the host has {count} CUDA device(s)"
            )
        self._backend = "cupy"
        self._device = cupy.cuda.Device(self._device_index)

    def _probe_device(self) -> Tuple[int, int]:
        """Return the free and the total VRAM of the device."""
        cupy = self._cupy
        if self._backend != "cupy" or cupy is None:
            return 0, 0
        try:
            with self._device:
                free, total = cupy.cuda.runtime.memGetInfo()
        except Exception:  # pragma: no cover - depends on the driver
            return 0, 0
        return int(free), int(total)

    @property
    def backend(self) -> str:
        """The backend of the pool: ``"cupy"`` or ``"numpy"``."""
        return self._backend

    @property
    def device_index(self) -> int:
        """The index of the device the pool belongs to."""
        return self._device_index

    @property
    def reserved_bytes(self) -> int:
        """The size of the buffer the pool holds."""
        return int(self._reserved_bytes)

    # ------------------------------------------------------------------
    # The plan
    # ------------------------------------------------------------------
    def plan(self) -> VramPoolPlan:
        """The memory plan of the pool.

        On a host with a CUDA device the numbers come from the driver
        (``cupy.cuda.runtime.memGetInfo``); on every other host they come
        from the constructor.
        """
        device_free, device_total = self._probe_device()
        self._device_free_bytes = device_free
        self._device_total_bytes = device_total
        if self._backend == "cupy" and device_total > 0:
            total = device_total
        else:
            total = self._declared_total_bytes
        budget = int(total * self._fraction)
        remaining = max(0, budget - self._reserved_bytes)
        if self._backend == "cupy":
            free = max(0, min(remaining, device_free))
        else:
            free = remaining
        return VramPoolPlan(
            device_index=self._device_index,
            total_bytes=int(total),
            budget_bytes=int(budget),
            reserved_bytes=int(self._reserved_bytes),
            free_bytes=int(free),
        )

    # ------------------------------------------------------------------
    # The buffer
    # ------------------------------------------------------------------
    def reserve(self) -> VramPoolPlan:
        """Allocate the buffer of the pool and return the new plan.

        The buffer is allocated exactly once: a second call only reports the
        plan of the pool that is already reserved.  Its size is the explicit
        request of the constructor, or the budget of the device (limited by
        what the driver still holds free).
        """
        if self._buffer is not None:
            return self.plan()
        current = self.plan()
        size = self._requested_reserve_bytes
        if size <= 0:
            size = current.budget_bytes
            if self._backend == "cupy":
                size = min(size, current.free_bytes)
        if size <= 0:
            raise ValueError(
                "the size of the pool is unknown: pass 'reserve_bytes' or 'total_bytes' to the "
                "constructor, or run the search on a host with a CUDA device"
            )
        self._buffer = self._allocate(int(size))
        self._reserved_bytes = int(size)
        self._offset = 0
        self._workspaces = {}
        return self.plan()

    def _allocate(self, size: int) -> Any:
        """Allocate the zeroed buffer of the pool."""
        cupy = self._cupy
        if self._backend == "cupy":
            if cupy is None:
                raise MemoryError("the CuPy backend of the pool is not available")
            try:
                with self._device:
                    return cupy.zeros(size, dtype=cupy.uint8)
            except Exception as exc:
                raise MemoryError(
                    f"cannot reserve {size} bytes on CUDA device {self._device_index}: {exc}"
                ) from exc
        try:
            return np.zeros(size, dtype=np.uint8)
        except MemoryError as exc:
            raise MemoryError(f"cannot reserve {size} bytes of host memory: {exc}") from exc

    def _ensure_reserved(self) -> None:
        """Reserve the pool when the caller did not do it."""
        if self._buffer is None:
            self.reserve()

    # ------------------------------------------------------------------
    # The workspaces
    # ------------------------------------------------------------------
    def workspace(self, name: str, shape: Sequence[int], dtype: Any = None) -> Any:
        """Return the view of a named workspace of the pool.

        The workspace is carved out of the buffer of the pool; no memory is
        allocated, the returned array shares its storage with the buffer.
        The name is the key of the layout: asking a second time for the same
        name with another shape raises a :class:`ValueError`, because the
        layout of the pool is fixed once a workspace exists.
        """
        self._ensure_reserved()
        key = str(name)
        if not key:
            raise ValueError("the name of a workspace must not be empty")
        dims = _normalise_shape(shape)
        target = np.dtype(np.float64 if dtype is None else dtype)
        existing = self._workspaces.get(key)
        if existing is not None:
            if tuple(existing["shape"]) != dims:
                raise ValueError(
                    f"workspace '{key}' already exists with the shape {tuple(existing['shape'])}; "
                    f"the layout of the pool is fixed and cannot be changed to {dims}"
                )
            if target != existing["dtype"]:
                if target.itemsize != existing["dtype"].itemsize:
                    raise ValueError(
                        f"workspace '{key}' already exists with the dtype {existing['dtype']}; "
                        f"the layout of the pool is fixed and cannot be changed to {target}"
                    )
            return self._view(key, target)
        itemsize = int(target.itemsize)
        count = 1
        for value in dims:
            count *= int(value)
        nbytes = count * itemsize
        start = _align_up(_align_up(self._offset, WORKSPACE_ALIGNMENT), itemsize)
        end = start + nbytes
        if end > self._reserved_bytes:
            left = max(0, self._reserved_bytes - self._offset)
            raise MemoryError(
                f"workspace '{key}' needs {nbytes} bytes but only {left} bytes are left in the pool"
            )
        self._workspaces[key] = {
            "shape": dims,
            "dtype": target,
            "offset": int(start),
            "nbytes": int(nbytes),
        }
        self._offset = int(end)
        return self._view(key, target)

    def _view(self, name: str, dtype: Any) -> Any:
        """Build the view of a workspace without copying anything."""
        record = self._workspaces[name]
        start = int(record["offset"])
        nbytes = int(record["nbytes"])
        segment = self._buffer[start:start + nbytes]
        typed = segment.view(dtype)
        return typed.reshape(tuple(record["shape"]))

    # ------------------------------------------------------------------
    # The report
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """A JSON serialisable report of the pool."""
        plan = self.plan()
        workspaces: Dict[str, Any] = {}
        for name, record in self._workspaces.items():
            workspaces[name] = {
                "shape": [int(value) for value in record["shape"]],
                "dtype": str(record["dtype"]),
                "offset": int(record["offset"]),
                "nbytes": int(record["nbytes"]),
            }
        return {
            "backend": self._backend,
            "device_index": int(self._device_index),
            "total_bytes": int(plan.total_bytes),
            "budget_bytes": int(plan.budget_bytes),
            "reserved_bytes": int(plan.reserved_bytes),
            "free_bytes": int(plan.free_bytes),
            "used_bytes": int(self._offset),
            "available_bytes": max(0, int(self._reserved_bytes) - int(self._offset)),
            "device_total_bytes": int(self._device_total_bytes),
            "device_free_bytes": int(self._device_free_bytes),
            "workspaces": workspaces,
        }

    # ------------------------------------------------------------------
    # The life cycle
    # ------------------------------------------------------------------
    def release(self) -> None:
        """Give the buffer of the pool back to the system."""
        self._buffer = None
        self._workspaces = {}
        self._offset = 0
        self._reserved_bytes = 0
        if self._backend == "cupy" and self._cupy is not None:
            try:
                self._cupy.get_default_memory_pool().free_all_blocks()
            except Exception as exc:  # pragma: no cover - depends on the driver
                self._pool_error = f"releasing the memory pool of CuPy failed: {exc}"

    def __enter__(self) -> "VramPool":
        """Reserve the pool and return it."""
        self._ensure_reserved()
        return self

    def __exit__(self, *exc: Any) -> None:
        """Release the pool when the block is left."""
        self.release()


def _normalise_shape(shape: Sequence[int]) -> Tuple[int, ...]:
    """Turn the shape of a workspace into a tuple of dimensions."""
    if isinstance(shape, (int, np.integer)):
        return (int(shape),)
    dims = tuple(int(value) for value in shape)
    for value in dims:
        if value < 0:
            raise ValueError(f"a workspace cannot have the negative dimension {value}")
    return dims
