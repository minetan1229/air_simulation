#!/usr/bin/env python3
"""Shared infrastructure for the verified paper collection pipeline.

Provides a retrying HTTP session, a SQLite-backed streaming store for candidate
papers, and text utilities used by the scoring, translation and report stages.
All stage scripts are designed to run with bounded memory: candidates are
streamed into SQLite and never held in a single in-memory collection.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / ".cache"
DB_PATH = CACHE_DIR / "paper_collection.db"

_THREAD_LOCAL = threading.local()

USER_AGENT = "paper-airplane-lit-review/1.0 (mailto:research@example.com)"
MAILTO = "research@example.com"

CATEGORIES: Tuple[str, ...] = (
    "01_aerodynamics_cfd",
    "02_mechanics_multiphysics",
    "03_numerical_solvers",
    "04_collision_geometry",
    "05_gpu_realtime_sim",
)

JATS_TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
ABSTRACT_HEADER_RE = re.compile(
    r"^\s*(abstract|summary|摘 要|要旨|abstract\s*/\s*摘要)\s*[:.\-–]?\s*", re.IGNORECASE
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def request_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    method: str = "GET",
    json_body: Optional[Dict[str, Any]] = None,
    tries: int = 5,
    timeout: int = 60,
    backoff: float = 2.0,
    sleep_between: float = 0.0,
) -> Optional[Any]:
    """Perform an HTTP request with bounded retries; return decoded JSON or None."""
    last_error = ""
    for attempt in range(tries):
        if sleep_between:
            time.sleep(sleep_between)
        try:
            response = session.request(
                method, url, params=params, json=json_body, timeout=timeout
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
            elif response.status_code == 404:
                return None
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:120]}"
        except Exception as exc:  # noqa: BLE001 - network faults are retried
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(backoff * (attempt + 1))
    print(f"    [warn] request failed ({last_error}) for {url}")
    return None


def get_db() -> sqlite3.Connection:
    """Return a per-thread SQLite connection to the collection cache."""
    conn = getattr(_THREAD_LOCAL, "conn", None)
    if conn is not None:
        return conn
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS papers (
            doi TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            authors TEXT,
            year INTEGER,
            venue TEXT,
            publisher TEXT,
            ptype TEXT,
            abstract TEXT,
            url TEXT,
            arxiv_id TEXT,
            citations INTEGER,
            subjects TEXT
        );
        CREATE TABLE IF NOT EXISTS hits (
            category TEXT,
            doi TEXT,
            query TEXT,
            origin TEXT,
            PRIMARY KEY (category, doi)
        );
        CREATE TABLE IF NOT EXISTS scores (
            doi TEXT PRIMARY KEY,
            score REAL,
            detail TEXT,
            core_hits INTEGER
        );
        CREATE TABLE IF NOT EXISTS selected (
            category TEXT,
            rank INTEGER,
            doi TEXT,
            score REAL,
            PRIMARY KEY (category, rank)
        );
        CREATE TABLE IF NOT EXISTS translations (
            doi TEXT PRIMARY KEY,
            title_ja TEXT,
            engine TEXT
        );
        CREATE TABLE IF NOT EXISTS verified (
            doi TEXT PRIMARY KEY,
            status TEXT,
            title_match INTEGER,
            checked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_hits_doi ON hits (doi);
        CREATE INDEX IF NOT EXISTS idx_papers_source ON papers (source);
        """
    )
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "abstract_origin" not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN abstract_origin TEXT DEFAULT 'own'")
        conn.commit()
    _THREAD_LOCAL.conn = conn
    return conn


def clean_abstract(raw: Optional[str]) -> str:
    """Convert a JATS/plain abstract into compact single-spaced text."""
    if not raw:
        return ""
    text = JATS_TAG_RE.sub(" ", raw)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#x000a0;", " ").replace("&nbsp;", " ")
    text = WS_RE.sub(" ", text).strip()
    text = ABSTRACT_HEADER_RE.sub("", text)
    return text


def split_sentences(text: str) -> List[str]:
    if not text:
        return []
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if len(s.strip()) > 25]


def short_term(term: str) -> bool:
    """Short alphanumeric tokens need word-boundary matching to avoid false hits."""
    return bool(re.fullmatch(r"[A-Za-z0-9+.\-]{1,6}", term))


def contains(text: str, term: str) -> bool:
    if not text:
        return False
    if short_term(term):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])", text) is not None
    return term.lower() in text


def term_hits(text: str, terms: Iterable[str]) -> List[str]:
    return [t for t in terms if contains(text, t)]


def log_scale(value: int) -> float:
    return math.log10(1 + max(0, value))


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def iter_rows(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> Iterator[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(sql, params)
    while True:
        rows = cursor.fetchmany(200)
        if not rows:
            break
        for row in rows:
            yield row
    conn.row_factory = None
