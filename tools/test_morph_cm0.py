import sys
sys.path.insert(0, '.')
import math
import numpy as np
import physics.records_domain as rd
from physics.geometry_utils import euler_zyx_from_quaternion
from physics.flight_sim import FlightSimulator
from dataclasses import replace


def main() -> None:
    preset_data = rd.apply_preset('unite_31s')
    ac = preset_data['aircraft']
    env = preset_data['environment']
    launch = preset_data['launch']

    class MorphFlightSimulator(FlightSimulator):
        def _compute_dynamics(self, y: np.ndarray, time_s: float):
            position = y[0:3]
            velocity = y[3:6]
            quaternion = y[6:10]
            speed = float(np.linalg.norm(velocity))
            q_dyn = 0.5 * 1.225 * speed * speed

            aero_comp_gain = max(getattr(self.aircraft, "aeroelastic_compliance", 0.0), 0.0)
            stiffness_val = max(getattr(self.aircraft, "paper_stiffness", 0.35), 0.05)

            q_aeroelastic = 30.0 * stiffness_val / max(aero_comp_gain, 0.1)
            pressure_ratio = q_dyn / q_aeroelastic
            morph_factor = 1.0 / (1.0 + (pressure_ratio ** 1.5))

            orig_cm0 = self.aircraft.cm0
            orig_elevon = self.aircraft.elevon_deg

            self.aircraft = replace(self.aircraft, cm0=orig_cm0 * morph_factor, elevon_deg=orig_elevon * morph_factor)
            try:
                return super()._compute_dynamics(y, time_s)
            finally:
                self.aircraft = replace(self.aircraft, cm0=orig_cm0, elevon_deg=orig_elevon)

    sim = MorphFlightSimulator(aircraft=ac, environment=env, launch=launch)
    res = sim.simulate(duration_s=35.0)

    samples = sim.history()
    step = max(1, len(samples) // 20)
    print('Time(s)\tAlt(m)\tSpd(m/s)\tPitch\tAlpha\tL/D\tG_load\tStalled')
    for s in samples[::step]:
        p = s.pitch_deg
        print(f"{s.time_s:.2f}\t{s.altitude_m:.2f}\t{s.speed_mps:.2f}\t\t{p:.1f}\t\t{s.alpha_deg:.1f}\t\t{s.ld_ratio:.2f}\t{s.g_load:.2f}\t{s.stalled}")


if __name__ == "__main__":
    main()
