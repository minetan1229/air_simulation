import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator

preset_data = rd.apply_preset('unite_31s')
launch = preset_data['launch']
launch['launch_angle_deg'] = 76.0
launch['release_pitch_deg'] = 0.0
launch['launch_height_m'] = 1.8

sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=launch
)
res = sim.simulate(duration_s=35.0)

samples = sim.history()
step = max(1, len(samples) // 30)
print("Result with 76 deg launch:")
print(f"  flight_time: {res.get('flight_time_s'):.2f}s")
print(f"  max_alt: {res.get('max_altitude_m'):.2f}m")
print(f"  apogee_time: {res.get('apogee_time_s'):.2f}s")
print(f"  stable_glide_duration: {res.get('stable_glide_duration_s'):.2f}s")

print('\nt(s)\tAlt(m)\tSpd(m/s)\tPitch\tAlpha\tL/D\tG_load')
for s in samples[::step]:
    print(f"{s.time_s:.2f}\t{s.altitude_m:.2f}\t{s.speed_mps:.2f}\t\t{s.pitch_deg:.1f}\t{s.alpha_deg:.1f}\t{s.ld_ratio:.2f}\t{s.g_load:.2f}")
