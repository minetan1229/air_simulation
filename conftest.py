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
    checkout still runs the suite.  See ``tools/branch_guard.py``.
    """
    from tools.branch_guard import assert_branch_allowed, describe_status, guard_status

    status = guard_status()
    if str(status.get("severity", "none")) == "error":
        from tools.branch_guard import BranchViolation

        raise BranchViolation(
            f"{status.get('message')} -- refusing to run the test suite on a protected branch"
        )
    if str(status.get("severity", "none")) == "warning":
        print(describe_status(status))

