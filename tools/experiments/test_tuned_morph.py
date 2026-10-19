import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator

# Let's test with q_aeroelastic tuned and launch_angle = 76 deg
preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['launch_angle_deg'] = 76.0
launch['release_pitch_deg'] = 0.0
launch['launch_height_m'] = 1.8

# Update flight_sim morphing reference pressure
import physics.flight_sim as fs

# Temporarily check with subclass
class TunedFlightSimulator(FlightSimulator):
    pass

sim = TunedFlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=launch
)

# We will test in a script
