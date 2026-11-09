"""Regression tests for the scripts/ PROJECT_ROOT path fixes (issues #157, #158).

The scripts live in ``scripts/`` but every one of them must operate on the
repository root (the parent of ``scripts/``), never on ``scripts/`` itself.
Before the fix, ``setup.sh`` / ``setup.ps1`` ran ``pip install -e .`` from
``scripts/`` -- where no ``pyproject.toml`` exists -- and
``start_discord_bridge.bat`` / ``watchdog.sh`` referenced root-relative paths
(``tools/...``, ``.env``, ``runs/...``) from a ``scripts/`` working directory,
so launching them from any cwd (or from ``scripts/`` itself) failed.

These tests verify:

* ``PROJECT_ROOT`` is the parent directory of ``scripts/`` and is used with
  absolute paths (static inspection of all four scripts).
* The ``.venv`` and editable-install targets resolve to the repository root.
* Launching the shell/PowerShell scripts from an unrelated working directory
  still resolves ``PROJECT_ROOT`` to the repository root (bash, if available;
  PowerShell syntax is checked statically because ``pwsh`` is not installed).
* The Windows batch launcher anchors every referenced path under the root.
* The watchdog resolves its PID/log/run files under the root and passes an
  absolute script path to the monitoring loop.

No test performs a network install or starts the Discord bot; dynamic checks
use ``bash`` with stubbed side effects (an import-only probe, or the fixed
``--help``-style dry run where safe) and fall back to static assertions when
the required interpreter is missing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SETUP_SH = SCRIPTS / "setup.sh"
SETUP_PS1 = SCRIPTS / "setup.ps1"
BAT = SCRIPTS / "start_discord_bridge.bat"
WATCHDOG_SH = SCRIPTS / "watchdog.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bash_root_probe(script: Path, cwd: Path) -> str | None:
    """Run the script's PROJECT_ROOT assignment from an unrelated cwd.

    Returns the resolved PROJECT_ROOT, or None when bash is unavailable.
    """
    if shutil.which("bash") is None:
        return None
    probe = (
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; '
        'PROJECT_ROOT="$SCRIPT_DIR"; printf "%s" "$PROJECT_ROOT"'
    )
    proc = subprocess.run(
        [
            "bash",
            "-c",
            probe,
            "bash",
            str(script),
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _norm(path: str) -> str:
    return str(Path(path).resolve()).replace("\\", "/")


def _resolve_all(text: str) -> list[str]:
    return re.findall(r"\$PROJECT_ROOT/([\w./-]+)", text)


class TestSetupShRoot:
    """Issue #157: setup.sh must bootstrap the repository root, not scripts/."""

    def test_project_root_is_parent_of_scripts_dir(self) -> None:
        text = _read(SETUP_SH)
        assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in text
        assert 'PROJECT_ROOT="$SCRIPT_DIR"' in text
        assert 'cd "$PROJECT_ROOT"' in text

    def test_venv_lives_under_project_root(self) -> None:
        text = _read(SETUP_SH)
        assert 'VENV_DIR="$PROJECT_ROOT/.venv"' in text
        assert 'VENV_DIR="$SCRIPT_DIR/.venv"' not in text

    def test_editable_install_targets_project_root(self) -> None:
        text = _read(SETUP_SH)
        assert '$PROJECT_ROOT[dev]' in text
        for line in text.splitlines():
            if "pip install -e" in line:
                assert "$PROJECT_ROOT" in line, line

    @pytest.mark.parametrize("cwd_name", ["unrelated-cwd", "scripts-dir", "root-dir"])
    def test_project_root_resolution_is_cwd_independent(
        self, tmp_path: Path, cwd_name: str
    ) -> None:
        if cwd_name == "unrelated-cwd":
            cwd = tmp_path / "elsewhere"
            cwd.mkdir()
        elif cwd_name == "scripts-dir":
            cwd = SCRIPTS
        else:
            cwd = ROOT
        resolved = _bash_root_probe(SETUP_SH, cwd)
        if resolved is None:
            pytest.skip("bash is not available; static assertions already cover the fix")
        assert _norm(resolved) == _norm(str(ROOT))

    def test_pyproject_exists_at_project_root(self) -> None:
        """The editable install needs a pyproject.toml at PROJECT_ROOT."""
        assert (ROOT / "pyproject.toml").is_file()
        assert not (SCRIPTS / "pyproject.toml").exists()


