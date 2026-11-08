import sys
import time
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
    client.connect(HOST, username=USER, password=PASS, timeout=15)
    return client

def safe_print(text):
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        sys.stdout.buffer.write(text.encode('utf-8', errors='replace'))
        sys.stdout.flush()

def run_sudo_stream(client, cmd):
    full_cmd = f"echo {PASS} | sudo -S env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C {cmd}"
    safe_print(f"\n=== STARTING: {cmd} ===\n")
    stdin, stdout, stderr = client.exec_command(full_cmd, get_pty=True)

    last_print_time = time.time()
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            data = stdout.channel.recv(4096).decode('utf-8', errors='replace')
            safe_print(data)
            last_print_time = time.time()
        time.sleep(0.5)

    remaining = stdout.read().decode('utf-8', errors='replace')
    if remaining:
        safe_print(remaining)

    status = stdout.channel.recv_exit_status()
    safe_print(f"\n=== FINISHED with status {status} ===\n")
    return status

if __name__ == "__main__":
    client = get_client()

    # 1. Base tools
    print("Step 1: apt-get install base packages")
    s1 = run_sudo_stream(client, "apt-get install -y python3-dev python3-venv python3-pip git rsync htop curl build-essential dkms")
    if s1 != 0:
        print(f"Step 1 failed with {s1}")
        sys.exit(s1)

    # 2. NVIDIA driver
    print("Step 2: apt-get install nvidia-driver")
    s2 = run_sudo_stream(client, "apt-get install -y nvidia-driver")
    if s2 != 0:
        print(f"Step 2 failed with {s2}")
        sys.exit(s2)

    # 3. Check nvidia modules
    print("Step 3: Checking nvidia module and tools")
    run_sudo_stream(client, "modprobe nvidia || true")
    run_sudo_stream(client, "nvidia-smi || true")

    client.close()
