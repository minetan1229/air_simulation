#!/usr/bin/env python3
"""Get GPU info from a remote machine via SSH.

Usage:
    python tools/get_remote_gpu.py --host your_remote_host --user <username>
    python tools/get_remote_gpu.py --host your_remote_host --user <username> --key ~/.ssh/id_rsa
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# The GPU detection script to run on the remote machine
REMOTE_SCRIPT = '''
import sys
sys.path.insert(0, "/path/to/project")  # Adjust if needed

try:
    from physics.parallel_engine import get_gpu_info
    info = get_gpu_info()
    print(json.dumps(info))
except Exception as e:
    print(json.dumps({"error": str(e), "available": False}))
'''


def main():
    parser = argparse.ArgumentParser(description="Get GPU info from remote machine via SSH")
    parser.add_argument("--host", required=True, help="Remote host IP or hostname")
    parser.add_argument("--user", required=True, help="SSH username")
    parser.add_argument("--key", help="Path to SSH private key")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--project-path", default="~/paper_airplanes/simulation/v3", help="Project path on remote")
    args = parser.parse_args()

    # Build the SSH command
    ssh_cmd = ["ssh"]
    if args.key:
        ssh_cmd.extend(["-i", args.key])
    ssh_cmd.extend(["-p", str(args.port), f"{args.user}@{args.host}"])

    # The command to run on remote
    remote_cmd = f"""
cd {args.project_path}
python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from physics.parallel_engine import get_gpu_info
    import json
    info = get_gpu_info()
    print(json.dumps(info))
except Exception as e:
    print(json.dumps({{'error': str(e), 'available': False}}))
"
"""

    full_cmd = ssh_cmd + [remote_cmd]

    print(f"Connecting to {args.user}@{args.host}:{args.port}...")
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"SSH error: {result.stderr}", file=sys.stderr)
            return 1

        # Parse the JSON output (last line should be the JSON)
        output_lines = result.stdout.strip().split('\n')
        for line in reversed(output_lines):
            line = line.strip()
            if line.startswith('{'):
                try:
                    info = json.loads(line)
                    print(json.dumps(info, indent=2))
                    return 0
                except json.JSONDecodeError:
                    continue

        print("No valid JSON output from remote", file=sys.stderr)
        print(f"stdout: {result.stdout}", file=sys.stderr)
        return 1

    except subprocess.TimeoutExpired:
        print("SSH connection timed out", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
