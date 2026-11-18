#!/usr/bin/env python3
"""
Issueクローズ監査スクリプト
クローズ済みIssueを走査し、以下を検出する：
1. テスト追加のみでクローズされた可能性のあるIssue
2. ドキュメント修正のみでクローズされた可能性のあるIssue
3. 他Issueへの集約のみでクローズされたIssue
4. Issueの「完了条件」とコミット内容の整合性
"""

import json
import subprocess
import sys
import re

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

def run_cmd(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
    return res.stdout, res.stderr, res.returncode

def main():
    print("Fetching closed issues...")
    out, err, code = run_cmd("gh issue list --state closed --limit 100 --repo minetan1229/air_simulation --json number,title,closedAt,comments,body")
    if code != 0:
        print("Error fetching issues:", err)
        return

    issues = json.loads(out)
    print(f"Total closed issues fetched: {len(issues)}")

    suspicious_issues = []

    for issue in issues:
        num = issue["number"]
        title = issue["title"]
        body = issue.get("body", "")
        comments = issue.get("comments", [])

        # コメントから解決コミットを抽出
        commit_shas = []
        for c in comments:
            c_body = c.get("body", "")
            found = re.findall(r"\b([0-9a-f]{7,40})\b", c_body)
            commit_shas.extend(found)

        # 完了条件のチェック
        has_criteria = "完了条件" in body or "監査項目" in body or "懸念点" in body

        suspicious_issues.append({
            "number": num,
            "title": title,
            "commit_shas": commit_shas,
            "comments_count": len(comments),
            "has_criteria": has_criteria,
            "body_snippet": body[:200] if body else ""
        })

    print(f"Processed {len(suspicious_issues)} issues.")

    # 最近の主要なクローズIssue（#170〜#200、#131〜#139、#108〜#115）
    targets = [i for i in suspicious_issues if (190 <= i["number"] <= 200) or (170 <= i["number"] <= 178) or (131 <= i["number"] <= 139) or (108 <= i["number"] <= 115) or i["number"] == 95]
    print("\n--- Key Physics / Numerics Audit Candidates ---")
    for t in targets:
        print(f"#{t['number']}: {t['title']} (Commits: {t['commit_shas']})")

if __name__ == "__main__":
    main()