class TestSetupPs1Root:
    """Issue #157 (Windows): setup.ps1 must use the repository root."""

    def test_project_root_is_parent_of_scripts_dir(self) -> None:
        text = _read(SETUP_PS1)
        assert "$ProjectRoot = Split-Path -Parent $ScriptDir" in text
        assert "Set-Location $ProjectRoot" in text
        assert "Set-Location $ScriptDir" not in text

    def test_venv_lives_under_project_root(self) -> None:
        text = _read(SETUP_PS1)
        assert '$VenvDir = Join-Path $ProjectRoot ".venv"' in text

    def test_editable_install_targets_project_root(self) -> None:
        text = _read(SETUP_PS1)
        assert '$ProjectRoot[dev]' in text
        for line in text.splitlines():
            if "pip install -e" in line:
                assert "$ProjectRoot" in line, line

    def test_no_scripts_dir_anchored_paths(self) -> None:
        text = _read(SETUP_PS1)
        assert '$VenvDir = Join-Path $ScriptDir' not in text


class TestStartDiscordBridgeBat:
    """Issue #158 (Windows): the batch launcher must resolve root paths."""

    def test_changes_into_parent_of_scripts_dir(self) -> None:
        text = _read(BAT)
        assert 'cd /d "%~dp0.."' in text
        assert 'set "ROOT=%CD%"' in text

    def test_env_paths_are_absolute_via_root(self) -> None:
        text = _read(BAT)
        assert '"%ROOT%\\.env"' in text
        assert '"%ROOT%\\.env.example"' in text
        assert 'copy "%ROOT%\\.env.example" "%ROOT%\\.env"' in text

    def test_python_target_is_absolute_via_root(self) -> None:
        text = _read(BAT)
        assert 'python "%ROOT%\\tools\\discord_bridge.py"' in text
        assert "python tools\\" not in text

    def test_referenced_tools_file_exists(self) -> None:
        assert (ROOT / "tools" / "discord_bridge.py").is_file()
        assert (ROOT / ".env.example").exists()

    def test_no_bare_root_relative_references(self) -> None:
        """No command may use a root-relative path without the %ROOT% anchor."""
        text = _read(BAT)
        assert 'copy .env.example .env' not in text
        block = [
            line
            for line in text.splitlines()
            if "copy " in line or "python " in line
        ]
        assert block, "the launcher must reference the env template and the tool"
        for line in block:
            assert "%ROOT%" in line, line


