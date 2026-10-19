import sys
sys.path.insert(0, '.')
import physics.records_domain as rd

for item in rd.list_presets():
    key = item['key']
    pr = rd.preset(key)
    ac = pr.aircraft if isinstance(pr.aircraft, dict) else pr.aircraft.__dict__
    cm_q = ac.get('cm_q')
    cm_alpha = ac.get('cm_alpha')
    cm0 = ac.get('cm0')
    comp = ac.get('aeroelastic_compliance')
    flex = ac.get('aeroelastic_flex')
    twist = ac.get('aeroelastic_twist')
    stiff = ac.get('paper_stiffness')
    print(f"{key:22s} | cm_q={cm_q!s:6s} | cm_a={cm_alpha!s:6s} | cm0={cm0!s:6s} | comp={comp!s:5s} | stiff={stiff}")
