"""Close CI failure issues when CI passes."""

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


def close_resolved_ci_issues() -> int:
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
        for issue in issues:
            if "pull_request" not in issue and issue.get("title") == title:
                issue_number = issue["number"]
                print(f"Closing resolved CI failure issue #{issue_number}...")

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
    except Exception as exc:
        print(f"Warning: Failed to auto-close CI issue: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(close_resolved_ci_issues())
