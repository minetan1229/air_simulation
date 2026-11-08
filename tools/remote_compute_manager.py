"""
Remote Compute Manager for Paper Airplane Simulation Compute Node
Host: 192.168.100.2
"""
import os
import sys
import time
import socket
import paramiko
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
REMOTE_WORK_DIR = "/home/minetan1229/paper_airplanes_worker"
REMOTE_VENV_DIR = f"{REMOTE_WORK_DIR}/venv"

def get_client(timeout=10):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=timeout)
    return client

def safe_print(text):
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        sys.stdout.buffer.write(text.encode('utf-8', errors='replace'))
        sys.stdout.flush()

def run_cmd(client, cmd, sudo=False, timeout=300):
    if sudo:
        full_cmd = f"echo {PASS} | sudo -S env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C {cmd}"
    else:
        full_cmd = f"LC_ALL=C LANG=C {cmd}"
    stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    status = stdout.channel.recv_exit_status()
    return status, out, err

def wait_for_ssh(max_wait=120):
    start = time.time()
    print(f"Waiting for {HOST}:22 to become available...")
    while time.time() - start < max_wait:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((HOST, 22))
            sock.close()
            if result == 0:
                time.sleep(2)
                # Test login
                c = get_client(timeout=5)
                c.close()
                print("Host is back online and accepting SSH connections.")
                return True
        except Exception:
            pass
        time.sleep(3)
    return False

if __name__ == "__main__":
    c = get_client()
    status, out, err = run_cmd(c, "uptime; uname -r")
    print("Remote status:\n", out)
    c.close()
