"""Close CI failure issues when CI passes."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


def get_env_var(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def github_api_request(
    endpoint: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    token: str = "",
) -> Any:
    api_url = f"https://api.github.com{endpoint}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CI-Failure-Reporter",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(api_url, data=body_bytes, headers=headers, method=method)

    with urllib.request.urlopen(req, timeout=15) as resp:
        res_data = resp.read().decode("utf-8")
        if res_data:
            return json.loads(res_data)
        return None


def close_resolved_ci_issues(
    dry_run: bool = False,
    yes_confirm: bool = False,
) -> int:
    token = get_env_var("GITHUB_TOKEN")
    if not token:
        print("Notice: GITHUB_TOKEN not configured. Skipping closing issues.", file=sys.stderr)
        return 0

    repo = get_env_var("GITHUB_REPOSITORY")
    if not repo:
        print("Warning: GITHUB_REPOSITORY is empty. Cannot close issues.", file=sys.stderr)
        return 0

    workflow_name = get_env_var("GITHUB_WORKFLOW", "CI Verification")
    ref = get_env_var("GITHUB_REF_NAME", get_env_var("GITHUB_REF", "unknown"))
    run_number = get_env_var("GITHUB_RUN_NUMBER", "unknown")
    run_id = get_env_var("GITHUB_RUN_ID", "unknown")
    sha = get_env_var("GITHUB_SHA", "unknown")[:7]
    server_url = get_env_var("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server_url}/{repo}/actions/runs/{run_id}"

    title = f"🚨 CI Failure: {workflow_name} on {ref}"

    endpoint = f"/repos/{repo}/issues?state=open&labels=ci-failure&per_page=50"
    try:
        issues: List[Dict[str, Any]] = github_api_request(endpoint, method="GET", token=token)
        matched = 0
        for issue in issues:
            if "pull_request" not in issue and issue.get("title") == title:
                issue_number = issue["number"]
                matched += 1
                print(f"対象: CI失敗Issue #{issue_number} ({issue.get('title')})")

                if dry_run:
                    print(f"  [dry-run] 閉じる対象として一覧表示のみ（API への書き込みなし）: #{issue_number}")
                    continue

                if not yes_confirm:
                    print(f"  [スキップ] --yes 未指定のため閉じません（安全側）: #{issue_number}")
                    continue

                # Post comment
                comment_endpoint = f"/repos/{repo}/issues/{issue_number}/comments"
                comment_body = {
                    "body": (
                        f"### ✅ CI Verification Passed in run [#{run_number}]({run_url}) (`{sha}`)\n\n"
                        f"All checks and pytest suites succeeded. Closing issue automatically."
                    )
                }
                github_api_request(comment_endpoint, method="POST", data=comment_body, token=token)

                # Close issue
                close_endpoint = f"/repos/{repo}/issues/{issue_number}"
                close_body = {"state": "closed", "state_reason": "completed"}
                github_api_request(close_endpoint, method="PATCH", data=close_body, token=token)
                print(f"Issue #{issue_number} closed successfully.")
        if dry_run:
            print(f"[dry-run] 閉じる対象: {matched} 件（副作用なし）")
        elif not yes_confirm:
            print(f"[安全側] 閉じる対象 {matched} 件を検出しましたが、--yes 未指定のため何も実行しませんでした。")
    except Exception as exc:
        print(f"Warning: Failed to auto-close CI issue: {exc}", file=sys.stderr)
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Close resolved CI failure issues.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="閉じる対象を一覧表示するのみで、実際のクローズ/コメント API は呼び出さない。",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="確認プロンプトをスキップして、実際に Issue をクローズする。",
    )
    args = parser.parse_args()

    return close_resolved_ci_issues(dry_run=args.dry_run, yes_confirm=args.yes)


if __name__ == "__main__":
    sys.exit(main())
