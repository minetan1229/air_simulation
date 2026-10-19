"""Work report collection for Discord webhook notifications.

Collects two independent sections used to enrich autonomous-loop notifications:

1. Git working-tree snapshot -> operation locations (touched files) and change counts.
2. GitHub integration status   -> push state, latest CI conclusion, open PR / Issue counts.

All collectors are non-destructive and fail soft: any error degrades the report to a
partial value instead of raising, so a notification is never blocked by reporting.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

GIT_TIMEOUT = 10
API_TIMEOUT = 6
MAX_LISTED_FILES = 12
MAX_FIELD_VALUE = 1000
MAX_SUMMARY_VALUE = 400

CATEGORY_LABELS = {
    "added": "追加",
    "modified": "変更",
    "deleted": "削除",
    "renamed": "移動",
    "copied": "コピー",
    "untracked": "未追跡",
    "conflict": "競合",
}

CATEGORY_MARKS = {
    "added": "+",
    "modified": "~",
    "deleted": "-",
    "renamed": ">",
    "copied": "=",
    "untracked": "?",
    "conflict": "!",
}

CATEGORY_ORDER = ("added", "modified", "deleted", "renamed", "copied", "conflict", "untracked")

REMOTE_PATTERNS = (
    re.compile(r"^https?://(?:[^@/]+@)?[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^git@[^:]+:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^ssh://git@[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
)


def _run_git(args: List[str], cwd: Path) -> Optional[str]:
    """Run a git command and return stdout, or None when git is unavailable/fails."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _parse_status_entries(raw: str) -> List[Tuple[str, str]]:
    """Parse `git status --porcelain -z` output into (code, path) tuples."""
    entries = raw.split("\0")
    parsed: List[Tuple[str, str]] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        code = entry[:2]
        path = entry[3:] if len(entry) > 3 else ""
        if "R" in code or "C" in code:
            index += 1  # rename/copy entries are followed by the original path
        if path:
            parsed.append((code, path))
    return parsed


def _categorize(code: str) -> str:
    """Map a porcelain status code to a coarse change category."""
    if code == "??":
        return "untracked"
    if "U" in code or code in ("AA", "DD"):
        return "conflict"
    if "D" in code:
        return "deleted"
    if "A" in code:
        return "added"
    if "R" in code:
        return "renamed"
    if "C" in code:
        return "copied"
    return "modified"


def _parse_numstat(raw: str) -> Tuple[int, int]:
    """Return (insertions, deletions) summed over `git diff --numstat` output."""
    insertions = 0
    deletions = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed = parts[0], parts[1]
        if added.isdigit():
            insertions += int(added)
        if removed.isdigit():
            deletions += int(removed)
    return insertions, deletions


def collect_git_snapshot(repo_root: Path) -> Dict[str, Any]:
    """Collect branch, push state, changed files and change counts of the working tree."""
    snapshot: Dict[str, Any] = {
        "is_repo": False,
        "branch": "",
        "upstream": "",
        "ahead": 0,
        "behind": 0,
        "head_sha": "",
        "head_subject": "",
        "remote_url": "",
        "files": [],
        "counts": {key: 0 for key in CATEGORY_LABELS},
        "insertions": 0,
        "deletions": 0,
        "total_files": 0,
    }

    inside = _run_git(["rev-parse", "--is-inside-work-tree"], repo_root)
    if inside is None or inside.strip() != "true":
        return snapshot
    snapshot["is_repo"] = True

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    snapshot["branch"] = (branch or "").strip()

    head = _run_git(["rev-parse", "--short", "HEAD"], repo_root)
    snapshot["head_sha"] = (head or "").strip()

    subject = _run_git(["log", "-1", "--pretty=%s"], repo_root)
    snapshot["head_subject"] = (subject or "").strip()

    remote = _run_git(["remote", "get-url", "origin"], repo_root)
    snapshot["remote_url"] = (remote or "").strip()

    upstream = _run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], repo_root)
    snapshot["upstream"] = (upstream or "").strip()

    tracking = _run_git(["rev-list", "--left-right", "--count", "HEAD...@{u}"], repo_root)
    if tracking:
        parts = tracking.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            snapshot["ahead"] = int(parts[0])
            snapshot["behind"] = int(parts[1])

    status_raw = _run_git(["status", "--porcelain", "-z"], repo_root)
    files: List[Dict[str, str]] = []
    counts = {key: 0 for key in CATEGORY_LABELS}
    if status_raw:
        for code, path in _parse_status_entries(status_raw):
            category = _categorize(code)
            counts[category] += 1
            files.append({"path": path, "category": category, "code": code})

    files.sort(key=lambda item: (CATEGORY_ORDER.index(item["category"]), item["path"]))
    snapshot["files"] = files
    snapshot["counts"] = counts
    snapshot["total_files"] = len(files)

    numstat = _run_git(["diff", "--numstat", "HEAD"], repo_root)
    insertions, deletions = _parse_numstat(numstat or "")
    snapshot["insertions"] = insertions
    snapshot["deletions"] = deletions
    return snapshot


