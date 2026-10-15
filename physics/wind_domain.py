"""Wind field domain -- mean wind, 3D simplex turbulence, thermals, ABL.

Frame convention (important, because the rest of the simulator needs it too)
-----------------------------------------------------------------------------
This module treats the **third component as the height above the ground plane,
positive upwards**.  That is forced by the physics itself:

* the atmospheric boundary layer is the log law ``u(z)/u_ref = ln(z/z0)/ln(z_ref/z0)``
  in the *height* ``z`` above the rough surface,
* the ground is the plane ``z = env.ground_level_m`` and no flow may penetrate it
  (so "up" must be ``+z``),
* :class:`config_schema.EnvironmentConfig` stores ``altitude_m``,
  ``ground_level_m`` and ``reference_height_m`` on the same positive-up axis.

The horizontal plane of motion is therefore the ``(x, y)`` plane and the two
compass axes are defined as::

    north = +y        east = +x        up = +z

**Meteorological wind-direction convention.**  ``wind_direction_deg`` follows
the WMO meteorological convention: it is the compass bearing, in degrees
clockwise from north, of the direction the wind blows **FROM**.  The velocity
vector therefore points the opposite way::

    theta  = radians(wind_direction_deg)
    "from" unit vector = ( sin(theta), cos(theta), 0 )   # north = +y, east = +x
    flow direction     = -(sin(theta), cos(theta), 0)

Consequences that the test-suite asserts verbatim:

====================  ==========================  ==========================
``wind_direction_deg``  meaning                     mean flow vector
====================  ==========================  ==========================
``0``                 from the north, blows south   ``(0, -V, 0)``
``90``                from the east, blows west     ``(-V, 0, 0)``
``180``               from the south, blows north   ``(0, +V, 0)``
``270``               from the west, blows east     ``(+V, 0, 0)``
====================  ==========================  ==========================

Turbulence
----------
Genuine 3D simplex noise in the canonical Gustavson formulation (the vectorised
Ashima / webgl-noise form: skew into the simplex lattice, four corner gradient
contributions, ``mod289`` / ``permute`` gradient hash, 42x output scaling).
The implementation is **fully vectorised** -- it consumes an ``(N, 3)`` array
and never loops over the points -- and it is *arithmetic only* (no fancy
indexing), which is precisely what lets the identical code run on CuPy and on
``torch.cuda``.  Seeding is done by a per-axis affine relabelling of the lattice
coordinates (``i -> A i + B (mod 289)`` with ``gcd(A, 289) = 1``), a bijection
of the lattice, which preserves the gradient statistics while producing a
completely different field for every ``turbulence_seed``.

GPU backend with honest fallback
--------------------------------
:func:`active_backend` probes for ``cupy`` then ``torch.cuda``.
:func:`wind_field` computes the field with that backend when it is real and with
NumPy otherwise, and **always reports in the response which one actually ran**
under the ``backend`` key.  The maths lives in a single backend-agnostic routine
that only swaps the array module, so the CuPy / torch path reproduces the NumPy
reference to well below 1e-6 (for the same float64 kernels, bit for bit).  On a
machine without a GPU ``active_backend() == "numpy"`` and ``wind_field`` reports
``"numpy"`` -- no silent GPU claim is ever made.
"""

from __future__ import annotations

import importlib
import math
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from .config_schema import EnvironmentConfig, clamp, deg2rad

__all__ = [
    "active_backend",
    "invalidate_backend_cache",
    "simplex_noise3",
    "gust_factor",
    "boundary_layer_profile",
    "thermal_updraft",
    "wind_velocity",
    "wind_field",
    "mean_wind_vector",
    "wind_direction_unit",
    "turbulence_amplitude",
    "boundary_layer_ratio",
    "thermal_geometry",
    "velocity_components",
    "BACKEND_NAMES",
]

#: The only backend identifiers this module will ever emit.
BACKEND_NAMES: Tuple[str, ...] = ("cupy", "torch", "numpy")

# --------------------------------------------------------------------------
# Simplex-noise kernel constants (Gustavson formulation)
# --------------------------------------------------------------------------
_SIMPLEX_SKEW = 1.0 / 3.0
_SIMPLEX_UNSKEW = 1.0 / 6.0
_SIMPLEX_MOD = 289.0                 # 17**2, the modulus used by mod289
_SIMPLEX_OUTPUT_SCALE = 42.0
_SIMPLEX_GRAD_SCALE = 0.6            # squared radius of the noise kernel
#: ``ns = n_ * D.wyz - D.xzx`` of the reference shader, with the shader's own
#: 12-significant-digit literal ``n_`` (kept verbatim for bit-exact portability).
_SIMPLEX_N_LIT = 0.142857142857
_SIMPLEX_NS_X = 0.285714285714
_SIMPLEX_NS_Y = -0.9285714285715
_SIMPLEX_NS_Z = 0.142857142857

#: Normalised gust spectrum ``(weight, period_s)``; the weights sum to one so the
#: envelope stays inside ``1 +/- gust_intensity``.
_GUST_COMPONENTS: Tuple[Tuple[float, float], ...] = (
    (0.60, 7.3),
    (0.30, 19.7),
    (0.10, 53.1),
)

#: Spatial offsets (noise units) that decorrelate the three turbulence channels.
_TURBULENCE_CHANNELS: Tuple[Tuple[float, float, float], ...] = (
    (0.0, 0.0, 0.0),
    (37.13, 11.71, 5.37),
    (-19.41, 83.23, 27.89),
)
_TURBULENCE_WAVELENGTH_BASE_M = 4.0
_TURBULENCE_WAVELENGTH_MIN_M = 0.50

