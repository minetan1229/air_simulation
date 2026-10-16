import sys
sys.path.insert(0, '.')
import math
import numpy as np
import physics.records_domain as rd
from physics.geometry_utils import euler_zyx_from_quaternion
from physics.flight_sim import FlightSimulator

# Let's test modifying the trailing_edge_bend and pushover damping
# We will subclass or monkeypatch temporarily to inspect the trajectory
preset_data = rd.apply_preset('unite_31s')

ac = preset_data['aircraft']
env = preset_data['environment']
launch = preset_data['launch']

# Let's inspect launch pitch and angle
print("Launch config:", launch)

sim = FlightSimulator(aircraft=ac, environment=env, launch=launch)
res = sim.simulate(duration_s=35.0)
print("Before fix:")
print(f"  flight_time: {res.get('flight_time_s'):.2f}s")
print(f"  max_alt: {res.get('max_altitude_m'):.2f}m")
print(f"  distance: {res.get('distance_m'):.2f}m")
print(f"  stable_glide_duration: {res.get('stable_glide_duration_s'):.2f}s")
print(f"  status: {res.get('status')}")
