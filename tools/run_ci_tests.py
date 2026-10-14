"""CI test runner script: runs pytest, prints output, and reports summary."""

import json
import subprocess
import sys
import urllib.request

DISCORD_WEBHOOK = (
    "https://discord.com/api/webhooks/1559481959061782528/"
    "n8lndbZf9zWCjv9LRs9CPL_XcavqKWkvR1bD8C8ffIevAXymTS8f9NtJZGjdDU6OcVIE"
)


def notify_discord(content: str) -> None:
    try:
        payload = {"content": content}
        req = urllib.request.Request(
            DISCORD_WEBHOOK,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        print(f"Warning: Failed to send Discord webhook: {exc}", file=sys.stderr)


def main() -> int:
    cmd = [sys.executable, "-m", "pytest", "-ra", "--tb=short"]
    print(f"Running: {' '.join(cmd)}", flush=True)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    output_lines = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        output_lines.append(line)

    ret = proc.wait()
    print(f"\nPytest finished with exit code {ret}", flush=True)

    if ret != 0:
        failures = [
            line.strip()
            for line in output_lines
            if any(k in line for k in ("FAILED ", "ERROR ", "failed in", "failures in"))
        ]
        summary_text = "\n".join(failures[-15:]) if failures else "Pytest failed (see CI logs)"
        msg = f"⚠️ **CI Test Failed (exit code {ret})**:\n```\n{summary_text[:1800]}\n```"
        notify_discord(msg)
    else:
        passed = [line.strip() for line in output_lines if "passed in" in line]
        summary_text = passed[-1] if passed else "All tests passed"
        msg = f"✅ **CI All Tests Passed**: {summary_text}"
        notify_discord(msg)

    return ret


if __name__ == "__main__":
    sys.exit(main())
