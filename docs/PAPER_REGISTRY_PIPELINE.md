# 学術論文レジストリ・パイプライン仕様（2,000件）

`search/` 配下に格納される 3D シミュレーション改善向けの学術論文レジストリの
収集・検証・生成手法を定義する。命题は「出典可以被検証であること」であり、
記憶情報に基づく論文の生成は一切禁止する。

## 1. ディレクトリ構成

| パス | 内容 |
|------|------|
| `search/01_aerodynamics_cfd/` | 航空力学・流体力学 400 件（8 チャンク） |
| `search/02_mechanics_multiphysics/` | 剛体・変形体・マルチフィジックス 400 件 |
| `search/03_numerical_solvers/` | 数値解析・時間積分・ソルバー安定化 400 件 |
| `search/04_collision_geometry/` | 衝突判定・空間分割・計算幾何学 400 件 |
| `search/05_gpu_realtime_sim/` | GPU 並列計算・リアルタイム 3D 最適化 400 件 |
| `search/papers_XXX_YYY.md` | 50 件単位の Markdown レコード（可読版） |
| `search/papers_XXX_YYY.jsonl` | 同範囲の JSON Lines（検索・集計用） |
| `search/progress_tracker.json` | カテゴリ別件数・取得済み DOI/arXiv ID・重複排除索引 |
| `search/validation_report.json` | 検証ゲート結果（構造 + レジストリ + URL 解決） |
| `search/INDEX.md` | 全チャンクへの索引 |
| `search/.cache/paper_collection.db` | 収集キャッシュ（SQLite、Git 管理外） |

## 2. データソース（実在検証の根拠）

| ソース | 用途 | 根拠 URL |
|--------|------|----------|
| Crossref REST API | ジャーナル/会議論文の DOI・著者・年・媒体・抄録 | `https://api.crossref.org/works` |
| DataCite REST API | arXiv プレプリント（DOI 接頭辞 `10.48550/arxiv.*`）と抄録 | `https://api.datacite.org/dois` |
| DOI コンテンツネゴシエーション | レジストリに抄録が無い場合の抄録回復 | `https://doi.org/<doi>`（CSL-JSON） |
| Google Translate 公開エンドポイント | 標題の日本語訳（原文のみを翻訳） | `https://translate.googleapis.com/translate_a/single` |

各レコードは必ず DOI を持つ。arXiv 版は `arXiv ID` を併記し、ソース URL は
`https://doi.org/<doi>` または `https://arxiv.org/abs/<arxiv_id>` とする。

## 3. パイプライン（`search/tools/`）

1. `collect_papers.py` — Crossref（`query.bibliographic` と `query.title` の二系統、
   `type:journal-article` / `type:proceedings-article`）と DataCite（arXiv 接頭辞）から
   候補を収穫し、SQLite へストリーミング格納する（メモリ常駐なし）。
2. `select_papers.py` — タイトルを基点とした関連度スコアリング
   （標題にコア語 + 補完語/追加コア語を含むもののみ採用）、標題正規化による
   出版版/arXiv 版の統合、カテゴリ横断の重複排除を行い 400 件/カテゴリを選定。
3. `translate_titles.py` — 登録済みの標題テキストを翻訳（8件/要求、原文のみ）。
4. `backfill_abstracts.py` — 抄録未登録の確定論文へ DOI コンテンツネゴシエーションで
   抄録を回復し、出典を記録する。
5. `build_reports.py` — 50 件単位の Markdown/JSONL チャンク、進捗トラッカー、索引を生成。
6. `validate_paper_collection.py` — 構造検証に加え、全件について
   (a) 発行レジストリ再照合（標題類似度 0.7 以上）、(b) ソース URL の HTTP 解決を確認。

再実行は `search/tools/` の各スクリプトを直列に実行することで再現できる
（キャッシュは `search/.cache/paper_collection.db`）。

## 4. レコード書式（Markdown）

```markdown
### [連番] 論文タイトル（原題）
**日本語訳（機械翻訳）**: 日本語タイトル
- **著者 / 発行年 / 媒体**: 著者名 (年), 掲載媒体
- **ソースURL**: [https://doi.org/xxx](https://doi.org/xxx)
- **ID**: DOI: xxx / arXiv: xxxx.xxxxx
- **理論・アルゴリズムの核心**:
  - 支配方程式・手法: ...
  - 手法タグ: ...
  - 抄録に基づく要約: ...（抄録未登録時は標題・媒体からの要約と明記）
- **本3Dシミュレーションへの適用価値**:
  - ...
```

「適用価値」は本シミュレータへの**適用提案**であり論文の主張ではない
（各チャンク冒頭と JSONL の `simulator_application.note` に明記）。

## 5. 検証ゲート（`tests/test_paper_collection.py` + `validate_paper_collection.py`）

- カテゴリ別 400 件、合計 2,000 件、DOI と標題の重複ゼロ。
- 全件に DOI / URL / 著者 / 年 / 媒体 / 核心要約 / 適用価値が揃うこと。
- 全 2,000 DOI が Crossref または DataCite に実在し、標題が一致すること。
- 全 2,000 のソース URL が HTTP で解決すること。
- `search/validation_report.json` の `passed` が true であること。

## 6. 運用上の注意

- Crossref / arXiv / OpenAlex / Semantic Scholar は共有 IP 由来でレート制限
  (HTTP 429) が発生しやすい。並列度は 4 以下、失敗時は指数バックオフで再試行する。
- 収集・検証はネットワーク依存のため、CI では構造検証（`--skip-live`）のみ実行する。
