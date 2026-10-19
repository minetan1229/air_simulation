#!/usr/bin/env python3
"""Stage 1 of the paper pipeline: harvest candidate papers from live registries.

Crossref supplies journal and proceedings articles (DOI + abstract + venue);
DataCite supplies arXiv preprints registered under DOI prefix 10.48550.
Every record is written straight into SQLite so the process keeps a bounded
memory footprint regardless of how many candidates are harvested.
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, cast

from paper_common import MAILTO, clean_abstract, dumps, get_db, get_session, request_json
from paper_taxonomy import CATEGORY_META
from paper_common import CATEGORIES

CROSSREF_URL = "https://api.crossref.org/works"
DATACITE_URL = "https://api.datacite.org/dois"
CROSSREF_TYPES = ("journal-article", "proceedings-article")
DATACITE_PREFIX = "10.48550"
ARXIV_PREFIX = "10.48550/arxiv."

_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def crossref_record(item: Dict) -> Optional[Dict]:
    doi = (item.get("DOI") or "").strip().lower()
    titles = item.get("title") or []
    if not doi or not titles:
        return None
    title = " ".join(titles[0].split())
    if len(title) < 12:
        return None
    issued = item.get("issued", {}).get("date-parts", [[None]])[0]
    year = issued[0] if issued and issued[0] else None
    if not isinstance(year, int):
        return None
    authors = []
    for author in (item.get("author") or [])[:12]:
        name = " ".join(
            part for part in (author.get("given"), author.get("family")) if part
        ).strip()
        if not name:
            name = (author.get("name") or "").strip()
        if name:
            authors.append(name)
    containers = item.get("container-title") or []
    venue = containers[0] if containers else ""
    if not venue:
        events = item.get("event") or {}
        venue = events.get("name") or ""
    return {
        "doi": doi,
        "source": "crossref",
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue or "(publisher record)",
        "publisher": item.get("publisher") or "",
        "ptype": item.get("type") or "",
        "abstract": clean_abstract(item.get("abstract")),
        "url": f"https://doi.org/{doi}",
        "arxiv_id": "",
        "citations": int(item.get("is-referenced-by-count") or 0),
        "subjects": (item.get("subject") or [])[:8],
    }


def datacite_record(item: Dict) -> Optional[Dict]:
    attributes = item.get("attributes") or {}
    doi = (attributes.get("doi") or "").strip().lower()
    titles = attributes.get("titles") or []
    if not doi or not titles:
        return None
    title = " ".join((titles[0].get("title") or "").split())
    if len(title) < 12:
        return None
    year = attributes.get("publicationYear")
    if not isinstance(year, int):
        return None
    creators = []
    for creator in (attributes.get("creators") or [])[:12]:
        name = (creator.get("name") or "").strip()
        if not name and creator.get("givenName"):
            name = f"{creator.get('givenName', '')} {creator.get('familyName', '')}".strip()
        if name:
            creators.append(name)
    description = ""
    for desc in attributes.get("descriptions") or []:
        text = desc.get("description") or ""
        if len(text) > len(description):
            description = text
    arxiv_id = ""
    url = attributes.get("url") or ""
    if doi.startswith(ARXIV_PREFIX):
        arxiv_id = doi[len(ARXIV_PREFIX):]
        url = f"https://arxiv.org/abs/{arxiv_id}"
        venue = f"arXiv preprint (arXiv:{arxiv_id})"
    else:
        venue = attributes.get("publisher") or "(repository record)"
    return {
        "doi": doi,
        "source": "datacite",
        "title": title,
        "authors": creators,
        "year": year,
        "venue": venue,
        "publisher": attributes.get("publisher") or "",
        "ptype": "posted-content",
        "abstract": clean_abstract(description),
        "url": url or f"https://doi.org/{doi}",
        "arxiv_id": arxiv_id,
        "citations": 0,
        "subjects": [attributes.get("publisher") or ""],
    }


def store_records(conn, records: List[Dict], category: str, query: str, origin: str) -> int:
    stored = 0
    for record in records:
        cur = conn.execute(
            """INSERT OR IGNORE INTO papers
               (doi, source, title, authors, year, venue, publisher, ptype, abstract, url,
                arxiv_id, citations, subjects)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record["doi"], record["source"], record["title"], dumps(record["authors"]),
                record["year"], record["venue"], record["publisher"], record["ptype"],
                record["abstract"], record["url"], record["arxiv_id"], record["citations"],
                dumps(record["subjects"]),
            ),
        )
        if cur.rowcount:
            stored += 1
        conn.execute(
            "INSERT OR IGNORE INTO hits (category, doi, query, origin) VALUES (?,?,?,?)",
            (category, record["doi"], query, origin),
        )
    conn.commit()
    return stored


