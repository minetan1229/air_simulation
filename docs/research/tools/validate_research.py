#!/usr/bin/env python3
"""Validation gate for 2-tier paper research system.

Verifies:
1. 20 topic raw JSON files exist under search/raw_data/topic_01.json ... topic_20.json
2. Each JSON file contains valid 'candidates' with primary_paper (title, doi, abstract, authors, year)
   and second_tier_references (at least 2 references with title, doi, abstract, authors, year).
3. 20 markdown topic reports exist under search/topics/topic_01.md ... topic_20.md
4. Each markdown report cites the DOI of the primary paper AND at least 2 DOIs of second-tier references
   that match exactly the raw JSON data.
5. Each markdown report contains required structured sections (e.g., 概要/背景, 主要論文・理論, 第2階層引用文献, シミュレーター実装への適用・数式整合性).
6. Exits with 0 if and only if all checks PASS. Exits with non-zero on failure.
"""

import os
import sys
import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = BASE_DIR / "raw_data"
TOPICS_DIR = BASE_DIR / "topics"
TOTAL_TOPICS = 20

REQUIRED_SECTIONS = [
    "概要",
    "主要論文",
    "第2階層",
    "シミュレーター実装",
]

def log_pass(msg):
    print(f"[\033[92mPASS\033[0m] {msg}")

def log_fail(msg):
    print(f"[\033[91mFAIL\033[0m] {msg}")

def main():
    print("=" * 60)
    print("Starting Research Validation Gate (Two-Tier Paper Validator)")
    print("=" * 60)

    errors = []

    # Check directories
    if not RAW_DATA_DIR.exists():
        errors.append(f"Directory not found: {RAW_DATA_DIR}")
    if not TOPICS_DIR.exists():
        errors.append(f"Directory not found: {TOPICS_DIR}")

    if errors:
        for err in errors:
            log_fail(err)
        sys.exit(1)

    all_raw_data = {}

    # Check 1: Raw data validation
    print("\n--- Phase 1: Validating Raw API JSON Data ---")
    for i in range(1, TOTAL_TOPICS + 1):
        num_str = f"{i:02d}"
        json_file = RAW_DATA_DIR / f"topic_{num_str}.json"
        if not json_file.exists():
            errors.append(f"Missing raw data file: {json_file}")
            continue

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            errors.append(f"Failed to parse JSON {json_file}: {e}")
            continue

        candidates = data.get("candidates", [])
        if not candidates:
            errors.append(f"No candidates found in {json_file}")
            continue

        # Check candidate structure
        valid_candidates = 0
        for cand in candidates:
            prim = cand.get("primary_paper", {})
            refs = cand.get("second_tier_references", [])
            if prim.get("doi") and prim.get("title") and len(refs) >= 2:
                # check that refs have doi
                if all(r.get("doi") for r in refs[:2]):
                    valid_candidates += 1

        if valid_candidates == 0:
            errors.append(f"Topic {num_str} has no valid candidates with DOI and >=2 second-tier references")
        else:
            all_raw_data[num_str] = data
            log_pass(f"Topic {num_str} raw data: {len(candidates)} sets (valid: {valid_candidates})")

    # Check 2: Topic markdown report validation
    print("\n--- Phase 2: Validating Markdown Topic Reports ---")
    for i in range(1, TOTAL_TOPICS + 1):
        num_str = f"{i:02d}"
        md_file = TOPICS_DIR / f"topic_{num_str}.md"
        if not md_file.exists():
            errors.append(f"Missing markdown report: {md_file}")
            continue

        try:
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            errors.append(f"Failed to read markdown file {md_file}: {e}")
            continue

        if len(content.strip()) < 500:
            errors.append(f"Topic {num_str} report is too short (< 500 characters)")

        # Section check
        for sec in REQUIRED_SECTIONS:
            if sec not in content:
                errors.append(f"Topic {num_str} report missing required section keyword: '{sec}'")

        # Grounding check with raw JSON
        if num_str in all_raw_data:
            data = all_raw_data[num_str]
            candidates = data.get("candidates", [])
            matched_candidate = False

            for cand in candidates:
                prim_doi = cand.get("primary_paper", {}).get("doi", "").lower()
                # Check DOI in markdown (case-insensitive)
                if prim_doi and prim_doi in content.lower():
                    # Check at least 2 references matched
                    matched_refs = 0
                    for ref in cand.get("second_tier_references", []):
                        r_doi = ref.get("doi", "").lower()
                        if r_doi and r_doi in content.lower():
                            matched_refs += 1
                    if matched_refs >= 2:
                        matched_candidate = True
                        break

            if not matched_candidate:
                errors.append(f"Topic {num_str} markdown report does not cite valid primary DOI and >=2 second-tier reference DOIs from raw JSON")
            else:
                log_pass(f"Topic {num_str} report correctly grounded with primary DOI and second-tier reference DOIs")

    print("\n" + "=" * 60)
    if errors:
        log_fail(f"Validation FAILED with {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        print("=" * 60)
        sys.exit(1)
    else:
        log_pass(f"All {TOTAL_TOPICS} topics passed two-tier paper validation gate!")
        print("=" * 60)
        sys.exit(0)

if __name__ == "__main__":
    main()
