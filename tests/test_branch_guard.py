"""Tests for the ``genetic-project`` branch guard (``tools/branch_guard.py``).

The guard is the mechanical enforcement of the branch-isolation protocol: the
autonomous-evolution (GA) phase runs on the dedicated ``genetic-project``
branch and must never write to ``master`` / ``main``.

The tests build throw-away git repositories in ``tmp_path`` so they never
depend on -- or mutate -- the checkout the suite runs in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import branch_guard  # noqa: E402
from tools.branch_guard import (  # noqa: E402
    EXPECTED_BRANCH,
    PROTECTED_BRANCHES,
    BranchViolation,
    assert_branch_allowed,
    ci_event_branch,
    ci_event_ref,
    current_branch,
    describe_status,
    enforce_branch_policy,
    guard_status,
    is_ci_event_checkout,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repository with one commit on branch ``master``."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "--initial-branch=master")
    _git(path, "config", "user.email", "guard@example.invalid")
    _git(path, "config", "user.name", "branch guard")
    (path / "file.txt").write_text("payload\n", encoding="utf-8")
    _git(path, "add", "file.txt")
    _git(path, "commit", "-m", "initial")
    return path


@pytest.fixture(autouse=True)
def _clean_guard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from the default (strict) policy.

    The CI markers are removed as well.  A GitHub runner exports
    ``GITHUB_ACTIONS`` and ``CI``, and the guard downgrades itself to a
    warning there - without this, every strict-policy assertion below would
    depend on where the suite happens to run.  ``GITHUB_REF`` is removed for
    the same reason: it names the ref the runner checked out, and the throw-away
    repositories of these tests must not be mistaken for a CI event checkout.
    The downgrade is exercised explicitly by
    ``test_a_ci_runner_downgrades_the_guard_to_a_warning``.
    """
    monkeypatch.delenv(branch_guard.GUARD_ENV_VAR, raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv(branch_guard.CI_EVENT_REF_VAR, raising=False)


# ---------------------------------------------------------------------------
# current_branch
# ---------------------------------------------------------------------------
def test_current_branch_reports_the_checked_out_branch(repo: Path) -> None:
    assert current_branch(repo) == "master"


def test_current_branch_is_none_outside_a_git_repository(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert current_branch(plain) is None


def test_current_branch_is_none_on_a_detached_head(repo: Path) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(repo, "checkout", "--detach", head)
    assert current_branch(repo) is None


# ---------------------------------------------------------------------------
# guard_status
# ---------------------------------------------------------------------------
def test_protected_branch_is_an_error(repo: Path) -> None:
    status = guard_status(repo_root=repo)
    assert status["branch"] == "master"
    assert status["protected"] is True
    assert status["severity"] == "error"
    assert status["ok"] is False
    assert "protected" in str(status["message"])


def test_expected_branch_is_accepted(repo: Path) -> None:
    _git(repo, "checkout", "-b", EXPECTED_BRANCH)
    status = guard_status(repo_root=repo)
    assert status["branch"] == EXPECTED_BRANCH
    assert status["severity"] == "none"
    assert status["ok"] is True


def test_other_non_protected_branch_is_only_a_warning(repo: Path) -> None:
    _git(repo, "checkout", "-b", "feat/some-experiment")
    status = guard_status(repo_root=repo)
    assert status["severity"] == "warning"
    assert status["ok"] is True
    assert EXPECTED_BRANCH in str(status["message"])


def test_unknown_branch_is_never_an_error(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    status = guard_status(repo_root=plain)
    assert status["unknown"] is True
    assert status["severity"] == "none"
    assert status["ok"] is True


def test_guard_can_be_disabled(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, "off")
    status = guard_status(repo_root=repo)
    assert status["severity"] == "none"
    assert status["ok"] is True


def test_guard_can_be_downgraded_to_a_warning(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, "warn")
    status = guard_status(repo_root=repo)
    assert status["severity"] == "warning"
    assert status["ok"] is True


def test_invalid_mode_falls_back_to_the_strict_policy(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, "nonsense")
    status = guard_status(repo_root=repo)
    assert status["mode"] == "error"
    assert status["severity"] == "error"


def test_a_ci_runner_downgrades_the_guard_to_a_warning(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CI job checks out the triggering branch, so it must not be blocked.

    The GitHub workflow runs on ``master``; the guard would otherwise abort
    the whole test suite there.  The protection is enforced by the branch
    policy, not by the process.
    """
    monkeypatch.delenv(branch_guard.GUARD_ENV_VAR, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("CI", "true")
    status = guard_status(repo_root=repo)
    assert status["mode"] == "warn"
    assert status["severity"] == "warning"
    assert status["ok"] is True


def test_an_explicit_mode_overrides_the_ci_downgrade(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, "error")
    status = guard_status(repo_root=repo)
    assert status["mode"] == "error"
    assert status["severity"] == "error"


# ---------------------------------------------------------------------------
# assert_branch_allowed
# ---------------------------------------------------------------------------
def test_assert_branch_allowed_raises_on_a_protected_branch(repo: Path) -> None:
    with pytest.raises(BranchViolation):
        assert_branch_allowed(repo_root=repo)


def test_assert_branch_allowed_can_be_overridden_for_a_protected_branch(repo: Path) -> None:
    status = assert_branch_allowed(repo_root=repo, allow_protected=True)
    assert status["protected"] is True


def test_assert_branch_allowed_passes_on_the_expected_branch(repo: Path) -> None:
    _git(repo, "checkout", "-b", EXPECTED_BRANCH)
    status = assert_branch_allowed(repo_root=repo)
    assert status["severity"] == "none"


def test_assert_branch_allowed_does_not_raise_on_a_feature_branch(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(repo, "checkout", "-b", "feat/other")
    status = assert_branch_allowed(repo_root=repo)
    assert status["severity"] == "warning"
    assert "branch-guard" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The real repository
# ---------------------------------------------------------------------------
def test_guard_runs_against_the_real_checkout() -> None:
    """The utility must be usable against the repository it lives in."""
    status = guard_status()
    assert status["mode"] in ("error", "warn", "off")
    assert status["severity"] in ("none", "warning", "error")
    assert isinstance(describe_status(status), str)


def test_expected_branch_constant_is_the_documented_one() -> None:
    assert EXPECTED_BRANCH == "genetic-project"
    assert "master" in PROTECTED_BRANCHES
    assert "main" in PROTECTED_BRANCHES


def test_cli_entry_point_reports_the_status(capsys: pytest.CaptureFixture[str]) -> None:
    code = branch_guard.main([])
    captured = capsys.readouterr().out
    assert "branch-guard" in captured
    assert code in (0, 2)


def test_guard_env_var_name_is_stable() -> None:
    assert branch_guard.GUARD_ENV_VAR == "PAPER_AIRCRAFT_BRANCH_GUARD"
    assert os.environ.get(branch_guard.GUARD_ENV_VAR, "error") in ("error", "warn", "off")


# ---------------------------------------------------------------------------
# The CI event checkout
#
# ``actions/checkout`` checks the triggering ref out: on a push it creates a
# local branch named after the pushed branch, on a pull request it checks the
# merge ref out as a detached HEAD.  The branch name of such a checkout says
# nothing about where the work was authored, so it must not be judged as a
# branch-isolation violation (the root cause of "a push to master kills the
# whole CI pipeline").
# ---------------------------------------------------------------------------
def _workflow_env_block(path: Path) -> list[str]:
    """The lines of the workflow-level ``env:`` block (a key in column 0)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.rstrip() != "env:":
            continue
        block: list[str] = []
        for follow in lines[index + 1:]:
            if follow.strip() and not follow.startswith((" ", "\t")):
                break
            block.append(follow)
        return block
    return []


@pytest.mark.parametrize("workflow", ["ci.yml", "verify.yml"])
def test_the_workflows_declare_the_ci_branch_guard_policy(workflow: str) -> None:
    """CI must not depend on the implicit CI-marker heuristic."""
    path = ROOT / ".github" / "workflows" / workflow
    block = _workflow_env_block(path)
    assert any(
        line.strip() == f"{branch_guard.GUARD_ENV_VAR}: warn" for line in block
    ), f"{workflow} must declare {branch_guard.GUARD_ENV_VAR}: warn at workflow level"


def test_ci_event_ref_is_read_from_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(branch_guard.CI_EVENT_REF_VAR, "refs/heads/master")
    assert ci_event_ref() == "refs/heads/master"
    assert ci_event_branch() == "master"


def test_a_pull_request_merge_ref_has_no_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pull request is checked out as a detached merge commit."""
    monkeypatch.setenv(branch_guard.CI_EVENT_REF_VAR, "refs/pull/12/merge")
    assert ci_event_ref() == "refs/pull/12/merge"
    assert ci_event_branch() is None


def test_no_event_ref_outside_a_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(branch_guard.CI_EVENT_REF_VAR, raising=False)
    assert ci_event_ref() is None
    assert ci_event_branch() is None
    assert is_ci_event_checkout("master") is False


def test_a_local_master_checkout_aborts_the_session(repo: Path) -> None:
    """The local protection must survive every CI fix."""
    with pytest.raises(BranchViolation):
        enforce_branch_policy(repo_root=repo)


def test_a_local_master_checkout_is_authoritative(repo: Path) -> None:
    status = guard_status(repo_root=repo)
    assert status["ci_event_checkout"] is False
    assert status["event_ref"] is None
    assert status["branch_authoritative"] is True


def test_a_ci_event_checkout_does_not_abort_the_session(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The push checkout of ``master`` must not stop the pipeline."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(branch_guard.CI_EVENT_REF_VAR, "refs/heads/master")
    status = enforce_branch_policy(repo_root=repo)
    assert status["severity"] != "error"
    assert status["ok"] is True
    assert status["ci_event_checkout"] is True
    assert status["event_ref"] == "refs/heads/master"
    assert status["branch_authoritative"] is False


def test_a_ci_event_checkout_is_recognised_for_a_detached_head(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pull request checkout is detached: the runner still made it."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(repo, "checkout", "--detach", head)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(branch_guard.CI_EVENT_REF_VAR, "refs/heads/master")
    status = guard_status(repo_root=repo)
    assert status["branch"] is None
    assert status["ci_event_checkout"] is True
    assert status["branch_authoritative"] is False
    assert status["severity"] == "none"


def test_a_branch_that_is_not_the_event_branch_stays_judged(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the event's own branch is excused, not every branch of the runner."""
    _git(repo, "checkout", "-b", "feat/other")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(branch_guard.CI_EVENT_REF_VAR, "refs/heads/master")
    status = guard_status(repo_root=repo)
    assert status["ci_event_checkout"] is False
    assert status["branch_authoritative"] is True


@pytest.mark.parametrize(
    "mode,expected_severity",
    [(None, "error"), ("warn", "warning"), ("off", "none")],
)
def test_the_documented_guard_modes_on_a_local_master_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None, expected_severity: str
) -> None:
    """``PAPER_AIRCRAFT_BRANCH_GUARD`` keeps its documented semantics."""
    if mode is None:
        monkeypatch.delenv(branch_guard.GUARD_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, mode)
    status = guard_status(repo_root=repo)
    assert status["mode"] == (mode or "error")
    assert status["severity"] == expected_severity
    if expected_severity == "error":
        with pytest.raises(BranchViolation):
            enforce_branch_policy(repo_root=repo)
    else:
        assert enforce_branch_policy(repo_root=repo)["severity"] == expected_severity


@pytest.mark.parametrize(
    "mode,expected_severity",
    [(None, "none"), ("warn", "none"), ("off", "none")],
)
def test_the_documented_guard_modes_on_a_ci_event_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None, expected_severity: str
) -> None:
    """No documented mode may abort the session on a CI event checkout."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(branch_guard.CI_EVENT_REF_VAR, "refs/heads/master")
    if mode is None:
        monkeypatch.delenv(branch_guard.GUARD_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, mode)
    status = enforce_branch_policy(repo_root=repo)
    assert status["severity"] == expected_severity
    assert status["ok"] is True


def test_an_explicit_error_mode_still_fails_on_a_ci_event_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment variable stays the last word."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(branch_guard.CI_EVENT_REF_VAR, "refs/heads/master")
    monkeypatch.setenv(branch_guard.GUARD_ENV_VAR, "error")
    status = guard_status(repo_root=repo)
    assert status["mode"] == "error"
    assert status["severity"] == "error"
    with pytest.raises(BranchViolation):
        enforce_branch_policy(repo_root=repo)
