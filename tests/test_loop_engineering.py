"""Unit tests for the deterministic Loop Engineering harness (`company_loop.py` & `redteam_sanity_screen.py`)."""

from __future__ import annotations

import json
from pathlib import Path

from tools.company_loop import CompanyLoopEngine, LoopConfig
from tools.redteam_sanity_screen import run_screening, screen_candidate_dict


def test_sanity_screener_catches_all_five_p0_violations() -> None:
    bad_cand = {
        "candidate_id": "evil-01",
        "geometry": {
            "wing_area_m2": 0.09,  # > A4 (0.06237 m^2)
            "penetrations": 3,
            "keel_height_m": 0.0,
            "keel_layers": 1,
        },
        "metrics": {
            "max_energy_delta_j": 0.05,
            "cpu_distance_m": 10.0,
            "gpu_distance_m": 25.0,
            "perturbation_blowup_ratio": 50.0,
        },
    }
    findings = screen_candidate_dict(bad_cand)
    types = {f.violation_type for f in findings}
    assert types == {
        "area_exceedance",
        "geometry_violation",
        "energy_generation",
        "cpu_gpu_divergence",
        "perturbation_collapse",
    }


def test_freeze_gate_blocks_early_freeze_when_budget_not_met(tmp_path: Path) -> None:
    good_cand = {
        "candidate_id": "clean-01",
        "geometry": {"wing_area_m2": 0.03, "penetrations": 0, "keel_height_m": 0.015, "keel_layers": 4},
        "metrics": {"max_energy_delta_j": 0.0, "cpu_distance_m": 12.0, "gpu_distance_m": 12.1, "perturbation_blowup_ratio": 1.2},
    }
    paths = []
    for i in range(3):
        p = tmp_path / f"cand_{i}.json"
        p.write_text(json.dumps(good_cand), encoding="utf-8")
        paths.append(p)

    # Budget=20, evaluated=3 -> MUST NOT freeze early
    rep_low = run_screening(paths, min_budget=20, write_quarantine=False)
    assert rep_low["p0_violation_count"] == 0
    assert rep_low["freeze_eligible"] is False
    assert rep_low["verdict"] == "INSUFFICIENT_BUDGET"

    # Budget=3, evaluated=3, violations=0 -> deterministic FREEZE_PASS
    rep_ok = run_screening(paths, min_budget=3, write_quarantine=False)
    assert rep_ok["freeze_eligible"] is True
    assert rep_ok["verdict"] == "FREEZE_PASS"


def test_company_loop_enforces_hard_2_strike_advisor_ratchet_and_freeze(tmp_path: Path) -> None:
    qdir = tmp_path / "quarantine"
    adv_dir = tmp_path / "adversarial_cases"
    kd_dir = tmp_path / "knowledge.d"
    tel_path = tmp_path / "telemetry" / "loop_history.jsonl"

    # Iteration 1 finds 1 P0 anomaly & quarantines it; Iteration 2 finds 0 anomalies with budget met -> FREEZE_PASS
    bad_cand = {
        "candidate_id": "loophole-99",
        "geometry": {"wing_area_m2": 0.08, "penetrations": 0, "keel_height_m": 0.012, "keel_layers": 4},
        "config": {"paper_gsm": 100.0},
    }
    bad_file = tmp_path / "cand_bad.json"
    bad_file.write_text(json.dumps(bad_cand), encoding="utf-8")

    def fake_screener(iteration: int):
        if iteration == 1:
            return run_screening([bad_file], min_budget=1, quarantine_dir=qdir, write_quarantine=True)
        return {
            "evaluated_count": 25,
            "min_budget": 20,
            "budget_met": True,
            "existing_adversarial_passed": True,
            "p0_violation_count": 0,
            "violations": [],
            "quarantined_files": [],
            "freeze_eligible": True,
            "verdict": "FREEZE_PASS",
        }

    received_prompts = []

    def fake_sub(ticket, prompt: str):
        received_prompts.append(prompt)
        # Fail first 2 attempts; succeed on 3rd attempt once Advisor guidance is injected
        if "FUNDAMENTAL_FIX_FROM_OPUS" in prompt:
            return {"ok": True, "root_cause": "Enforced A4 sheet area upper bound in geometry kernel"}
        return {"ok": False, "error": "still exceeding A4 area"}

    def fake_advisor(ticket, failure_summary: str):
        return {"success": True, "model": "opus4.6", "answer": "FUNDAMENTAL_FIX_FROM_OPUS"}

    def fake_watchdog(ticket, sub_res):
        if sub_res.get("ok"):
            return {"verdict": "PASS", "reason": "verified"}
        return {"verdict": "REJECT", "reason": sub_res.get("error", "failed")}

    cfg = LoopConfig(
        goal="Seal P0 loopholes",
        max_iterations=4,
        escalate_after_failures=2,
        quarantine_dir=qdir,
        adversarial_dir=adv_dir,
        knowledge_d_dir=kd_dir,
        telemetry_path=tel_path,
    )
    engine = CompanyLoopEngine(
        cfg,
        screener_fn=fake_screener,
        sub_runner=fake_sub,
        advisor_runner=fake_advisor,
        watchdog_runner=fake_watchdog,
    )
    res = engine.run()

    assert res["final_state"] == "FREEZE_PASS"
    assert res["iterations_run"] == 2
    assert res["total_advisor_calls"] == 1
    assert res["total_promoted_cases"] == 1
    assert len(received_prompts) == 3
    assert "FUNDAMENTAL_FIX_FROM_OPUS" in received_prompts[2]

    # Verify Ratchet promoted the anomaly permanently into adversarial_cases/
    promoted_files = list(adv_dir.glob("*.json"))
    assert len(promoted_files) == 1
    promoted_data = json.loads(promoted_files[0].read_text(encoding="utf-8"))
    assert promoted_data["sanity_violation_type"] == "area_exceedance"
    assert "A4 sheet area" in promoted_data["root_cause"]
    assert len(list(qdir.glob("*.json"))) == 0

    # Verify telemetry JSONL & knowledge.d fragment
    tel_lines = [json.loads(line) for line in tel_path.read_text(encoding="utf-8").splitlines()]
    assert len(tel_lines) == 2
    assert tel_lines[0]["state"] == "ITERATION_COMPLETE"
    assert tel_lines[1]["state"] == "FREEZE_PASS"
    assert len(list(kd_dir.glob("*.md"))) == 1
