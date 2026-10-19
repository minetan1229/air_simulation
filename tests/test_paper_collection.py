"""Structure tests for the verified academic paper registry under ``search/``.

The tests are offline: they assert the shape of the generated registry
(chunk files, mandatory metadata fields, identifier uniqueness, tracker
consistency) so that a regression in the collection pipeline is caught by CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIR = REPO_ROOT / "search"
CATEGORIES = (
    "01_aerodynamics_cfd",
    "02_mechanics_multiphysics",
    "03_numerical_solvers",
    "04_collision_geometry",
    "05_gpu_realtime_sim",
)
PER_CATEGORY = 400
CHUNK_SIZE = 50
ENTRY_RE = re.compile(r"^### \[(\d+)\] (.+)$", re.MULTILINE)
REQUIRED_MD_FIELDS = (
    "- **著者 / 発行年 / 媒体**:",
    "- **ソースURL**:",
    "- **ID**:",
    "- **理論・アルゴリズムの核心**:",
    "- **本3Dシミュレーションへの適用価値**:",
)
REQUIRED_JSON_KEYS = (
    "rank", "category", "title", "title_ja", "authors", "year", "venue",
    "doi", "arxiv_id", "url", "abstract", "core_theory", "simulator_application",
)


def _category_entries(category: str):
    directory = SEARCH_DIR / category
    assert directory.is_dir(), f"missing category directory: {directory}"
    entries = []
    for jsonl_file in sorted(directory.glob("papers_*.jsonl")):
        for line in jsonl_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    return entries


def test_all_category_directories_exist():
    for category in CATEGORIES:
        assert (SEARCH_DIR / category).is_dir(), category


@pytest.mark.parametrize("category", CATEGORIES)
def test_category_has_expected_number_of_entries(category):
    entries = _category_entries(category)
    assert len(entries) == PER_CATEGORY


@pytest.mark.parametrize("category", CATEGORIES)
def test_entries_carry_mandatory_metadata(category):
    for entry in _category_entries(category):
        assert entry["title"]
        assert entry["year"] and 1950 <= entry["year"] <= 2026
        assert entry["venue"]
        assert entry["doi"]
        assert entry["url"].startswith(("https://doi.org/", "https://arxiv.org/abs/"))
        assert entry["core_theory"]["formulations"] or entry["core_theory"]["summary_sentences"]
        assert entry["simulator_application"]["proposals"]


def test_identifiers_are_globally_unique():
    dois = []
    titles = []
    for category in CATEGORIES:
        for entry in _category_entries(category):
            dois.append(entry["doi"])
            titles.append(entry["title"].strip().lower())
    assert len(dois) == len(set(dois)) == PER_CATEGORY * len(CATEGORIES)
    assert len(titles) == len(set(titles))


def test_markdown_chunks_render_all_entries():
    total = 0
    for category in CATEGORIES:
        for md_file in sorted((SEARCH_DIR / category).glob("papers_*.md")):
            text = md_file.read_text(encoding="utf-8")
            entries = ENTRY_RE.findall(text)
            assert entries, md_file.name
            assert len(entries) <= CHUNK_SIZE
            for field in REQUIRED_MD_FIELDS:
                assert field in text, f"{md_file.name}: {field}"
            total += len(entries)
    assert total == PER_CATEGORY * len(CATEGORIES)


def test_progress_tracker_matches_registry():
    tracker = json.loads((SEARCH_DIR / "progress_tracker.json").read_text(encoding="utf-8"))
    assert tracker["totals"]["papers"] == PER_CATEGORY * len(CATEGORIES)
    assert tracker["totals"]["unique_doi"] == PER_CATEGORY * len(CATEGORIES)
    assert len(tracker["dedup_index"]["dois"]) == PER_CATEGORY * len(CATEGORIES)
    for category in CATEGORIES:
        info = tracker["categories"][category]
        assert info["collected"] == PER_CATEGORY
        assert len(info["chunks"]) == PER_CATEGORY // CHUNK_SIZE


def test_validation_report_passes():
    report_path = SEARCH_DIR / "validation_report.json"
    assert report_path.is_file(), "validation report missing"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert not report["errors"]
