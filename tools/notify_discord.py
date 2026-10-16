"""Discord Webhook Notification Utility.

Loads DISCORD_WEBHOOK_URL from environment variables or .env file.
Adheres to the color conventions in AGENTS.md:
  - start: Green (0x2ECC71 / 3066993)
  - subagent: Orange (0xE67E22 / 15105570)
  - end / finish: Blue (0x3498DB / 3447003)
  - fail / error: Red (0xE74C3C / 15158332)
  - warning: Yellow (0xF1C40F / 15844367)
  - question: Pink (0xE91E63 / 15277667)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

COLOR_MAP = {
    "start": 3066993,      # 緑: 0x2ECC71
    "subagent": 15105570,  # オレンジ: 0xE67E22
    "end": 3447003,        # 青: 0x3498DB
    "finish": 3447003,     # 青
    "fail": 15158332,      # 赤: 0xE74C3C
    "error": 15158332,     # 赤
    "warning": 15844367,   # 黄色: 0xF1C40F
    "warn": 15844367,      # 黄色
    "question": 15277667,  # ぴんく: 0xE91E63
}


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


def send_notification(
    status_type: str,
    title: str,
    summary: str,
    extra_info: str = "",
    webhook_url: Optional[str] = None,
) -> bool:
    """Send embed notification to Discord Webhook."""
    target_url = webhook_url or get_webhook_url()
    if not target_url:
        print("[notify_discord] Notice: DISCORD_WEBHOOK_URL is not set. Webhook notification skipped.")
        return False

    color = COLOR_MAP.get(status_type.lower(), 3447003)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    desc = f"**時刻:** {now_str}\n**内容:** {summary}"
    if extra_info:
        desc += f"\n{extra_info}"

    payload = {
        "embeds": [
            {
                "title": title,
                "description": desc,
                "color": color,
            }
        ]
    }

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


if __name__ == "__main__":
    if len(sys.argv) >= 4:
        stype = sys.argv[1]
        t = sys.argv[2]
        s = sys.argv[3]
        extra = sys.argv[4] if len(sys.argv) > 4 else ""
        success = send_notification(stype, t, s, extra)
        sys.exit(0 if success else 1)
    else:
        print("Usage: python notify_discord.py <start|subagent|end|fail|warning|question> <title> <summary> [extra_info]")
        sys.exit(1)
