"""Regression tests for #159 / #160 / #161 (remote SSH auth hardening).

All tests are offline: paramiko is mocked, no real SSH connection is
made. Secret handling is proven by asserting the password never
appears in command strings, logs, or redacted stdout/stderr.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("REMOTE_SSH_HOST", "192.0.2.10")
os.environ.setdefault("REMOTE_SSH_USER", "worker")
SPECIAL_PASS = "p@$$ `echo hi` ;rm -rf / \" $HOME * ? ! # ~ 'mixed'"
os.environ["REMOTE_SSH_PASSWORD"] = SPECIAL_PASS

import tools.remote_auth as remote_auth


class _FakeChannel:
    def __init__(self, out: bytes = b"done\n"):
        self._out = out
        self._status = 0
        self._written: list = []

    def recv_exit_status(self):
        return self._status

    def shutdown_write(self):
        self._written.append("<shutdown>")


class _FakeStdin:
    def __init__(self):
        self.written = ""
        self.channel = _FakeChannel()

    def write(self, data):
        self.written += data

    def flush(self):
        pass


class _FakeStdout:
    def __init__(self, out: bytes = b"done\n"):
        self._out = out
        self._channel = _FakeChannel()

    def read(self):
        return self._out

    @property
    def channel(self):
        return self._channel


def _make_fake_paramiko(calls: dict):
    class _FakeClient:
        def __init__(self):
            self.policy = None
            self.loaded = False

        def load_system_host_keys(self):
            self.loaded = True

        def set_missing_host_key_policy(self, policy):
            self.policy = policy
            calls["policy"] = policy

        def connect(self, host, username=None, password=None, timeout=None):
            calls["connect"] = {"host": host, "user": username, "password": password, "timeout": timeout}

        def exec_command(self, cmd, timeout=None, get_pty=False):
            calls["exec_cmd"] = cmd
            calls["exec_get_pty"] = get_pty
            stdin = _FakeStdin()
            stdout = _FakeStdout((f"echoed {SPECIAL_PASS} done\n").encode())
            stderr = _FakeStdout(b"no literal secret here\n")
            return stdin, stdout, stderr

    class _RejectPolicy:
        pass

    class _AutoAddPolicy:
        pass

    mod = types.ModuleType("paramiko")
    mod.SSHClient = _FakeClient
    mod.RejectPolicy = _RejectPolicy
    mod.AutoAddPolicy = _AutoAddPolicy
    return mod


def test_clean_venv_import_without_paramiko(monkeypatch):
    """#159: modules import even when paramiko is absent (lazy import)."""
    monkeypatch.setitem(sys.modules, "paramiko", None)
    # Force a re-import by dropping cached modules
    for name in ("tools.remote_auth", "tools.remote_compute_manager", "tools.setup_remote_worker", "tools.install_remote_deps", "tools.check_remote_hw"):
        sys.modules.pop(name, None)
    import importlib

    mod = importlib.import_module("tools.remote_auth")
    assert hasattr(mod, "build_ssh_client")
    assert hasattr(mod, "build_sudo_command")
    assert hasattr(mod, "exec_command")
    manager = importlib.import_module("tools.remote_compute_manager")
    assert hasattr(manager, "get_client")
    assert hasattr(manager, "run_cmd")
    setup_mod = importlib.import_module("tools.setup_remote_worker")
    assert hasattr(setup_mod, "get_client")
    install_mod = importlib.import_module("tools.install_remote_deps")
    assert hasattr(install_mod, "get_client")
    hw_mod = importlib.import_module("tools.check_remote_hw")
    assert hasattr(hw_mod, "run")
    with pytest.raises(SystemExit, match="remote extra"):
        mod.import_paramiko()


def test_host_key_reject_policy(monkeypatch):
    """#160: RejectPolicy + load_system_host_keys; AutoAddPolicy absent."""
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "paramiko", _make_fake_paramiko(calls))
    client = remote_auth.build_ssh_client(timeout=7)
    assert client.loaded is True
    assert type(calls["policy"]).__name__ == "_RejectPolicy"
    assert calls["connect"]["timeout"] == 7
    assert calls["connect"]["password"] == SPECIAL_PASS
    source = (ROOT / "tools" / "remote_auth.py").read_text(encoding="utf-8")
    assert "RejectPolicy" in source
    assert "load_system_host_keys" in source
    assert "AutoAddPolicy" not in source
    for name in ("remote_compute_manager.py", "setup_remote_worker.py", "install_remote_deps.py", "check_remote_hw.py", "check_nvidia_packages.py"):
        text = (ROOT / "tools" / name).read_text(encoding="utf-8")
        assert "AutoAddPolicy" not in text, f"{name} still contains AutoAddPolicy"
        assert "import paramiko" not in text, f"{name} should not import paramiko directly"


