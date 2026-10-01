"""Pytest bootstrap.

Ensures the project root is importable so that ``import physics`` resolves for
every test module, regardless of the working directory pytest is invoked from.
Parallel test workers share this file; do not add per-domain fixtures here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep BLAS single-threaded: the parallel domains manage their own pools and
# nested threading makes the ETA measurements in test_jobs.py non-deterministic.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
