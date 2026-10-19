import sys
sys.path.insert(0, '.')
import math
import numpy as np
import physics.records_domain as rd
from physics.aero_domain import evaluate_aero, elevon_effects
from physics.config_schema import AircraftConfig, EnvironmentConfig

pr = rd.preset('unite_31s')
ac = pr.aircraft if isinstance(pr.aircraft, AircraftConfig) else AircraftConfig(**pr.aircraft)
env = EnvironmentConfig()

print("Aircraft parameters for unite_31s:")
print(f"  mass: {ac.mass_kg*1000:.1f} g")
print(f"  span: {ac.wingspan_m*100:.1f} cm")
print(f"  area: {ac.wing_area_m2*10000:.1f} cm2")
print(f"  cm0: {ac.cm0}")
print(f"  cm_alpha: {ac.cm_alpha}")
print(f"  cm_q: {ac.cm_q}")
print(f"  cL0: {ac.cL0}")
print(f"  cd0: {ac.cd0}")
print(f"  elevon_deg: {ac.elevon_deg}")
print(f"  compliance: {ac.aeroelastic_compliance}")

# Check trim angle of attack
# cm(alpha) = cm0 + cm_alpha * rad(alpha) + elevon_dcm = 0
# rad(alpha_trim) = -(cm0 + elevon_dcm) / cm_alpha
elevon = elevon_effects(ac.elevon_deg, ac)
alpha_trim_rad = -(ac.cm0 + elevon['dcm']) / ac.cm_alpha
alpha_trim_deg = math.degrees(alpha_trim_rad)
print("\nTrim analysis:")
print(f"  elevon dcm: {elevon['dcm']:.4f}")
print(f"  alpha_trim: {alpha_trim_deg:.2f} deg")

# Evaluate aero at trim alpha for glide speed V = 3.0 m/s
res = evaluate_aero(ac, env, speed_mps=3.0, alpha_deg=alpha_trim_deg)
print(f"\nAero at V = 3.0 m/s, alpha = {alpha_trim_deg:.2f} deg:")
print(f"  CL: {res.cl:.3f}")
print(f"  CD: {res.cd:.4f}")
print(f"  L/D: {res.ld_ratio:.2f}")
print(f"  Lift: {res.lift_n*1000:.2f} mN (Weight = {ac.mass_kg*9.80665*1000:.2f} mN)")
print(f"  Cm: {res.cm:.5f}")
print(f"  Pitching moment: {res.pitching_moment_nm:.6f} Nm")

# Required speed for steady level flight (Lift = Weight)
# V_trim = sqrt( 2 * m * g / (rho * S * CL) )
rho = 1.225
w = ac.mass_kg * 9.80665
v_level = math.sqrt(2 * w / (rho * ac.wing_area_m2 * res.cl))
print(f"  V for level flight (L=W): {v_level:.2f} m/s")

# Sink rate in steady glide
# V_sink = V_level / (L/D)
v_sink = v_level / res.ld_ratio
print(f"  Glide sink rate: {v_sink:.3f} m/s")
print(f"  Time to descend 15m: {15.0 / v_sink:.1f} seconds")