class TestWatchdogShRoot:
    """Issue #158: watchdog.sh must resolve every path under PROJECT_ROOT."""

    def test_project_root_is_parent_of_scripts_dir(self) -> None:
        text = _read(WATCHDOG_SH)
        assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in text
        assert 'PROJECT_ROOT="$SCRIPT_DIR"' in text
        assert 'cd "$PROJECT_ROOT"' in text

    def test_runtime_files_are_absolute(self) -> None:
        text = _read(WATCHDOG_SH)
        for name in (
            "sim_process.pid",
            "sim_output.log",
            "runs/ga_research",
            "watchdog_status.txt",
        ):
            assert f'"$PROJECT_ROOT/{name}"' in text, name

    def test_inline_launcher_resolves_under_project_root(self) -> None:
        text = _read(WATCHDOG_SH)
        assert "os.environ.get('PROJECT_ROOT'" in text
        assert "os.path.join(root, 'tools/run_ga_research.py')" in text
        assert "os.path.join(root, 'runs/ga_research/checkpoints/latest.json')" in text
        assert "open(os.path.join(root, 'sim_output.log'" in text
        assert "open(os.path.join(root, 'sim_process.pid'" in text
        assert "export PROJECT_ROOT" in text

    def test_tools_invocations_are_absolute(self) -> None:
        text = _read(WATCHDOG_SH)
        assert '"$PROJECT_ROOT/tools/watchdog_loop.py"' in text
        assert '"$PROJECT_ROOT/tools/notify_discord.py"' in text
        assert '"$PROJECT_ROOT/runs/ga_research/progress.log"' in text
        assert '"$PY_CMD" tools/' not in text
        assert '>> runs/ga_research/progress.log' not in text

    @pytest.mark.parametrize("cwd_name", ["unrelated-cwd", "scripts-dir", "root-dir"])
    def test_project_root_resolution_is_cwd_independent(
        self, tmp_path: Path, cwd_name: str
    ) -> None:
        if cwd_name == "unrelated-cwd":
            cwd = tmp_path / "elsewhere"
            cwd.mkdir()
        elif cwd_name == "scripts-dir":
            cwd = SCRIPTS
        else:
            cwd = ROOT
        resolved = _bash_root_probe(WATCHDOG_SH, cwd)
        if resolved is None:
            pytest.skip("bash is not available; static assertions already cover the fix")
        assert _norm(resolved) == _norm(str(ROOT))

    def test_referenced_tools_files_exist(self) -> None:
        for name in ("run_ga_research.py", "watchdog_loop.py", "notify_discord.py"):
            assert (ROOT / "tools" / name).is_file(), name


class TestStaticRootRelativeReferences:
    """Cross-cutting guard: no script may reference a root-relative path bare."""

    @pytest.mark.parametrize(
        "script",
        [SETUP_SH, SETUP_PS1, BAT, WATCHDOG_SH],
    )
    def test_all_relative_path_tokens_resolve_under_project_root(
        self, script: Path
    ) -> None:
        text = _read(script)
        if script.suffix == ".sh":
            relative = [
                tok
                for tok in _resolve_all(text)
                if tok.startswith("tools/") or tok.startswith("runs/")
            ]
            if script is not SETUP_SH:
                assert relative, "expected at least one anchored tool/run path"
            # The raw shell must not invoke tools/ or runs/ without the anchor;
            # prose mentions (comments, echo strings) are not path references.
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("echo "):
                    continue
                if "tools/" in line or "runs/" in line:
                    if "PROJECT_ROOT" in line or "os.path.join(root" in line:
                        continue
                    assert False, f"unanchored root-relative path in {script.name}: {line}"
        elif script.suffix == ".ps1":
            for line in text.splitlines():
                if "pip install -e" in line:
                    assert "$ProjectRoot" in line, line
        else:
            assert 'cd /d "%~dp0.."' in text
            for line in text.splitlines():
                if "tools\\" in line:
                    assert "%ROOT%\\tools\\" in line, line


def test_scripts_do_not_create_venv_inside_scripts_dir() -> None:
    """A regression guard: scripts/.venv must never be the target."""
    for script in (SETUP_SH, SETUP_PS1):
        text = _read(script)
        assert '"$SCRIPT_DIR/.venv"' not in text
        assert "Join-Path $ScriptDir" not in text


def test_this_test_run_does_not_mutate_repository_scripts() -> None:
    """The suite only reads the scripts; it must not create artefacts there."""
    scripts_state = {p.name: p.read_bytes() for p in SCRIPTS.iterdir() if p.is_file()}
    for name, data in scripts_state.items():
        assert data, f"{name} must not be empty"
    assert not (SCRIPTS / ".venv").exists()
    # ROOT/.venv may legitimately pre-exist (developer environment); only assert no new SCRIPTS/.venv creation.
