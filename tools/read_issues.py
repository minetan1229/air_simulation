import urllib.request
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')
url = 'https://api.github.com/repos/minetan1229/air_simulation/issues?state=open'
req = urllib.request.Request(url, headers={'User-Agent': 'Python'})
try:
    with urllib.request.urlopen(req) as resp:
        issues = json.loads(resp.read().decode('utf-8'))
        for issue in issues:
            print(f"=== Issue #{issue['number']}: {issue['title']} ===")
            print(issue.get('body', ''))
            print("-" * 50)
except Exception as e:
    print(f"Error fetching issues: {e}", file=sys.stderr)
    sys.exit(1)