#: Thermal plume geometry, see :func:`thermal_geometry`.
_THERMAL_SIGMA_BASE_M = 0.35
_THERMAL_SIGMA_PER_MS = 0.45
_THERMAL_SIGMA_MAX_M = 12.0
_THERMAL_HEIGHT_BASE_M = 1.20
_THERMAL_HEIGHT_PER_MS = 1.60
_THERMAL_HEIGHT_MAX_M = 40.0
_THERMAL_WANDER_BASE_M = 2.00
_THERMAL_WANDER_PER_MS = 1.00
_THERMAL_WANDER_PERIOD_S = 24.0
_THERMAL_ENTRAINMENT_RATIO = 0.25

#: Depth over which the impermeable ground ramps the vertical motion out.
_GROUND_DAMPING_DEPTH_M = 0.50

_MIN_ROUGHNESS_M = 1.0e-6
_MIN_DIRECTION_SPEED = 1.0e-9
_MIN_LENGTH_M = 1.0e-9

_BACKEND_CACHE: Dict[str, Any] = {}


# ==========================================================================
# Backend probing -- never claims a GPU that is not really there
# ==========================================================================
def _try_import(name: str):
    """Import ``name`` or return ``None``; a missing package is never an error."""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _probe_backend() -> str:
    """Return ``"cupy"`` / ``"torch"`` if a usable device exists, else ``"numpy"``."""
    cupy_module = _try_import("cupy")
    if cupy_module is not None:
        try:
            if int(cupy_module.cuda.runtime.getDeviceCount()) > 0:  # type: ignore[attr-defined]
                _BACKEND_CACHE["module"] = cupy_module
                return "cupy"
        except Exception:
            _BACKEND_CACHE["module"] = None

    torch_module = _try_import("torch")
    if torch_module is not None:
        try:
            if bool(torch_module.cuda.is_available()) and int(torch_module.cuda.device_count()) > 0:
                _BACKEND_CACHE["module"] = torch_module
                return "torch"
        except Exception:
            _BACKEND_CACHE["module"] = None

    return "numpy"


def active_backend() -> str:
    """Name of the array backend that will actually execute the field maths.

    Probes CuPy first, then ``torch.cuda``; both probes are wrapped so a missing
    package or a broken driver can never break :func:`wind_field`.  Returns
    exactly one of ``"cupy"``, ``"torch"``, ``"numpy"``.
    """
    if "name" not in _BACKEND_CACHE:
        _BACKEND_CACHE["name"] = _probe_backend()
    return str(_BACKEND_CACHE["name"])


def invalidate_backend_cache() -> None:
    """Force the next :func:`active_backend` call to re-probe the machine."""
    _BACKEND_CACHE.pop("name", None)
    _BACKEND_CACHE.pop("module", None)


def _array_module(backend: str):
    """The array namespace (``numpy`` / ``cupy`` / ``torch``) for ``backend``."""
    if backend == "numpy":
        return np
    module = _BACKEND_CACHE.get("module")
    if module is None:
        _probe_backend()
        module = _BACKEND_CACHE.get("module")
    return module if module is not None else np


def _resolve_backend(requested: str) -> Tuple[str, str]:
    """Map a user request onto the backend that will really run.

    Returns ``(backend, backend_requested)``.  An explicit GPU request that
    cannot be honoured degrades to NumPy and the response then honestly says
    ``"numpy"`` -- a GPU is never claimed without evidence.
    """
    name = str(requested or "auto").strip().lower()
    if name == "auto":
        return active_backend(), "auto"
    if name == "numpy":
        return "numpy", name
    if name in ("cupy", "torch"):
        real = active_backend()
        return (name, name) if real == name else ("numpy", name)
    raise ValueError(
        f"unknown wind backend '{requested}'; expected 'auto', 'numpy', 'cupy' or 'torch'"
    )


class _Ops:
    """Backend array namespace plus a float64 host->device transfer helper."""

    __slots__ = ("name", "xp")

    def __init__(self, backend: str) -> None:
        self.name = backend
        self.xp = _array_module(backend)

    def const(self, values: Any):
        """Upload a small host constant to the backend device as float64."""
        host = np.asarray(values, dtype=np.float64)
        if self.name == "numpy":
            return host
        module = self.xp
        if self.name == "cupy":
            return module.asarray(host)
        if self.name == "torch":
            return module.tensor(host, dtype=module.float64, device=module.device("cuda"))
        return host

    def to_host(self, values: Any) -> np.ndarray:
        """Copy a backend array back into a NumPy float64 array."""
        if self.name == "numpy":
            return np.asarray(values, dtype=np.float64)
        module = self.xp
        if self.name == "cupy":
            return module.asnumpy(values)
        if self.name == "torch":
            return values.detach().cpu().numpy().astype(np.float64, copy=False)
        return np.asarray(values, dtype=np.float64)


# ==========================================================================
# Vectorised 3D simplex noise (Gustavson / Ashima formulation)
# ==========================================================================
_SIMPLEX_SEED_TABLE_CACHE: Dict[int, Tuple[float, ...]] = {}


