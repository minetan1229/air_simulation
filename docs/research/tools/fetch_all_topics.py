#!/usr/bin/env python3
import subprocess
import sys
import os

TOPICS = [
    ("01", "paper airplane aerodynamics low Reynolds glider"),
    ("02", "paper airplane flight mechanics launch trajectory"),
    ("03", "wind tunnel low Reynolds number aerodynamic testing"),
    ("04", "delta wing slender wing vortex lift Polhamus suction"),
    ("05", "six degree of freedom flight dynamics RK4 quaternion"),
    ("06", "aircraft flight dynamic stability longitudinal lateral trim"),
    ("07", "Viterna post-stall aerodynamic model wind turbine airfoil"),
    ("08", "aeroelastic wing deformation dynamic pressure structural stress"),
    ("09", "GPU accelerated fluid simulation CUDA parallel computing"),
    ("10", "US standard atmosphere Sutherland dynamic viscosity model"),
    ("11", "atmospheric turbulence wind Dryden von Karman simulation"),
    ("12", "NSGA-II multi-objective aerodynamic shape optimization genetic algorithm"),
    ("13", "aircraft mass properties center of gravity inertia tensor polyhedron"),
    ("14", "GPU particle system fluid vector field WebGL visualization"),
    ("15", "flight simulation telemetry data logging CSV JSON"),
    ("16", "ground effect aerodynamics low altitude induced drag reduction"),
    ("17", "laminar separation bubble transition low Reynolds number airfoil"),
    ("18", "origami folding stiffness mechanics thin sheet paper"),
    ("19", "aircraft post-stall pushover angle of attack pitching moment recovery"),
    ("20", "micro air vehicle bio-inspired flight aerodynamics Reynolds"),
]

def main():
    print(f"Starting batch fetch for {len(TOPICS)} topics...")
    for topic_num, query in TOPICS:
        print(f"\n[{topic_num}/20] Fetching for topic {topic_num}: {query}")
        cmd = [sys.executable, "search/tools/fetch_paper.py", topic_num, query]
        res = subprocess.run(cmd)
        if res.returncode != 0:
            print(f"ERROR: Fetch failed for topic {topic_num}")
            sys.exit(1)
    print("\nAll 20 topics fetched successfully!")

if __name__ == "__main__":
    main()
