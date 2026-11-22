import json

for i in range(1, 21):
    num_str = f"{i:02d}"
    with open(f"search/raw_data/topic_{num_str}.json", "r", encoding="utf-8") as f:
        d = json.load(f)
    cand = d.get("candidates", [])[0]
    p = cand.get("primary_paper", {})
    refs = cand.get("second_tier_references", [])
    t = p.get('title', '').encode('ascii', 'replace').decode('ascii')[:45]
    print(f"Topic {num_str}: Prim='{t}' ({p.get('year')}) | Refs count={len(refs)}")
    for r_idx, r in enumerate(refs[:2]):
        rt = r.get('title', '').encode('ascii', 'replace').decode('ascii')[:40]
        print(f"   Ref {r_idx+1}: '{rt}' ({r.get('year')}) [{r.get('doi')}]")