def _simplex_seed_table(seed: int) -> Tuple[float, ...]:
    """Per-axis affine lattice relabelling ``i -> (A i + B) mod 289``.

    ``A`` is forced coprime with 289 so the map is a bijection of the lattice,
    keeping the gradient distribution of the untouched simplex noise while fully
    decorrelating the field from the unseeded one.  Deterministic in ``seed``.

    Memoised: the table depends only on ``seed``, and the flight integrator asks
    for it on every RK4 stage (thousands of times per simulated second), where
    rebuilding the RNG was the single largest cost in the wind inner loop.
    """
    key = int(seed) & 0xFFFFFFFF
    cached = _SIMPLEX_SEED_TABLE_CACHE.get(key)
    if cached is not None:
        return cached
    rng = np.random.default_rng(key)
    multipliers: List[float] = []
    offsets: List[float] = []
    for _ in range(3):
        while True:
            candidate = int(rng.integers(2, int(_SIMPLEX_MOD)))
            if math.gcd(candidate, int(_SIMPLEX_MOD)) == 1:
                multipliers.append(float(candidate))
                break
        offsets.append(float(rng.integers(0, int(_SIMPLEX_MOD))))
    table = (multipliers[0], offsets[0], multipliers[1], offsets[1], multipliers[2], offsets[2])
    _SIMPLEX_SEED_TABLE_CACHE[key] = table
    return table


def _mod289(values, xp):
    return values - xp.floor(values * (1.0 / _SIMPLEX_MOD)) * _SIMPLEX_MOD


def _permute(values, xp):
    return _mod289(((values * 34.0) + 1.0) * values, xp)


def _inv_sqrt(values, xp):
    """Exact inverse square root of the gradient norm.

    The reference shader uses the two-term Taylor series
    ``1.79284291400159 - 0.85373472095314 r`` instead of ``1/sqrt(r)``.  That
    approximation is only valid for ``r`` near 1 and turns *negative* for
    ``r > 2.1``, which happens for the far corners of a tetrahedron and produces
    heavy tails.  The exact form is used here: it is the normalisation of
    Gustavson's original C code, it is a pure elementwise op available in
    NumPy / CuPy / torch, and it keeps the kernel strictly bounded.
    """
    return 1.0 / xp.sqrt(xp.maximum(values, 1.0e-300))


