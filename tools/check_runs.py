import argparse
import json
import os
import sys
import urllib.request

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.notify_discord import load_env_file

load_env_file()
token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

headers = {"User-Agent": "Python-Workflow-Checker"}
if token:
    headers["Authorization"] = f"token {token}"


def resolve_repo_slug() -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args, _ = parser.parse_known_args()
    repo = (args.repo or "").strip()
    if not repo:
        print("Error: GITHUB_REPOSITORY 環境変数または --repo owner/repo が必要です。", file=sys.stderr)
        sys.exit(1)
    return repo


repo = resolve_repo_slug()
url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=6"
req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.loads(res.read().decode())
        runs = data.get("workflow_runs", [])
        print(f"Total workflow runs: {len(runs)}")
        for r in runs:
            r_num = r.get("run_number")
            r_id = r.get("id")
            name = r.get("name")
            status = r.get("status")
            conclusion = r.get("conclusion")
            branch = r.get("head_branch")
            commit = (r.get("head_sha") or "")[:7]
            event = r.get("event")
            print(f"Run #{r_num} [{name}] id={r_id} status={status} conclusion={conclusion} branch={branch} commit={commit} event={event}")
except Exception as e:
    print(f"Error fetching runs: {e}", file=sys.stderr)
    sys.exit(1)
