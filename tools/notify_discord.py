import json
import urllib.request
import sys
from datetime import datetime

WEBHOOK_URL = "https://discord.com/api/webhooks/1560152284057567353/2udSqpx3P4ZVhrGi1JnyRbgAY-FyB6cySreOpSOxSi8U9KbTvTj4KtOm91oaMOQCQ8u_"

def send_notification(status_type, title, summary):
    # status_type: 'start' (green: 3066993 / 0x2ECC71), 'subagent' (orange: 15105570 / 0xE67E22), 'end' (blue: 3447003 / 0x3498DB)
    color_map = {
        'start': 3066993,      # 緑
        'subagent': 15105570,  # オレンジ
        'end': 3447003         # 青
    }
    color = color_map.get(status_type, 3447003)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "embeds": [{
            "title": title,
            "description": f"**時刻:** {now_str}\n**内容:** {summary}",
            "color": color
        }]
    }
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'AntigravityWebhook/1.0'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print("Webhook sent successfully:", resp.status)
    except Exception as e:
        print("Failed to send webhook:", e)

if __name__ == "__main__":
    if len(sys.argv) >= 4:
        stype = sys.argv[1]
        t = sys.argv[2]
        s = sys.argv[3]
        send_notification(stype, t, s)
    else:
        print("Usage: python notify_discord.py <start|subagent|end> <title> <summary>")
