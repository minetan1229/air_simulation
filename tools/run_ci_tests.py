"""CI test runner script: runs pytest, prints output, and reports summary."""

import os
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.notify_discord import get_webhook_url, send_notification


def notify_discord(status_type: str, title: str, summary: str, extra_info: str = "") -> None:
    """Send a coloured embed notification (verify / fail) to the configured webhook."""
    if not get_webhook_url():
        print("Notice: DISCORD_WEBHOOK_URL not configured. Discord notification skipped.", file=sys.stderr)
        return
    try:
        send_notification(status_type, title, summary, extra_info)
    except Exception as exc:
        print(f"Warning: Failed to send Discord webhook: {exc}", file=sys.stderr)


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-ra",
        "--tb=short",
        "--cov=physics",
        "--cov=api",
        "--cov-report=term-missing:skip-covered",
    ]
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

        notify_discord(
            "fail",
            "CI テスト失敗",
            f"pytest が終了コード {ret} で失敗しました。",
            f"```\n{summary_text[:1500]}\n```",
        )

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

        notify_discord(
            "verify",
            "CI テスト全件パス",
            "pytest が全テストの通過を確認しました。",
            f"**結果:** {summary_text}",
        )

    return ret


if __name__ == "__main__":
    sys.exit(main())
