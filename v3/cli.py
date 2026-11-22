"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.cli`.
"""
from optimization.cli import *  # noqa: F401,F403

if __name__ == "__main__":
    from optimization.cli import main
    main()
