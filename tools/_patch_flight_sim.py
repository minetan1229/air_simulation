with open("physics/flight_sim.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update __init__
target_init = """        self._stiffness_cache: Optional[Tuple[float, float]] = None
        self._refresh_derived()"""

repl_init = """        self._stiffness_cache: Optional[Tuple[float, float]] = None
        self._aero_dihedral_lag: float = 0.0
        self._aero_twist_lag: float = 0.0
        self._last_dt: float = 0.01
        self._refresh_derived()"""

assert target_init in content, "target_init not found"
content = content.replace(target_init, repl_init, 1)

# 2. Update _compute_dynamics: aeroelastic lag, added mass, mass ratio, model validity
target_aeroelastic = """        effective_alpha_deg = alpha_deg - aero_twist_deg
        effective_dihedral_deg = float(self.aircraft.dihedral_deg) + aero_dihedral_deg"""

repl_aeroelastic = """        # Simple first-order lag filter on quasi-static deformations (Task item 3)
        lag_tau = max(_finite(getattr(self.aircraft, "aeroelastic_tau_s", 0.0), 0.0), 0.0)
        dt_step = getattr(self, "_last_dt", 0.01)
        aero_dihedral_deg = float(
            aero_kernel.first_order_lag(
                ScalarMath, self._aero_dihedral_lag, aero_dihedral_deg, dt_step, lag_tau
            )
        )
        aero_twist_deg = float(
            aero_kernel.first_order_lag(
                ScalarMath, self._aero_twist_lag, aero_twist_deg, dt_step, lag_tau
            )
        )
        self._aero_dihedral_lag = aero_dihedral_deg
        self._aero_twist_lag = aero_twist_deg

        effective_alpha_deg = alpha_deg - aero_twist_deg
        effective_dihedral_deg = float(self.aircraft.dihedral_deg) + aero_dihedral_deg"""

assert target_aeroelastic in content, "target_aeroelastic not found"
content = content.replace(target_aeroelastic, repl_aeroelastic, 1)

# 3. Update added mass in _compute_dynamics
target_added_mass = """        span = max(self._derived["wingspan_m"], 1.0e-6)
        if bool(getattr(self.aircraft, "added_mass_enabled", False)):
            rho = max(air["density_kgm3"], 1.0e-6)
            m_add_z = (math.pi / 4.0) * rho * span * (self._chord ** 2)
            m_eff_z = self._mass + m_add_z
            # Accelerations in body frame before world rotation:
            gravity_body = world_to_body(quaternion, gravity_world)
            a_body = (force_body / self._mass) + gravity_body
            a_body[2] = (force_body[2] / m_eff_z) + gravity_body[2]
            acceleration = body_to_world(quaternion, a_body)"""

repl_added_mass = """        span = max(self._derived["wingspan_m"], 1.0e-6)
        ref_area = max(self._derived.get("ref_area_m2", 0.015), 1.0e-6)
        rho_val = max(air["density_kgm3"], 1.0e-6)
        mass_ratio = float(
            aero_kernel.compute_mass_ratio(
                ScalarMath, self._mass, rho_val, ref_area, self._chord
            )
        )
        mass_ratio_warning = bool(mass_ratio < 5.0)

        # Single implementation of added mass tensor from aero_kernel (Task item 4 & 6)
        if bool(getattr(self.aircraft, "added_mass_enabled", False)):
            added = aero_kernel.compute_added_mass(
                ScalarMath, rho_val, ref_area, self._chord
            )
            m_add_z = float(added["added_mass_z_kg"])
            m_eff_z = self._mass + m_add_z
            gravity_body = world_to_body(quaternion, gravity_world)
            a_body = (force_body / self._mass) + gravity_body
            a_body[2] = (force_body[2] / m_eff_z) + gravity_body[2]
            acceleration = body_to_world(quaternion, a_body)"""

assert target_added_mass in content, "target_added_mass not found"
content = content.replace(target_added_mass, repl_added_mass, 1)

# 4. Update model_valid computation in _compute_dynamics
target_model_valid = """        model_valid = bool(
            mach <= MODEL_MACH_LIMIT
            and MODEL_REYNOLDS_MIN <= reynolds <= MODEL_REYNOLDS_MAX
        )"""

repl_model_valid = """        validity_res = aero_kernel.evaluate_model_validity(
            ScalarMath,
            mach=mach,
            reynolds=reynolds,
            alpha_deg=alpha_deg,
            mass_ratio=mass_ratio if 'mass_ratio' in locals() else 10.0,
        )
        model_valid = bool(validity_res["valid"])"""

assert target_model_valid in content, "target_model_valid not found"
content = content.replace(target_model_valid, repl_model_valid, 1)

# 5. Return dict of _compute_dynamics
target_core_dict = """            "model_valid": model_valid,
            "speed_capped": speed_capped,
        }"""

repl_core_dict = """            "model_valid": model_valid,
            "speed_capped": speed_capped,
            "mass_ratio": mass_ratio,
            "mass_ratio_warning": mass_ratio_warning,
        }"""

assert target_core_dict in content, "target_core_dict not found"
content = content.replace(target_core_dict, repl_core_dict, 1)

# 6. Packet in _diagnostics
target_packet = """            "aeroelastic_dihedral_deg": core.get("aeroelastic_dihedral_deg", 0.0),
            "aeroelastic_twist_deg": core.get("aeroelastic_twist_deg", 0.0),
            "trailing_edge_bend_deg": core.get("trailing_edge_bend_deg", 0.0),
            "geometric_alpha_deg": core["geometric_alpha_deg"],
        }"""

repl_packet = """            "aeroelastic_dihedral_deg": core.get("aeroelastic_dihedral_deg", 0.0),
            "aeroelastic_twist_deg": core.get("aeroelastic_twist_deg", 0.0),
            "trailing_edge_bend_deg": core.get("trailing_edge_bend_deg", 0.0),
            "geometric_alpha_deg": core["geometric_alpha_deg"],
            "model_valid": bool(core.get("model_valid", True)),
            "mass_ratio": float(core.get("mass_ratio", 10.0)),
            "mass_ratio_warning": bool(core.get("mass_ratio_warning", False)),
        }"""

assert target_packet in content, "target_packet not found"
content = content.replace(target_packet, repl_packet, 1)

with open("physics/flight_sim.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Successfully updated physics/flight_sim.py")