def _simplex_noise3_points(P, seed: int, ops: _Ops):
    """Simplex noise of an ``(N, 3)`` device array; returns an ``(N,)`` device array.

    Every operation is elementwise arithmetic / ``floor`` / ``minimum`` /
    ``maximum`` / ``where`` -- all provided by NumPy, CuPy and torch -- so the
    numerical result does not depend on which one is used.
    """
    xp = ops.xp
    px = P[:, 0]
    py = P[:, 1]
    pz = P[:, 2]

    skew = (px + py + pz) * _SIMPLEX_SKEW
    i_raw = xp.floor(px + skew)
    j_raw = xp.floor(py + skew)
    k_raw = xp.floor(pz + skew)

    # Unskew with the *raw* lattice indices -- the corners must stay geometric.
    unskew = (i_raw + j_raw + k_raw) * _SIMPLEX_UNSKEW
    x0 = px - (i_raw - unskew)
    y0 = py - (j_raw - unskew)
    z0 = pz - (k_raw - unskew)

    ax, bx, ay, by, az, bz = _simplex_seed_table(seed)
    i = _mod289(i_raw * ax + bx, xp)
    j = _mod289(j_raw * ay + by, xp)
    k = _mod289(k_raw * az + bz, xp)

    # --- which of the six tetrahedra of the unit cube contains (x0, y0, z0)?
    # g = step(x0.yzx, x0.xyz): component k compares x0[(k+1)%3] <= x0[k].
    gx = xp.where(x0 >= y0, 1.0, 0.0)
    gy = xp.where(y0 >= z0, 1.0, 0.0)
    gz = xp.where(z0 >= x0, 1.0, 0.0)
    lx = 1.0 - gx
    ly = 1.0 - gy
    lz = 1.0 - gz
    i1x = xp.minimum(gx, lz)
    i1y = xp.minimum(gy, lx)
    i1z = xp.minimum(gz, ly)
    i2x = xp.maximum(gx, lz)
    i2y = xp.maximum(gy, lx)
    i2z = xp.maximum(gz, ly)

    x1 = x0 - i1x + _SIMPLEX_UNSKEW
    y1 = y0 - i1y + _SIMPLEX_UNSKEW
    z1 = z0 - i1z + _SIMPLEX_UNSKEW
    x2 = x0 - i2x + 2.0 * _SIMPLEX_UNSKEW
    y2 = y0 - i2y + 2.0 * _SIMPLEX_UNSKEW
    z2 = z0 - i2z + 2.0 * _SIMPLEX_UNSKEW
    x3 = x0 - 1.0 + 3.0 * _SIMPLEX_UNSKEW
    y3 = y0 - 1.0 + 3.0 * _SIMPLEX_UNSKEW
    z3 = z0 - 1.0 + 3.0 * _SIMPLEX_UNSKEW

    zeros = xp.zeros_like(px)
    ones = xp.ones_like(px)
    offsets_z = xp.stack([zeros, i1z, i2z, ones], axis=-1)
    offsets_y = xp.stack([zeros, i1y, i2y, ones], axis=-1)
    offsets_x = xp.stack([zeros, i1x, i2x, ones], axis=-1)

    p = _permute(
        _permute(
            _permute(k.reshape(-1, 1) + offsets_z, xp) + j.reshape(-1, 1) + offsets_y,
            xp,
        )
        + i.reshape(-1, 1)
        + offsets_x,
        xp,
    )

    j_hash = p - 49.0 * xp.floor(p * _SIMPLEX_NS_Z * _SIMPLEX_NS_Z)
    xh = xp.floor(j_hash * _SIMPLEX_NS_Z)
    yh = xp.floor(j_hash - 7.0 * xh)
    gxv = xh * _SIMPLEX_NS_X + _SIMPLEX_NS_Y
    gyv = yh * _SIMPLEX_NS_X + _SIMPLEX_NS_Y
    h = 1.0 - xp.abs(gxv) - xp.abs(gyv)

    # b0 = (gxv.xy, gyv.xy) ; b1 = (gxv.zw, gyv.zw)
    b0 = xp.stack([gxv[:, 0], gxv[:, 1], gyv[:, 0], gyv[:, 1]], axis=-1)
    b1 = xp.stack([gxv[:, 2], gxv[:, 3], gyv[:, 2], gyv[:, 3]], axis=-1)
    s0 = xp.floor(b0) * 2.0 + 1.0
    s1 = xp.floor(b1) * 2.0 + 1.0
    # sh = -step(h, 0)  ->  0 where h > 0, -1 where h <= 0
    sh = xp.where(h > 0.0, 0.0, -1.0)

    a0 = xp.stack(
        [
            b0[:, 0] + s0[:, 0] * sh[:, 0],
            b0[:, 2] + s0[:, 2] * sh[:, 0],
            b0[:, 1] + s0[:, 1] * sh[:, 1],
            b0[:, 3] + s0[:, 3] * sh[:, 1],
        ],
        axis=-1,
    )
    a1 = xp.stack(
        [
            b1[:, 0] + s1[:, 0] * sh[:, 2],
            b1[:, 2] + s1[:, 2] * sh[:, 2],
            b1[:, 1] + s1[:, 1] * sh[:, 3],
            b1[:, 3] + s1[:, 3] * sh[:, 3],
        ],
        axis=-1,
    )

    # Four gradient vectors of the tetra vertices, normalised with the
    # Taylor series inverse square root (Selle / Gustavson).
    px0 = a0[:, 0]
    py0 = a0[:, 1]
    pz0 = h[:, 0]
    px1 = a0[:, 2]
    py1 = a0[:, 3]
    pz1 = h[:, 1]
    px2 = a1[:, 0]
    py2 = a1[:, 1]
    pz2 = h[:, 2]
    px3 = a1[:, 2]
    py3 = a1[:, 3]
    pz3 = h[:, 3]

    norm = _inv_sqrt(
        xp.stack(
            [
                px0 * px0 + py0 * py0 + pz0 * pz0,
                px1 * px1 + py1 * py1 + pz1 * pz1,
                px2 * px2 + py2 * py2 + pz2 * pz2,
                px3 * px3 + py3 * py3 + pz3 * pz3,
            ],
            axis=-1,
        ),
        xp,
    )
    px0 = px0 * norm[:, 0]
    py0 = py0 * norm[:, 0]
    pz0 = pz0 * norm[:, 0]
    px1 = px1 * norm[:, 1]
    py1 = py1 * norm[:, 1]
    pz1 = pz1 * norm[:, 1]
    px2 = px2 * norm[:, 2]
    py2 = py2 * norm[:, 2]
    pz2 = pz2 * norm[:, 2]
    px3 = px3 * norm[:, 3]
    py3 = py3 * norm[:, 3]
    pz3 = pz3 * norm[:, 3]

    d0 = x0 * x0 + y0 * y0 + z0 * z0
    d1 = x1 * x1 + y1 * y1 + z1 * z1
    d2 = x2 * x2 + y2 * y2 + z2 * z2
    d3 = x3 * x3 + y3 * y3 + z3 * z3
    m0 = xp.maximum(_SIMPLEX_GRAD_SCALE - d0, 0.0)
    m1 = xp.maximum(_SIMPLEX_GRAD_SCALE - d1, 0.0)
    m2 = xp.maximum(_SIMPLEX_GRAD_SCALE - d2, 0.0)
    m3 = xp.maximum(_SIMPLEX_GRAD_SCALE - d3, 0.0)
    m0 = m0 * m0
    m1 = m1 * m1
    m2 = m2 * m2
    m3 = m3 * m3

    total = (
        m0 * m0 * (px0 * x0 + py0 * y0 + pz0 * z0)
        + m1 * m1 * (px1 * x1 + py1 * y1 + pz1 * z1)
        + m2 * m2 * (px2 * x2 + py2 * y2 + pz2 * z2)
        + m3 * m3 * (px3 * x3 + py3 * y3 + pz3 * z3)
    )
    return _SIMPLEX_OUTPUT_SCALE * total


def _as_points(position: Any) -> Tuple[np.ndarray, bool]:
    """Coerce ``position`` into an ``(N, 3)`` float64 array.

    Returns ``(points, flat_output)``; ``flat_output`` is ``True`` when the
    caller passed a single 3-vector so the public wrappers hand back a ``(3,)``
    array instead of a ``(1, 3)`` one.
    """
    raw = np.asarray(position, dtype=np.float64)
    flat = raw.ndim == 1
    arr = raw.reshape(-1)
    if arr.size == 0:
        return np.zeros((0, 3), dtype=np.float64), flat
    if arr.size == 3:
        return arr.reshape(1, 3), flat
    if arr.size % 3 == 0:
        return arr.reshape(-1, 3), flat
    padded = np.zeros(3, dtype=np.float64)
    padded[: min(arr.size, 3)] = arr[:3]
    return padded.reshape(1, 3), flat


