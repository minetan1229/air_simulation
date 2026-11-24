"""Check NVIDIA packages - delegates SSH auth to tools.remote_auth."""

from tools.remote_auth import build_ssh_client, exec_command


def run(cmd):
    status, out, err = exec_command(client, cmd)
    print(out)
    if err and status != 0:
        print("ERR:", err)
    return status


if __name__ == "__main__":
    client = build_ssh_client()
    run("apt-cache search '^nvidia-driver' | head -n 10")
    run("apt-cache search '^nvidia-open' | head -n 10")
    run("apt-cache search '^nvidia-cuda-toolkit' || true")
    run("dpkg -l | grep -E 'linux-headers|nvidia' || true")
    client.close()
