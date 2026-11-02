"""Archival stub: the spent one-shot migration of ``physics/records_domain.py``.

This script used to rewrite ``physics/records_domain.py`` in place with a
hard-coded Japanese payload, undoing a UTF-8 -> CP932 byte-level mangling of
the Japanese strings of the record-airframe domain.  That migration is done
and the payload it carried is retired: the domain now holds Japanese that was
re-authored from the Guinness primary sources.

Running this script must therefore never be able to reintroduce the
corruption, so the rewriting code has been removed entirely - there is no
path in this file that opens the domain for writing.  What is left is a stub
that

*   states plainly that the migration is spent, and
*   delegates the actual inspection to the canonical validator,
    ``tools/build_fixed_records_domain.py``, which reads every
    Japanese-bearing field of the domain and checks it with the repository
    wide detector ``tools/check_mojibake.py``.

The inspection tooling and the ``scratch_*`` dumps of the domain are owned by
``tools/check_records_ja.py``; ``tools/build_fixed_records_domain.py`` is the
validator that reports on them.

Usage::

    python tools/build_fixed_records_domain_v2.py          # human readable report
    python tools/build_fixed_records_domain_v2.py --json   # machine readable report

Exit code is 0 when every Japanese field of the domain is clean, 1 otherwise.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_fixed_records_domain  # noqa: E402

#: The notice printed before the validator report.  It names the file that
#: must never be rewritten and the file that owns the real inspection.
ARCHIVAL_NOTICE = (
    "build_fixed_records_domain_v2.py is an archival stub: the one-shot "
    "rewrite of physics/records_domain.py is spent and has been removed, so "
    "this script can no longer modify the domain. The report below comes "
    "from the validator tools/build_fixed_records_domain.py, which reads "
    "the domain and checks every Japanese field with "
    "tools/check_mojibake.py."
)


def main(argv: list[str] | None = None) -> int:
    """Print the archival notice, then run the canonical validator."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    print(ARCHIVAL_NOTICE)
    return build_fixed_records_domain.main(argv)


if __name__ == "__main__":
    sys.exit(main())
