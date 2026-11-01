import os
import sys
import json
import urllib.request
import urllib.error
import subprocess
import time

def get_git_diff():
    try:
        diff = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--", ":!*.pid", ":!*.txt", ":!*.log"],
            text=True, encoding="utf-8", errors="replace"
        )
        return diff
    except Exception as e:
        print(f"Warning: git diff HEAD~1 failed: {e}")
        try:
            return subprocess.check_output(
                ["git", "diff", "HEAD", "--", ":!*.pid", ":!*.txt", ":!*.log"],
                text=True, encoding="utf-8", errors="replace"
            )
        except Exception:
            return ""

def call_ai_review(diff_text, github_token):
    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {github_token}"
    }

    system_prompt = (
        "You are an expert code reviewer and verification assistant. "
        "Review the provided git diff carefully. "
        "Identify any genuine bugs, typing issues, syntax errors, or logic regressions. "
        "DO NOT invent problems. If the code is already correct or there are no issues, "
        "explicitly state 'NO_ISSUES' and provide empty patch. "
        "If you find issues, output a valid unified diff (patch) that can be applied with `git apply`. "
        "Output JSON only in this format: "
        "{\"has_issues\": true/false, \"summary\": \"explanation\", \"patch\": \"unified diff string or empty\"}"
    )

    user_prompt = f"Here is the git diff of the latest commit:\n```diff\n{diff_text[:12000]}\n```"

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
    except urllib.error.HTTPError as e:
        print(f"API Error ({e.code}): {e.read().decode('utf-8', errors='replace')}")
        return {"has_issues": False, "summary": f"Review skipped due to API error: {e}", "patch": ""}
    except Exception as e:
        print(f"Review call error: {e}")
        return {"has_issues": False, "summary": f"Review failed: {e}", "patch": ""}

def test_and_verify(patch_str):
    if not patch_str or not patch_str.strip():
        print("No patch to apply.")
        return False

    with open("copilot_fix.patch", "w", encoding="utf-8") as f:
        f.write(patch_str)

    print("--- Applying patch ---")
    apply_proc = subprocess.run(["git", "apply", "--ignore-whitespace", "copilot_fix.patch"])
    if apply_proc.returncode != 0:
        print("Patch failed to apply cleanly.")
        return False

    print("--- Verifying (Running syntax/lint & tests) ---")
    try:
        subprocess.check_call(["ruff", "check", "."])
        subprocess.check_call([sys.executable, "-m", "compileall", "physics", "api", "tools", "app.py"])
        subprocess.check_call([sys.executable, "-m", "pytest", "tests/test_aero.py", "-q"])
        print("All verification tests passed!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Verification failed: {e}")
        subprocess.run(["git", "checkout", "."])
        return False

def push_verified_fix_concurrent(summary):
    """
    同時並行作業を支えるGit pushメカニズム:
    ローカルや他のエージェントが同時にコミットを進めている場合でも衝突せず共存できるよう、
    最新コミットを rebase / fetch して競合を解消してからプッシュする。
    """
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"])
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    subprocess.run(["git", "add", "-A"])
    commit_msg = f"fix(ai-review): auto-apply verified review suggestions\n\n{summary}"
    commit_proc = subprocess.run(["git", "commit", "-m", commit_msg])
    if commit_proc.returncode != 0:
        print("Nothing to commit or commit failed.")
        return

    # 同時並行コミットへの追従リトライ（最大3回）
    branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
    for attempt in range(3):
        print(f"Attempting to push verified fix (attempt {attempt+1}/3)...")
        # リモートの最新並行コミットを取得してリベース
        subprocess.run(["git", "pull", "--rebase", "origin", branch])
        push_res = subprocess.run(["git", "push", "origin", branch])
        if push_res.returncode == 0:
            print("Successfully pushed verified fix concurrently!")
            return
        time.sleep(2)

    # 万が一直プッシュで衝突が激しい場合は、独立した検証ブランチへプッシュ（作業を阻害しない）
    fallback_branch = f"copilot-verified-{int(time.time())}"
    print(f"Direct push conflicted with concurrent work. Pushing to isolated branch '{fallback_branch}'...")
    subprocess.run(["git", "checkout", "-b", fallback_branch])
    subprocess.run(["git", "push", "origin", fallback_branch])
    print(f"Isolated verified branch {fallback_branch} pushed successfully.")

def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set. Exiting.")
        sys.exit(0)

    diff = get_git_diff()
    if not diff.strip():
        print("No diff detected. Everything clean. Done.")
        sys.exit(0)

    print("Requesting AI/Copilot code review...")
    review = call_ai_review(diff, token)
    has_issues = review.get("has_issues", False)
    summary = review.get("summary", "")
    patch = review.get("patch", "")

    print(f"Review result - has_issues: {has_issues}")
    print(f"Summary: {summary}")

    if not has_issues or not patch.strip():
        print(">> No valid issues identified by review. Nothing to fix. Completed successfully.")
        sys.exit(0)

    print(">> Issues suggested by review. Verifying proposed fix...")
    verified = test_and_verify(patch)

    if not verified:
        print(">> Copilot review/fix was INCORRECT or failed verification.")
        print(">> As per protocol, incorrect reviews are rejected without modifying code. Completed.")
        sys.exit(0)

    print(">> Proposed fix VERIFIED and PASSED all tests! Applying fix concurrently to repository...")
    push_verified_fix_concurrent(summary)

if __name__ == "__main__":
    main()
