"""send_webhook.py: Wrapper around tools.notify_discord.

Reads webhook URL from DISCORD_WEBHOOK_URL or .env file.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Add project root to sys.path if not present
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.notify_discord import send_notification, COLOR_MAP


def notify(
    msg_type: str,
    title: str,
    summary: str,
    extra_info: str = "",
    report: Optional[Dict[str, Any]] = None,
    auto_report: Optional[bool] = None,
) -> bool:
    return send_notification(
        msg_type,
        title,
        summary,
        extra_info,
        report=report,
        auto_report=auto_report,
    )


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python send_webhook.py <type> <title> <summary> [extra]")
        sys.exit(1)
    msg_type = sys.argv[1]
    title = sys.argv[2]
    summary = sys.argv[3]
    extra = sys.argv[4] if len(sys.argv) > 4 else ""
    success = notify(msg_type, title, summary, extra)
    sys.exit(0 if success else 1)
