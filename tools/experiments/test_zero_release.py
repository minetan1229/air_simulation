import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator

preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['release_pitch_deg'] = 0.0

sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=launch
)
res = sim.simulate(duration_s=35.0)

samples = sim.history()
step = max(1, len(samples) // 25)
print('t(s)\tAlt(m)\tSpd(m/s)\tPitch\tAlpha\tL/D\tG_load')
for s in samples[::step]:
    print(f"{s.time_s:.3f}\t{s.altitude_m:.2f}\t{s.speed_mps:.2f}\t\t{s.pitch_deg:.1f}\t{s.alpha_deg:.1f}\t{s.ld_ratio:.2f}\t{s.g_load:.2f}")
