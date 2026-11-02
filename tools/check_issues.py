import argparse
import json
import urllib.request
import sys
import os

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

headers = {"User-Agent": "Python-Issue-Checker"}
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
url = f"https://api.github.com/repos/{repo}/issues?state=all"
req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.loads(res.read().decode())
        print(f"Total items fetched: {len(data)}")
        for x in data:
            item_type = "PR" if "pull_request" in x else "Issue"
            state = x.get('state')
            num = x.get('number')
            title = x.get('title')
            print(f"#{num} [{state}] {item_type}: {title}")
            if state == "open":
                body = x.get('body') or ''
                print(f"   Body: {body[:300]}")
except Exception as e:
    print(f"Error fetching issues: {e}", file=sys.stderr)
    sys.exit(1)
