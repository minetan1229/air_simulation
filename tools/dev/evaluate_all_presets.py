import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from physics.records_domain import RECORD_PRESET_CATALOGUE, simulate_preset

def evaluate():
    print(f"{'Key':<20} | {'Name':<35} | {'Category':<10} | {'Time (s)':<8} | {'Dist (m)':<8} | {'Max Alt':<8} | {'Status'}")
    print("-" * 105)
    for p in RECORD_PRESET_CATALOGUE:
        try:
            res = simulate_preset(p.key, duration_s=60.0, calibrated=False)
            m = res.get("metrics", {})
            t = m.get("flight_time_s", 0.0)
            dist = m.get("distance_m", 0.0)
            max_alt = m.get("max_altitude_m", 0.0)
            status = m.get("status", "unknown")
            target = getattr(p.achievement, "metric_value", 0.0)
            unit = getattr(p.achievement, "unit", "")
            print(f"{p.key:<20} | {p.name[:35]:<35} | {p.category:<10} | {t:<8.2f} | {dist:<8.1f} | {max_alt:<8.1f} | {status:<8} (target: {target}{unit})")
        except Exception as e:
            print(f"{p.key:<20} | ERROR: {e}")

if __name__ == "__main__":
    evaluate()
