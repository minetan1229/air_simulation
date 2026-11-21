#!/usr/bin/env python3
"""Abstract backfill for selected records whose registry entry has no abstract.

The DOI content-negotiation endpoint (https://doi.org/<doi> with the CSL-JSON
media type) is queried; publishers that expose an abstract there have it stored
in the local cache together with its provenance. The abstract text is never
paraphrased, only cleaned of markup.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from paper_common import clean_abstract, get_db, get_session

CSL_ACCEPT = "application/vnd.citationstyles.csl+json"


def fetch_abstract(session, doi: str) -> Optional[str]:
    try:
        response = session.get(f"https://doi.org/{doi}", headers={"Accept": CSL_ACCEPT}, timeout=40)
    except Exception:  # noqa: BLE001 - backfill is best effort
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    abstract = payload.get("abstract")
    if not isinstance(abstract, str) or len(abstract.strip()) < 80:
        return None
    cleaned = clean_abstract(abstract)
    return cleaned if len(cleaned) >= 80 else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill abstracts via DOI content negotiation")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--scope", choices=("selected", "all"), default="selected")
    args = parser.parse_args()

    conn = get_db()
    conn.row_factory = __import__("sqlite3").Row
    if args.scope == "selected":
        rows = conn.execute(
            """SELECT DISTINCT p.doi AS doi FROM selected s JOIN papers p ON p.doi = s.doi
               WHERE length(p.abstract) <= 80"""
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT doi FROM papers WHERE length(abstract) <= 80 AND source = 'crossref'"
        ).fetchall()
    dois: List[str] = [row["doi"] for row in rows]
    print(f"[backfill] candidates without abstract: {len(dois)}")

    session = get_session()
    found = 0
    done = 0

    def work(doi: str) -> Optional[str]:
        time.sleep(0.05)
        return fetch_abstract(session, doi)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, doi): doi for doi in dois}
        for future in as_completed(futures):
            doi = futures[future]
            done += 1
            try:
                abstract = future.result()
            except Exception:  # noqa: BLE001 - keep the batch alive
                abstract = None
            if abstract:
                conn.execute(
                    "UPDATE papers SET abstract = ?, abstract_origin = 'doi-negotiation' WHERE doi = ?",
                    (abstract, doi))
                found += 1
            if done % 100 == 0:
                conn.commit()
                print(f"[backfill] {done}/{len(dois)} checked, {found} abstracts recovered")
    conn.commit()
    conn.close()
    print(f"[backfill] finished: {found}/{len(dois)} abstracts recovered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
