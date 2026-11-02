"""Regression guard: the repository must stay free of mojibake (文字化け).

A UTF-8 -> CP932 byte-level round trip once mangled the Japanese text of
``physics/records_domain.py``.  The module has been repaired, but the damage
is invisible in a diff and easy to reintroduce: a single copy/paste through
the wrong codec turns readable Japanese into noise again.  This module is the
mechanical guard against that.

The detection rules are not re-implemented here.  They are imported from
``tools/check_mojibake.py``, the same detector the maintainers run by hand, so
the guard and the command line tool can never disagree about what counts as
mojibake.

What is scanned
---------------
Every text file of the repository - source, documentation, configuration,
templates, static assets and the search data - except the binary formats the
detector skips.  The scan is a plain directory walk that prunes the
directories the detector ignores, so it costs a couple of seconds and needs
no network access.

Allow lists
-----------
``tools/check_mojibake.py`` names the artifact codepoints it looks for, and
this module has to name the ones it tolerates in the files that quote them
itself; both are written as escaped literals and are skipped by the scan.
The repair is complete, so no file of the repository is exempt any more.
"""

from __future__ import annotations

import os
import pathlib
import sys
import warnings
from dataclasses import dataclass
from typing import Iterator

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from tools.check_mojibake import (  # noqa: E402
    SKIP_DIRS,
    SKIP_SUFFIX,
    line_findings,
)

#: Files that legitimately carry the artifact codepoints as escaped literals:
#: the detector names the codepoints it hunts for, and this module names the
#: ones it tolerates in the files that quote them itself - the detector, this
#: module and the two tools that inspect the Japanese text of the record
#: domain.  The repair is complete, so these entries exist for the tooling
#: and not for text that is still damaged.
SELF_REFERENCE = frozenset({
    "tools/check_mojibake.py",
    "tests/test_no_mojibake.py",
    "tools/check_records_ja.py",
    "tools/build_fixed_records_domain.py",
})

#: Mojibake that is owned by somebody else in this worktree, keyed by the
#: file it lives in.  The value lists the artifact codepoints that are
#: excused *in that file only*, so a new defect with a different marker is
#: still reported.  The exemption lapses by itself as soon as the owning
#: agent repairs the line: the finding disappears and nothing is excused any
#: more.
#:
#: The repair is complete - every Japanese file of the repository is clean -
#: so the map is empty and every finding is reported.  The mechanism is kept
#: because a future repair of a different file will need it again, and
#: because an entry that is no longer needed must not be able to hide a
#: defect: ``test_the_pending_exemptions_are_reported`` fails when the map
#: is empty but a finding is still excused, and
#: ``test_no_mojibake_in_the_repository`` fails when a finding is excused
#: without being declared.
PENDING_EXEMPTIONS: dict[str, tuple[str, ...]] = {}


@dataclass(frozen=True)
class Finding:
    """One mojibake marker found in a repository file."""

    path: str
    line: int
    reason: str
    text: str


