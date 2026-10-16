import sys
sys.path.insert(0, '.')
import physics.records_domain as rd

res = rd.simulate_preset('unite_31s', duration_s=35.0)
samples = res['samples']
print(f'Total frames: {len(samples)}')
s0 = samples[0]
print('Keys in sample:', list(s0.keys()) if isinstance(s0, dict) else [a for a in dir(s0) if not a.startswith('_')])

print('Time(s)\tAlt(m)\tSpd(m/s)\tPitch\tAlpha\tCl\tCd\tCm\tStalled')
step = max(1, len(samples) // 25)
for s in samples[::step]:
    get = s.get if isinstance(s, dict) else lambda k, d=None: getattr(s, k, d)
    t = get('time_s')
    alt = get('altitude_m')
    spd = get('speed_mps')
    p = get('pitch_deg')
    alpha = get('alpha_deg')
    cl = get('cl')
    cd = get('cd')
    cm = get('cm')
    stalled = get('stalled')
    def fmt(v, f):
        return 'None' if v is None else f % v
    print(f"{fmt(t, '%.2f')}\t{fmt(alt, '%.2f')}\t{fmt(spd, '%.2f')}\t\t{fmt(p, '%.1f')}\t{fmt(alpha, '%.1f')}\t{fmt(cl, '%.2f')}\t{fmt(cd, '%.2f')}\t{fmt(cm, '%.3f')}\t{stalled}")
