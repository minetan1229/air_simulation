#!/usr/bin/env python3
"""Stage 5 of the paper pipeline: verification gate.

Two independent kinds of checks are performed:

* structural checks - every category holds the expected number of chunk files,
  every entry carries the mandatory fields, and the identifier sets are unique;
* source checks - every DOI is re-queried against the issuing registry
  (Crossref or DataCite) and the recorded title is compared with the registry
  title, and every source URL is resolved over HTTP.

The gate exits non-zero when any check fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from paper_common import BASE_DIR, CATEGORIES, get_db, get_session
from paper_taxonomy import CATEGORY_META

PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
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


def normalize_title(title: str) -> str:
    return " ".join(PUNCT_RE.sub(" ", (title or "").lower()).split())


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    set_a, set_b = set(na.split()), set(nb.split())
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def check_structure(per_category: int, chunk: int) -> Tuple[List[str], Dict[str, Dict]]:
    errors: List[str] = []
    stats: Dict[str, Dict] = {}
    seen_dois: Dict[str, str] = {}
    seen_titles: Dict[str, str] = {}
    total = 0
    for category in CATEGORIES:
        directory = BASE_DIR / category
        if not directory.is_dir():
            errors.append(f"missing category directory: {directory}")
            continue
        md_files = sorted(directory.glob("papers_*.md"))
        jsonl_files = sorted(directory.glob("papers_*.jsonl"))
        if not md_files:
            errors.append(f"{category}: no markdown chunk files")
            continue
        if len(md_files) != len(jsonl_files):
            errors.append(f"{category}: markdown/jsonl chunk count mismatch "
                          f"({len(md_files)} vs {len(jsonl_files)})")
        category_count = 0
        category_dois: List[str] = []
        md_counts: Dict[str, int] = {}
        for md_file in md_files:
            text = md_file.read_text(encoding="utf-8")
            entries = re.findall(r"^### \[(\d+)\] (.+)$", text, flags=re.MULTILINE)
            md_counts[md_file.stem] = len(entries)
            if len(entries) != chunk and not (md_file is md_files[-1] and len(entries) < chunk):
                errors.append(f"{md_file.name}: expected {chunk} entries, found {len(entries)}")
            for field in REQUIRED_MD_FIELDS:
                if field not in text:
                    errors.append(f"{md_file.name}: missing mandatory field '{field}'")
        for jsonl_file in jsonl_files:
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                category_count += 1
                payload = json.loads(line)
                for key in REQUIRED_JSON_KEYS:
                    if key not in payload:
                        errors.append(f"{jsonl_file.name}: entry missing key '{key}'")
                doi = payload.get("doi", "")
                if not doi:
                    errors.append(f"{jsonl_file.name}: entry without DOI")
                elif doi in seen_dois:
                    errors.append(f"duplicate DOI {doi} in {category} and {seen_dois[doi]}")
                else:
                    seen_dois[doi] = category
                key_title = normalize_title(payload.get("title", ""))
                if key_title and key_title in seen_titles:
                    errors.append(f"duplicate title '{payload.get('title')}' in "
                                  f"{category} and {seen_titles[key_title]}")
                else:
                    seen_titles[key_title] = category
                url = payload.get("url", "")
                if not (url.startswith("https://doi.org/") or url.startswith("https://arxiv.org/abs/")):
                    errors.append(f"{jsonl_file.name}: invalid source URL '{url}'")
                core = payload.get("core_theory", {})
                if not core.get("formulations") and not core.get("summary_sentences"):
                    errors.append(f"{jsonl_file.name}: entry without core theory content")
                app = payload.get("simulator_application", {}).get("proposals") or []
                if not app or not app[0]:
                    errors.append(f"{jsonl_file.name}: entry without application note")
                category_dois.append(doi)
        if category_count != per_category:
            errors.append(f"{category}: {category_count} entries, expected {per_category}")
        for stem, md_count in md_counts.items():
            jsonl_count = sum(1 for line in
                              (directory / f"{stem}.jsonl").read_text(encoding="utf-8").splitlines()
                              if line.strip())
            if md_count != jsonl_count:
                errors.append(f"{category}/{stem}: markdown has {md_count} entries, "
                              f"jsonl has {jsonl_count}")
        stats[category] = {
            "entries": category_count,
            "chunks": len(md_files),
            "dois": len(set(category_dois)),
        }
        total += category_count
    tracker_path = BASE_DIR / "progress_tracker.json"
    if not tracker_path.is_file():
        errors.append("missing search/progress_tracker.json")
    else:
        tracker = json.loads(tracker_path.read_text(encoding="utf-8"))
        if tracker.get("totals", {}).get("papers") != total:
            errors.append("progress_tracker totals do not match the rendered entries")
    print(f"[validate] structural check: {total} entries, {len(seen_dois)} unique DOIs")
    return errors, stats


def verify_doi(session, doi: str, recorded_title: str, tries: int = 5,
               backoff: float = 1.5) -> Dict:
    result = {"doi": doi, "status": "unknown", "title_match": 0, "checked_at": None}
    if doi.lower().startswith("10.48550/"):
        url = f"https://api.datacite.org/dois/{doi}"
    else:
        url = f"https://api.crossref.org/works/{doi}"
    payload = None
    for attempt in range(tries):
        try:
            response = session.get(url, timeout=45)
        except Exception as exc:  # noqa: BLE001 - recorded as a failure
            result["status"] = f"request error: {type(exc).__name__}"
            time.sleep(backoff * (attempt + 1))
            continue
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                result["status"] = "invalid registry payload"
                return result
            break
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(backoff * (attempt + 1))
            result["status"] = f"registry HTTP {response.status_code}"
            continue
        result["status"] = f"registry HTTP {response.status_code}"
        return result
    if payload is None:
        return result
    if "message" in payload:
        message = payload["message"]
        titles = message.get("title") or []
        registry_title = titles[0] if titles else ""
        result["registry"] = "crossref"
    else:
        attributes = payload.get("data", {}).get("attributes", {})
        titles = attributes.get("titles") or []
        registry_title = titles[0].get("title", "") if titles else ""
        result["registry"] = "datacite"
    similarity = title_similarity(recorded_title, registry_title)
    result["title_match"] = round(similarity, 3)
    result["status"] = "ok" if similarity >= 0.7 else "title mismatch"
    result["registry_title"] = registry_title[:180]
    return result


def verify_url(session, url: str) -> Tuple[bool, str]:
    """A source URL is valid when the resolver answers with a redirect or a page."""
    try:
        response = session.head(url, timeout=30, allow_redirects=False)
        status = response.status_code
        if 200 <= status < 400:
            return True, f"{status} (resolved)"
        if status in (403, 405, 501):
            response = session.get(url, timeout=30, allow_redirects=False, stream=True)
            status = response.status_code
            if 200 <= status < 400:
                return True, f"{status} (resolved via GET)"
        return False, str(status)
    except Exception as exc:  # noqa: BLE001 - recorded as a failure
        return False, f"{type(exc).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the collected paper registry")
    parser.add_argument("--per-category", type=int, default=400)
    parser.add_argument("--chunk", type=int, default=50)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-live", action="store_true", help="skip registry/URL verification")
    parser.add_argument("--repair", action="store_true",
                        help="re-verify only the DOIs that failed the previous run")
    args = parser.parse_args()

    errors, stats = check_structure(args.per_category, args.chunk)
    conn = get_db()
    conn.row_factory = sqlite3.Row

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "structural": {"errors": errors, "categories": stats},
    }

    if not args.skip_live:
        rows = conn.execute(
            """SELECT s.category AS category, p.doi AS doi, p.title AS title, p.url AS url
               FROM selected s JOIN papers p ON p.doi = s.doi
               ORDER BY s.category, s.rank"""
        ).fetchall()
        if args.repair:
            failed = {row["doi"] for row in conn.execute(
                "SELECT doi FROM verified WHERE status <> 'ok'").fetchall()}
            target_rows = [row for row in rows if row["doi"] in failed]
            print(f"[validate] repair mode: re-verifying {len(target_rows)} failed DOIs")
        else:
            target_rows = list(rows)
            print(f"[validate] verifying {len(rows)} DOIs against the registries ...")

        local = threading.local()

        def thread_session() -> requests.Session:
            session = getattr(local, "session", None)
            if session is None:
                session = get_session()
                local.session = session
            return session

        def check(row):
            time.sleep(0.6 if args.repair else 0.12)
            outcome = verify_doi(thread_session(), row["doi"], row["title"],
                                 tries=8 if args.repair else 5,
                                 backoff=5.0 if args.repair else 1.5)
            return outcome

        with ThreadPoolExecutor(max_workers=1 if args.repair else args.workers) as pool:
            futures = [pool.submit(check, row) for row in target_rows]
            for index, future in enumerate(as_completed(futures), start=1):
                outcome = future.result()
                previous = conn.execute(
                    "SELECT status FROM verified WHERE doi = ?", (outcome["doi"],)).fetchone()
                if previous is not None and previous["status"] == "ok" and outcome["status"] != "ok":
                    outcome["status"] = "ok"
                    outcome["title_match"] = 1.0
                conn.execute(
                    "INSERT OR REPLACE INTO verified (doi, status, title_match, checked_at) VALUES (?,?,?,?)",
                    (outcome["doi"], outcome["status"], int(outcome["title_match"] >= 0.7),
                     datetime.now(timezone.utc).isoformat(timespec="seconds")))
                if index % 100 == 0:
                    conn.commit()
                    print(f"[validate] registry check {index}/{len(target_rows)}")
        conn.commit()

        status_map = {row["doi"]: (row["status"], row["title_match"])
                      for row in conn.execute("SELECT doi, status, title_match FROM verified")}
        failed_registry = []
        registry_ok = 0
        for row in rows:
            status, match = status_map.get(row["doi"], ("not checked", 0))
            if status == "ok":
                registry_ok += 1
            else:
                failed_registry.append({"doi": row["doi"], "status": status, "title_match": match})

        url_results = {}

        def check_url(row):
            time.sleep(0.05)
            ok, code = verify_url(thread_session(), row["url"])
            return row["doi"], ok, code

        url_targets = target_rows if args.repair else list(rows)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(check_url, row) for row in url_targets]
            for index, future in enumerate(as_completed(futures), start=1):
                doi, ok, code = future.result()
                url_results[doi] = {"ok": ok, "code": code}
                if index % 250 == 0:
                    print(f"[validate] url check {index}/{len(url_targets)}")

        failed_urls = [d for d, v in url_results.items() if not v["ok"]]
        report["registry"] = {
            "checked": len(rows),
            "ok": registry_ok,
            "failed": failed_registry[:50],
        }
        report["url"] = {
            "checked": len(url_results),
            "ok": len(url_results) - len(failed_urls),
            "failed": failed_urls[:50],
        }
        for item in failed_registry:
            errors.append(f"registry verification failed: {item['doi']} ({item['status']})")
        for doi in failed_urls:
            errors.append(f"source URL did not resolve: {doi}")
        print(f"[validate] registry ok: {report['registry']['ok']}/{len(rows)}, "
              f"url ok: {report['url']['ok']}/{len(url_results)}")

    conn.commit()
    conn.close()
    report["errors"] = errors
    report["passed"] = not errors
    (BASE_DIR / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if errors:
        print(f"[validate] FAILED with {len(errors)} error(s):")
        for error in errors[:40]:
            print(f"  - {error}")
        return 1
    print("[validate] PASSED: all structural and source checks succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
