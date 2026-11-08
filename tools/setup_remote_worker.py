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

def run_cmd(client, cmd, sudo=False, timeout=300):
    if sudo:
        full_cmd = f"echo {PASS} | sudo -S DEBIAN_FRONTEND=noninteractive {cmd}"
    else:
        full_cmd = cmd
    print(f"--- RUN: {cmd[:80]} ---")
    stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    status = stdout.channel.recv_exit_status()
    print(f"STATUS: {status}")
    if out:
        print(out)
    if err and status != 0:
        print("ERR:", err)
    return status, out, err

if __name__ == "__main__":
    client = get_client()
    run_cmd(client, "uname -r")
    run_cmd(client, "cat /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true")
    client.close()
