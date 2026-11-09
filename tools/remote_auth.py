"""Shared SSH authentication for remote-compute tools.

Centralises the three security-sensitive concerns that previously were
duplicated (unsafely) in every ``tools/remote_*.py`` /
``tools/setup_remote_worker.py`` / ``tools/install_remote_deps.py`` /
``tools/check_remote_hw.py`` script:

* #159 - paramiko is an optional ``remote`` extra, imported lazily, so
  importing this module (or a caller that does not connect) works in a
  clean venv without paramiko installed.
* #160 - host keys are verified with ``RejectPolicy`` after
  ``load_system_host_keys()``; an unknown key is refused instead of
  being auto-added.  The password is never logged or interpolated into
  a shell command string.
* #161 - sudo passwords are sent over the channel stdin after
  ``sudo -S -p ''`` rather than embedded in an ``echo <pass> | sudo``
  f-string, so special characters are preserved and the secret never
  appears in the command string, process list, or logs.

Only this module imports paramiko at connect time.  Callers keep their
existing public API (``get_client`` / ``run_cmd`` ...) and delegate here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Re-exported host-key policy class name for tests without paramiko.
HOST_KEY_POLICY = "RejectPolicy"

SUDO_PROMPT_EMPTY = "-p"
REMOTE_ENV_VARS = ("REMOTE_SSH_HOST", "REMOTE_SSH_USER", "REMOTE_SSH_PASSWORD")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    except ImportError:
        pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"{name} is not set. Copy .env.example to .env (repo root) and fill REMOTE_SSH_* values."
        )
    return value


def import_paramiko() -> Any:
    """Lazily import paramiko, raising an actionable error if missing.

    Lazy import is the #159 fix: a clean venv without the ``remote``
    extra can still import this module and the callers that reference
    it; the ImportError surfaces only on an actual SSH connect, with
    install instructions in the message.
    """
    try:
        import paramiko

        return paramiko
    except ImportError as exc:  # pragma: no cover - exercised by clean-venv check
        raise SystemExit(
            "paramiko is required for remote SSH. Install the remote extra first: "
            "pip install -e \".[remote]\"  (or: pip install paramiko)"
        ) from exc


def get_ssh_settings() -> tuple:
    _load_env()
    return (
        _require("REMOTE_SSH_HOST"),
        _require("REMOTE_SSH_USER"),
        _require("REMOTE_SSH_PASSWORD"),
    )


def build_ssh_client(timeout: int = 10, host: str | None = None, user: str | None = None, password: str | None = None) -> Any:
    """Create a verified SSHClient. Never logs the password."""
    paramiko = import_paramiko()
    env_host, env_user, env_pass = get_ssh_settings()
    host = host if host is not None else env_host
    user = user if user is not None else env_user
    password = password if password is not None else env_pass

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(host, username=user, password=password, timeout=timeout)
    return client


def build_sudo_command(cmd: str, sudo: bool) -> str:
    """Return the remote command string. The sudo password is NOT in it."""
    if sudo:
        return f"sudo -S -p '' env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C {cmd}"
    return f"LC_ALL=C LANG=C {cmd}"


def exec_sudo(client: Any, cmd: str, timeout: int = 300) -> tuple:
    """Run *cmd* via sudo, feeding the password over stdin.

    The password travels only via channel stdin, never via the command
    string, so shell metacharacters in it are preserved verbatim and it
    cannot leak into logs, ``ps`` output, or a shell history entry.
    Returns ``(status, out, err)`` with the password redacted from any
    echoed output.
    """
    _, _, password = get_ssh_settings()
    full_cmd = build_sudo_command(cmd, sudo=True)
    stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout, get_pty=False)
    if stdin is not None:
        try:
            stdin.write(password + "\n")
            stdin.flush()
        except (BrokenPipeError, AttributeError, ValueError):
            pass
        try:
            stdin.channel.shutdown_write()
        except Exception:
            pass
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    status = stdout.channel.recv_exit_status()
    return status, redact(out, password), redact(err, password)


def exec_command(client: Any, cmd: str, sudo: bool = False, timeout: int = 300, get_pty: bool = False) -> tuple:
    """Run *cmd* (with or without sudo) and return ``(status, out, err)``."""
    _, _, password = get_ssh_settings()
    full_cmd = build_sudo_command(cmd, sudo=sudo)
    if sudo:
        stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout, get_pty=get_pty)
        if stdin is not None:
            try:
                stdin.write(password + "\n")
                stdin.flush()
            except (BrokenPipeError, AttributeError, ValueError):
                pass
            try:
                stdin.channel.shutdown_write()
            except Exception:
                pass
    else:
        stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout, get_pty=get_pty)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    status = stdout.channel.recv_exit_status()
    return status, redact(out, password), redact(err, password)


def redact(text: str, secret: str) -> str:
    """Strip *secret* from anything that might be logged or printed."""
    if secret and text and secret in text:
        return text.replace(secret, "***")
    return text


def safe_print(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.flush()


__all__ = [
    "HOST_KEY_POLICY",
    "REMOTE_ENV_VARS",
    "SUDO_PROMPT_EMPTY",
    "build_ssh_client",
    "build_sudo_command",
    "exec_command",
    "exec_sudo",
    "get_ssh_settings",
    "import_paramiko",
    "redact",
    "safe_print",
]
