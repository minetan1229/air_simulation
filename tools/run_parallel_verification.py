"""
Massive Parallel Verification Runner & Kilo Code Worker Orchestrator
Executes domain-specific pytest suites and dispatches parallel Kilo Code CLI audit tasks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

# Domain test bundles
TEST_BUNDLES: Dict[str, Dict[str, Any]] = {
    "Worker-1-Static-UI-API": {
        "commands": [
            [sys.executable, "-m", "compileall", "core", "physics", "geometry", "optimization", "infrastructure", "api", "app.py"],
            ["ruff", "check", "core", "physics", "geometry", "optimization", "infrastructure", "api", "app.py"],
            [sys.executable, "-m", "pytest", "tests/test_js_modules.py", "tests/test_ui_css.py", "tests/test_api_contract.py", "tests/test_payload_contract.py", "-q"],
        ],
        "kilo_prompt": "以下の変更点を確認し、フロントエンドのモジュール分割（app_modes.js, app_events.js, app_ensemble.js）およびAPI契約の型安全性について100文字以内で監査評価してください。",
    },
    "Worker-2-Aero-PhysicsLaws": {
        "commands": [
            [sys.executable, "-m", "pytest", "tests/test_aero.py", "tests/test_physical_laws_audit.py", "tests/test_coefficient_buildup_audit.py", "-q"],
        ],
        "kilo_prompt": "physics パッケージから core/geometry への分離における、空力計算（aero_kernel）と第一原理物理定数の整合性・エネルギー保存性について100文字以内で監査評価してください。",
    },
    "Worker-3-Flight-6DoF": {
        "commands": [
            [sys.executable, "-m", "pytest", "tests/test_6dof_physical_audit.py", "tests/test_energy_conservation_ground.py", "tests/test_ground_friction_mass.py", "-q"],
        ],
        "kilo_prompt": "flight_sim.py から throw_solver.py および ground_contact.py を抽出したことによる、数値積分と接地挙動への影響を100文字以内で監査評価してください。",
    },
    "Worker-4-CFD-Solvers": {
        "commands": [
            [sys.executable, "-m", "pytest", "tests/test_cfd_stage0.py", "tests/test_cfd_stage1.py", "tests/test_tunneling_ccd.py", "-q"],
        ],
        "kilo_prompt": "CFDソルバーおよび連続衝突判定（CCD）における、新しい幾何インターフェース（geometry_utils）との結合性を100文字以内で監査評価してください。",
    },
    "Worker-5-Origami-Geometry": {
        "commands": [
            [sys.executable, "-m", "pytest", "tests/test_origami.py", "tests/test_origami_genome.py", "tests/test_mesh.py", "tests/test_structural_model_calibration.py", "-q"],
        ],
        "kilo_prompt": "幾何ドメイン（geometry/origami_domain.py）の新設と、折り紙ゲノム・メッシュ変形計算の分離独立性について100文字以内で監査評価してください。",
    },
    "Worker-6-GA-Optimization": {
        "commands": [
            [sys.executable, "-m", "pytest", "tests/test_ga_engine.py", "tests/test_ga_physics_readiness.py", "tests/test_optimizer.py", "tests/test_diversity_quota.py", "-q"],
        ],
        "kilo_prompt": "最適化ドメイン（optimization/ga_domain.py, optimizer_domain.py）の新設と、パレート多様性アーカイブの独立性について100文字以内で監査評価してください。",
    },
    "Worker-7-V3-Smoke": {
        "commands": [
            [sys.executable, "scripts/smoke_test.py"],
        ],
        "kilo_prompt": "v3 MCTS + 3D MAP-Elites パイプラインおよび 14 ステージ E2E スモークテストの総合合格判定を100文字以内で評価してください。",
    },
}


def run_command(cmd: List[str], cwd: str) -> Dict[str, Any]:
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        return {
            "cmd": " ".join(cmd),
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-1500:] if proc.stdout else "",
            "duration": round(time.time() - start, 2),
            "success": proc.returncode == 0,
        }
    except Exception as exc:
        return {
            "cmd": " ".join(cmd),
            "exit_code": -1,
            "stdout": str(exc),
            "duration": round(time.time() - start, 2),
            "success": False,
        }


def run_kilo_audit(worker_name: str, prompt: str, root_dir: str) -> Dict[str, Any]:
    from tools.kilo_subagent import execute_kilo_worker

    start = time.time()
    try:
        res = execute_kilo_worker(
            prompt=prompt,
            model="kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
            timeout=120,
        )
        return {
            "worker": worker_name,
            "success": res.get("success", False),
            "text": res.get("text", "")[:500],
            "error": res.get("error"),
            "duration": round(time.time() - start, 2),
        }
    except Exception as exc:
        return {
            "worker": worker_name,
            "success": False,
            "text": "",
            "error": str(exc),
            "duration": round(time.time() - start, 2),
        }


def execute_worker(worker_name: str, bundle: Dict[str, Any], root_dir: str) -> Dict[str, Any]:
    print(f"[*] Starting {worker_name}...")
    cmd_results = []
    all_cmd_success = True

    for cmd in bundle["commands"]:
        res = run_command(cmd, root_dir)
        cmd_results.append(res)
        if not res["success"]:
            all_cmd_success = False
            print(f"[!] {worker_name} failed command: {res['cmd']}")
            if res.get("stdout"):
                print(f"    Tail output: {res['stdout'][-300:]}")

    # Run Kilo Code audit worker
    print(f"[*] Dispatching Kilo Code CLI audit for {worker_name}...")
    kilo_res = run_kilo_audit(worker_name, bundle["kilo_prompt"], root_dir)

    print(f"[+] Completed {worker_name} (commands_ok={all_cmd_success}, kilo_ok={kilo_res['success']})")
    return {
        "worker": worker_name,
        "commands_success": all_cmd_success,
        "command_details": cmd_results,
        "kilo_audit": kilo_res,
    }


def main():
    if sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    root_dir = str(Path(__file__).resolve().parent.parent)
    output_dir = Path(root_dir) / "output" / "parallel_verification"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("======================================================================")
    print(">> Massive Parallel Verification & Kilo Code CLI Worker Orchestration")
    print(f"Root: {root_dir}")
    print(f"Workers: {len(TEST_BUNDLES)} concurrent domains")
    print("======================================================================")

    start_total = time.time()
    results = {}

    with ThreadPoolExecutor(max_workers=len(TEST_BUNDLES)) as executor:
        futures = {
            executor.submit(execute_worker, name, bundle, root_dir): name
            for name, bundle in TEST_BUNDLES.items()
        }
        for future in as_completed(futures):
            worker_name = futures[future]
            try:
                res = future.result()
                results[worker_name] = res
            except Exception as exc:
                print(f"[!] Worker {worker_name} raised exception: {exc}")
                results[worker_name] = {
                    "worker": worker_name,
                    "commands_success": False,
                    "error": str(exc),
                }

    total_duration = round(time.time() - start_total, 2)
    summary_path = output_dir / "verification_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n======================================================================")
    print(f"=== Verification Finished in {total_duration}s ===")
    print("======================================================================")
    all_passed = True
    for name, res in sorted(results.items()):
        cmd_ok = res.get("commands_success", False)
        kilo_ok = res.get("kilo_audit", {}).get("success", False)
        status = "[PASS]" if cmd_ok else "[FAIL]"
        kilo_status = "[Kilo: OK]" if kilo_ok else "[Kilo: Fallback/Skip]"
        print(f" - {name:<28}: {status} | {kilo_status}")
        if not cmd_ok:
            all_passed = False

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
