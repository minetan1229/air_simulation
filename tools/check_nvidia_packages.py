import paramiko
from setup_remote_worker import get_client, run_cmd

client = get_client()
run_cmd(client, "apt-cache search '^nvidia-driver' | head -n 10")
run_cmd(client, "apt-cache search '^nvidia-open' | head -n 10")
run_cmd(client, "apt-cache search '^nvidia-cuda-toolkit' || true")
run_cmd(client, "dpkg -l | grep -E 'linux-headers|nvidia' || true")
client.close()
