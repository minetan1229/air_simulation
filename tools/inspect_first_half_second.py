import sys
sys.path.insert(0, '.')
import math
import numpy as np
import physics.records_domain as rd
from physics.geometry_utils import euler_zyx_from_quaternion
from physics.flight_sim import FlightSimulator

preset_data = rd.apply_preset('unite_31s')
sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=preset_data['launch']
)

print("Stepping through first 0.5s:")
print(f"{'t(s)':6s} | {'alt(m)':6s} | {'V(m/s)':6s} | {'Pitch':6s} | {'omega_y':8s} | {'My(Nm)':10s} | {'acc_y':8s}")

for i in range(25):
    sim.step()
    s = sim.state
    r, p, y_deg = euler_zyx_from_quaternion(s.quaternion)
    y_arr = np.array([
        s.position_m[0], s.position_m[1], s.position_m[2],
        s.velocity_mps[0], s.velocity_mps[1], s.velocity_mps[2],
        s.quaternion[0], s.quaternion[1], s.quaternion[2], s.quaternion[3],
        s.angular_velocity_radps[0], s.angular_velocity_radps[1], s.angular_velocity_radps[2]
    ], dtype=np.float64)
    dyn = sim._compute_dynamics(y_arr, s.time_s)
    print(f"{s.time_s:6.3f} | {-s.position_m[2]:6.2f} | {dyn['speed']:6.2f} | {p:6.1f} | {s.angular_velocity_radps[1]:8.2f} | {dyn['moment_body'][1]:10.6f} | {dyn['angular_acceleration'][1]:8.2f}")