def _iter_text_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    """Every text file of the repository the detector is able to read.

    ``tools.check_mojibake.iter_text_files`` walks the same tree but does not
    prune the directories it skips, which costs several seconds on this
    checkout; pruning here keeps the guard fast.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            path = pathlib.Path(dirpath) / name
            if path.suffix.lower() in SKIP_SUFFIX:
                continue
            yield path


def _split_reason(reason: str) -> tuple[int, str]:
    """``"line 12: U+FFFD"`` -> ``(12, "U+FFFD")``."""
    head, sep, marker = reason.partition(": ")
    if sep:
        parts = head.split()
        if len(parts) == 2 and parts[0] == "line" and parts[1].isdigit():
            return int(parts[1]), marker
    return 0, reason


def _marker_codepoints(marker: str) -> tuple[str, ...]:
    """The artifact codepoints a finding marker names."""
    head, sep, tail = marker.partition(" ")
    if sep and head == "artifact":
        return tuple(tail)
    if marker == "U+FFFD":
        return ("\ufffd",)
    return ()


def _is_exempt(path: str, marker: str) -> bool:
    """Whether a finding is one of the known, separately owned defects."""
    allowed = PENDING_EXEMPTIONS.get(path)
    if not allowed:
        return False
    codepoints = _marker_codepoints(marker)
    return bool(codepoints) and all(code in allowed for code in codepoints)


def _collect_findings(root: pathlib.Path) -> list[Finding]:
    """Every mojibake marker of the repository, exempted ones included."""
    findings: list[Finding] = []
    for path in _iter_text_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in SELF_REFERENCE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for reason, snippet in line_findings(text):
            lineno, marker = _split_reason(reason)
            findings.append(Finding(relative, lineno, marker, snippet))
    return findings


def _scan_repository(root: pathlib.Path) -> list[Finding]:
    """Every mojibake marker in the repository, except the exempted ones."""
    return [
        item
        for item in _collect_findings(root)
        if not _is_exempt(item.path, item.reason)
    ]


def _format_findings(findings: list[Finding]) -> str:
    """The failure report: file, line and the offending text."""
    if not findings:
        return "  (none)"
    return "\n".join(
        f"  {item.path}:{item.line}: {item.reason}\n      {item.text}"
        for item in findings
    )


def test_no_mojibake_in_the_repository() -> None:
    """No file of the repository may carry a mojibake marker."""
    findings = _scan_repository(ROOT)
    assert not findings, (
        "mojibake found in the repository:\n"
        f"{_format_findings(findings)}\n"
        "Run `python tools/check_mojibake.py .` for the same report."
    )


def test_the_guard_detects_reintroduced_mojibake() -> None:
    """The guard must actually catch mojibake, not merely never fire."""
    assert line_findings('paper_ja = "\ufffd\ufffd\ufffd"\n'), (
        "a U+FFFD replacement must be reported"
    )
    assert line_findings('label_ja = "\u7a41\u91c1\u7a41"\n'), (
        "an artifact codepoint must be reported"
    )
    assert line_findings('label_ja = "\u7d19E\u98db"\n'), (
        "a latin 'E' wedged between two ideographs must be reported"
    )
    assert not line_findings('label_ja = "\u7d19\u98db\u884c\u6a5f"\n'), (
        "well formed Japanese must not be reported"
    )
    # A Hangul run wedged between two Japanese characters is corruption: the
    # foreign script is glued into the Japanese prose.
    assert line_findings('label_ja = "\u7a7a\uc6c0\ubc29\uad11\u8a18\u9332"\n'), (
        "a Hangul run glued between two Japanese characters must be reported"
    )
    # The same Korean name standing alone in an author metadata line is
    # legitimate registry data and must not be reported.
    assert not line_findings('authors = "\u8457\u8005: \uc6c0\ubc29\uad11"\n'), (
        "a standalone Korean author name must not be reported"
    )


def test_the_allow_listed_files_exist() -> None:
    """A stale allow-list entry would silently stop guarding a file."""
    for relative in sorted(SELF_REFERENCE | set(PENDING_EXEMPTIONS)):
        assert (ROOT / relative).is_file(), f"{relative} does not exist any more"


def test_the_pending_exemptions_are_reported() -> None:
    """Exemptions still in use are surfaced, so they cannot be forgotten."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exempted = [
            item
            for item in _collect_findings(ROOT)
            if _is_exempt(item.path, item.reason)
        ]
    if not PENDING_EXEMPTIONS:
        # Nothing is exempt any more: no finding may be excused and no
        # warning may be raised, otherwise the guard would be silently
        # narrowing its own coverage.
        assert not exempted, (
            "the exemption map is empty, so no finding may be excused"
        )
        assert not caught, (
            "the exemption map is empty, so no exemption warning may be raised"
        )
        return
    if exempted:
        paths = ", ".join(sorted({item.path for item in exempted}))
        warnings.warn(
            f"pending mojibake exemptions are still in use: {paths}",
            UserWarning,
            stacklevel=2,
        )
    assert all(item.path in PENDING_EXEMPTIONS for item in exempted)
