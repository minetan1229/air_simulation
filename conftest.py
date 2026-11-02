"""Pytest bootstrap.

Ensures the project root is importable so that ``import physics`` resolves for
every test module, regardless of the working directory pytest is invoked from.
Parallel test workers share this file; do not add per-domain fixtures here.

It also installs the **branch guard** session fixture.  The genetic-project
development phase is isolated from ``master``: running the suite on a
protected branch is an error, running it on any other non-protected branch is
a warning.  The guard is disabled with ``PAPER_AIRCRAFT_BRANCH_GUARD=off`` and
downgraded to a warning with ``PAPER_AIRCRAFT_BRANCH_GUARD=warn`` (used by CI,
where the checkout is a detached HEAD or a ``pull/<n>/merge`` ref).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep BLAS single-threaded: the parallel domains manage their own pools and
# nested threading makes the ETA measurements in test_jobs.py non-deterministic.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")


@pytest.fixture(scope="session", autouse=True)
def branch_guard_session_check() -> None:
    """Refuse to run the suite on a protected branch.

    ``master`` / ``main`` are hard forbidden during the ``genetic-project``
    phase.  Any other branch is accepted with a warning, so a pull-request
    checkout still runs the suite.  A checkout that the CI runner made for
    its own event is not judged from its branch name at all: the branch of a
    push checkout is created by ``actions/checkout``, not by the author.
    See ``tools/branch_guard.py``.
    """
    from tools.branch_guard import enforce_branch_policy

    enforce_branch_policy()

