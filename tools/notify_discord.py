"""Discord Webhook Notification Utility.

Loads DISCORD_WEBHOOK_URL from environment variables or .env file.
Adheres to the color conventions in AGENTS.md:
  - plan: Purple (0x9B59B6 / 10181046)
  - start: Green (0x2ECC71 / 3066993)
  - subagent: Orange (0xE67E22 / 15105570)
  - verify: Teal (0x1ABC9C / 1752220)
  - github: Blurple (0x5865F2 / 5793266)
  - end / finish: Blue (0x3498DB / 3447003)
  - fail / error: Red (0xE74C3C / 15158332)
  - warning: Yellow (0xF1C40F / 15844367)
  - question: Pink (0xE91E63 / 15277667)

Completion oriented statuses (end / finish / github / commit / push / fail) automatically
attach a work report: operation locations, change counts and GitHub integration status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

COLOR_MAP = {
    "plan": 10181046,      # 紫: 0x9B59B6
    "start": 3066993,      # 緑: 0x2ECC71
    "subagent": 15105570,  # オレンジ: 0xE67E22
    "verify": 1752220,     # 青緑: 0x1ABC9C
    "github": 5793266,     # 藍: 0x5865F2
    "commit": 5793266,     # 藍
    "push": 5793266,       # 藍
    "end": 3447003,        # 青: 0x3498DB
    "finish": 3447003,     # 青
    "fail": 15158332,      # 赤: 0xE74C3C
    "error": 15158332,     # 赤
    "warning": 15844367,   # 黄色: 0xF1C40F
    "warn": 15844367,      # 黄色
    "question": 15277667,  # ぴんく: 0xE91E63
}

STATUS_EMOJI = {
    "plan": "🗺️",
    "start": "🚀",
    "subagent": "👥",
    "verify": "🔬",
    "github": "🐙",
    "commit": "🐙",
    "push": "🐙",
    "end": "✅",
    "finish": "✅",
    "fail": "❌",
    "error": "❌",
    "warning": "⚠️",
    "warn": "⚠️",
    "question": "❓",
}

STATUS_LABEL = {
    "plan": "計画",
    "start": "開始",
    "subagent": "サブエージェント",
    "verify": "検証",
    "github": "GitHub連携",
    "commit": "コミット",
    "push": "プッシュ",
    "end": "完了",
    "finish": "完了",
    "fail": "失敗",
    "error": "失敗",
    "warning": "警告",
    "warn": "警告",
    "question": "確認",
}

# Statuses that automatically carry the work report (operation locations / change counts /
# GitHub linkage).
AUTO_REPORT_TYPES = frozenset({"end", "finish", "github", "commit", "push", "fail"})

try:
    from tools.work_report import build_report_fields, collect_work_report, format_report_summary
except ImportError:  # pragma: no cover - executed when the module is run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.work_report import build_report_fields, collect_work_report, format_report_summary


def load_env_file(env_path: Optional[Path] = None) -> None:
    """Simple .env file loader that does not require python-dotenv."""
    if env_path is None:
        # Search from current working directory up to git root / project root
        candidates = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parent.parent / ".env",
        ]
        for c in candidates:
            if c.is_file():
                env_path = c
                break

    if env_path and env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as exc:
            print(f"[notify_discord] Notice: Failed to read .env file: {exc}", file=sys.stderr)


def get_webhook_url() -> Optional[str]:
    """Retrieve webhook URL from environment or .env file."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        load_env_file()
        url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    return url if url else None


def resolve_report(
    status_type: str,
    report: Optional[Dict[str, Any]] = None,
    auto_report: Optional[bool] = None,
    repo_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return the work report to embed, or None when reporting is disabled/unavailable."""
    if report is not None:
        return report
    key = status_type.lower()
    if auto_report is False:
        return None
    if auto_report is None and key not in AUTO_REPORT_TYPES:
        return None
    try:
        return collect_work_report(repo_root)
    except Exception as exc:  # reporting must never break the notification
        print(f"[notify_discord] Notice: work report collection failed: {exc}", file=sys.stderr)
        return None


def build_payload(
    status_type: str,
    title: str,
    summary: str,
    extra_info: str = "",
    report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the Discord webhook payload (embed + optional work report fields)."""
    key = status_type.lower()
    color = COLOR_MAP.get(key, 3447003)
    emoji = STATUS_EMOJI.get(key, "🔔")
    label = STATUS_LABEL.get(key, key)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    desc = f"**{emoji} {label}** · {now_str}\n**内容:** {summary}"
    if extra_info:
        desc += f"\n{extra_info}"

    fields: List[Dict[str, Any]] = []
    content = f"{emoji} {title}"
    if report:
        fields = build_report_fields(report)
        summary_line = format_report_summary(report)
        if summary_line:
            content = f"{emoji} {title}\n{summary_line}"

    embed: Dict[str, Any] = {
        "title": title,
        "description": desc,
        "color": color,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    if fields:
        embed["fields"] = fields

    snapshot = (report or {}).get("snapshot") or {}
    footer_bits = [f"phase: {key}"]
    if snapshot.get("branch"):
        footer_bits.append(f"branch: {snapshot['branch']}")
    footer_bits.append(now_str)
    embed["footer"] = {"text": " | ".join(footer_bits)[:2048]}

    return {"content": content[:2000], "embeds": [embed]}


def send_notification(
    status_type: str,
    title: str,
    summary: str,
    extra_info: str = "",
    webhook_url: Optional[str] = None,
    report: Optional[Dict[str, Any]] = None,
    auto_report: Optional[bool] = None,
    repo_root: Optional[Path] = None,
) -> bool:
    """Send embed notification to Discord Webhook."""
    target_url = webhook_url or get_webhook_url()
    if not target_url:
        print("[notify_discord] Notice: DISCORD_WEBHOOK_URL is not set. Webhook notification skipped.")
        return False

    resolved_report = resolve_report(status_type, report, auto_report, repo_root)
    payload = build_payload(status_type, title, summary, extra_info, resolved_report)

    req = urllib.request.Request(
        target_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "AntigravityWebhook/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code = getattr(resp, "status", getattr(resp, "code", 200))
            if isinstance(status_code, int) and 200 <= status_code < 300:
                print(f"[notify_discord] Webhook sent successfully (HTTP {status_code}).")
                return True
            print(f"[notify_discord] Webhook responded with status: {status_code}", file=sys.stderr)
            return False
    except Exception as exc:
        print(f"[notify_discord] Failed to send webhook: {exc}", file=sys.stderr)
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a Discord webhook notification with optional work report.",
    )
    parser.add_argument(
        "status_type",
        help="plan|start|subagent|verify|github|commit|push|end|finish|fail|warning|question",
    )
    parser.add_argument("title", help="Notification title")
    parser.add_argument("summary", help="Notification summary")
    parser.add_argument("extra_info", nargs="?", default="", help="Optional extra details")
    parser.add_argument(
        "--report",
        dest="report",
        action="store_true",
        default=None,
        help="Force attaching the work report (operation locations / change counts / GitHub).",
    )
    parser.add_argument(
        "--no-report",
        dest="report",
        action="store_false",
        help="Never attach the work report.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Repository root used for collecting the work report (default: current directory).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve() if args.repo else None
    success = send_notification(
        status_type=args.status_type,
        title=args.title,
        summary=args.summary,
        extra_info=args.extra_info,
        auto_report=args.report,
        repo_root=repo_root,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
