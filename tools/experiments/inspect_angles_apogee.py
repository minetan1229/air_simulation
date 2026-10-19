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

sim.simulate(duration_s=3.0)
samples = sim.history()

print(f"{'t(s)':6s} | {'Alt':6s} | {'Spd':6s} | {'Roll':6s} | {'Pitch':6s} | {'Yaw':6s} | {'Alpha':6s} | {'wy':6s}")
for s in samples:
    if 1.2 <= s.time_s <= 2.6:
        print(f"{s.time_s:6.3f} | {s.altitude_m:6.2f} | {s.speed_mps:6.2f} | {s.roll_deg:6.1f} | {s.pitch_deg:6.1f} | {s.yaw_deg:6.1f} | {s.alpha_deg:6.1f}")
