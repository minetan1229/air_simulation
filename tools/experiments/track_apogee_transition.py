import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.geometry_utils import euler_zyx_from_quaternion
from physics.flight_sim import FlightSimulator
import numpy as np

preset_data = rd.apply_preset('unite_31s')
sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=preset_data['launch']
)

# Advance until t ~ 2.5s (near apogee)
while sim.state.time_s < 2.3:
    sim.step()

print("Approaching apogee...")
print(f"{'t(s)':6s} | {'alt(m)':6s} | {'V(m/s)':6s} | {'Vx':6s} | {'Vz':6s} | {'Pitch':6s} | {'Alpha':6s} | {'q_rate':7s} | {'My(Nm)':9s} | {'Stall':5s} | {'TE_bend':7s}")

for i in range(80):
    sim.step()
    s = sim.state
    r, p, y = euler_zyx_from_quaternion(s.quaternion)
    y_arr = np.array([
        s.position_m[0], s.position_m[1], s.position_m[2],
        s.velocity_mps[0], s.velocity_mps[1], s.velocity_mps[2],
        s.quaternion[0], s.quaternion[1], s.quaternion[2], s.quaternion[3],
        s.angular_velocity_radps[0], s.angular_velocity_radps[1], s.angular_velocity_radps[2]
    ], dtype=np.float64)
    dyn = sim._compute_dynamics(y_arr, s.time_s)
    aero = dyn['aero']
    te_bend = dyn.get('trailing_edge_bend_deg', 0.0)
    print(f"{s.time_s:6.3f} | {-s.position_m[2]:6.2f} | {dyn['speed']:6.2f} | {s.velocity_mps[0]:6.2f} | {s.velocity_mps[2]:6.2f} | {p:6.1f} | {dyn['alpha_deg']:6.1f} | {s.angular_velocity_radps[1]:7.2f} | {dyn['moment_body'][1]:9.6f} | {str(aero.stalled):5s} | {te_bend:7.2f}")
    if s.time_s > 4.5:
        break
