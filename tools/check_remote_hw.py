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

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS)

def run(cmd):
    print(f"=== {cmd} ===")
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out:
        print(out)
    if err:
        print("ERR:", err)

run("lspci")
run("lsmod | grep -i nvidia || true")
run("ls -la /dev/nvidia* 2>/dev/null || true")
run("which nvidia-smi 2>/dev/null || true")
run("dmesg | grep -i -E 'nvidia|gpu|vga' | tail -n 20 2>/dev/null || true")

client.close()
