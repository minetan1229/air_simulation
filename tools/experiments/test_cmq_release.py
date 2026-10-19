import sys
sys.path.insert(0, '.')
import math
import numpy as np
import physics.records_domain as rd
from physics.geometry_utils import euler_zyx_from_quaternion
from physics.flight_sim import FlightSimulator
from dataclasses import replace

preset_data = rd.apply_preset('unite_31s')
ac = preset_data['aircraft']
env = preset_data['environment']
launch = preset_data['launch']

# Test with cm_q = -12.0 and release_pitch_deg = 0.0
if isinstance(ac, dict):
    ac['cm_q'] = -12.0
else:
    ac = replace(ac, cm_q=-12.0)

if isinstance(launch, dict):
    launch['release_pitch_deg'] = 0.0
else:
    launch = replace(launch, release_pitch_deg=0.0)

sim = FlightSimulator(aircraft=ac, environment=env, launch=launch)
res = sim.simulate(duration_s=35.0)

print("Test with cm_q=-12.0 and release_pitch=0.0:")
print(f"  flight_time: {res.get('flight_time_s'):.2f}s")
print(f"  max_alt: {res.get('max_altitude_m'):.2f}m")
print(f"  apogee_time: {res.get('apogee_time_s'):.2f}s")
print(f"  distance: {res.get('distance_m'):.2f}m")
print(f"  stable_glide_duration: {res.get('stable_glide_duration_s'):.2f}s")
print(f"  status: {res.get('status')}")