def test_sudo_password_over_stdin_not_in_command(monkeypatch):
    """#161: password is never embedded; sent via stdin; special chars preserved."""
    calls: dict = {}
    fake = _make_fake_paramiko(calls)
    monkeypatch.setitem(sys.modules, "paramiko", fake)
    client = fake.SSHClient()

    status, out, err = remote_auth.exec_sudo(client, "apt-get update", timeout=5)
    assert status == 0
    cmd_used = calls["exec_cmd"]
    assert "sudo -S -p ''" in cmd_used
    assert SPECIAL_PASS not in cmd_used
    assert "echo" not in cmd_used
    assert "PASS" not in cmd_used
    auth_stdin = getattr(client, "_stdin", None)
    # The fake stdin is created inside exec_command; re-run capturing it
    class _RecordingStdin:
        def __init__(self):
            self.written = ""
            self.channel = _FakeChannel()

        def write(self, data):
            self.written += data

        def flush(self):
            pass

    rec = _RecordingStdin()

    def _exec(command, timeout=None, get_pty=False):
        calls["exec_cmd"] = command
        stdout = _FakeStdout(b"ok\n")
        stderr = _FakeStdout(b"")
        return rec, stdout, stderr

    client.exec_command = _exec  # type: ignore[method-assign]
    remote_auth.exec_sudo(client, "nvidia-smi", timeout=5)
    assert SPECIAL_PASS in rec.written
    assert rec.written == SPECIAL_PASS + "\n"

    shown, _, _ = remote_auth.exec_sudo(client, "whoami", timeout=5)
    assert shown == 0
    # redact proven on command string level
    assert remote_auth.build_sudo_command("whoami", sudo=True) == "sudo -S -p '' env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C whoami"
    assert remote_auth.build_sudo_command("whoami", sudo=False) == "LC_ALL=C LANG=C whoami"


def test_redact_removes_secret_from_output(monkeypatch):
    """#161: echoed secrets are redacted from stdout/stderr."""
    calls: dict = {}
    fake = _make_fake_paramiko(calls)
    monkeypatch.setitem(sys.modules, "paramiko", fake)
    client = fake.SSHClient()
    _, out, err = remote_auth.exec_sudo(client, "echo test", timeout=5)
    assert SPECIAL_PASS not in out
    assert "***" in out
    assert SPECIAL_PASS not in err
    assert remote_auth.redact(f"leak {SPECIAL_PASS} end", SPECIAL_PASS) == "leak *** end"
    assert remote_auth.redact("clean", SPECIAL_PASS) == "clean"


def test_dependency_declared_in_pyproject_and_requirements():
    """#159: paramiko declared as the `remote` extra in both files."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    extra_block = pyproject.split("[project.optional-dependencies]")[1].split("[tool.setuptools")[0]
    assert "remote" in pyproject
    assert "paramiko" in extra_block
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "paramiko" in requirements
    assert 'extra == "remote"' in requirements
    doc = (ROOT / "docs" / "REMOTE_COMPUTE_ENVIRONMENT.md").read_text(encoding="utf-8")
    assert "remote" in doc and "paramiko" in doc
    assert "RejectPolicy" in doc
    assert "sudo -S -p" in doc


def test_public_api_preserved():
    """Callers keep existing public API names (no breaking change)."""
    from tools import remote_compute_manager, setup_remote_worker, install_remote_deps, check_remote_hw

    assert callable(remote_compute_manager.get_client)
    assert callable(remote_compute_manager.run_cmd)
    assert callable(remote_compute_manager.wait_for_ssh)
    assert callable(setup_remote_worker.get_client)
    assert callable(setup_remote_worker.run_cmd)
    assert callable(install_remote_deps.run_sudo_stream)
    assert callable(check_remote_hw.run)
