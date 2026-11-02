import os
import sys
import json
import urllib.request
import urllib.error
import subprocess
import time

def post_github_comment(repo, commit_sha, comment_body, token):
    """コミットに対してCopilotレビュー結果をコメント返信する"""
    if not token or not repo or not commit_sha:
        return
    url = f"https://api.github.com/repos/{repo}/commits/{commit_sha}/comments"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }
    payload = {"body": comment_body}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"Posted review comment to commit {commit_sha}: {resp.status}")
    except Exception as e:
        print(f"Failed to post commit comment: {e}")

def write_step_summary(markdown_body):
    """GitHub Actions の Job Summary に出力する"""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and os.path.exists(summary_path):
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(f"\n{markdown_body}\n")
        except Exception as e:
            print(f"Failed to write step summary: {e}")

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
    # LLM API 設定（OpenRouter / OpenAI / Azure / 互換エンドポイント対応）
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("LLM_MODEL", "")

    if not api_key:
        # APIキー未設定時は明示的にスキップを返し、成功と誤認させない
        print("Notice: LLM_API_KEY (or OPENROUTER_API_KEY / OPENAI_API_KEY) is not configured.")
        return {
            "has_issues": False,
            "status": "skipped",
            "summary": "Review skipped: LLM_API_KEY secret is not configured in repository.",
            "patch": ""
        }

    # デフォルトは OpenRouter (多様なモデル/無料・安価枠が利用可能)
    if not base_url:
        base_url = "https://openrouter.ai/api/v1"
    if not model:
        model = "openai/gpt-4o-mini"

    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
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
        "model": model,
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
            result = json.loads(content)
            result["status"] = "success"
            return result
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8', errors='replace')
        print(f"API Error ({e.code}): {err_msg}")
        return {
            "has_issues": False,
            "status": "error",
            "summary": f"Review failed (API {e.code}): {err_msg[:200]}",
            "patch": ""
        }
    except Exception as e:
        print(f"Review call error: {e}")
        return {
            "has_issues": False,
            "status": "error",
            "summary": f"Review call failed: {e}",
            "patch": ""
        }

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
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"])
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    subprocess.run(["git", "add", "-A"])
    commit_msg = f"fix(ai-review): auto-apply verified review suggestions\n\n{summary}"
    commit_proc = subprocess.run(["git", "commit", "-m", commit_msg])
    if commit_proc.returncode != 0:
        print("Nothing to commit or commit failed.")
        return

    branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
    for attempt in range(3):
        print(f"Attempting to push verified fix (attempt {attempt+1}/3)...")
        subprocess.run(["git", "pull", "--rebase", "origin", branch])
        push_res = subprocess.run(["git", "push", "origin", branch])
        if push_res.returncode == 0:
            print("Successfully pushed verified fix concurrently!")
            return
        time.sleep(2)

    fallback_branch = f"copilot-verified-{int(time.time())}"
    print(f"Direct push conflicted with concurrent work. Pushing to isolated branch '{fallback_branch}'...")
    subprocess.run(["git", "checkout", "-b", fallback_branch])
    subprocess.run(["git", "push", "origin", fallback_branch])
    print(f"Isolated verified branch {fallback_branch} pushed successfully.")

def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    commit_sha = os.environ.get("GITHUB_SHA", "")

    if not token:
        print("GITHUB_TOKEN is not set. Exiting.")
        sys.exit(0)

    try:
        if not commit_sha:
            commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        commit_sha = ""

    diff = get_git_diff()
    if not diff.strip():
        msg = "### 🤖 Copilot Review: No Changes Detected\n差分が検出されなかったため、レビューをスキップしました。"
        post_github_comment(repo, commit_sha, msg, token)
        write_step_summary(msg)
        sys.exit(0)

    print("Requesting AI/Copilot code review...")
    review = call_ai_review(diff, token)
    status = review.get("status", "success")
    has_issues = review.get("has_issues", False)
    summary = review.get("summary", "")
    patch = review.get("patch", "")

    print(f"Review result - status: {status}, has_issues: {has_issues}")
    print(f"Summary: {summary}")

    if status == "skipped":
        msg = (
            f"### 🤖 Copilot Review: ℹ️ スキップ (Skipped)\n\n"
            f"**コミット:** `{commit_sha[:7]}`\n\n"
            f"> {summary}\n\n"
            f"AI レビューを実行するには、リポジトリの Secrets に `LLM_API_KEY`（OpenRouter または OpenAI / 互換キー）を設定してください。"
        )
        print(msg)
        post_github_comment(repo, commit_sha, msg, token)
        write_step_summary(msg)
        sys.exit(0)

    if status == "error":
        msg = (
            f"### 🤖 Copilot Review: ❌ API エラー (Review Failed)\n\n"
            f"**コミット:** `{commit_sha[:7]}`\n\n"
            f"> {summary}\n\n"
            f"レビュー API 呼び出しに失敗しました。エンドポイント設定または API キーを確認してください。"
        )
        print(msg)
        post_github_comment(repo, commit_sha, msg, token)
        write_step_summary(msg)
        sys.exit(1)

    if not has_issues or not patch.strip():
        msg = (
            f"### 🤖 Copilot Review: ✅ 問題なし (All Clean)\n\n"
            f"**コミット:** `{commit_sha[:7]}`\n\n"
            f"> {summary or '問題・不具合・型エラー等は検出されませんでした。現在の実装で問題ありません。'}"
        )
        print(msg)
        post_github_comment(repo, commit_sha, msg, token)
        write_step_summary(msg)
        sys.exit(0)

    print(">> Issues suggested by review. Verifying proposed fix...")
    verified = test_and_verify(patch)

    if not verified:
        msg = (
            f"### 🤖 Copilot Review: ⚠️ 修正案棄却 (Verification Failed)\n\n"
            f"**コミット:** `{commit_sha[:7]}`\n\n"
            f"Copilot から以下の修正案が出されましたが、CI テスト検証に失敗したため**提案を棄却**しました（コードは変更されず完了）。\n\n"
            f"- **指摘内容:** {summary}\n"
            f"- **判定:** 不一致 / テスト不合格（テキサスの狙撃兵の誤謬防止ガード発動）"
        )
        print(msg)
        post_github_comment(repo, commit_sha, msg, token)
        write_step_summary(msg)
        sys.exit(0)

    msg = (
        f"### 🤖 Copilot Review: 🚀 修正案検証合格・自動反映 (Verified & Applied)\n\n"
        f"**コミット:** `{commit_sha[:7]}`\n\n"
        f"Copilot の修正案が全テスト検証に合格したため、リポジトリに自動反映しました。\n\n"
        f"- **修正内容:** {summary}"
    )
    print(msg)
    post_github_comment(repo, commit_sha, msg, token)
    write_step_summary(msg)
    push_verified_fix_concurrent(summary)

if __name__ == "__main__":
    main()
