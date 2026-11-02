import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator
from physics.geometry_utils import euler_zyx_from_quaternion
import numpy as np

preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['launch_angle_deg'] = 75.0
launch['release_pitch_deg'] = 0.0
launch['launch_height_m'] = 1.8

sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=launch
)

# Step to t=2.0s
while sim.state.time_s < 2.0:
    sim.step()

print(f"{'t(s)':6s} | {'Alt':6s} | {'V':6s} | {'Pitch':6s} | {'Alpha':6s} | {'wy':7s} | {'acc_y':8s} | {'My_tot':10s} | {'q_dyn':6s} | {'cm':7s} | {'pushover':10s}")

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
    aero = dyn['aero']
    push = dyn.get('pushover_moment', 0.0)
    print(f"{s.time_s:6.3f} | {-s.position_m[2]:6.2f} | {dyn['speed']:6.2f} | {p:6.1f} | {dyn['alpha_deg']:6.1f} | {s.angular_velocity_radps[1]:7.2f} | {dyn['angular_acceleration'][1]:8.2f} | {dyn['moment_body'][1]:10.6f} | {aero.dynamic_pressure_pa:6.2f} | {aero.cm:7.3f} | {push if push is not None else 0.0:10.6f}")
    if s.time_s > 2.6:
        break