def fetch_crossref(session, category: str, query: str, work_type: str, rows: int,
                   title_mode: bool = False) -> int:
    params = {
        "rows": rows,
        "filter": f"type:{work_type}",
        "mailto": MAILTO,
        "select": "DOI,title,author,issued,container-title,event,abstract,type,publisher,"
                  "is-referenced-by-count,subject",
    }
    if title_mode:
        params["query.title"] = query
    else:
        params["query.bibliographic"] = query
    data = request_json(session, CROSSREF_URL, params, tries=4, timeout=60)
    if not data:
        return 0
    items = data.get("message", {}).get("items", [])
    records = [r for r in (crossref_record(i) for i in items) if r]
    conn = get_db()
    return store_records(conn, records, category, query, f"crossref:{work_type}:{'title' if title_mode else 'relevance'}")


def fetch_datacite(session, category: str, query: str, rows: int) -> int:
    params = {"query": query, "page[size]": rows, "prefix": DATACITE_PREFIX}
    data = request_json(session, DATACITE_URL, params, tries=4, timeout=60)
    if not data:
        return 0
    records = [r for r in (datacite_record(i) for i in data.get("data", [])) if r]
    records = [r for r in records if r["doi"].startswith(ARXIV_PREFIX)]
    conn = get_db()
    return store_records(conn, records, category, query, "datacite:arxiv")


def collect_category(session, category: str, rows: int, datacite_rows: int, workers: int,
                    limit_queries: int = 0) -> Dict[str, int]:
    meta = CATEGORY_META[category]
    queries: List[str] = list(cast(List[str], meta["queries"]))
    if limit_queries:
        queries = queries[:limit_queries]
    tasks = []
    for query in queries:
        for work_type in CROSSREF_TYPES:
            tasks.append(("crossref", query, work_type, rows, False))
    canonical = queries[:15]
    for query in canonical:
        tasks.append(("crossref", query, "journal-article", 40, True))
    for query in queries:
        tasks.append(("datacite", query, "", datacite_rows, False))

    stats = {"crossref": 0, "datacite": 0, "requests": 0}
    log(f"  [{category}] {len(queries)} queries -> {len(tasks)} requests")

    def run(task):
        kind, query, work_type, row_count, title_mode = task
        time.sleep(random.uniform(0.0, 0.4))
        if kind == "crossref":
            return fetch_crossref(session, category, query, work_type, row_count, title_mode)
        return fetch_datacite(session, category, query, row_count)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, task): task for task in tasks}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                count = future.result()
            except Exception as exc:  # noqa: BLE001 - keep the batch alive
                log(f"    [error] {futures[future][0]}: {type(exc).__name__}: {exc}")
                count = 0
            if futures[future][0] == "crossref":
                stats["crossref"] += count
            else:
                stats["datacite"] += count
            stats["requests"] += 1
            if done % 40 == 0:
                log(f"    ... {done}/{len(tasks)} requests, "
                    f"crossref={stats['crossref']} datacite={stats['datacite']}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Harvest candidate papers from Crossref and DataCite")
    parser.add_argument("--categories", nargs="*", default=list(CATEGORIES))
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--datacite-rows", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit-queries", type=int, default=0)
    args = parser.parse_args()

    session = get_session()
    for category in args.categories:
        if category not in CATEGORY_META:
            log(f"unknown category: {category}")
            continue
        log(f"[collect] {category}")
        stats = collect_category(session, category, args.rows, args.datacite_rows, args.workers,
                                 args.limit_queries)
        log(f"  [collect] {category} done: {stats}")

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    conn.close()
    log(f"[collect] unique papers in store: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
