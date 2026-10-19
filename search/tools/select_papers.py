#!/usr/bin/env python3
"""Stage 2 of the paper pipeline: score, deduplicate and select the final set.

Scoring is purely mechanical: the taxonomy's core/support keyword weights are
applied to the title, abstract and venue of each harvested record, with a
citation and recency tie-breaker. Works that appear twice (for example an
arXiv preprint and its journal version) are merged into a single entry, and
every work is assigned to exactly one category so the 2,000 record total holds
no duplicates.
"""

from __future__ import annotations

import argparse
import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from paper_common import CATEGORIES, get_db, loads
from paper_taxonomy import CATEGORY_META

PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
CURRENT_YEAR = 2026
ACCEPTED_TYPES = ("journal-article", "proceedings-article", "posted-content")
TITLE_BLOCKLIST = (
    "corrigendum", "erratum", "correction to", "correction of", "editorial board",
    "editorial:", "editorial on", "author index", "subject index", "table of contents",
    "contents of", "front matter", "back matter", "preface", "foreword", "obituary",
    "book review", "abstracts of", "call for papers", "call for participation",
    "acknowledgement of reviewers", "list of reviewers", "conference report",
    "retraction of", "retracted:", "withdrawn:", "erratum for", "expression of concern",
    "introduction to the special", "editorial material", "author index for",
)
ABSTRACT_BONUS = 0.8


def is_acceptable_type(ptype: str, title: str) -> bool:
    if ptype not in ACCEPTED_TYPES:
        return False
    lowered = title.lower()
    for phrase in TITLE_BLOCKLIST:
        if phrase in lowered:
            return False
    return True


def normalize_title(title: str) -> str:
    text = PUNCT_RE.sub(" ", title.lower())
    text = re.sub(r"\b(arxiv preprint|preprint|journal of|proceedings of|part)\b", " ", text)
    return " ".join(text.split())


def contains(text: str, term: str) -> bool:
    if not text:
        return False
    if re.fullmatch(r"[A-Za-z0-9+.\-]{1,6}", term):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])", text) is not None
    return term.lower() in text


def score_record(title: str, abstract: str, venue: str, year: int, citations: int,
                 category: str, allow_support_only: bool = False,
                 strict: bool = True) -> Tuple[float, int, str]:
    meta = CATEGORY_META[category]
    core: Dict[str, float] = meta["core"]  # type: ignore[assignment]
    support: List[str] = meta["support"]  # type: ignore[assignment]
    neg: List[str] = meta["neg"]  # type: ignore[assignment]
    t_low = title.lower()
    a_low = abstract.lower()
    v_low = venue.lower()

    score = 0.0
    core_hits = 0
    core_title_hits = 0
    support_title_hits = 0
    matched: List[str] = []
    for term, weight in core.items():
        if contains(t_low, term):
            score += 3.0 * weight
            core_hits += 1
            core_title_hits += 1
            matched.append(term)
        elif contains(a_low, term):
            score += 1.0 * weight
            core_hits += 1
            matched.append(term)
        elif contains(v_low, term):
            score += 0.25 * weight
    for term in support:
        if contains(t_low, term):
            score += 0.6
            support_title_hits += 1
        elif contains(a_low, term):
            score += 0.2
    for term in neg:
        if contains(t_low, term):
            score -= 4.0
        elif contains(a_low, term):
            score -= 0.8
    if citations:
        score += 0.35 * math.log10(1 + citations)
    if year:
        score += 0.2 * min(6, max(0, year - 2012))
    if len(abstract) > 80:
        score += ABSTRACT_BONUS
    if core_hits == 0 and not allow_support_only:
        return 0.0, 0, ""
    if strict and not allow_support_only:
        title_anchored = core_title_hits >= 1 and (core_hits >= 2 or support_title_hits >= 2)
        if not title_anchored:
            return 0.0, 0, ""
    return score, core_hits, ",".join(sorted(set(matched)))


def representative_rank(row) -> Tuple[int, int, int]:
    has_abstract = 1 if len(row["abstract"] or "") > 80 else 0
    is_journal = 1 if row["ptype"] == "journal-article" else 0
    return (has_abstract, is_journal, int(row["citations"] or 0))


