"""Detect mojibake (文字化け) in repository text files.

The corruption present in this repository was produced by mangling UTF-8 byte
sequences through a CP932/Shift-JIS style codec.  The mangling leaves three
reliable traces:

1. ``U+FFFD`` replacement characters (bytes that could not be mapped at all).
2. A literal capital ``E`` dropped between two CJK characters.  The mangling
   emitted the raw byte ``0x45`` (``'E'``) as a stand-in for a byte pair that
   could not be decoded, so correctly written Japanese never contains a Latin
   ``E`` wedged between two ideographs.  (A capital ``G`` in "Gフォース",
   ``V`` in "V字", ``I`` in "慣性テンソルI計算" and a lower case ``n`` in
   "n点凹凸" are legitimate and are deliberately not flagged.)
3. Rare CJK codepoints that only ever appear as the mangled "trail byte" side
   of a CP932 double byte pair, plus stray Hangul syllables.
4. Hangul or Cyrillic fragments injected *into* Japanese prose.  A Korean or
   Russian author name that stands alone as a token (``우병광``, ``Мanapova``)
   is legitimate registry data; the same scripts wedged between two Japanese
   characters are corruption.

Usage::

    python tools/check_mojibake.py            # scan the whole repository
    python tools/check_mojibake.py path ...   # scan specific paths
    python tools/check_mojibake.py --json     # machine readable report

Exit code is 1 when mojibake is found, 0 otherwise.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPLACEMENT = "\ufffd"

#: Codepoints that essentially never occur in legitimate Japanese technical
#: writing; a single occurrence is a definitive mojibake marker.
IMPOSSIBLE = (
    "\u5181"  # mangled ken
    "\u6a41"  # mangled ki
    "\u7401"  # mangled tou
    "\u8f01"  # mangled ten
    "\u8a01"  # mangled gonben
    "\u5bc1"  # mangled sei
    "\u7a41"  # mangled sai
    "\u8ac1"  # mangled sei
    "\u7681"  # mangled sou
    "\u6701"  # mangled mou
    "\u7d81"  # mangled kin
    "\u4e81"  # mangled ichi
    "\u5fc1"  # mangled shitsu
    "\ubcf4"  # hangul syllable da
    "\u91c1"  # mangled kou
    "\u8d81"  # mangled chin
    "\u8d34"  # mangled chi-tie
    "\u5e01"  # mangled hei
    "\u6581"  # mangled kou
)
#: Codepoints that are rare in this repository but still plausible; two or
#: more on the same line are treated as a mojibake marker.
UNUSUAL = (
    "\u3041"  # small hiragana a
    "\u5e04"  # ou
    "\u5b81"  # nei
    "\u6241"  # hen
    "\u4e3a"  # ?
    "\u4e01"  # chou
    "\u5141"  # kyou
)
IMPOSSIBLE_SET = set(IMPOSSIBLE)
UNUSUAL_SET = set(UNUSUAL)

SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "site-packages", ".idea", ".vscode",
    ".kilo", "artifacts", "runs", "logs",
})
SKIP_SUFFIX = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico", ".woff", ".woff2",
    ".ttf", ".mp4", ".mp3", ".wasm", ".pyc", ".so", ".dll", ".pyd", ".npy",
    ".npz", ".ply", ".stl", ".obj", ".glb", ".gltf", ".db", ".sqlite",
})


def is_cjk(ch: str) -> bool:
    return (
        "\u3040" <= ch <= "\u30ff"
        or "\u3400" <= ch <= "\u9fff"
        or "\uff66" <= ch <= "\uff9f"
    )


def is_cjk_context(ch: str) -> bool:
    """Japanese text around a foreign fragment (kanji, kana or CJK punctuation)."""
    return (
        "\u3000" <= ch <= "\u30ff"
        or "\u3400" <= ch <= "\u9fff"
        or "\uff66" <= ch <= "\uff9f"
    )


def foreign_runs(text: str) -> list[tuple[str, int, int]]:
    """Return ``(script, start, end)`` for every Hangul/Cyrillic run."""
    runs: list[tuple[str, int, int]] = []
    start = -1
    script = ""
    for i, ch in enumerate(text):
        kind = ""
        code = ord(ch)
        if (0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF
                or 0x3130 <= code <= 0x318F):
            kind = "HANGUL"
        elif 0x0400 <= code <= 0x052F:
            kind = "CYRILLIC"
        if kind != script:
            if script:
                runs.append((script, start, i))
            script = kind
            start = i
    if script:
        runs.append((script, start, len(text)))
    return runs


#: Tokens that mark a line as author / publisher metadata, where a Korean or
#: Russian personal name or journal title is legitimate registry data.
NAME_CONTEXT = ("著者", "authors", "媒体", "publisher")


def has_foreign_fragment(text: str) -> str | None:
    """Return the offending fragment when a foreign script is wedged in Japanese.

    A Korean or Russian author name that stands alone as a token
    (``우병광``, ``Глумова``) is legitimate registry data.  The same scripts
    glued to a Japanese character, or dropped into a Japanese sentence, are
    corruption.
    """
    if not any(is_cjk(ch) for ch in text):
        return None
    runs = foreign_runs(text)
    if not runs:
        return None
    name_context = any(marker in text for marker in NAME_CONTEXT)
    has_kana = any("\u3041" <= ch <= "\u30ff" for ch in text)
    for script, start, end in runs:
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if is_cjk_context(before) or is_cjk_context(after):
            return f"{script}:{text[start:end]}"
        if has_kana and not name_context and end - start >= 2:
            return f"{script}:{text[start:end]}"
    return None


def line_findings(text: str) -> list[tuple[str, str]]:
    """Return ``(reason, offending_line)`` for every suspicious line."""
    findings: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if REPLACEMENT in line:
            findings.append((f"line {lineno}: U+FFFD", line.strip()[:160]))
            continue
        mangled = any(
            line[i] == "E" and is_cjk(line[i - 1]) and is_cjk(line[i + 1])
            for i in range(1, len(line) - 1)
        )
        if mangled:
            findings.append((f"line {lineno}: mangled 'E'", line.strip()[:160]))
            continue
        impossible = sorted(set(line) & IMPOSSIBLE_SET)
        if impossible:
            findings.append(
                (f"line {lineno}: artifact {''.join(impossible)}", line.strip()[:160])
            )
            continue
        unusual = sorted(set(line) & UNUSUAL_SET)
        if len(unusual) >= 2:
            findings.append(
                (f"line {lineno}: artifact {''.join(unusual)}", line.strip()[:160])
            )
            continue
        foreign = has_foreign_fragment(line)
        if foreign:
            findings.append(
                (f"line {lineno}: foreign fragment {foreign}", line.strip()[:160])
            )
    return findings


def iter_text_files(root: pathlib.Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        yield path


def scan(targets: list[pathlib.Path]) -> dict[str, list[tuple[str, str]]]:
    report: dict[str, list[tuple[str, str]]] = {}
    for target in targets:
        paths = [target] if target.is_file() else iter_text_files(target)
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            findings = line_findings(text)
            if findings:
                try:
                    key = str(path.relative_to(pathlib.Path.cwd()))
                except ValueError:
                    key = str(path)
                report[key] = findings
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect mojibake in text files.")
    parser.add_argument("paths", nargs="*", default=["."],
                        help="files or directories to scan (default: repository root)")
    parser.add_argument("--json", action="store_true",
                        help="emit a JSON report instead of text")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    targets = [pathlib.Path(p) for p in (args.paths or ["."])]
    report = scan(targets)

    if args.json:
        payload = {
            path: [{"reason": r, "text": t} for r, t in items]
            for path, items in report.items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        total = 0
        for path, items in report.items():
            print(f"{path}: {len(items)} suspicious lines")
            for reason, text in items:
                print(f"    {reason}: {text}")
            total += len(items)
        if report:
            print(f"\nTOTAL: {total} suspicious lines in {len(report)} files")
        else:
            print("OK: no mojibake detected")
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
