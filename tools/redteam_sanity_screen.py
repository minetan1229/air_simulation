"""Deterministic 5-criterion physical sanity screener & Freeze gate for Red-Teaming loops.

Screens aircraft candidate JSONs (e.g. from `output/v3/` or `milcy` runs) across
the 5 P0 sanity invariants:
  1. area_exceedance       : wing_area_m2 > A4 sheet area (0.06237 m^2) or invalid dims
  2. geometry_violation    : penetrations > 0, keel_height_m <= 0, keel_layers < 2
  3. energy_generation     : unpowered zero-wind mechanical energy increase (dE > tol)
  4. cpu_gpu_divergence    : CPU vs GPU metric mismatch above tolerance (when present)
  5. perturbation_collapse : NaN/Inf or unphysical blow-up under small perturbation

Anomalous candidates are automatically quarantined to `.agents/quarantine/` in
the exact schema expected by `tests/adversarial_cases/`.

`FREEZE_PASS` eligibility is computed deterministically:
    freeze_eligible = (
        evaluated_count >= min_budget
        and existing_adversarial_passed
        and len(violations) == 0
    )
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

A4_MAX_AREA_M2 = 0.210 * 0.297  # 0.06237 m^2 physical upper bound for a single A4 sheet
DEFAULT_QUARANTINE_DIR = ROOT_DIR / ".agents" / "quarantine"
DEFAULT_MIN_BUDGET = 20


@dataclass
class SanityFinding:
    case_id: str
    violation_type: str
    detail: str
    source_file: str
    payload: Dict[str, Any] = field(default_factory=dict)


def _finite_float(val: Any, default: float = float("nan")) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else float("nan")
    except (TypeError, ValueError):
        return default


def screen_candidate_dict(
    data: Dict[str, Any],
    source_name: str = "inline",
    energy_evaluator: Optional[Callable[[Dict[str, Any]], float]] = None,
    perturbation_evaluator: Optional[Callable[[Dict[str, Any]], float]] = None,
    cpu_gpu_tol: float = 0.05,
    energy_tol_j: float = 1e-4,
    perturbation_ratio_limit: float = 10.0,
) -> List[SanityFinding]:
    """Run all 5 P0 sanity checks on a candidate JSON dictionary."""
    findings: List[SanityFinding] = []
    cid = str(data.get("candidate_id") or data.get("case_id") or Path(source_name).stem)
    geom = data.get("geometry") if isinstance(data.get("geometry"), dict) else {}
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}

    # 1. area_exceedance
    if "wing_area_m2" in geom:
        area = _finite_float(geom.get("wing_area_m2"))
        if math.isnan(area) or area <= 0.0 or area > A4_MAX_AREA_M2:
            findings.append(
                SanityFinding(
                    case_id=f"p0_area_{cid}",
                    violation_type="area_exceedance",
                    detail=f"wing_area_m2={geom.get('wing_area_m2')} violates (0, {A4_MAX_AREA_M2:.5f}]",
                    source_file=source_name,
                    payload=data,
                )
            )

    # 2. geometry_violation
    penetrations = geom.get("penetrations")
    keel_h = _finite_float(geom.get("keel_height_m"), default=0.01) if "keel_height_m" in geom else 0.01
    keel_layers = geom.get("keel_layers", 2)
    if (
        (penetrations is not None and int(penetrations) > 0)
        or (math.isnan(keel_h) or keel_h <= 0.0)
        or (keel_layers is not None and int(keel_layers) < 2)
    ):
        findings.append(
            SanityFinding(
                case_id=f"p0_geom_{cid}",
                violation_type="geometry_violation",
                detail=f"penetrations={penetrations}, keel_height_m={keel_h}, keel_layers={keel_layers}",
                source_file=source_name,
                payload=data,
            )
        )

    # 3. energy_generation
    delta_e: Optional[float] = None
    if energy_evaluator is not None:
        delta_e = _finite_float(energy_evaluator(data))
    elif "max_energy_delta_j" in metrics:
        delta_e = _finite_float(metrics.get("max_energy_delta_j"))
    if delta_e is not None and (math.isnan(delta_e) or delta_e > energy_tol_j):
        findings.append(
            SanityFinding(
                case_id=f"p0_energy_{cid}",
                violation_type="energy_generation",
                detail=f"unpowered mechanical energy delta={delta_e} > {energy_tol_j}",
                source_file=source_name,
                payload=data,
            )
        )

    # 4. cpu_gpu_divergence
    if "cpu_distance_m" in metrics and "gpu_distance_m" in metrics:
        cpu_d = _finite_float(metrics.get("cpu_distance_m"))
        gpu_d = _finite_float(metrics.get("gpu_distance_m"))
        denom = max(1.0, abs(cpu_d))
        if math.isnan(cpu_d) or math.isnan(gpu_d) or abs(cpu_d - gpu_d) / denom > cpu_gpu_tol:
            findings.append(
                SanityFinding(
                    case_id=f"p0_cpugpu_{cid}",
                    violation_type="cpu_gpu_divergence",
                    detail=f"cpu={cpu_d} vs gpu={gpu_d} exceeds rel_tol={cpu_gpu_tol}",
                    source_file=source_name,
                    payload=data,
                )
            )

    # 5. perturbation_collapse
    pert_ratio: Optional[float] = None
    if perturbation_evaluator is not None:
        pert_ratio = _finite_float(perturbation_evaluator(data))
    elif "perturbation_blowup_ratio" in metrics:
        pert_ratio = _finite_float(metrics.get("perturbation_blowup_ratio"))
    if pert_ratio is not None and (math.isnan(pert_ratio) or pert_ratio > perturbation_ratio_limit):
        findings.append(
            SanityFinding(
                case_id=f"p0_perturb_{cid}",
                violation_type="perturbation_collapse",
                detail=f"perturbation_blowup_ratio={pert_ratio} > {perturbation_ratio_limit}",
                source_file=source_name,
                payload=data,
            )
        )

    return findings


def quarantine_findings(
    findings: List[SanityFinding],
    quarantine_dir: Optional[Path] = None,
    max_items: int = 5,
) -> List[Path]:
    """Write top anomalous findings to `.agents/quarantine/` in adversarial schema."""
    qdir = quarantine_dir or DEFAULT_QUARANTINE_DIR
    qdir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    written: List[Path] = []
    for finding in findings[:max_items]:
        record = {
            "case_id": finding.case_id,
            "discovered_by": "redteam-sanity-screen",
            "sanity_violation_type": finding.violation_type,
            "root_cause": f"PENDING_ANATOMY: {finding.detail}",
            "linked_test": "tests/test_adversarial_benchmark.py",
            "genome_or_payload": finding.payload.get("config") or finding.payload,
            "source_file": finding.source_file,
        }
        out_path = qdir / f"{ts}_{finding.case_id}.json"
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(out_path)
    return written


def run_screening(
    candidate_paths: List[Path],
    min_budget: int = DEFAULT_MIN_BUDGET,
    existing_adversarial_passed: bool = True,
    quarantine_dir: Optional[Path] = None,
    write_quarantine: bool = True,
    energy_evaluator: Optional[Callable[[Dict[str, Any]], float]] = None,
    perturbation_evaluator: Optional[Callable[[Dict[str, Any]], float]] = None,
) -> Dict[str, Any]:
    all_findings: List[SanityFinding] = []
    evaluated = 0
    for path in candidate_paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        evaluated += 1
        all_findings.extend(
            screen_candidate_dict(
                data,
                source_name=path.name,
                energy_evaluator=energy_evaluator,
                perturbation_evaluator=perturbation_evaluator,
            )
        )

    quarantined_paths = (
        quarantine_findings(all_findings, quarantine_dir=quarantine_dir)
        if (write_quarantine and all_findings)
        else []
    )
    budget_met = evaluated >= min_budget
    freeze_eligible = bool(budget_met and existing_adversarial_passed and len(all_findings) == 0)

    return {
        "evaluated_count": evaluated,
        "min_budget": min_budget,
        "budget_met": budget_met,
        "existing_adversarial_passed": existing_adversarial_passed,
        "p0_violation_count": len(all_findings),
        "violations": [
            {
                "case_id": f.case_id,
                "violation_type": f.violation_type,
                "detail": f.detail,
                "source_file": f.source_file,
            }
            for f in all_findings
        ],
        "quarantined_files": [str(p) for p in quarantined_paths],
        "freeze_eligible": freeze_eligible,
        "verdict": "FREEZE_PASS" if freeze_eligible else ("NEEDS_FIX" if all_findings else "INSUFFICIENT_BUDGET"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="5-criterion P0 physical sanity screener & Freeze gate.")
    parser.add_argument("--input-dir", default=str(ROOT_DIR / "output" / "v3"), help="Directory of candidate JSONs")
    parser.add_argument("--min-budget", type=int, default=DEFAULT_MIN_BUDGET, help="Minimum evaluated candidates required for FREEZE_PASS")
    parser.add_argument("--quarantine-dir", default=str(DEFAULT_QUARANTINE_DIR))
    parser.add_argument("--no-quarantine", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    paths = sorted(in_dir.glob("*.json")) if in_dir.is_dir() else []
    report = run_screening(
        candidate_paths=paths,
        min_budget=args.min_budget,
        quarantine_dir=Path(args.quarantine_dir),
        write_quarantine=not args.no_quarantine,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"[redteam-screen] verdict={report['verdict']} evaluated={report['evaluated_count']}/{report['min_budget']} "
            f"p0_violations={report['p0_violation_count']} freeze_eligible={report['freeze_eligible']}"
        )
        for v in report["violations"]:
            print(f"  - [{v['violation_type']}] {v['case_id']}: {v['detail']}")
    return 0 if report["p0_violation_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
