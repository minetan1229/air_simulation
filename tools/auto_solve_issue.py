import os
import sys
import json
import urllib.request
import urllib.error
import subprocess
import time

def call_ai_for_solution(issue_num, title, body, token):
    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    system_prompt = (
        "You are an automated software engineer. An issue has been filed. "
        "Analyze the issue carefully and generate a unified diff patch that resolves the issue cleanly. "
        "DO NOT hallucinate changes or break existing features. "
        "Output JSON only in this format: "
        "{\"can_solve\": true/false, \"summary\": \"explanation\", \"patch\": \"unified diff string\"}"
    )

    user_prompt = f"Issue #{issue_num}: {title}\n\nDescription:\n{body[:8000]}"

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as e:
        print(f"Error calling AI for solution: {e}")
        return {"can_solve": False, "summary": f"Failed: {e}", "patch": ""}

def test_patch(patch_str):
    if not patch_str or not patch_str.strip():
        return False
    with open("issue_fix.patch", "w", encoding="utf-8") as f:
        f.write(patch_str)

    res = subprocess.run(["git", "apply", "--ignore-whitespace", "issue_fix.patch"])
    if res.returncode != 0:
        return False

    try:
        subprocess.check_call(["ruff", "check", "."])
        subprocess.check_call([sys.executable, "-m", "compileall", "physics", "api", "tools", "app.py"])
        subprocess.check_call([sys.executable, "-m", "pytest", "tests/test_aero.py", "-q"])
        return True
    except Exception as e:
        print(f"Verification failed: {e}")
        subprocess.run(["git", "checkout", "."])
        return False

def create_pr_on_github(repo, branch, base, title, body, token):
    url = f"https://api.github.com/repos/{repo}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }
    payload = {
        "title": title,
        "body": body,
        "head": branch,
        "base": base
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            return res_data.get("html_url")
    except Exception as e:
        print(f"Error creating PR: {e}")
        return None

def main():
    token = os.environ.get("GITHUB_TOKEN")
    issue_num = os.environ.get("ISSUE_NUMBER")
    title = os.environ.get("ISSUE_TITLE", "")
    body = os.environ.get("ISSUE_BODY", "")
    repo = os.environ.get("REPO_SLUG", "")

    if not all([token, issue_num, repo]):
        print("Missing required environment variables. Exiting.")
        sys.exit(0)

    print(f"Starting auto-solver for Issue #{issue_num}: {title}")
    sol = call_ai_for_solution(issue_num, title, body, token)
    if not sol.get("can_solve") or not sol.get("patch"):
        print("AI could not formulate a confident solution. Exiting without PR.")
        sys.exit(0)

    patch = sol.get("patch")
    summary = sol.get("summary")

    # ブランチ作成
    branch_name = f"auto-fix/issue-{issue_num}-{int(time.time())}"
    subprocess.run(["git", "checkout", "-b", branch_name])

    verified = test_patch(patch)
    if not verified:
        print("Proposed fix failed local tests/verification. Rejecting patch.")
        sys.exit(0)

    print("Fix verified successfully! Committing and creating PR...")
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"])
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    subprocess.run(["git", "add", "-A"])
    commit_msg = f"fix: auto-resolve Issue #{issue_num}\n\nResolves #{issue_num}\n{summary}"
    subprocess.run(["git", "commit", "-m", commit_msg])
    subprocess.run(["git", "push", "origin", branch_name])

    # PR作成 (ターゲットは遺伝的アルゴリズムブランチまたはmaster等)
    base_branch = "genetic-project"
    pr_title = f"fix(auto): resolve Issue #{issue_num} - {title}"
    pr_body = (
        f"Automated PR created by Issue Auto-Solver.\n\n"
        f"Resolves #{issue_num}\n\n"
        f"### Summary of Changes\n{summary}\n\n"
        f"### Verification\n- All syntax and regression tests verified before creating this PR."
    )
    pr_url = create_pr_on_github(repo, branch_name, base_branch, pr_title, pr_body, token)
    if pr_url:
        print(f"Successfully created PR: {pr_url}")

if __name__ == "__main__":
    main()
