#!/usr/bin/env python3
import urllib.request
import urllib.parse
import json
import sys
import os
import time

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'AntigravityResearchAgent/1.0 (mailto:research@example.com)'})
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode('utf-8'))

def reconstruct_abstract(inv_index):
    if not inv_index:
        return ""
    words = {}
    for word, positions in inv_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[k] for k in sorted(words.keys()))

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 fetch_paper.py <topic_num(01-20)> <search_query>")
        sys.exit(1)

    topic_num = sys.argv[1]
    query = sys.argv[2]

    # 1. 第1階層（主要論文）の検索（DOI必須・引用数順）
    search_url = f"https://api.openalex.org/works?search={urllib.parse.quote(query)}&filter=has_doi:true&sort=cited_by_count:desc&per-page=5"
    data = fetch_json(search_url)
    results = data.get("results", [])
    if not results:
        print(f"No papers found for query: {query}")
        sys.exit(1)

    candidates = []
    for work in results:
        ref_ids = [r.split("/")[-1] for r in work.get("referenced_works", [])[:5]]
        cited_papers = []
        # 2. 第2階層（その論文が実際に引用している論文）の取得
        if ref_ids:
            time.sleep(0.5)
            ref_filter = "|".join(ref_ids)
            ref_url = f"https://api.openalex.org/works?filter=openalex:{ref_filter}&per-page=5"
            ref_data = fetch_json(ref_url)
            for ref in ref_data.get("results", []):
                if ref.get("doi"):
                    cited_papers.append({
                        "title": ref.get("title"),
                        "year": ref.get("publication_year"),
                        "doi": ref.get("doi"),
                        "authors": [a.get("author", {}).get("display_name") for a in ref.get("authorships", [])[:3]],
                        "abstract": reconstruct_abstract(ref.get("abstract_inverted_index"))[:500]
                    })

        if len(cited_papers) >= 2:
            candidates.append({
                "primary_paper": {
                    "title": work.get("title"),
                    "year": work.get("publication_year"),
                    "doi": work.get("doi"),
                    "cited_by_count": work.get("cited_by_count"),
                    "authors": [a.get("author", {}).get("display_name") for a in work.get("authorships", [])[:5]],
                    "abstract": reconstruct_abstract(work.get("abstract_inverted_index"))[:1000]
                },
                "second_tier_references": cited_papers
            })

    os.makedirs("search/raw_data", exist_ok=True)
    out_path = f"search/raw_data/topic_{topic_num}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"topic_num": topic_num, "query": query, "candidates": candidates}, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(candidates)} verified paper sets with references to {out_path}")

if __name__ == "__main__":
    main()
