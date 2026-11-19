import json
import subprocess
import sys
import re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def run(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
    return res.stdout, res.stderr, res.returncode

def main():
    out, err, code = run("gh issue list --state closed --limit 300 --repo minetan1229/air_simulation --json number,title,closedAt,comments,body")
    if code != 0:
        print("Error:", err)
        return
    issues = json.loads(out)

    fake_commits = []
    no_commit = []
    resolved_real = []

    for iss in issues:
        num = iss["number"]
        title = iss["title"]
        comments = iss.get("comments", [])
        body = iss.get("body", "")

        shas = []
        for c in comments:
            shas.extend(re.findall(r"\b([0-9a-f]{7,40})\b", c.get("body", "")))

        real_shas = []
        fake_shas = []
        for s in set(shas):
            _, _, rc = run(f"git cat-file -e {s}")
            if rc == 0:
                real_shas.append(s)
            else:
                fake_shas.append(s)

        if fake_shas and not real_shas:
            fake_commits.append((num, title, fake_shas))
        elif not shas:
            no_commit.append((num, title))
        elif real_shas:
            resolved_real.append((num, title, real_shas))

    print(f"=== Total Closed Issues: {len(issues)} ===")
    print(f"=== Issues with Fake Commits: {len(fake_commits)} ===")
    for num, title, fshas in sorted(fake_commits):
        print(f"#{num}: {title} -> Fake SHAs: {fshas}")

    print(f"\n=== Issues Closed without Any Commit SHA: {len(no_commit)} ===")
    for num, title in sorted(no_commit, reverse=True)[:40]:
        print(f"#{num}: {title}")

if __name__ == "__main__":
    main()
