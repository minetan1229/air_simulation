"""Git branch guard for the ``genetic-project`` development phase.

The autonomous-evolution work is carried out on the dedicated
``genetic-project`` branch and is deliberately **isolated from ``master``**:
no commit, merge, cherry-pick or pull request may ever target ``master``
while the genetic-algorithm (GA) research phase is running.  The GA searches
the simulator's design space, so a partially-audited physics engine must not
be reachable from the protected branch.

This module is the single place that knows the rule.  It is used by

* the test suite (``conftest.py`` session fixture and ``tests/test_branch_guard.py``),
* the main execution entry points (``app.py`` and ``smoke_test.py``),
* the CI helpers (``tools/run_ci_tests.py``).

Policy
------
``PROTECTED_BRANCHES``
    ``master`` / ``main`` are **hard forbidden**: any command that would touch
    the protected branch raises :class:`BranchViolation` unless the caller
    explicitly opts out through ``allow_protected=True``.
``EXPECTED_BRANCH``
    ``genetic-project`` is the branch the current development phase must be
    carried out on.  A different (non-protected) branch is a **warning**: the
    process keeps running, but the notice is emitted so the divergence from
    the protocol is visible in the log and in the test report.
``detached HEAD`` / no git repository
    Treated as "unknown": a warning, never an error.  A source tarball has no
    branch, and refusing to run there would be wrong.

The environment variable ``PAPER_AIRCRAFT_BRANCH_GUARD`` can override the
severity for unusual setups:

``off``
    Disable the guard completely (the process neither warns nor fails).
``warn``
    Downgrade every violation -- including the protected-branch error -- to a
    warning.  Used by CI, where the checkout is a detached HEAD or a
    ``pull/<n>/merge`` ref and the protection cannot be enforced by git.
``error``
    (default) Full policy: protected branches fail, other branches warn.
``CI`` (environment)
    A CI runner checks out the *triggering* branch, so the branch name says
    nothing about where the work was done.  The severity is therefore
    downgraded to ``warn`` inside CI unless the variable above says
    otherwise.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

__all__ = [
    "EXPECTED_BRANCH",
    "PROTECTED_BRANCHES",
    "GUARD_ENV_VAR",
    "BranchViolation",
    "current_branch",
    "guard_status",
    "describe_status",
    "assert_branch_allowed",
    "warn_if_wrong_branch",
    "main",
]

#: Branch the current development phase must be carried out on.
EXPECTED_BRANCH = "genetic-project"

#: Branches that must never be written to from this repository.
PROTECTED_BRANCHES: Tuple[str, ...] = ("master", "main")

#: Environment variable that overrides the guard severity.
GUARD_ENV_VAR = "PAPER_AIRCRAFT_BRANCH_GUARD"

_VALID_MODES = ("error", "warn", "off")


class BranchViolation(RuntimeError):
    """Raised when the current branch violates the branch-isolation policy."""


def _repo_root() -> Path:
    """Repository root, i.e. the directory that owns the ``.git`` entry."""
    return Path(__file__).resolve().parent.parent


def _run_git(*args: str, cwd: Optional[Path] = None) -> Optional[str]:
    """Run ``git`` and return its stripped stdout, or ``None`` on any failure."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd or _repo_root()),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def current_branch(repo_root: Optional[Path] = None) -> Optional[str]:
    """Return the checked-out branch name, or ``None`` when it is unknown.

    ``None`` is returned for a detached HEAD, for a directory that is not a git
    repository and when ``git`` is not installed.  Callers must treat that as
    "cannot tell", never as "wrong branch".
    """
    name = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_root)
    if not name:
        return None
    if name == "HEAD":  # detached HEAD
        return None
    return name


def _is_ci() -> bool:
    """True when the process runs inside a CI runner.

    A CI job checks out the *triggering* branch, so the branch name says
    nothing about where the work was done: a ``master`` CI run must not be
    blocked by the guard, because the protection is enforced by the branch
    policy itself, not by the process.  The guard therefore degrades to a
    warning there.
    """
    if str(os.environ.get("GITHUB_ACTIONS", "")).strip().lower() in ("1", "true", "yes"):
        return True
    return str(os.environ.get("CI", "")).strip().lower() in ("1", "true", "yes")


def _mode() -> str:
    """Resolve the effective severity from the environment."""
    raw = str(os.environ.get(GUARD_ENV_VAR, "")).strip().lower()
    if raw in _VALID_MODES:
        return raw
    if raw:
        # An explicit but invalid value falls back to the strict policy.
        return "error"
    if _is_ci():
        return "warn"
    return "error"


