with open("physics/gpu_dynamics.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update FlightParams
target_fp = """    mean_wind: Any = None
    use_mean_wind_only: bool = False"""

repl_fp = """    mean_wind: Any = None
    use_mean_wind_only: bool = False
    inertia_matrix: Any = None
    inertia_inv: Any = None
    sample_wind_stages: bool = False"""

assert target_fp in content, "target_fp not found"
content = content.replace(target_fp, repl_fp, 1)

# 2. Update build_params to build 3x3 inertia tensors
target_bp = """    inertia = xp.stack(
        (
            xp.asarray([max(float(item["roll_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["pitch_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["yaw_inertia_kgm2"]), 1.0e-12) for item in derived]),
        ),
        axis=-1,
    )"""

repl_bp = """    inertia = xp.stack(
        (
            xp.asarray([max(float(item["roll_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["pitch_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["yaw_inertia_kgm2"]), 1.0e-12) for item in derived]),
        ),
        axis=-1,
    )
    # Build full 3x3 inertia tensors and inverses (including off-diagonal cross terms)
    mats_list = []
    invs_list = []
    for item in derived:
        ixx = max(float(item["roll_inertia_kgm2"]), 1.0e-12)
        iyy = max(float(item["pitch_inertia_kgm2"]), 1.0e-12)
        izz = max(float(item["yaw_inertia_kgm2"]), 1.0e-12)
        ixy = float(item.get("inertia_xy_kgm2", 0.0))
        ixz = float(item.get("inertia_xz_kgm2", 0.0))
        iyz = float(item.get("inertia_yz_kgm2", 0.0))
        t_mat = np.array([
            [ixx, ixy, ixz],
            [ixy, iyy, iyz],
            [ixz, iyz, izz],
        ], dtype=np.float64)
        mats_list.append(t_mat)
        try:
            invs_list.append(np.linalg.inv(t_mat))
        except np.linalg.LinAlgError:
            invs_list.append(np.linalg.pinv(t_mat))
    inertia_matrix = xp.asarray(np.stack(mats_list, axis=0))
    inertia_inv = xp.asarray(np.stack(invs_list, axis=0))"""

assert target_bp in content, "target_bp not found"
content = content.replace(target_bp, repl_bp, 1)

# 2b. Add inertia_matrix / inv to FlightParams constructor call in build_params
target_fp_call = """        mean_wind=wind,
        use_mean_wind_only=use_mean_wind,
    )"""

repl_fp_call = """        mean_wind=wind,
        use_mean_wind_only=use_mean_wind,
        inertia_matrix=inertia_matrix,
        inertia_inv=inertia_inv,
    )"""

assert target_fp_call in content, "target_fp_call not found"
content = content.replace(target_fp_call, repl_fp_call, 1)

# 3. Update _evaluate_state to apply full inertia tensor
target_eval = """    angular_momentum = params.inertia * omega
    angular_acceleration = (
        moment_body - _cross(xp, omega, angular_momentum)
    ) / params.inertia"""

repl_eval = """    if getattr(params, "inertia_matrix", None) is not None:
        angular_momentum = xp.matmul(
            params.inertia_matrix, omega[:, :, xp.newaxis]
        )[:, :, 0]
        gyro = _cross(xp, omega, angular_momentum)
        net_moment = moment_body - gyro
        angular_acceleration = xp.matmul(
            params.inertia_inv, net_moment[:, :, xp.newaxis]
        )[:, :, 0]
    else:
        angular_momentum = params.inertia * omega
        angular_acceleration = (
            moment_body - _cross(xp, omega, angular_momentum)
        ) / params.inertia"""

assert target_eval in content, "target_eval not found"
content = content.replace(target_eval, repl_eval, 1)

# 4. Update _rk4 for per-stage wind sampling
target_rk4 = """def _rk4(
    xp: Any,
    params: FlightParams,
    y0: Any,
    h: Any,
    wind: Any,
    k1: Any = None,
) -> Any:
    \"\"\"One classical RK4 sweep, renormalising the quaternions afterwards.

    The wind field is sampled **once per step** and shared by the four
    stages: the field varies over seconds, not over the millisecond of one
    step, and re-evaluating the (expensive) turbulence model four times per
    step for every case would make the batch far slower than the path it
    replaces.

    ``k1`` may be supplied by the caller when the derivative of ``y0`` has
    already been evaluated; it is then reused instead of being computed a
    second time.
    \"\"\"
    if k1 is None:
        k1 = _derivative(xp, params, y0, wind)
    k2 = _derivative(xp, params, y0 + 0.5 * h[:, xp.newaxis] * k1, wind)
    k3 = _derivative(xp, params, y0 + 0.5 * h[:, xp.newaxis] * k2, wind)
    k4 = _derivative(xp, params, y0 + h[:, xp.newaxis] * k3, wind)
    y1 = y0 + (h[:, xp.newaxis] / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)"""

repl_rk4 = """def _rk4(
    xp: Any,
    params: FlightParams,
    y0: Any,
    h: Any,
    wind: Any,
    k1: Any = None,
    time_s: float = 0.0,
    sample_wind_stages: bool = False,
) -> Any:
    \"\"\"One classical RK4 sweep, renormalising the quaternions afterwards.

    When ``sample_wind_stages`` is True, each intermediate RK4 stage samples
    the local wind field at its updated position and time, matching the
    reference 6-DoF integrator (flight_sim.py::_rk4).
    \"\"\"
    h_col = h[:, xp.newaxis]
    half_h = 0.5 * h_col
    if k1 is None:
        k1 = _derivative(xp, params, y0, wind)
    if sample_wind_stages and not getattr(params, "use_mean_wind_only", False):
        mean_dt = float(xp.mean(h))
        y2 = y0 + half_h * k1
        w2 = wind_batch(xp, params, y2[:, 0:3], time_s + 0.5 * mean_dt)
        k2 = _derivative(xp, params, y2, w2)

        y3 = y0 + half_h * k2
        w3 = wind_batch(xp, params, y3[:, 0:3], time_s + 0.5 * mean_dt)
        k3 = _derivative(xp, params, y3, w3)

        y4 = y0 + h_col * k3
        w4 = wind_batch(xp, params, y4[:, 0:3], time_s + mean_dt)
        k4 = _derivative(xp, params, y4, w4)
    else:
        k2 = _derivative(xp, params, y0 + half_h * k1, wind)
        k3 = _derivative(xp, params, y0 + half_h * k2, wind)
        k4 = _derivative(xp, params, y0 + h_col * k3, wind)
    y1 = y0 + (h_col / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)"""

assert target_rk4 in content, "target_rk4 not found"
content = content.replace(target_rk4, repl_rk4, 1)

# 5. Update call to _rk4 in step()
target_step_call = """        k1 = _derivative_from_core(xp, params, y, core)
        y_next = _rk4(xp, params, y, size, wind, k1=k1)"""

repl_step_call = """        k1 = _derivative_from_core(xp, params, y, core)
        current_time = float(xp.mean(acc["time"]))
        y_next = _rk4(
            xp,
            params,
            y,
            size,
            wind,
            k1=k1,
            time_s=current_time,
            sample_wind_stages=getattr(params, "sample_wind_stages", False),
        )"""

assert target_step_call in content, "target_step_call not found"
content = content.replace(target_step_call, repl_step_call, 1)

# 6. Update simulate_batch signature and params setup
target_sim = """    use_graph: Optional[bool] = None,
    record_interval_s: Optional[float] = None,
) -> Dict[str, Any]:"""

repl_sim = """    use_graph: Optional[bool] = None,
    record_interval_s: Optional[float] = None,
    sample_wind_stages: bool = False,
) -> Dict[str, Any]:"""

assert target_sim in content, "target_sim not found"
content = content.replace(target_sim, repl_sim, 1)

target_sim_set = """    if use_graph is None:
        use_graph = _is_device_module(xp)"""

repl_sim_set = """    params.sample_wind_stages = bool(sample_wind_stages)
    if use_graph is None:
        use_graph = _is_device_module(xp)"""

assert target_sim_set in content, "target_sim_set not found"
content = content.replace(target_sim_set, repl_sim_set, 1)

with open("physics/gpu_dynamics.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Successfully updated physics/gpu_dynamics.py")
