import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.flight_sim import FlightSimulator


def main() -> None:
    preset_data = rd.apply_preset('unite_31s')
    launch = preset_data['launch']
    launch['launch_angle_deg'] = 75.0
    launch['release_pitch_deg'] = 0.0
    launch['launch_height_m'] = 1.8

    ac = preset_data['aircraft']
    if isinstance(ac, dict):
        ac['rudder_deg'] = 0.25  # slight spiral turn bias as used by real Guinness throwers
    else:
        from dataclasses import replace
        ac = replace(ac, rudder_deg=0.25)

    sim = FlightSimulator(
        aircraft=ac,
        environment=preset_data['environment'],
        launch=launch
    )
    res = sim.simulate(duration_s=35.0)

    samples = sim.history()
    step = max(1, len(samples) // 30)

    print("Result with spiral trim:")
    print(f"  flight_time: {res.get('flight_time_s'):.2f}s")
    print(f"  max_alt: {res.get('max_altitude_m'):.2f}m")
    print(f"  apogee_time: {res.get('apogee_time_s'):.2f}s")
    print(f"  stable_glide_duration: {res.get('stable_glide_duration_s'):.2f}s")
    print(f"  status: {res.get('status')}")

    print('\nt(s)\tAlt(m)\tSpd(m/s)\tRoll\tPitch\tYaw\tAlpha\tL/D')
    for s in samples[::step]:
        print(f"{s.time_s:.2f}\t{s.altitude_m:.2f}\t{s.speed_mps:.2f}\t\t{s.roll_deg:.1f}\t{s.pitch_deg:.1f}\t{s.yaw_deg:.1f}\t{s.alpha_deg:.1f}\t{s.ld_ratio:.2f}")


if __name__ == "__main__":
    main()
