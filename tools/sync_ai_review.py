import os
import sys
import subprocess
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.notify_discord import load_env_file

load_env_file()
token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

def fetch_latest_remote():
    try:
        subprocess.run(["git", "fetch", "origin"], check=True, capture_output=True, text=True)
    except Exception as e:
        print(f"Warning: git fetch failed: {e}")

def check_remote_ai_review_updates():
    fetch_latest_remote()
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], text=True
        ).strip()
        if not branch:
            return {"has_updates": False, "commits": []}

        remote_ref = f"origin/{branch}"
        log_out = subprocess.check_output(
            ["git", "log", f"HEAD..{remote_ref}", "--oneline"],
            text=True
        ).strip()

        if not log_out:
            return {"has_updates": False, "commits": []}

        commits = log_out.splitlines()
        ai_commits = [c for c in commits if "fix(ai-review)" in c or "fix(auto):" in c]

        return {
            "has_updates": len(ai_commits) > 0,
            "all_commits": commits,
            "ai_commits": ai_commits,
            "remote_ref": remote_ref
        }
    except Exception as e:
        print(f"Error checking remote review updates: {e}")
        return {"has_updates": False, "commits": []}

def get_open_auto_prs(repo):
    if not token:
        return []
    url = f"https://api.github.com/repos/{repo}/pulls?state=open"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            prs = json.loads(resp.read().decode())
            auto_prs = [p for p in prs if p.get("head", {}).get("ref", "").startswith("auto-fix/")]
            return auto_prs
    except Exception as e:
        print(f"Error listing open PRs: {e}")
        return []

def is_pr_healthy_to_merge(repo, pr):
    """
    PRが安全にマージ可能か（問題がないか）を多角的に検証:
    1. mergeable_state が 'dirty' (コンフリクト) または 'blocked' ではないこと
    2. 最新コミットの CI Check Runs / 結合ステータスが failed でないこと
    """
    if not token:
        return False

    head_sha = pr.get("head", {}).get("sha")
    pr_num = pr.get("number")
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    # 1. コンフリクト・マージ可否の詳細を取得
    detail_url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}"
    try:
        req = urllib.request.Request(detail_url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            detail = json.loads(resp.read().decode())
            mergeable = detail.get("mergeable")
            mergeable_state = detail.get("mergeable_state")
            if mergeable is False or mergeable_state == "dirty":
                print(f"    [PR #{pr_num}] Has merge conflicts (mergeable_state: {mergeable_state}). Cannot merge.")
                return False
    except Exception as e:
        print(f"    [PR #{pr_num}] Warning checking mergeable detail: {e}")

    # 2. Check Runs のステータス確認
    if head_sha:
        check_url = f"https://api.github.com/repos/{repo}/commits/{head_sha}/check-runs"
        try:
            req = urllib.request.Request(check_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                check_data = json.loads(resp.read().decode())
                check_runs = check_data.get("check_runs", [])
                for cr in check_runs:
                    conclusion = cr.get("conclusion")
                    status = cr.get("status")
                    cr_name = cr.get("name")
                    if conclusion in ["failure", "cancelled", "timed_out"]:
                        print(f"    [PR #{pr_num}] CI check failed: '{cr_name}' ({conclusion}). Rejecting merge.")
                        return False
                    if status in ["in_progress", "queued"]:
                        print(f"    [PR #{pr_num}] CI check '{cr_name}' is still in progress. Waiting before merge.")
                        return False
        except Exception as e:
            print(f"    [PR #{pr_num}] Warning checking CI runs: {e}")

    print(f"    [PR #{pr_num}] Health checks passed. Safe to merge!")
    return True

def merge_pr_on_github(repo, pr_number):
    if not token:
        return False
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/merge"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json", "Content-Type": "application/json"}
    payload = {
        "commit_title": f"Merge pull request #{pr_number} from auto-solver",
        "merge_method": "squash"
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("merged", False)
    except Exception as e:
        print(f"Error merging PR #{pr_number}: {e}")
        return False

def startup_agent_sync():
    """
    AI Agent 起動時・作業開始時に実行するプロトコル:
    1. GitHub上の自動解決PR（auto-fix/*）のヘルスチェック（CI全通過＆コンフリクトなし＝問題なし）
    2. 問題のないPRのみ自動マージ（問題があればマージせず維持）
    3. マージ後のリモート最新状態をローカルに自動同期
    """
    print("=== [AI Agent Startup Sync & Health-Verified PR Merge] ===")
    repo = os.environ.get("GITHUB_REPOSITORY", "minetan1229/air_simulation")

    # 1. 自動PRの確認と健全性検証マージ
    open_prs = get_open_auto_prs(repo)
    if open_prs:
        print(f"[Startup] Found {len(open_prs)} open auto-solve PR(s). Checking health before merge...")
        for pr in open_prs:
            pr_num = pr["number"]
            pr_title = pr["title"]
            print(f" -> Inspecting PR #{pr_num}: {pr_title}")
            if is_pr_healthy_to_merge(repo, pr):
                print(f"    PR #{pr_num} has NO ISSUES. Merging...")
                merged = merge_pr_on_github(repo, pr_num)
                if merged:
                    print(f"    PR #{pr_num} successfully merged!")
                else:
                    print(f"    PR #{pr_num} merge attempt failed.")
            else:
                print(f"    PR #{pr_num} has failing checks or unresolved issues. Skipped merge.")
    else:
        print("[Startup] No open auto-solve PRs to process.")

    # 2. リモートの最新変更をローカルに反映
    info = check_remote_ai_review_updates()
    if info.get("has_updates"):
        print(f"[Startup] Pulling remote updates: {info['ai_commits']}")
        try:
            status = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
            if status:
                print("[Startup] Uncommitted changes detected. Skipping pull to avoid overwrite.")
            else:
                subprocess.run(["git", "pull", "--ff-only"], check=True)
            print("[Startup] Local workspace updated to latest remote head.")
        except Exception as e:
            print(f"[Startup] Pull failed: {e}")
    else:
        print("[Startup] Local workspace is fully up to date with remote.")
    print("==========================================================")
    return True

if __name__ == "__main__":
    startup_agent_sync()
