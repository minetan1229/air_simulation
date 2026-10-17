"""CI test runner script: runs pytest, prints output, and reports summary."""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.notify_discord import get_webhook_url


def notify_discord(content: str) -> None:
    webhook_url = get_webhook_url()
    if not webhook_url:
        print("Notice: DISCORD_WEBHOOK_URL not configured. Discord notification skipped.", file=sys.stderr)
        return
    try:
        payload = {"content": content}
        req = urllib.request.Request(
            webhook_url,
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
        summary_text = "\n".join(failures[-20:]) if failures else "Pytest failed (see CI logs)"

        # Write to GitHub Step Summary if running in GitHub Actions
        step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary_path:
            try:
                with open(step_summary_path, "a", encoding="utf-8") as f:
                    f.write(f"## ❌ Pytest Failures (Exit code {ret})\n\n")
                    f.write(f"```\n{summary_text}\n```\n\n")
                    f.write("### Detailed Test Output (Last 150 lines)\n\n```\n")
                    f.write("".join(output_lines[-150:]))
                    f.write("\n```\n")
            except Exception as exc:
                print(f"Warning: could not write GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)

        for line in failures:
            escaped = line.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Pytest Failure::{escaped}", flush=True)

        msg = f"⚠️ **CI Test Failed (exit code {ret})**:\n```\n{summary_text[:1800]}\n```"
        notify_discord(msg)

        # Also write summary to file for automated issue creation step
        try:
            with open("pytest_failure_summary.txt", "w", encoding="utf-8") as f:
                f.write(summary_text + "\n\n")
                f.write("Last 60 lines of output:\n")
                f.write("".join(output_lines[-60:]))
        except Exception as exc:
            print(f"Warning: could not write pytest_failure_summary.txt: {exc}", file=sys.stderr)
    else:
        passed = [line.strip() for line in output_lines if "passed in" in line]
        summary_text = passed[-1] if passed else "All tests passed"

        step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary_path:
            try:
                with open(step_summary_path, "a", encoding="utf-8") as f:
                    f.write(f"## ✅ All Pytest Tests Passed\n\n`{summary_text}`\n")
            except Exception as exc:
                print(f"Warning: could not write GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)

        msg = f"✅ **CI All Tests Passed**: {summary_text}"
        notify_discord(msg)

    return ret


if __name__ == "__main__":
    sys.exit(main())
