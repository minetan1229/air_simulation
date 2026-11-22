#!/usr/bin/env python3
"""Convenience root wrapper for scripts/build_docs.py."""
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.build_docs import main

if __name__ == "__main__":
    main()
