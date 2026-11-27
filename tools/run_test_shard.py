#!/usr/bin/env python3
"""Run a designated shard of pytest test files and write results to output/test_shards/."""

import argparse
import fnmatch
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SHARD_DEFINITIONS = {
    "A": [
        "test_aero*.py",
        "test_wind*.py",
        "test_low_*.py",
        "test_environment*.py",
        "test_sideslip*.py",
        "test_polhamus*.py",
        "test_washout*.py",
        "test_lowre*.py",
        "test_unsteady*.py",
        "test_re_audit.py",
        "test_spanwise*.py",
        "test_turbulence*.py",
    ],
    "B": [
        "test_flight*.py",
        "test_simulation*.py",
        "test_tunneling_ccd*.py",
        "test_ground_*.py",
        "test_launch*.py",
        "test_stress*.py",
        "test_energy_*.py",
        "test_long_horizon*.py",
        "test_world_bounds.py",
        "test_yaw_added_inertia*.py",
        "test_morphing*.py",
    ],
    "C": [
        "test_origami*.py",
        "test_mesh*.py",
        "test_structural*.py",
        "test_kappa_sigma*.py",
        "test_geometry*.py",
        "test_issue90*.py",
        "test_issue98*.py",
        "test_panel*.py",
        "test_paper*.py",
    ],
    "D": [
        "test_jobs*.py",
        "test_optimizer*.py",
        "test_multifidelity*.py",
        "test_records.py",
        "test_process_hygiene.py",
        "test_docs*.py",
        "test_api*.py",
        "test_app*.py",
        "test_webhook*.py",
        "test_js_modules.py",
        "test_p1_contracts.py",
        "test_payload_contract.py",
        "test_open_issues_audit.py",
        "test_physical_laws_audit.py",
        "test_physics_audit.py",
        "test_physics_enhancements.py",
        "test_physics_first_principles.py",
        "test_physics_model_registry.py",
        "test_physics_pushover_and_ground_effect.py",
        "test_physics_references.py",
        "test_model_discrepancy.py",
        "test_model_fidelity_metadata_p1.py",
        "test_remote_compute_fixes.py",
        "test_repair_*.py",
        "test_security_and_frames_audit.py",
        "test_ui_*.py",
        "test_work_report.py",
        "test_no_mojibake.py",
    ],
}


def find_files(patterns):
    matched = []
    tests_dir = PROJECT_ROOT / "tests"
    for p in tests_dir.rglob("test_*.py"):
        rel = p.relative_to(tests_dir).as_posix()
        name = p.name
        for pat in patterns:
            if "/" in pat:
                if fnmatch.fnmatch(rel, pat):
                    matched.append(str(p))
                    break
            else:
                if fnmatch.fnmatch(name, pat):
                    matched.append(str(p))
                    break
    return sorted(list(set(matched)))


def main():
    parser = argparse.ArgumentParser(description="Test shard runner")
    parser.add_argument("shard", choices=["A", "B", "C", "D"], help="Shard identifier")
    args = parser.parse_args()

    patterns = SHARD_DEFINITIONS[args.shard]
    test_files = find_files(patterns)

    out_dir = PROJECT_ROOT / "output" / "test_shards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"shard_{args.shard}.txt"

    print(f"[*] Shard {args.shard}: Found {len(test_files)} test files.")
    if not test_files:
        print("[!] No test files matched patterns.")
        return 0

    cmd = [sys.executable, "-m", "pytest"] + test_files + ["-q", "--tb=short"]
    t0 = time.time()
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, encoding="utf-8")
    elapsed = time.time() - t0

    summary_text = (
        f"=== SHARD {args.shard} SUMMARY ===\n"
        f"Files: {len(test_files)}\n"
        f"Elapsed: {elapsed:.2f}s\n"
        f"Return Code: {res.returncode}\n\n"
        f"--- STDOUT ---\n{res.stdout}\n"
        f"--- STDERR ---\n{res.stderr}\n"
    )

    out_file.write_text(summary_text, encoding="utf-8")
    print(summary_text)
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
