"""Check remote hardware - delegates SSH auth to tools.remote_auth."""

from tools.remote_auth import build_ssh_client, exec_command


def run(cmd):
    status, out, err = exec_command(client, cmd)
    print(f"=== {cmd} ===")
    if out:
        print(out)
    if err:
        print("ERR:", err)
    return status


if __name__ == "__main__":
    client = build_ssh_client()
    run("lspci")
    run("lsmod | grep -i nvidia || true")
    run("ls -la /dev/nvidia* 2>/dev/null || true")
    run("which nvidia-smi 2>/dev/null || true")
    run("dmesg | grep -i -E 'nvidia|gpu|vga' | tail -n 20 2>/dev/null || true")
    client.close()