def guard_status(
    repo_root: Optional[Path] = None,
    expected_branch: str = EXPECTED_BRANCH,
) -> Dict[str, object]:
    """Evaluate the branch policy and describe the outcome.

    Returns a dictionary with the keys

    ``branch``
        the checked-out branch, or ``None`` when it cannot be determined,
    ``expected_branch``
        the branch the current phase must run on,
    ``mode``
        the effective severity (``error`` / ``warn`` / ``off``),
    ``protected``
        ``True`` when the current branch is one of :data:`PROTECTED_BRANCHES`,
    ``unknown``
        ``True`` when the branch could not be determined,
    ``severity``
        ``"none"``, ``"warning"`` or ``"error"``,
    ``ok``
        ``True`` when the current state needs no action,
    ``message``
        a human readable explanation.
    """
    mode = _mode()
    branch = current_branch(repo_root)
    protected = branch is not None and branch in PROTECTED_BRANCHES

    if mode == "off":
        return {
            "branch": branch,
            "expected_branch": expected_branch,
            "mode": mode,
            "protected": protected,
            "unknown": branch is None,
            "severity": "none",
            "ok": True,
            "message": "branch guard disabled via " + GUARD_ENV_VAR,
        }

    if branch is None:
        return {
            "branch": None,
            "expected_branch": expected_branch,
            "mode": mode,
            "protected": False,
            "unknown": True,
            "severity": "none" if mode == "error" else "none",
            "ok": True,
            "message": "branch could not be determined (detached HEAD or not a git repository)",
        }

    if protected:
        message = (
            f"branch '{branch}' is protected: the {expected_branch!r} development phase "
            f"must never commit, merge, cherry-pick or open a pull request against "
            f"{' / '.join(PROTECTED_BRANCHES)}"
        )
        return {
            "branch": branch,
            "expected_branch": expected_branch,
            "mode": mode,
            "protected": True,
            "unknown": False,
            "severity": "error" if mode == "error" else "warning",
            "ok": mode != "error",
            "message": message,
        }

    if branch == expected_branch:
        return {
            "branch": branch,
            "expected_branch": expected_branch,
            "mode": mode,
            "protected": False,
            "unknown": False,
            "severity": "none",
            "ok": True,
            "message": f"on the expected branch '{branch}'",
        }

    message = (
        f"current branch '{branch}' is not the expected development branch "
        f"'{expected_branch}'; the genetic-project phase is meant to stay isolated"
    )
    return {
        "branch": branch,
        "expected_branch": expected_branch,
        "mode": mode,
        "protected": False,
        "unknown": False,
        "severity": "warning",
        "ok": True,
        "message": message,
    }


def describe_status(status: Optional[Dict[str, object]] = None) -> str:
    """One-line human readable rendering of :func:`guard_status`."""
    data = status if status is not None else guard_status()
    return "[branch-guard] {message} (branch={branch}, expected={expected_branch}, mode={mode})".format(
        message=data.get("message", ""),
        branch=data.get("branch"),
        expected_branch=data.get("expected_branch"),
        mode=data.get("mode"),
    )


def assert_branch_allowed(
    repo_root: Optional[Path] = None,
    expected_branch: str = EXPECTED_BRANCH,
    allow_protected: bool = False,
) -> Dict[str, object]:
    """Raise :class:`BranchViolation` when the policy is violated.

    A protected branch always raises unless ``allow_protected`` is set; a
    different (non-protected) branch only produces a warning, so the process
    keeps running while the divergence stays visible.
    """
    status = guard_status(repo_root=repo_root, expected_branch=expected_branch)
    severity = str(status.get("severity", "none"))
    if severity == "error" and not (allow_protected and status.get("protected")):
        raise BranchViolation(str(status.get("message", "branch policy violated")))
    if severity == "warning":
        print(describe_status(status), file=sys.stderr)
    return status


def warn_if_wrong_branch(
    stream=None,
    repo_root: Optional[Path] = None,
    expected_branch: str = EXPECTED_BRANCH,
) -> Dict[str, object]:
    """Emit the guard notice without ever raising.  Returns the status dict."""
    import warnings

    status = guard_status(repo_root=repo_root, expected_branch=expected_branch)
    if str(status.get("severity", "none")) == "none":
        return status
    notice = describe_status(status)
    target = stream if stream is not None else sys.stderr
    print(notice, file=target)
    warnings.warn(notice, RuntimeWarning, stacklevel=2)
    return status


def main(argv: Optional[list] = None) -> int:
    """CLI entry point: print the status and fail on a protected branch."""
    args = list(sys.argv[1:] if argv is None else argv)
    expected = EXPECTED_BRANCH
    if "--expected" in args:
        index = args.index("--expected")
        if index + 1 < len(args):
            expected = args[index + 1]
    status = guard_status(expected_branch=expected)
    print(describe_status(status))
    if str(status.get("severity", "none")) == "error":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
