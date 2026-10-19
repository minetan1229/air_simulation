"""Show the failing jobs and their log output of a workflow run.

Usage::

    python tools/show_run_logs.py [run_id]

Without an argument the most recent failing run is used.  The script needs
``GITHUB_TOKEN`` (or ``GH_TOKEN``) in the environment or in ``.env``.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.notify_discord import load_env_file

load_env_file()
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
REPO = "minetan1229/air_simulation"

HEADERS = {"User-Agent": "Python-Workflow-Checker"}
if TOKEN:
    HEADERS["Authorization"] = f"token {TOKEN}"


def _get(url: str, accept: str = "application/vnd.github+json"):
    headers = dict(HEADERS)
    headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request) as response:
        return response.read()


def _runs() -> list:
    payload = json.loads(_get(f"https://api.github.com/repos/{REPO}/actions/runs?per_page=8"))
    return payload.get("workflow_runs", [])


def _jobs(run_id: int) -> list:
    payload = json.loads(_get(f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=30"))
    return payload.get("jobs", [])


def _job_log(job_id: int) -> str:
    try:
        raw = _get(f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}/logs", "application/vnd.github.v3+json")
    except Exception as exc:  # pragma: no cover - network diagnostics
        return f"(log unavailable: {exc})"
    text = raw.decode("utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-120:])


def main() -> int:
    runs = _runs()
    if not runs:
        print("no runs found")
        return 1
    if len(sys.argv) > 1:
        run_id = int(sys.argv[1])
    else:
        failing = [r for r in runs if r.get("conclusion") == "failure"]
        if not failing:
            print("no failing run found")
            return 0
        run_id = failing[0]["id"]
    print(f"run id={run_id}")
    for job in _jobs(run_id):
        print(
            f"  job {job.get('id')} {job.get('name')}: {job.get('status')}/{job.get('conclusion')}"
        )
        if job.get("conclusion") == "failure":
            print("-" * 70)
            print(_job_log(job["id"]))
            print("-" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
