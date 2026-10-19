import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator
from physics.geometry_utils import euler_zyx_from_quaternion
import numpy as np

preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['release_pitch_deg'] = 0.0

sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=launch
)

s = sim.state
y = np.array([
    s.position_m[0], s.position_m[1], s.position_m[2],
    s.velocity_mps[0], s.velocity_mps[1], s.velocity_mps[2],
    s.quaternion[0], s.quaternion[1], s.quaternion[2], s.quaternion[3],
    s.angular_velocity_radps[0], s.angular_velocity_radps[1], s.angular_velocity_radps[2]
], dtype=np.float64)

dyn = sim._compute_dynamics(y, 0.0)
aero = dyn['aero']

print("Initial step detailed forces & moments:")
print("  velocity:", s.velocity_mps)
print("  body_relative:", dyn['body_relative'])
print("  alpha_deg:", dyn['alpha_deg'])
print("  CL:", aero.cl, "CD:", aero.cd, "CM:", aero.cm)
print("  Lift (N):", aero.lift_n, "Drag (N):", aero.drag_n)
print("  force_body:", dyn['force_body'])
print("  moment_body:", dyn['moment_body'])
print("  pitching_moment_offset_nm:", aero.pitching_moment_nm)
print("  pushover_moment:", dyn.get('pushover_moment'))
print("  restoring_moment:", sim._restoring_moment(s.quaternion, s.angular_velocity_radps, aero.dynamic_pressure_pa, dyn['speed']))

deriv = sim._derivative(y, 0.0)
print("  deriv q_dot:", deriv[6:10])
print("  deriv omega_dot:", deriv[10:13])