def simplex_noise3(points, seed: int = 0) -> np.ndarray:
    """Vectorised 3D simplex noise, ``(N, 3)`` points -> ``(N,)`` values in ``[-1, 1]``.

    Parameters
    ----------
    points:
        ``(N, 3)`` array of sample positions.  A single ``(3,)`` vector, a bare
        scalar, or any array whose length is a multiple of three is accepted and
        reshaped internally -- the function never raises on scalar input.
    seed:
        Lattice relabelling seed; bit-for-bit reproducible for a fixed seed.

    The output is clipped into ``[-1, 1]``, the documented range of the
    Gustavson kernel (its theoretical maximum is exactly 1.0).
    """
    pts, _ = _as_points(points)
    if pts.shape[0] == 0:
        return np.zeros(0, dtype=np.float64)
    values = _simplex_noise3_points(pts, int(seed), _Ops("numpy"))
    return np.clip(np.asarray(values, dtype=np.float64), -1.0, 1.0)


# ==========================================================================
# Mean wind and gusts
# ==========================================================================
def wind_direction_unit(env: EnvironmentConfig) -> np.ndarray:
    """Unit horizontal vector the wind flows **along** (``north = +y, east = +x``).

    ``wind_direction_deg`` is the meteorological "from" bearing, so the returned
    vector is the negative of the "from" direction.
    """
    theta = deg2rad(env.wind_direction_deg)
    return np.array([-math.sin(theta), -math.cos(theta), 0.0], dtype=np.float64)


def mean_wind_vector(env: EnvironmentConfig) -> np.ndarray:
    """Steady mean horizontal wind ``(u, v, 0)``.

    The magnitude is exactly ``max(env.wind_speed, 0)``; gusts, turbulence,
    thermal plumes and the boundary-layer shaping are *separate* factors applied
    by :func:`wind_velocity`, which keeps this primitive exactly testable.
    """
    speed = max(float(env.wind_speed), 0.0)
    if speed <= _MIN_DIRECTION_SPEED:
        return np.zeros(3, dtype=np.float64)
    return wind_direction_unit(env) * speed


def gust_factor(env: EnvironmentConfig, time_s: float = 0.0) -> float:
    """Slow gust envelope ``1 + gust_intensity * sum_k w_k sin(2 pi t / T_k + phi_k)``.

    The three weights sum to one and the phases are derived from
    ``turbulence_seed``, so the envelope stays inside
    ``[1 - gust_intensity, 1 + gust_intensity]`` and never goes negative.  With
    ``gust_intensity == 0`` the factor is exactly ``1.0``.
    """
    intensity = clamp(env.gust_intensity, 0.0, 1.0)
    if intensity <= 0.0:
        return 1.0
    seed = int(env.turbulence_seed)
    time = float(time_s)
    envelope = 0.0
    for index, (weight, period) in enumerate(_GUST_COMPONENTS):
        phase = ((seed * (index + 1) * 0.6180339887498949) % 1.0) * (2.0 * math.pi)
        envelope += weight * math.sin(2.0 * math.pi * time / period + phase)
    return max(0.0, 1.0 + intensity * envelope)


def turbulence_amplitude(env: EnvironmentConfig) -> float:
    """Turbulence velocity amplitude ``gust_intensity * wind_speed * turbulence_scale``."""
    return (
        clamp(env.gust_intensity, 0.0, 1.0)
        * max(float(env.wind_speed), 0.0)
        * max(float(env.turbulence_scale), 0.0)
    )


# ==========================================================================
# Atmospheric boundary layer (log wind profile)
# ==========================================================================
def _roughness_and_reference(env: EnvironmentConfig) -> Tuple[float, float]:
    roughness = max(abs(float(env.surface_roughness_m)), _MIN_ROUGHNESS_M)
    reference_height = abs(float(env.reference_height_m))
    if reference_height <= roughness:
        reference_height = roughness * (1.0 + 1.0e-6)
    return roughness, reference_height


def boundary_layer_profile(
    z: float,
    surface_roughness_m: float,
    reference_speed: float,
    reference_height_m: float,
    enabled: bool = True,
) -> float:
    """Neutral ABL log-law profile ``u(z) = u_ref ln(z/z0) / ln(z_ref/z0)``.

    ``z`` is the height above the rough surface, ``z0`` the aerodynamic
    roughness length and ``u_ref`` the wind speed at the reference height
    ``z_ref``.  The von Karman constant :data:`config_schema.VON_KARMAN` appears
    in the ``u/z_u = (1/kappa) ln(z/z0)`` form of the law; it cancels in this
    ratio form, which is why the ratio is the useful primitive.

    Guarantees: monotonically increasing in ``z``, exactly ``0`` for
    ``z <= z0`` (no log blow-up below the roughness length), non-negative
    everywhere, and exactly ``reference_speed`` at ``z == reference_height_m``.
    When ``enabled`` is ``False`` the uniform ``reference_speed`` is returned.
    """
    speed = float(reference_speed)
    if not bool(enabled):
        return speed

    roughness = max(abs(float(surface_roughness_m)), _MIN_ROUGHNESS_M)
    reference_height = abs(float(reference_height_m))
    if reference_height <= roughness:
        reference_height = roughness * (1.0 + 1.0e-6)

    height = float(z)
    if height <= roughness:
        return 0.0

    denominator = math.log(reference_height / roughness)
    if denominator <= 0.0:
        denominator = 1.0e-6
    return max(0.0, speed * math.log(height / roughness) / denominator)


