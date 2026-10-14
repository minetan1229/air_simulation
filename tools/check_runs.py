import json
import urllib.request

url = "https://api.github.com/repos/minetan1229/air_simulation/actions/runs?per_page=3"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        for r in data.get("workflow_runs", []):
            num = r.get("run_number")
            run_id = r.get("id")
            status = r.get("status")
            conclusion = r.get("conclusion")
            msg = (r.get("head_commit") or {}).get("message", "")
            print(f"Run #{num}: id={run_id}, status={status}, conclusion={conclusion}, msg={msg}")
except Exception as e:
    print("Error:", e)
