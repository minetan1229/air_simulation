import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator

preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['release_pitch_deg'] = 0.0

# Let's inspect with different n_morph
import numpy as np

for n_morph in [1.5, 2.0, 2.5, 3.0]:
    # Test
    q_ratio = 413.0 / 20.0
    mf = 1.0 / (1.0 + (q_ratio ** n_morph))
    print(f"n_morph={n_morph}: morph_factor at 413 Pa = {mf:.2e}")