def boundary_layer_ratio(heights, env: EnvironmentConfig) -> np.ndarray:
    """Vectorised ``ln(z/z0)/ln(z_ref/z0)`` for an array of heights above ground.

    Batched counterpart of :func:`boundary_layer_profile`; it returns the
    dimensionless shape factor ``u(z)/u_ref`` that the field solver multiplies
    the steady mean speed by.  Zero at and below the roughness length, unit at
    the reference height, non-negative and monotonically increasing everywhere.
    """
    values = np.asarray(heights, dtype=np.float64).reshape(-1)
    roughness, reference_height = _roughness_and_reference(env)
    denominator = math.log(reference_height / roughness)
    if denominator <= 0.0:
        denominator = 1.0e-6
    clamped = np.maximum(values, roughness)
    return np.maximum(np.log(clamped / roughness) / denominator, 0.0)


# ==========================================================================
# Thermal plumes
# ==========================================================================
def thermal_geometry(env: EnvironmentConfig) -> Tuple[float, float, float]:
    """Return ``(strength_mps, sigma_m, height_m)`` of the plume column.

    The plume is a Gaussian column of rising air whose core wanders in ``x``/``y``.
    Both the horizontal Gaussian width ``sigma`` and the vertical extent ``H``
    grow with the buoyancy, following the usual ``w_max`` scaling of a
    convective column::

        sigma = min(0.35 + 0.45 * w_max, 12.0)   [m]
        H     = min(1.20 + 1.60 * w_max, 40.0)   [m]

    The core sits one ``H`` above the ground plane, so the peak vertical speed
    is exactly ``thermal_strength`` (both exponentials are 1 there).  The
    horizontal entrainment amplitude is ``0.25 * thermal_strength`` (Rankine
    vortex profile), zero on the axis and far from the plume.
    """
    strength = max(float(env.thermal_strength), 0.0)
    sigma = min(_THERMAL_SIGMA_BASE_M + _THERMAL_SIGMA_PER_MS * strength, _THERMAL_SIGMA_MAX_M)
    height = min(_THERMAL_HEIGHT_BASE_M + _THERMAL_HEIGHT_PER_MS * strength, _THERMAL_HEIGHT_MAX_M)
    return strength, max(sigma, _MIN_LENGTH_M), max(height, _MIN_LENGTH_M)


def _thermal_core(env: EnvironmentConfig, strength: float, time_s: float) -> Tuple[float, float, float]:
    """Horizontal core position ``(x_c, y_c)`` and core height ``z_c`` of the plume."""
    seed = int(env.turbulence_seed)
    time = float(time_s)
    phase_x = ((seed % 6283) / 6283.0) * (2.0 * math.pi)
    phase_y = ((seed % 9973) / 9973.0) * (2.0 * math.pi)
    omega = 2.0 * math.pi / _THERMAL_WANDER_PERIOD_S
    amplitude = (_THERMAL_WANDER_BASE_M + _THERMAL_WANDER_PER_MS * strength) if strength > 0.0 else 0.0
    x_c = amplitude * math.sin(omega * time + phase_x)
    y_c = amplitude * math.cos(omega * time + phase_y)
    core_height = float(env.ground_level_m) + _THERMAL_HEIGHT_BASE_M + _THERMAL_HEIGHT_PER_MS * strength
    return x_c, y_c, core_height


def _thermal_components(P, env: EnvironmentConfig, time_s: float, ops: _Ops):
    """Plume velocity ``(u, v, w)`` for an ``(N, 3)`` device array."""
    xp = ops.xp
    strength, sigma, height = thermal_geometry(env)
    n = int(P.shape[0])
    if n == 0:
        return xp.zeros((0, 3), dtype=P.dtype)
    if strength <= 0.0:
        return xp.zeros((n, 3), dtype=P.dtype)

    x_c, y_c, core_height = _thermal_core(env, strength, time_s)
    dx = P[:, 0] - x_c
    dy = P[:, 1] - y_c
    dz = P[:, 2] - core_height

    radial_sq = dx * dx + dy * dy
    gaussian = xp.exp(-radial_sq / (2.0 * sigma * sigma))
    vertical = xp.exp(-(dz * dz) / (height * height))
    w = strength * gaussian * vertical

    distance = xp.sqrt(radial_sq)
    safe = xp.where(distance > _MIN_LENGTH_M, distance, xp.ones_like(distance))
    radial_speed = _THERMAL_ENTRAINMENT_RATIO * strength * (distance / sigma) * gaussian * vertical
    # Thermal radial divergence/entrainment:
    u = radial_speed * (dx / safe)
    v = radial_speed * (dy / safe)

    return xp.stack([u, v, w], axis=-1)


def thermal_updraft(position, env: EnvironmentConfig, time_s: float = 0.0) -> np.ndarray:
    """Plume velocity ``(u, v, w)`` at ``position``; the third component is ``w``.

    ::

        w = thermal_strength
            * exp( -( (x-x_c)^2 + (y-y_c)^2 ) / (2 sigma^2) )
            * exp( -( (z-z_c) / H )^2 )

    with ``sigma``/``H`` from :func:`thermal_geometry` and the core position
    ``(x_c, y_c)`` slowly orbiting on a 24 s period (phases from
    ``turbulence_seed``).  The first two components are the horizontal
    entrainment inflow, so the result can be summed straight into
    :func:`wind_velocity`.

    Returns a ``(3,)`` vector for a single point and an ``(N, 3)`` array for a
    batch.  At the core both exponentials equal 1, hence ``w`` is exactly
    ``thermal_strength`` -- the plume peak is strictly linear in the strength --
    and the whole field is identically zero for ``thermal_strength == 0``.
    """
    pts, flat = _as_points(position)
    values = _thermal_components(pts, env, time_s, _Ops("numpy"))
    return values[0] if flat else values


