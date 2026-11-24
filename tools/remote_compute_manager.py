"""
Remote Compute Manager for Paper Airplane Simulation Compute Node
Host: 192.168.100.2
"""

from tools.remote_auth import build_ssh_client, exec_command, get_ssh_settings, safe_print

# Env values are loaded lazily at connect/read time (not at import),
# so a clean venv without paramiko can still import this module.
REMOTE_WORK_DIR = "/home/minetan1229/paper_airplanes_worker"
REMOTE_VENV_DIR = f"{REMOTE_WORK_DIR}/venv"


def get_client(timeout=10):
    return build_ssh_client(timeout=timeout)


def run_cmd(client, cmd, sudo=False, timeout=300):
    return exec_command(client, cmd, sudo=sudo, timeout=timeout)


def wait_for_ssh(max_wait=120):
    import socket
    import time

    host = get_ssh_settings()[0]
    start = time.time()
    print(f"Waiting for {host}:22 to become available...")
    while time.time() - start < max_wait:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, 22))
            sock.close()
            if result == 0:
                time.sleep(2)
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
    safe_print(f"Remote status:\n{out}")
    if err:
        safe_print(f"stderr: {err}")
    c.close()
