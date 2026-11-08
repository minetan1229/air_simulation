"""Validate the Japanese text of the record-airframe domain.

The first revision of this tool rewrote ``physics/records_domain.py`` in
place, undoing a UTF-8 -> CP932 mangling of the Japanese strings of the
domain.  That repair is done and superseded by
``tools/build_fixed_records_domain_v2.py``; the corrupted payloads this file
used to carry are gone for good.

The tool now *validates* the domain instead of rewriting it, so running it can
never reintroduce the corruption it was written to undo.  Every
Japanese-bearing field of the domain - ``name_ja``, ``holder_ja``,
``aircraft_name_ja``, ``technique_ja``, ``label_ja``, ``statement_ja``,
``notes_ja``, ``message_ja`` and the ``title`` of a source note - is checked
with the detector in ``tools/check_mojibake.py``, the same rules the
repository wide guard ``tests/test_no_mojibake.py`` applies.

Usage::

    python tools/build_fixed_records_domain.py          # human readable report
    python tools/build_fixed_records_domain.py --json   # machine readable report

Exit code is 0 when every Japanese field is clean, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics import records_domain  # noqa: E402
from tools.check_records_ja import (  # noqa: E402
    SOURCE_MODULE,
    collect_japanese_fields,
    format_report,
    scan_fields,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Japanese text of the record-airframe domain."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine readable report"
    )
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    fields = collect_japanese_fields(records_domain)
    findings = scan_fields(fields)

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
