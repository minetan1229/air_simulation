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

_load_env()
HOST = _require("REMOTE_SSH_HOST")
USER = _require("REMOTE_SSH_USER")
PASS = _require("REMOTE_SSH_PASSWORD")

def get_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=10)
    return client

def run_sudo(client, cmd):
    # Pass environment correctly to sudo
    full_cmd = f"echo {PASS} | sudo -S env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C {cmd}"
    print(f"--- SUDO RUN: {cmd} ---")
    stdin, stdout, stderr = client.exec_command(full_cmd)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    status = stdout.channel.recv_exit_status()
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
