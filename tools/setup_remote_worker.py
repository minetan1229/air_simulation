"""Setup the remote worker - delegates SSH auth to tools.remote_auth."""

from tools.remote_auth import build_ssh_client, exec_command


def get_client():
    return build_ssh_client(timeout=10)


def run_cmd(client, cmd, sudo=False, timeout=300):
    status, out, err = exec_command(client, cmd, sudo=sudo, timeout=timeout)
    print(f"--- RUN: {cmd[:80]} ---")
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
