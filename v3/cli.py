"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.cli`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optimization.cli import *  # noqa: F401,F403

if __name__ == "__main__":
    from optimization.cli import main
    main()
