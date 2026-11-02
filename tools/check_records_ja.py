"""Inspect and maintain the Japanese text of the record-airframe domain.

The domain module (``physics/records_domain.py``) used to carry Japanese
strings that had been mangled by a UTF-8 -> CP932 byte-level round trip.  The
module itself has been repaired; the tooling and the diagnostic dumps around
it still quoted the corrupted text.  This module is the single place that
knows how the Japanese text of the domain is inspected, so neither the
validator nor the dumps can reintroduce the corruption.

Two jobs:

*   Collect every Japanese-bearing field of the domain - ``name_ja``,
    ``holder_ja``, ``aircraft_name_ja``, ``technique_ja``, ``label_ja``,
    ``statement_ja``, ``notes_ja``, ``message_ja`` and the ``title`` of a
    source note - and check it with the detector in
    ``tools/check_mojibake.py``.
*   Regenerate the four ``scratch_*`` inspection dumps that describe the
    Japanese text of the domain.  They are diagnostic artefacts of the repair
    work; they used to quote the *corrupted* strings and are rewritten here so
    that they describe the repaired module.

Usage::

    python tools/check_records_ja.py                  # report on the domain
    python tools/check_records_ja.py --json           # machine readable report
    python tools/check_records_ja.py --write-scratch  # regenerate the dumps
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from typing import Any, Iterable

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_mojibake import is_cjk, line_findings  # noqa: E402

#: The module the Japanese text belongs to.
SOURCE_MODULE = "physics/records_domain.py"

#: How the inspection dumps are regenerated; quoted in the dumps themselves.
GENERATOR_COMMAND = "python tools/check_records_ja.py --write-scratch"

#: Key suffixes whose value is Japanese text.  ``_ja`` is the convention of
#: the domain; ``title`` names the headline of a source note, which is
#: Japanese or Chinese.
JAPANESE_KEY_SUFFIXES = ("_ja",)
JAPANESE_KEYS = frozenset({"title"})

#: Module level containers that hold the Japanese text of the domain.
DOMAIN_ROOTS = (
    "RECORD_METRICS",
    "DESIGN_CLASSES",
    "THROW_RULES",
    "SOURCE_NOTES",
    "CITATIONS",
    "COMPETITION_RULES",
    "RECORD_PRESET_CATALOGUE",
)

#: The inspection dumps, and the renderer that produces each of them.
SCRATCH_DUMPS = (
    "scratch_all_chunks.txt",
    "scratch_fffd.json",
    "scratch_suspicious.txt",
    "scratch_preset_dump.txt",
)

#: How much surrounding source a dumped chunk keeps around a Japanese region.
CHUNK_CONTEXT = 6

#: Rule of the preset dump: one record per block, closed by a separator.
PRESET_SEPARATOR = "=" * 60


@dataclass(frozen=True)
class JapaneseField:
    """One Japanese string of the domain and the path it was found at."""

    location: str
    key: str
    text: str


@dataclass(frozen=True)
class FieldFinding:
    """A mojibake marker found inside a Japanese field."""

    location: str
    key: str
    reason: str
    text: str


# ---------------------------------------------------------------------------
# Collecting the Japanese fields
# ---------------------------------------------------------------------------
def _is_japanese_key(key: str) -> bool:
    return key.endswith(JAPANESE_KEY_SUFFIXES) or key in JAPANESE_KEYS


def _collect(value: Any, location: str, out: list[JapaneseField]) -> None:
    """Walk ``value`` and collect every Japanese string it holds."""
    if isinstance(value, str):
        key = location.rsplit(".", 1)[-1]
        if _is_japanese_key(key):
            out.append(JapaneseField(location, key, value))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _collect(item, f"{location}.{key}", out)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _collect(item, f"{location}[{index}]", out)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for spec in dataclass_fields(value):
            _collect(getattr(value, spec.name), f"{location}.{spec.name}", out)


def _limitation_fields(module: Any) -> list[JapaneseField]:
    """The ``message_ja`` strings of the solver limitations.

    The limitations are only produced while a flight is being simulated, so
    they are not reachable from a module level container.  Calling the
    helper directly with a flight that trips every branch is the cheapest way
    to cover them.
    """
    builder = getattr(module, "_limitations", None)
    if builder is None:
        return []
    out: list[JapaneseField] = []
    limitations = builder(
        pull=7.0,
        metrics={"stalled": True, "status": "crashed"},
        calibrated=False,
    )
    for index, item in enumerate(limitations):
        text = str(item.get("message_ja", ""))
        if text:
            out.append(JapaneseField(f"_limitations[{index}].message_ja", "message_ja", text))
    return out


def collect_japanese_fields(module: Any) -> list[JapaneseField]:
    """Every Japanese-bearing field of the record-airframe domain."""
    out: list[JapaneseField] = []
    for name in DOMAIN_ROOTS:
        container = getattr(module, name, None)
        if container is None:
            continue
        _collect(container, name, out)
    out.extend(_limitation_fields(module))
    return out


# ---------------------------------------------------------------------------
# Checking the Japanese fields
# ---------------------------------------------------------------------------
def _marker(reason: str) -> str:
    """``"line 12: U+FFFD"`` -> ``"U+FFFD"``."""
    head, sep, marker = reason.partition(": ")
    if sep and head.startswith("line "):
        return marker
    return reason


def scan_fields(fields: Iterable[JapaneseField]) -> list[FieldFinding]:
    """Check every Japanese field with the repository mojibake detector."""
    findings: list[FieldFinding] = []
    for item in fields:
        for reason, text in line_findings(item.text):
            findings.append(FieldFinding(item.location, item.key, _marker(reason), text))
    return findings


def format_report(fields: list[JapaneseField], findings: list[FieldFinding]) -> str:
    """A human readable report of the Japanese text of the domain."""
    lines = [
        "records domain Japanese check",
        f"  module: {SOURCE_MODULE}",
        f"  fields inspected: {len(fields)}",
        f"  mojibake findings: {len(findings)}",
    ]
    for finding in findings:
        lines.append(f"  {finding.location}: {finding.reason}")
        lines.append(f"      {finding.text}")
    if findings:
        lines.append("FAIL: mojibake found in the Japanese text of the records domain")
    else:
        lines.append("OK: every Japanese field of the records domain is clean")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The inspection dumps
# ---------------------------------------------------------------------------
def _flagged_lines(source: str) -> set[int]:
    """The 1-based line numbers of the source that carry a mojibake marker."""
    flagged: set[int] = set()
    for reason, _text in line_findings(source):
        head, _, _marker_text = reason.partition(": ")
        parts = head.split()
        if len(parts) == 2 and parts[0] == "line" and parts[1].isdigit():
            flagged.add(int(parts[1]))
    return flagged


def _chunk_windows(lines: list[str], context: int = CHUNK_CONTEXT) -> list[tuple[int, int]]:
    """Windows of the source around every region of Japanese text.

    A region is a cluster of lines that carry Japanese; two lines belong to
    the same cluster when they are no more than ``2 * context`` lines apart,
    so a neighbourhood of Japanese text is dumped once.  The window adds
    ``context`` lines of surrounding source so the dump can be read in place.
    """
    targets = [
        index for index, line in enumerate(lines) if any(is_cjk(ch) for ch in line)
    ]
    regions: list[tuple[int, int]] = []
    for index in targets:
        if regions and index - regions[-1][1] <= 2 * context:
            regions[-1] = (regions[-1][0], index)
        else:
            regions.append((index, index))
    return [
        (max(0, first - context), min(len(lines), last + context + 1))
        for first, last in regions
    ]


def render_chunks(source: str) -> str:
    """The source windows that carry the Japanese text of the domain."""
    lines = source.splitlines()
    flagged = _flagged_lines(source)
    out: list[str] = [
        f"# source: {SOURCE_MODULE}",
        f"# generator: {GENERATOR_COMMAND}",
        "# a line that carries a mojibake marker is prefixed with '>>>'",
    ]
    for number, (start, end) in enumerate(_chunk_windows(lines), 1):
        out.append(f"=== CHUNK {number}: lines {start + 1} to {end} ===")
        for index in range(start, end):
            lineno = index + 1
            prefix = f">>>{lineno:>5}: " if lineno in flagged else f"{lineno:>8}: "
            out.append(prefix + lines[index])
        out.append("")
    out.append("")
    return "\n".join(out) + "\n"


def render_fffd_json(source: str) -> str:
    """The lines of the domain that still contain a U+FFFD replacement."""
    entries = []
    for number, line in enumerate(source.splitlines(), 1):
        if "\ufffd" in line:
            entries.append({"line_num": number, "line": line + "\n"})
    payload = {
        "source": SOURCE_MODULE,
        "generator": GENERATOR_COMMAND,
        "status": "no issues" if not entries else "mojibake found",
        "count": len(entries),
        "lines": entries,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_suspicious(source: str) -> str:
    """Every line of the domain that the detector considers suspicious."""
    out: list[str] = [
        f"# source: {SOURCE_MODULE}",
        f"# generator: {GENERATOR_COMMAND}",
    ]
    findings = line_findings(source)
    if not findings:
        out.append(
            f"no issues | clean | {SOURCE_MODULE}: 0 suspicious lines "
            "(no U+FFFD, no mangled 'E', no artifact codepoints)"
        )
    for reason, text in findings:
        head, _, _marker_text = reason.partition(": ")
        parts = head.split()
        number = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
        has_fffd = "\ufffd" in text
        out.append(f"{number:>4} | has_fffd={has_fffd} | {text}")
    return "\n".join(out) + "\n"


def render_preset_dump(module: Any) -> str:
    """The Japanese and English text of every record preset."""
    out: list[str] = []
    for preset in module.RECORD_PRESET_CATALOGUE:
        achievement = preset.achievement
        out.append(f"KEY: {preset.key}")
        out.append(f"  name: {preset.name}")
        out.append(f"  name_ja: {preset.name_ja}")
        out.append(f"  holder: {achievement.holder}")
        out.append(f"  holder_ja: {achievement.holder_ja}")
        out.append(f"  aircraft_name: {achievement.aircraft_name}")
        out.append(f"  aircraft_name_ja: {achievement.aircraft_name_ja}")
        out.append(f"  technique: {preset.throw.technique}")
        out.append(f"  technique_ja: {preset.throw.technique_ja}")
        out.append(f"  achievement.notes: {achievement.notes}")
        out.append(f"  achievement.notes_ja: {achievement.notes_ja}")
        out.append(f"  entry.notes: {preset.notes}")
        out.append(f"  entry.notes_ja: {preset.notes_ja}")
        out.append("")
        out.append(PRESET_SEPARATOR)
        out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Writing the inspection dumps
# ---------------------------------------------------------------------------
def _write(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def write_scratch_dumps(root: pathlib.Path, module: Any) -> list[pathlib.Path]:
    """Regenerate the four inspection dumps from the current domain module."""
    source = (root / SOURCE_MODULE).read_text(encoding="utf-8")
    rendered = {
        "scratch_all_chunks.txt": render_chunks(source),
        "scratch_fffd.json": render_fffd_json(source),
        "scratch_suspicious.txt": render_suspicious(source),
        "scratch_preset_dump.txt": render_preset_dump(module),
    }
    written: list[pathlib.Path] = []
    for name in SCRATCH_DUMPS:
        target = root / name
        _write(target, rendered[name])
        written.append(target)
    return written


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def _load_domain() -> Any:
    from physics import records_domain

    return records_domain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the Japanese text of the record-airframe domain."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine readable report"
    )
    parser.add_argument(
        "--write-scratch",
        action="store_true",
        help="regenerate the scratch_* inspection dumps from the current domain",
    )
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    domain = _load_domain()
    fields = collect_japanese_fields(domain)
    findings = scan_fields(fields)

    if args.write_scratch:
        written = write_scratch_dumps(ROOT, domain)
        for path in written:
            print(f"wrote {path.relative_to(ROOT)}")

    if args.json:
        payload = {
            "module": SOURCE_MODULE,
            "fields_inspected": len(fields),
            "findings": [
                {
                    "location": item.location,
                    "key": item.key,
                    "reason": item.reason,
                    "text": item.text,
                }
                for item in findings
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(fields, findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