def _parse_repo_slug(remote_url: str) -> str:
    """Extract an `owner/repo` slug from a git remote URL."""
    url = (remote_url or "").strip()
    if not url:
        return ""
    for pattern in REMOTE_PATTERNS:
        match = pattern.match(url)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return ""


def _github_headers() -> Dict[str, str]:
    headers = {
        "User-Agent": "paper-airplanes-work-report/1.0",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_get(url: str, headers: Dict[str, str]) -> Optional[Any]:
    """GET a GitHub API endpoint, returning parsed JSON or None on any failure."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except Exception:
        return None


def _describe_run(run: Dict[str, Any]) -> str:
    """Render a workflow run as a human readable status label."""
    conclusion = run.get("conclusion")
    status = run.get("status")
    run_number = run.get("run_number")
    label = "実行中"
    if conclusion == "success":
        label = "成功"
    elif conclusion == "failure":
        label = "失敗"
    elif conclusion == "cancelled":
        label = "キャンセル"
    elif conclusion == "skipped":
        label = "スキップ"
    elif conclusion == "timed_out":
        label = "タイムアウト"
    elif status == "queued":
        label = "待機中"
    elif status == "completed" and not conclusion:
        label = "未判定"
    suffix = f" (#{run_number})" if run_number else ""
    return f"{label}{suffix}"


def _fetch_open_count(slug: str, kind: str, headers: Dict[str, str]) -> Optional[int]:
    data = _api_get(
        f"https://api.github.com/search/issues?q=repo:{slug}+type:{kind}+state:open",
        headers,
    )
    if isinstance(data, dict) and isinstance(data.get("total_count"), int):
        return int(data["total_count"])
    return None


def collect_github_status(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Collect GitHub linkage: repository, latest CI conclusion, open PR / Issue counts."""
    status: Dict[str, Any] = {
        "available": False,
        "repo": "",
        "ci_status": "",
        "ci_url": "",
        "open_prs": None,
        "open_issues": None,
        "error": "",
    }

    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not slug:
        slug = _parse_repo_slug(snapshot.get("remote_url", ""))
    if not slug:
        status["error"] = "GitHub リポジトリ未特定"
        return status
    status["repo"] = slug

    headers = _github_headers()
    branch = str(snapshot.get("branch") or "")

    def fetch_runs() -> Optional[Any]:
        url = f"https://api.github.com/repos/{slug}/actions/runs?per_page=5"
        return _api_get(url, headers)

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs_future = pool.submit(fetch_runs)
        prs_future = pool.submit(_fetch_open_count, slug, "pr", headers)
        issues_future = pool.submit(_fetch_open_count, slug, "issue", headers)
        runs_data = runs_future.result()
        open_prs = prs_future.result()
        open_issues = issues_future.result()

    if isinstance(runs_data, dict):
        runs = runs_data.get("workflow_runs") or []
        selected = None
        for candidate in runs:
            if not isinstance(candidate, dict):
                continue
            if branch and candidate.get("head_branch") == branch:
                selected = candidate
                break
        if selected is None and runs and isinstance(runs[0], dict):
            selected = runs[0]
        if selected is not None:
            status["available"] = True
            status["ci_status"] = _describe_run(selected)
            status["ci_url"] = str(selected.get("html_url") or "")

    if open_prs is not None:
        status["open_prs"] = open_prs
        status["available"] = True
    if open_issues is not None:
        status["open_issues"] = open_issues
        status["available"] = True

    if not status["available"]:
        status["error"] = "GitHub API 取得不可 (トークン未設定 or ネットワーク制限)"
    return status


def collect_work_report(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Collect the full work report (git snapshot + GitHub linkage) for a repository."""
    root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
    snapshot = collect_git_snapshot(root)
    github = collect_github_status(snapshot)
    return {"snapshot": snapshot, "github": github}


def _format_locations(snapshot: Dict[str, Any]) -> str:
    files = snapshot.get("files") or []
    if not files:
        return "変更ファイルなし（作業ツリーはクリーン）"
    lines = []
    for item in files[:MAX_LISTED_FILES]:
        mark = CATEGORY_MARKS.get(item["category"], "~")
        lines.append(f"`{mark}` `{item['path']}`")
    remaining = len(files) - MAX_LISTED_FILES
    if remaining > 0:
        lines.append(f"… 他 {remaining} ファイル")
    legend = "凡例: `+`追加 `~`変更 `-`削除 `>`移動 `=`コピー `?`未追跡 `!`競合"
    return "\n".join(lines + [legend])


def _format_counts(snapshot: Dict[str, Any]) -> str:
    counts = snapshot.get("counts") or {}
    total = int(snapshot.get("total_files") or 0)
    if total == 0:
        return "変更なし (0 ファイル / +0 / -0 行)"
    breakdown = " / ".join(
        f"{CATEGORY_LABELS[key]} {counts.get(key, 0)}"
        for key in ("added", "modified", "deleted", "renamed", "copied", "conflict", "untracked")
        if counts.get(key, 0)
    )
    insertions = int(snapshot.get("insertions") or 0)
    deletions = int(snapshot.get("deletions") or 0)
    lines = [
        f"**対象ファイル:** {total} 件 ({breakdown or '内訳なし'})",
        f"**行数:** +{insertions} / -{deletions}",
    ]
    untracked = int(counts.get("untracked", 0))
    if untracked:
        lines.append(f"**未追跡:** {untracked} 件 (行数集計外)")
    return "\n".join(lines)


def _format_push_state(snapshot: Dict[str, Any]) -> str:
    ahead = int(snapshot.get("ahead") or 0)
    behind = int(snapshot.get("behind") or 0)
    if ahead and behind:
        return f"未送信 {ahead} コミット / リモートと乖離 {behind} コミット"
    if ahead:
        return f"未送信 {ahead} コミット (push 必要)"
    if behind:
        return f"リモートより {behind} コミット遅れ (fetch 必要)"
    return "同期済み (push 不要)"


def _format_github(snapshot: Dict[str, Any], github: Dict[str, Any]) -> str:
    lines: List[str] = []
    repo = str(github.get("repo") or "")
    if repo:
        lines.append(f"**リポジトリ:** `{repo}`")
    branch = str(snapshot.get("branch") or "")
    if branch:
        upstream = str(snapshot.get("upstream") or "")
        upstream_text = f" (追跡先: `{upstream}`)" if upstream else " (追跡先なし)"
        lines.append(f"**ブランチ:** `{branch}`{upstream_text}")
    if snapshot.get("is_repo"):
        lines.append(f"**プッシュ状態:** {_format_push_state(snapshot)}")
        head = str(snapshot.get("head_sha") or "")
        subject = str(snapshot.get("head_subject") or "")
        if head:
            head_text = f"**HEAD:** `{head}`"
            if subject:
                head_text += f" {subject}"
            lines.append(head_text)
    if github.get("ci_status"):
        ci_text = f"**最新CI:** {github['ci_status']}"
        if github.get("ci_url"):
            ci_text += f" (<{github['ci_url']}>)"
        lines.append(ci_text)
    if github.get("open_prs") is not None:
        lines.append(f"**オープンPR:** {github['open_prs']} 件")
    if github.get("open_issues") is not None:
        lines.append(f"**オープンIssue:** {github['open_issues']} 件")
    if github.get("error"):
        lines.append(f"**GitHub:** {github['error']}")
    return "\n".join(lines)


def build_report_fields(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert a work report into Discord embed fields."""
    snapshot = report.get("snapshot") or {}
    github = report.get("github") or {}
    fields: List[Dict[str, Any]] = []
    locations = _format_locations(snapshot)
    counts = _format_counts(snapshot)
    github_text = _format_github(snapshot, github)
    if locations:
        fields.append({"name": "📍 操作場所一覧", "value": locations[:MAX_FIELD_VALUE], "inline": False})
    if counts:
        fields.append({"name": "📊 変更数", "value": counts[:MAX_FIELD_VALUE], "inline": False})
    if github_text:
        fields.append(
            {"name": "🔗 GitHub連携状況", "value": github_text[:MAX_FIELD_VALUE], "inline": False}
        )
    return fields


def format_report_summary(report: Dict[str, Any]) -> str:
    """One-line summary of the work report (used for the webhook `content` field)."""
    snapshot = report.get("snapshot") or {}
    github = report.get("github") or {}
    total = int(snapshot.get("total_files") or 0)
    insertions = int(snapshot.get("insertions") or 0)
    deletions = int(snapshot.get("deletions") or 0)
    parts = [f"📍 {total}ファイル", f"+{insertions}/-{deletions}行"]
    if snapshot.get("branch"):
        parts.append(f"🌿 {snapshot['branch']}")
    if github.get("ci_status"):
        parts.append(f"CI {github['ci_status']}")
    return " | ".join(parts)[:MAX_SUMMARY_VALUE]
