with open("physics/flight_sim.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add import of aero_kernel
target_import = "from .geometry_utils import ("
repl_import = "from . import aero_kernel\nfrom .aero_kernel import ScalarMath\nfrom .geometry_utils import ("
assert target_import in content, "target_import not found"
content = content.replace(target_import, repl_import, 1)

# Ensure mass_ratio is computed before model_valid
target_mv = """        validity_res = aero_kernel.evaluate_model_validity(
            ScalarMath,
            mach=mach,
            reynolds=reynolds,
            alpha_deg=alpha_deg,
            mass_ratio=mass_ratio if 'mass_ratio' in locals() else 10.0,
        )
        model_valid = bool(validity_res["valid"])"""

repl_mv = """        # Mass ratio assessment
        ref_area = max(self._derived.get("ref_area_m2", 0.015), 1.0e-6)
        rho_val = max(air["density_kgm3"], 1.0e-6)
        mass_ratio = float(
            aero_kernel.compute_mass_ratio(
                ScalarMath, self._mass, rho_val, ref_area, self._chord
            )
        )
        mass_ratio_warning = bool(mass_ratio < 5.0)

        validity_res = aero_kernel.evaluate_model_validity(
            ScalarMath,
            mach=mach,
            reynolds=reynolds,
            alpha_deg=alpha_deg,
            mass_ratio=mass_ratio,
        )
        model_valid = bool(validity_res["valid"])"""

assert target_mv in content, "target_mv not found"
content = content.replace(target_mv, repl_mv, 1)

with open("physics/flight_sim.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Successfully patched flight_sim.py import and mass_ratio order")
