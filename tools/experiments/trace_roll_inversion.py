import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator
from physics.geometry_utils import euler_zyx_from_quaternion

preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['release_pitch_deg'] = 0.0

sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=launch
)
sim.simulate(duration_s=1.8)
samples = sim.history()

print(f"{'t(s)':6s} | {'Alt':6s} | {'Roll':8s} | {'Pitch':8s} | {'Yaw':8s} | {'wx':7s} | {'wy':7s} | {'wz':7s}")
for s in samples:
    if s.time_s < 1.6:
        print(f"{s.time_s:6.3f} | {s.altitude_m:6.2f} | {s.roll_deg:8.1f} | {s.pitch_deg:8.1f} | {s.yaw_deg:8.1f}")
