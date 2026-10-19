import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator

preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['launch_angle_deg'] = 75.0
launch['release_pitch_deg'] = 0.0
launch['launch_height_m'] = 1.8

ac = preset_data['aircraft']
ac['rudder_deg'] = 0.25

sim = FlightSimulator(
    aircraft=ac,
    environment=preset_data['environment'],
    launch=launch
)
res = sim.simulate(duration_s=60.0)

print("Full flight result:")
print(f"  flight_time: {res.get('flight_time_s'):.2f}s")
print(f"  max_alt: {res.get('max_altitude_m'):.2f}m")
print(f"  distance: {res.get('distance_m'):.2f}m")
print(f"  apogee_time: {res.get('apogee_time_s'):.2f}s")
print(f"  stable_glide_duration: {res.get('stable_glide_duration_s'):.2f}s")
print(f"  status: {res.get('status')}")
