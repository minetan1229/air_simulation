"""Automated regression harness for all registered adversarial benchmark cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ADVERSARIAL_DIR = REPO_ROOT / "tests" / "adversarial_cases"
VALID_VIOLATION_TYPES = {
    "energy_generation",
    "area_exceedance",
    "cpu_gpu_divergence",
    "perturbation_collapse",
    "geometry_violation",
}
REQUIRED_KEYS = {
    "case_id",
    "discovered_by",
    "sanity_violation_type",
    "root_cause",
    "linked_test",
    "genome_or_payload",
}


def _load_cases() -> list[tuple[Path, dict]]:
    cases = []
    for jf in sorted(ADVERSARIAL_DIR.glob("*.json")):
        cases.append((jf, json.loads(jf.read_text(encoding="utf-8"))))
    return cases


def test_adversarial_registry_has_at_least_one_case() -> None:
    cases = _load_cases()
    assert len(cases) >= 1, "tests/adversarial_cases/ must contain at least one registered P0 regression case"


@pytest.mark.parametrize("case_path,case_data", _load_cases(), ids=lambda x: x.name if isinstance(x, Path) else "")
def test_adversarial_case_schema_and_linked_test_exist(case_path: Path, case_data: dict) -> None:
    missing = REQUIRED_KEYS - set(case_data.keys())
    assert not missing, f"{case_path.name} missing keys: {sorted(missing)}"
    assert case_data["sanity_violation_type"] in VALID_VIOLATION_TYPES, (
        f"{case_path.name}: invalid sanity_violation_type={case_data['sanity_violation_type']!r}"
    )
    assert isinstance(case_data["genome_or_payload"], dict) and case_data["genome_or_payload"], (
        f"{case_path.name}: genome_or_payload must be a non-empty object"
    )
    linked = REPO_ROOT / str(case_data["linked_test"])
    assert linked.is_file(), f"{case_path.name}: linked_test not found at {case_data['linked_test']}"
