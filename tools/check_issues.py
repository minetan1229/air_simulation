import json
import urllib.request
import sys
import os

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from tools.notify_discord import load_env_file

load_env_file()
token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

headers = {"User-Agent": "Python-Issue-Checker"}
if token:
    headers["Authorization"] = f"token {token}"

url = "https://api.github.com/repos/minetan1229/air_simulation/issues?state=all"
req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode())
        print(f"Total items fetched: {len(data)}")
        for x in data:
            item_type = "PR" if "pull_request" in x else "Issue"
            state = x.get('state')
            num = x.get('number')
            title = x.get('title')
            print(f"#{num} [{state}] {item_type}: {title}")
            if state == "open":
                body = x.get('body') or ''
                print(f"   Body: {body[:300]}")
except Exception as e:
    print("Error fetching issues:", e)

