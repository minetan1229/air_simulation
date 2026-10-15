"""Rotation algebra shared by the flight-dynamics, stress and mesh domains.

All functions are pure NumPy.  Quaternions follow the Hamilton convention
``q = (w, x, y, z)`` with unit norm, and attitude matrices map **body frame
vectors to world frame vectors**:

    v_world = R(q) @ v_body

Euler angles use the aviation ZYX (yaw, pitch, roll) sequence applied as
``R = Rz(psi) @ Ry(theta) @ Rx(phi)``.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np

from .config_schema import deg2rad, rad2deg

__all__ = [
    "vec3", "normalize", "cross", "dot", "skew", "outer",
    "quaternion_multiply", "quaternion_conjugate", "quaternion_normalize",
    "quaternion_from_euler", "euler_from_quaternion", "quaternion_to_matrix",
    "matrix_to_quaternion", "body_to_world", "world_to_body",
    "euler_to_matrix", "matrix_to_euler", "rotate_vector",
    "quaternion_derivative", "quaternion_to_body_angular_rate",
    "angular_acceleration_body", "inertia_tensor_box",
    "principal_inertia_from_matrix", "angle_between_vectors",
    "wrap_pi", "euler_zyx_from_quaternion", "quaternion_from_axis_angle",
]

_EPS = 1.0e-12


def vec3(value: Sequence[float], default: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> np.ndarray:
    """Coerce ``value`` into a fresh float64 3-vector."""
    arr = np.asarray(value, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return np.array(default, dtype=np.float64)
    if arr.size == 1:
        return np.full(3, float(arr[0]), dtype=np.float64)
    if arr.size < 3:
        padded = np.full(3, float(arr[-1]), dtype=np.float64)
        padded[: arr.size] = arr
        return padded
    return np.array(arr[:3], dtype=np.float64)


def normalize(v: np.ndarray, fallback: Sequence[float] = (1.0, 0.0, 0.0)) -> np.ndarray:
    """Unit vector; falls back when the input is (near) zero length."""
    arr = np.asarray(v, dtype=np.float64)
    norm = float(np.linalg.norm(arr))
    if norm < _EPS or not math.isfinite(norm):
        return vec3(fallback)
    return arr / norm


def cross(a: Sequence[float], b: Sequence[float]) -> np.ndarray:
    return np.cross(vec3(a), vec3(b))


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(np.dot(vec3(a), vec3(b)))


def skew(v: Sequence[float]) -> np.ndarray:
    """Skew-symmetric matrix with ``skew(a) @ b == cross(a, b)``."""
    x, y, z = vec3(v)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=np.float64)


def outer(a: Sequence[float], b: Sequence[float]) -> np.ndarray:
    return np.outer(vec3(a), vec3(b))


def wrap_pi(angle: float) -> float:
    """Wrap an angle into ``(-pi, pi]``."""
    wrapped = (float(angle) + math.pi) % (2.0 * math.pi) - math.pi
    return wrapped if wrapped > -math.pi else math.pi


# --------------------------------------------------------------------------
# Quaternion algebra (Hamilton, q = (w, x, y, z))
# --------------------------------------------------------------------------
def quaternion_multiply(a: Sequence[float], b: Sequence[float]) -> np.ndarray:
    aw, ax, ay, az = vec4(a)
    bw, bx, by, bz = vec4(b)
    return np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        dtype=np.float64,
    )


def vec4(value: Sequence[float], default: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return np.array(default, dtype=np.float64)
    if arr.size < 4:
        padded = np.zeros(4, dtype=np.float64)
        padded[: min(arr.size, 4)] = arr[:4]
        if arr.size == 1:
            padded[0] = 1.0
        return padded
    return np.array(arr[:4], dtype=np.float64)


def quaternion_conjugate(q: Sequence[float]) -> np.ndarray:
    w, x, y, z = vec4(q)
    return np.array([w, -x, -y, -z], dtype=np.float64)


def quaternion_normalize(q: Sequence[float]) -> np.ndarray:
    arr = vec4(q)
    norm = float(np.linalg.norm(arr))
    if norm < _EPS or not math.isfinite(norm):
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    return arr / norm


def quaternion_from_euler(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    """ZYX (yaw-pitch-roll) Euler degrees -> unit quaternion."""
    phi = deg2rad(roll_deg)
    theta = deg2rad(pitch_deg)
    psi = deg2rad(yaw_deg)
    cr, sr = math.cos(phi * 0.5), math.sin(phi * 0.5)
    cp, sp = math.cos(theta * 0.5), math.sin(theta * 0.5)
    cy, sy = math.cos(psi * 0.5), math.sin(psi * 0.5)
    return np.array(
        [
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        ],
        dtype=np.float64,
    )


def euler_zyx_from_quaternion(q: Sequence[float]) -> Tuple[float, float, float]:
    """Unit quaternion -> ZYX Euler angles in **degrees** (roll, pitch, yaw)."""
    w, x, y, z = quaternion_normalize(q)
    sin_pitch = 2.0 * (w * y - z * x)
    if abs(sin_pitch) < 1.0 - 1.0e-9:
        sin_roll = 2.0 * (w * x + y * z)
        cos_roll = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sin_roll, cos_roll)
        pitch = math.asin(float(np.clip(sin_pitch, -1.0, 1.0)))
        sin_yaw = 2.0 * (w * z + x * y)
        cos_yaw = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(sin_yaw, cos_yaw)
    else:  # Gimbal lock singularity at pitch = ±90 deg
        pitch = math.copysign(math.pi * 0.5, sin_pitch)
        m12 = 2.0 * (y * z - w * x)
        m11 = 1.0 - 2.0 * (x * x + z * z)
        roll = math.atan2(-m12, m11)
        yaw = 0.0
    return (rad2deg(roll), rad2deg(pitch), rad2deg(yaw))


def quaternion_to_matrix(q: Sequence[float]) -> np.ndarray:
    """Body->world direction cosine matrix of a (unit) quaternion."""
    w, x, y, z = quaternion_normalize(q)
    xx, yy, zz = x * x, y * y, z * z
    wx, wy, wz = w * x, w * y, w * z
    xy, xz, yz = x * y, x * z, y * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float64,
    )


def matrix_to_quaternion(matrix: np.ndarray) -> np.ndarray:
    """Direction cosine matrix -> unit quaternion (Shepperd's method)."""
    m = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
    trace = float(m[0, 0] + m[1, 1] + m[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        q = np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        q = np.array([(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s])
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        q = np.array([(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s])
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        q = np.array([(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s])
    return quaternion_normalize(q)


def euler_to_matrix(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    phi, theta, psi = deg2rad(roll_deg), deg2rad(pitch_deg), deg2rad(yaw_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    ct, st = math.cos(theta), math.sin(theta)
    cy, sy = math.cos(psi), math.sin(psi)
    rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    ry = np.array([[ct, 0.0, st], [0.0, 1.0, 0.0], [-st, 0.0, ct]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])
    return rz @ ry @ rx


def matrix_to_euler(matrix: np.ndarray) -> Tuple[float, float, float]:
    """Direction cosine matrix -> ZYX Euler degrees (roll, pitch, yaw)."""
    m = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
    pitch = math.asin(float(np.clip(-m[2, 0], -1.0, 1.0)))
    if abs(m[2, 0]) < 1.0 - 1.0e-9:
        roll = math.atan2(m[2, 1], m[2, 2])
        yaw = math.atan2(m[1, 0], m[0, 0])
    else:  # gimbal lock
        roll = math.atan2(-m[1, 2], m[1, 1])
        yaw = 0.0
    return (rad2deg(roll), rad2deg(pitch), rad2deg(yaw))


#: Backwards-compatible aliases used across the codebase.
quaternion_from_euler_zyx = quaternion_from_euler
euler_from_quaternion = euler_zyx_from_quaternion


def rotate_vector(q: Sequence[float], v: Sequence[float]) -> np.ndarray:
    """Rotate a body-frame vector into the world frame."""
    return quaternion_to_matrix(q) @ vec3(v)


def body_to_world(q: Sequence[float], v: Sequence[float]) -> np.ndarray:
    return quaternion_to_matrix(q) @ vec3(v)


def world_to_body(q: Sequence[float], v: Sequence[float]) -> np.ndarray:
    return quaternion_to_matrix(q).T @ vec3(v)


def quaternion_derivative(q: Sequence[float], omega_body: Sequence[float]) -> np.ndarray:
    """``q_dot = 0.5 * q (x) [0, omega_body]`` for body-frame angular rate."""
    w, x, y, z = quaternion_normalize(q)
    p, q_pitch, r_yaw = vec3(omega_body)
    return 0.5 * np.array(
        [
            -(x * p + y * q_pitch + z * r_yaw),
            w * p + y * r_yaw - z * q_pitch,
            w * q_pitch - x * r_yaw + z * p,
            w * r_yaw + x * q_pitch - y * p,
        ],
        dtype=np.float64,
    )


def quaternion_to_body_angular_rate(q: Sequence[float], q_dot: Sequence[float]) -> np.ndarray:
    """Inverse of :func:`quaternion_derivative` for unit quaternions."""
    w, x, y, z = quaternion_normalize(q)
    d = vec4(q_dot)
    return 2.0 * np.array(
        [
            w * d[1] - x * d[0] - y * d[3] + z * d[2],
            w * d[2] - y * d[0] + x * d[3] - z * d[1],
            w * d[3] - z * d[0] - x * d[2] + y * d[1],
        ],
        dtype=np.float64,
    )


def angular_acceleration_body(
    inertia: np.ndarray, omega: Sequence[float], moment: Sequence[float]
) -> np.ndarray:
    """Euler's equation ``I w' = M - w x (I w)`` in the body frame."""
    i_mat = np.asarray(inertia, dtype=np.float64).reshape(3, 3)
    w_vec = vec3(omega)
    m_vec = vec3(moment)
    angular_momentum = i_mat @ w_vec
    rhs = m_vec - np.cross(w_vec, angular_momentum)
    return np.linalg.solve(i_mat, rhs)


def inertia_tensor_box(mass: float, dx: float, dy: float, dz: float) -> np.ndarray:
    """Analytic box inertia tensor about its centre of mass."""
    m = float(mass)
    return np.diag(
        [
            m * (dy * dy + dz * dz) / 12.0,
            m * (dx * dx + dz * dz) / 12.0,
            m * (dx * dx + dy * dy) / 12.0,
        ]
    )


def wind_force_to_body(relative_velocity: Sequence[float], drag: float, side: float, lift: float) -> np.ndarray:
    """Resolve wind-axis forces with actual flow, independently of wing twist.

    +z points down. The spanwise wind axis is orthogonal to the velocity
    and the x-z lift axis, including sideslip. At zero speed no axis exists.
    """
    velocity = vec3(relative_velocity)
    speed = float(np.linalg.norm(velocity))
    if speed <= 1.0e-12:
        return np.zeros(3)
    drag_axis = velocity / speed
    horizontal = math.hypot(float(velocity[0]), float(velocity[2]))
    lift_axis = (np.array([velocity[2], 0.0, -velocity[0]]) / horizontal
                 if horizontal > 1.0e-12 else np.array([0.0, 0.0, -1.0]))
    side_axis = np.cross(drag_axis, lift_axis)
    side_axis /= np.linalg.norm(side_axis)
    return -float(drag) * drag_axis + float(side) * side_axis + float(lift) * lift_axis


def body_inertia_tensor(properties: dict) -> np.ndarray:
    """Build and validate a realizable symmetric inertia tensor in body axes."""
    tensor = np.diag([properties['roll_inertia_kgm2'], properties['pitch_inertia_kgm2'],
                      properties['yaw_inertia_kgm2']]).astype(float)
    for i, j, key in [(0, 1, 'inertia_xy_kgm2'), (0, 2, 'inertia_xz_kgm2'),
                      (1, 2, 'inertia_yz_kgm2')]:
        tensor[i, j] = tensor[j, i] = properties.get(key, 0.0)
    if not np.isfinite(tensor).all():
        raise ValueError('inertia tensor must contain finite components')
    principal = np.linalg.eigvalsh(tensor)
    if principal[0] <= 0.0:
        raise ValueError('inertia tensor must be positive definite')
    if principal[-1] > principal[0] + principal[1] + 1e-12 * principal[-1]:
        raise ValueError('inertia tensor violates the principal-moment triangle inequality')
    return tensor


def principal_inertia_from_matrix(matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Eigen decomposition of an inertia tensor.

    Returns ``(principal_inertia, rotation)`` where ``rotation`` has the
    principal axes as columns, sorted by increasing moment of inertia.
    """
    m = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
    sym = 0.5 * (m + m.T)
    values, vectors = np.linalg.eigh(sym)
    order = np.argsort(values)
    return values[order], vectors[:, order]


def angle_between_vectors(a: Sequence[float], b: Sequence[float]) -> float:
    """Signed-free angle between two vectors, in degrees."""
    ua = normalize(vec3(a))
    ub = normalize(vec3(b))
    return rad2deg(math.acos(float(np.clip(np.dot(ua, ub), -1.0, 1.0))))


def quaternion_from_axis_angle(axis: Sequence[float], angle_deg: float) -> np.ndarray:
    unit = normalize(vec3(axis))
    half = deg2rad(angle_deg) * 0.5
    s = math.sin(half)
    return np.array([math.cos(half), unit[0] * s, unit[1] * s, unit[2] * s], dtype=np.float64)