def main() -> int:
    parser = argparse.ArgumentParser(description="Score and select the final paper set")
    parser.add_argument("--per-category", type=int, default=400)
    parser.add_argument("--allow-support-only", action="store_true")
    parser.add_argument("--relaxed", action="store_true",
                        help="disable the title-anchored relevance rule")
    args = parser.parse_args()

    conn = get_db()
    conn.row_factory = __import__("sqlite3").Row
    total_papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    print(f"[select] harvested records: {total_papers}")

    work_best: Dict[str, Dict] = {}
    work_members: Dict[str, List[str]] = defaultdict(list)
    per_category_scores: Dict[str, Dict[str, Tuple[float, int, str]]] = {
        category: {} for category in CATEGORIES
    }

    rows = conn.execute(
        """SELECT p.doi, p.source, p.title, p.abstract, p.venue, p.year, p.citations,
                  p.ptype, p.arxiv_id, p.url, p.authors, p.publisher, p.subjects,
                  h.category AS category
           FROM papers p JOIN hits h ON h.doi = p.doi"""
    )
    for row in rows:
        category = row["category"]
        if category not in per_category_scores:
            continue
        if not is_acceptable_type(row["ptype"] or "", row["title"] or ""):
            continue
        score, core_hits, matched = score_record(
            row["title"], row["abstract"] or "", row["venue"] or "", row["year"] or 0,
            int(row["citations"] or 0), category, args.allow_support_only, not args.relaxed)
        if score <= 0:
            continue
        previous = per_category_scores[category].get(row["doi"])
        if previous is None or score > previous[0]:
            per_category_scores[category][row["doi"]] = (score, core_hits, matched)
        key = normalize_title(row["title"])
        work_members[key].append(row["doi"])
        current = work_best.get(key)
        if current is None:
            work_best[key] = {
                "doi": row["doi"], "score": score, "core_hits": core_hits, "matched": matched,
                "category": category, "title": row["title"], "abstract": row["abstract"] or "",
                "venue": row["venue"] or "", "year": row["year"], "citations": int(row["citations"] or 0),
                "source": row["source"], "arxiv_id": row["arxiv_id"] or "", "url": row["url"],
                "authors": loads(row["authors"], []) or [], "publisher": row["publisher"] or "",
                "ptype": row["ptype"] or "", "subjects": loads(row["subjects"], []) or [],
            }
        else:
            work_members[key].append(row["doi"])
            if score > current["score"]:
                current["score"] = score
                current["core_hits"] = core_hits
                current["matched"] = matched
                current["category"] = category
            if len(row["abstract"] or "") > len(current["abstract"]):
                current["abstract"] = row["abstract"]
            if int(row["citations"] or 0) > current["citations"]:
                current["citations"] = int(row["citations"] or 0)
            if row["ptype"] == "journal-article" and current["ptype"] != "journal-article":
                current["ptype"] = row["ptype"]
                current["venue"] = row["venue"] or current["venue"]
                current["year"] = row["year"] or current["year"]
                current["source"] = row["source"]
                current["doi"] = row["doi"]
                current["url"] = row["url"]
                current["authors"] = loads(row["authors"], []) or current["authors"]
                current["arxiv_id"] = row["arxiv_id"] or current["arxiv_id"]

    print(f"[select] unique works after title merge: {len(work_best)}")

    abstract_by_title: Dict[str, str] = {}
    for row in conn.execute(
        "SELECT title, abstract FROM papers WHERE length(abstract) > 80 ORDER BY length(abstract) DESC"
    ):
        key = normalize_title(row["title"])
        if key and key not in abstract_by_title:
            abstract_by_title[key] = row["abstract"]
    print(f"[select] abstract source index: {len(abstract_by_title)} titles")
    backfilled = 0
    for work in work_best.values():
        if len(work["abstract"] or "") <= 80:
            candidate = abstract_by_title.get(normalize_title(work["title"]))
            if candidate:
                work["abstract"] = candidate
                work["abstract_origin"] = "sibling"
                backfilled += 1
        else:
            work["abstract_origin"] = "own"
    print(f"[select] abstracts backfilled from sibling records: {backfilled}")
    for work in work_best.values():
        if work.get("abstract_origin") == "sibling":
            conn.execute(
                "UPDATE papers SET abstract = ?, abstract_origin = 'sibling' WHERE doi = ?",
                (work["abstract"], work["doi"]))
    conn.commit()

    assigned: Dict[str, List[Dict]] = {category: [] for category in CATEGORIES}
    pool: List[Tuple[float, str, str]] = []
    for key, work in work_best.items():
        if len(work["title"]) < 20:
            continue
        if not work["year"] or work["year"] < 1950 or work["year"] > CURRENT_YEAR:
            continue
        pool.append((work["score"], key, work["category"]))
    pool.sort(key=lambda item: (-item[0], item[1]))

    remaining = {category: args.per_category for category in CATEGORIES}
    for score, key, category in pool:
        if remaining.get(category, 0) <= 0:
            continue
        work = work_best[key]
        assigned[category].append(work)
        remaining[category] -= 1

    for category, works in assigned.items():
        works.sort(key=lambda w: -w["score"])
        works = works[: args.per_category]

    conn.execute("DELETE FROM selected")
    conn.execute("DELETE FROM scores")
    summary = {}
    for category, works in assigned.items():
        kept = works[: args.per_category]
        summary[category] = len(kept)
        for rank, work in enumerate(kept, start=1):
            conn.execute(
                "INSERT OR REPLACE INTO selected (category, rank, doi, score) VALUES (?,?,?,?)",
                (category, rank, work["doi"], work["score"]))
            conn.execute(
                "INSERT OR REPLACE INTO scores (doi, score, detail, core_hits) VALUES (?,?,?,?)",
                (work["doi"], work["score"], work["matched"], work["core_hits"]))
        with_abstract = sum(1 for w in kept if len(w["abstract"] or "") > 80)
        print(f"[select] {category}: {len(kept)} selected "
              f"({with_abstract} with abstract, {len(kept) - with_abstract} title-derived)")
    conn.commit()
    conn.close()
    print(f"[select] total selected: {sum(summary.values())}")
    short = {c: args.per_category - n for c, n in summary.items() if n < args.per_category}
    if short:
        print(f"[select] categories below target: {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
