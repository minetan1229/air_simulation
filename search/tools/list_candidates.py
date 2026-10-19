import json

for i in range(1, 21):
    num_str = f"{i:02d}"
    with open(f"search/raw_data/topic_{num_str}.json", "r", encoding="utf-8") as f:
        d = json.load(f)
    print(f"=== Topic {num_str} Candidates ===")
    for idx, c in enumerate(d.get("candidates", [])):
        p = c.get("primary_paper", {})
        t = p.get('title', '').encode('ascii', 'replace').decode('ascii')[:50]
        y = p.get('year')
        cit = p.get('cited_by_count')
        print(f"  [{idx}] {t} ({y}) [Cited: {cit}]")
