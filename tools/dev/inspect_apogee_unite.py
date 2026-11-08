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
step = max(1, len(samples) // 30)

print("Flight Result:")
print(f"  flight_time: {res.get('flight_time_s'):.2f}s")
print(f"  max_alt: {res.get('max_altitude_m'):.2f}m")
print(f"  apogee_time: {res.get('apogee_time_s'):.2f}s")
print(f"  stable_glide_duration: {res.get('stable_glide_duration_s'):.2f}s")
print(f"  status: {res.get('status')}")

# Print points around apogee
print('\nt(s)\tAlt(m)\tSpd(m/s)\tPitch\tAlpha\tL/D\tG_load')
for s in samples:
    if 1.5 <= s.time_s <= 3.0:
        print(f"{s.time_s:.3f}\t{s.altitude_m:.2f}\t{s.speed_mps:.2f}\t\t{s.pitch_deg:.1f}\t{s.alpha_deg:.1f}\t{s.ld_ratio:.2f}\t{s.g_load:.2f}")
