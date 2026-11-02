with open('physics/records_domain.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines, 1):
    if any(k in l for k in ['name_ja=', 'holder_ja=', 'aircraft_name_ja=', 'technique_ja=', 'notes_ja=']):
        clean = l.strip()[:70].encode('ascii', errors='backslashreplace').decode('ascii')
        print(f"{i:4d}: {clean}")
