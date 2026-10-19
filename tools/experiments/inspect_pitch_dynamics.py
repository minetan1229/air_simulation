import sys
sys.path.insert(0, '.')
import physics.records_domain as rd
from physics.geometry_utils import euler_zyx_from_quaternion
import numpy as np

# Let's inspect the moments during the flight of unite_31s
# We will create the simulator manually and step through it
preset_data = rd.apply_preset('unite_31s')
from physics.flight_sim import FlightSimulator
sim = FlightSimulator(
    aircraft=preset_data['aircraft'],
    environment=preset_data['environment'],
    launch=preset_data['launch']
)

print("Inertia:", sim._inertia)
print("Mass:", sim._mass)
print("Derived:", sim._derived)

hist = []
for i in range(120): # run ~4 seconds
    res = sim.step()
    s = sim.state
    r, p, y = euler_zyx_from_quaternion(s.quaternion)
    diag = sim._diagnostics()
    # Let's check dynamics at current state
    y = np.array([
        s.position_m[0], s.position_m[1], s.position_m[2],
        s.velocity_mps[0], s.velocity_mps[1], s.velocity_mps[2],
        s.quaternion[0], s.quaternion[1], s.quaternion[2], s.quaternion[3],
        s.angular_velocity_radps[0], s.angular_velocity_radps[1], s.angular_velocity_radps[2]
    ], dtype=np.float64)
    dyn = sim._compute_dynamics(y, s.time_s)
    aero = dyn['aero']
    m_body = dyn['moment_body']
    omega = s.angular_velocity_radps
    hist.append({
        't': s.time_s,
        'alt': -s.position_m[2],
        'spd': dyn['speed'],
        'pitch': p,
        'alpha': dyn['alpha_deg'],
        'q_pitch': omega[1],
        'My': m_body[1],
        'cm': aero.cm,
        'cm_q': getattr(sim.aircraft, 'cm_q', 0.0),
        'q_dyn': aero.dynamic_pressure_pa,
        'pushover': dyn.get('pushover_moment', 0.0),
        'stalled': aero.stalled
    })
    if s.landed or s.crashed:
        break

print(f"{'t':6s} | {'alt':6s} | {'spd':6s} | {'pitch':6s} | {'alpha':6s} | {'q_rate':7s} | {'My(Nm)':9s} | {'cm':7s} | {'q_dyn':7s}")
for row in hist[::4]:
    print(f"{row['t']:6.2f} | {row['alt']:6.2f} | {row['spd']:6.2f} | {row['pitch']:6.1f} | {row['alpha']:6.1f} | {row['q_pitch']:7.2f} | {row['My']:9.5f} | {row['cm']:7.3f} | {row['q_dyn']:7.1f}")