# ==========================================================================
# Full velocity field
# ==========================================================================
def _turbulence_components(P, env: EnvironmentConfig, drift_host: np.ndarray, ops: _Ops):
    """Three-channel vectorised simplex turbulence for an ``(N, 3)`` device array."""
    xp = ops.xp
    amplitude = turbulence_amplitude(env)
    n = int(P.shape[0])
    if n == 0 or amplitude <= 0.0:
        return xp.zeros((n, 3), dtype=P.dtype)

    scale = max(
        _TURBULENCE_WAVELENGTH_MIN_M,
        _TURBULENCE_WAVELENGTH_BASE_M * max(float(env.turbulence_scale), 0.0),
    )
    seed = int(env.turbulence_seed)
    drift = ops.const(np.asarray(drift_host, dtype=np.float64) / scale)

    channels: List[Any] = []
    for index, offset in enumerate(_TURBULENCE_CHANNELS):
        sampled = (P - drift) + ops.const(offset)
        channels.append(_simplex_noise3_points(sampled, seed + index, ops))
    return xp.stack(channels, axis=-1) * amplitude


def velocity_components(points, env: EnvironmentConfig, time_s: float = 0.0, backend: str = "numpy") -> np.ndarray:
    """Complete ``(N, 3)`` velocity field on the NumPy host -- the reference path.

    This is the single source of truth shared by :func:`wind_velocity` (which is
    the NumPy specialisation used by the flight integrator) and
    :func:`wind_field` (which may run the identical arithmetic on CuPy or
    ``torch.cuda``).  The composition is, in order:

    1. wind-tunnel mode -> uniform ``(wind_tunnel_speed, 0, 0)`` stream with no
       turbulence, no boundary layer and no thermals,
    2. the steady mean wind direction, its magnitude shaped per grid point by
       the log-law ABL profile evaluated at the height above the ground plane
       and scaled by the gust envelope,
    3. the thermal plume (vertical velocity + horizontal entrainment),
    4. three decorrelated channels of vectorised 3D simplex turbulence with
       amplitude ``gust_intensity * wind_speed * turbulence_scale``, advected by
       Taylor's frozen turbulence hypothesis,
    5. an impermeable ground: the plume and vertical motion are ramped linearly
       to zero across the lowest 0.5 m, so the vertical component is exactly
       ``0`` at and below ``env.ground_level_m``.
    """
    backend_name, _ = _resolve_backend(backend)
    return _velocity_device(np.asarray(points, dtype=np.float64), env, float(time_s), backend_name)


def _velocity_device(points: np.ndarray, env: EnvironmentConfig, time_s: float, backend: str) -> np.ndarray:
    """Run the field maths with ``backend`` and return a NumPy ``(N, 3)`` array."""
    ops = _Ops(backend)
    xp = ops.xp
    n = int(points.shape[0])
    if n == 0:
        return np.zeros((0, 3), dtype=np.float64)

    # --- wind tunnel measurement mode (AGENTS.md 5) -----------------------
    if bool(env.wind_tunnel_enabled):
        stream = np.zeros((n, 3), dtype=np.float64)
        stream[:, 0] = float(env.wind_tunnel_speed)
        return stream

    ground = float(env.ground_level_m)
    gust = gust_factor(env, time_s)

    # --- steady horizontal mean, shaped per point by the ABL log profile ---
    mean_vector = mean_wind_vector(env)
    mean_speed = float(math.hypot(mean_vector[0], mean_vector[1]))
    heights_agl = points[:, 2] - ground
    if bool(env.boundary_layer_enabled):
        ratio_host = boundary_layer_ratio(heights_agl, env)
    else:
        ratio_host = np.ones(n, dtype=np.float64)
    horizontal_speed = mean_speed * gust * ratio_host
    u_mean = horizontal_speed * (float(mean_vector[0]) / mean_speed if mean_speed > _MIN_DIRECTION_SPEED else 0.0)
    v_mean = horizontal_speed * (float(mean_vector[1]) / mean_speed if mean_speed > _MIN_DIRECTION_SPEED else 0.0)

    # --- thermal plume, faded out by the impermeable ground --------------
    P = ops.const(points)
    thermal = _thermal_components(P, env, time_s, ops)
    damping = xp.maximum(xp.minimum((P[:, 2] - ground) / _GROUND_DAMPING_DEPTH_M, 1.0), 0.0)

    # --- turbulence, frozen-field advected by the mean wind ---------------
    drift_host = np.array(
        [float(mean_vector[0]) * time_s, float(mean_vector[1]) * time_s, 0.0], dtype=np.float64
    )
    turbulence = _turbulence_components(P, env, drift_host, ops)

    total = xp.stack(
        [
            thermal[:, 0] + ops.const(u_mean) + turbulence[:, 0],
            thermal[:, 1] + ops.const(v_mean) + turbulence[:, 1],
            (thermal[:, 2] + turbulence[:, 2]) * damping,
        ],
        axis=-1,
    )
    return ops.to_host(total)


