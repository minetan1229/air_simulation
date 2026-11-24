"""Install remote dependencies - sudo via tools.remote_auth stdin."""

import sys

from tools.remote_auth import build_ssh_client, exec_sudo, safe_print


def get_client():
    return build_ssh_client(timeout=15)


def run_sudo_stream(client, cmd):
    status, out, err = exec_sudo(client, cmd, timeout=300)
    safe_print(f"\n=== STARTING: {cmd} ===\n")
    if out:
        safe_print(out)
    if err:
        safe_print(err)
    safe_print(f"\n=== FINISHED with status {status} ===\n")
    return status


if __name__ == "__main__":
    client = get_client()

    print("Step 1: apt-get install base packages")
    s1 = run_sudo_stream(client, "apt-get install -y python3-dev python3-venv python3-pip git rsync htop curl build-essential dkms")
    if s1 != 0:
        print(f"Step 1 failed with {s1}")
        sys.exit(s1)

    print("Step 2: apt-get install nvidia-driver")
    s2 = run_sudo_stream(client, "apt-get install -y nvidia-driver")
    if s2 != 0:
        print(f"Step 2 failed with {s2}")
        sys.exit(s2)

    print("Step 3: Checking nvidia module and tools")
    run_sudo_stream(client, "modprobe nvidia || true")
    run_sudo_stream(client, "nvidia-smi || true")

    client.close()
