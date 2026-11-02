import argparse
import urllib.request
import json
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')


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
url = f'https://api.github.com/repos/{repo}/issues?state=open'
req = urllib.request.Request(url, headers={'User-Agent': 'Python'})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        issues = json.loads(resp.read().decode('utf-8'))
        for issue in issues:
            print(f"=== Issue #{issue['number']}: {issue['title']} ===")
            print(issue.get('body', ''))
            print("-" * 50)
except Exception as e:
    print(f"Error fetching issues: {e}", file=sys.stderr)
    sys.exit(1)
