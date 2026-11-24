import sys
import paramiko
import os
from pathlib import Path

def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    except ImportError:
        pass

def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set. Copy .env.example to .env (repo root) and fill REMOTE_SSH_* values.")
    return value

from tools.remote_auth import build_ssh_client, exec_sudo

def get_client():
    return build_ssh_client()

def run_sudo(client, cmd):
    print(f"--- SUDO RUN: {cmd} ---")
    status, out, err = exec_sudo(client, cmd)
    print(f"STATUS: {status}")
    if out:
        print(out[-500:])
    if err:
        print("ERR:", err[-500:])
    return status

if __name__ == "__main__":
    c = get_client()
    run_sudo(c, "ps aux | grep -E 'apt|dpkg' | grep -v grep || true")
    run_sudo(c, "apt-get update")
    c.close()
