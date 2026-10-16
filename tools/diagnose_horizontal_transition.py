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

# Advance to t=1.8s
while sim.state.time_s < 1.8:
    sim.step()

print(f"{'t(s)':6s} | {'alt':6s} | {'V':6s} | {'Pitch':6s} | {'Alpha':6s} | {'wy':7s} | {'acc_y':8s} | {'cm':7s} | {'cm_q*q':8s} | {'cm0':7s}")

for i in range(35):
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
    q_hat = s.angular_velocity_radps[1] * sim._chord / (2.0 * max(dyn['speed'], 0.1))
    cm_q_term = getattr(sim.aircraft, 'cm_q', -3.5) * q_hat
    print(f"{s.time_s:6.3f} | {-s.position_m[2]:6.2f} | {dyn['speed']:6.2f} | {p:6.1f} | {dyn['alpha_deg']:6.1f} | {s.angular_velocity_radps[1]:7.2f} | {dyn['angular_acceleration'][1]:8.2f} | {aero.cm:7.3f} | {cm_q_term:8.3f} | {getattr(aero, 'cm0', 0.0):7.3f}")
    if s.time_s > 2.5:
        break