def wind_velocity(position, env: EnvironmentConfig, time_s: float = 0.0) -> np.ndarray:
    """Local wind velocity ``(u, v, w)`` at ``position``.

    Thin wrapper around :func:`velocity_components` on the NumPy reference path
    (the flight integrator calls this once per integration step).  Returns a
    ``(3,)`` array for a single point and an ``(N, 3)`` array for a batch; see
    :func:`velocity_components` for the exact composition and its guarantees.
    """
    pts, flat = _as_points(position)
    values = _velocity_device(pts, env, float(time_s), "numpy")
    return values[0] if flat else values


def _parse_resolution(resolution: Any) -> Tuple[Tuple[int, int, int], Any]:
    """Normalise ``resolution`` into ``((nx, ny, nz), echoed_value)``."""
    if isinstance(resolution, bool):
        raise ValueError("resolution must be an integer or a 3-sequence, not a bool")
    if isinstance(resolution, (int, np.integer)):
        count = int(resolution)
        if count < 1:
            raise ValueError(f"resolution must be >= 1, got {count}")
        return (count, count, count), count
    values = [int(v) for v in np.asarray(resolution, dtype=np.int64).reshape(-1)]
    if len(values) == 1:
        count = values[0]
        if count < 1:
            raise ValueError(f"resolution must be >= 1, got {count}")
        return (count, count, count), count
    if len(values) != 3:
        raise ValueError(
            f"resolution must be an int or a 3-sequence (nx, ny, nz); got {len(values)} values"
        )
    if min(values) < 1:
        raise ValueError(f"every resolution component must be >= 1, got {values}")
    if values[0] * values[1] * values[2] > 262144:
        raise ValueError(f"total grid points ({values[0] * values[1] * values[2]}) exceeds safety limit 262144")
    return (values[0], values[1], values[2]), values


def _axis(lo: float, hi: float, count: int) -> np.ndarray:
    """``count`` samples spanning ``lo``..``hi`` (degenerate single sample when equal)."""
    if count == 1:
        return np.array([float(lo)], dtype=np.float64)
    return np.linspace(float(lo), float(hi), int(count), dtype=np.float64)


def wind_field(
    bounds_min: Sequence[float],
    bounds_max: Sequence[float],
    resolution: Any,
    env: EnvironmentConfig,
    time_s: float = 0.0,
    backend: str = "auto",
) -> Dict[str, Any]:
    """Sampled 3D wind velocity field over an axis aligned box.

    The grid is built with ``np.meshgrid(..., indexing="ij")`` so the flat point
    order is deterministic and matches ``shape == [nx, ny, nz]`` exactly (``x``
    varies slowest, ``z`` fastest) and ``len(positions[0]) == prod(shape)``.

    Parameters
    ----------
    bounds_min, bounds_max:
        Opposite corners of the sampling box, ``(x, y, z)`` with ``z`` the
        height above the ground plane.
    resolution:
        ``int`` for a cubic grid, or a 3-sequence ``(nx, ny, nz)``.
    env:
        Environment configuration (see :mod:`physics.config_schema`).
    time_s:
        Simulation time handed to the gust envelope, the drifting turbulence
        field and the wandering thermal core.
    backend:
        ``"auto"`` (default), ``"numpy"``, ``"cupy"`` or ``"torch"``.  A GPU
        request that cannot be honoured degrades to NumPy *and the response
        reports the truth* under ``backend``.

    Returns
    -------
    dict
        ``positions`` (three lists of ``prod(shape)`` floats), ``vectors``
        (three lists of the same length), ``speeds``, ``shape``, ``resolution``,
        ``backend``, ``backend_requested``, ``grid_point_count``, ``bounds``,
        ``time_s``, ``gust_factor``, ``mean_wind_mps``, ``max_speed`` and
        ``mean_speed``.
    """
    lo = np.asarray(bounds_min, dtype=np.float64).reshape(-1)
    hi = np.asarray(bounds_max, dtype=np.float64).reshape(-1)
    if lo.size != 3 or hi.size != 3:
        raise ValueError("bounds_min and bounds_max must both hold exactly three values")
    shape, echoed_resolution = _parse_resolution(resolution)
    nx, ny, nz = shape
    backend_name, backend_requested = _resolve_backend(backend)

    grid_x = _axis(lo[0], hi[0], nx)
    grid_y = _axis(lo[1], hi[1], ny)
    grid_z = _axis(lo[2], hi[2], nz)
    mesh_x, mesh_y, mesh_z = np.meshgrid(grid_x, grid_y, grid_z, indexing="ij")
    points = np.stack(
        [mesh_x.reshape(-1), mesh_y.reshape(-1), mesh_z.reshape(-1)], axis=-1
    ).astype(np.float64, copy=False)

    velocity = _velocity_device(points, env, float(time_s), backend_name)
    speeds = np.sqrt(np.sum(velocity * velocity, axis=1))

    return {
        "positions": [points[:, 0].tolist(), points[:, 1].tolist(), points[:, 2].tolist()],
        "vectors": [velocity[:, 0].tolist(), velocity[:, 1].tolist(), velocity[:, 2].tolist()],
        "speeds": [float(value) for value in speeds],
        "shape": [int(nx), int(ny), int(nz)],
        "resolution": echoed_resolution,
        "backend": backend_name,
        "backend_requested": backend_requested,
        "grid_point_count": int(points.shape[0]),
        "bounds": {"min": lo.tolist(), "max": hi.tolist()},
        "time_s": float(time_s),
        "gust_factor": float(gust_factor(env, time_s)),
        "mean_wind_mps": [float(value) for value in mean_wind_vector(env)],
        "max_speed": float(np.max(speeds)) if speeds.size else 0.0,
        "mean_speed": float(np.mean(speeds)) if speeds.size else 0.0,
    }
