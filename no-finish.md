# no-finish.md — 本タスクで意図的に未解決とした残課題一覧

> 対象作業: 3Dシミュレーション改善に向けた学術論文 2,000 件の収集・検証・データベース化
> 成果物: `search/01_aerodynamics_cfd` … `search/05_gpu_realtime_sim`（各400件）、`search/progress_tracker.json`、
> `search/validation_report.json`、`search/INDEX.md`、パイプライン `search/tools/*`、仕様 `docs/PAPER_REGISTRY_PIPELINE.md`
> 基準時点: master `97ee7b8`（以降 `search/` 配下の改変なし）、検証は DOI 2000/2000・URL 2000/2000・構造エラー0
> 記載時点: 2026-10-20（計測値はすべて本リポジトリで再計測した実測値）

---

## 0. 総覧（優先度順）

| # | 優先度 | 残課題 | 影響範囲 | 実測値 |
|---|--------|--------|----------|--------|
| 1 | P0 | タスク指定のサブトピック 4 件が未充足（SPH/MPM・SoA/ECS・PBD/XPBD・KD-Tree） | カテゴリ 01/02/04/05 | 0件 / 0件 / 8件 / 4件 |
| 2 | P0 | 同一論文の近重複タイトル 11 組が残存 | カテゴリ横断 | Jaccard≥0.8 で 11 組 |
| 3 | P1 | 抄録未登録 800 件（40%）→「核心要約」が標題・媒体からの推定 | カテゴリ 04 が最悪 | 800/2000（04 は 265/400） |
| 4 | P1 | 外部抄録ソース（arXiv / OpenAlex / S2）が HTTP 429 で遮断 | 抄録・引用数・被引用 | 遮断 3 API、回復 4/904 |
| 5 | P1 | 手法タグ未判定 87 件＝カテゴリ汎用テンプレートの適用値のみ | カテゴリ 01/03 が偏重 | 87/2000（01:44, 03:35） |
| 6 | P2 | 著者名未登録 20 件 | 各カテゴリ | 20/2000 |
| 7 | P2 | タイトル機械翻訳の用語辞書・人による校正が未実施 | 全2,000件 | — |
| 8 | P2 | 候補プール 62,010 件が成果物に残らず（再選定の再現性が失われる） | 選定工程 | 62,010 → 2,000 |
| 9 | P2 | ローカルテスト 10 件失敗（2 件再現）。同時進行中の物理リファクタ由来 | CI は master で green | 735 passed / 10 failed |
| 10 | P3 | ローカルブランチ・worktree の未整理（integration ブランチ2件、head_check worktree） | Git 衛生 | 2 ブランチ + 1 worktree |
| 11 | P3 | `.agents/Project.md` の Loop State 未更新（他セッション所有のため意図的に回避） | 自律ループ | — |
| 12 | P3 | メモリ／VRAM 実測ピーク値が未取得（設計上は有界のみ担保） | 資源制約 | — |
| 13 | P3 | レジストリの定期更新（スナップショット更新）機構が無い | 運用 | スナップショット 2026-10-20 時点 |

---

## P0-1 タスク指定サブトピックの未充足（4 件）

**現状**: タスク指示書のカテゴリ内サブトピック列挙に対し、実測で以下が未充足。

| カテゴリ | 指定サブトピック | 該当論文数 | 判定 |
|---|---|---:|---|
| 01_aerodynamics_cfd | SPH／MPM 法 | **0** | 未充足 |
| 05_gpu_realtime_sim | SoA／ECS 設計（キャッシュ局所性） | **0** | 未充足 |
| 02_mechanics_multiphysics | 位置ベース動力学（PBD／XPBD） | 8 | 不足 |
| 04_collision_geometry | KD-Tree の動的更新 | 4 | 不足 |

**原因**: 収集クエリ設計時に該当語を `core` 語として重み付けしすぎており、
上位400件の挤出で落ちた。`paper_taxonomy.py` の `core` 重み（SPH=2.0, PBD 系=3.0 等）と
検索クエリの Recall が、他サブトピック群（例: LES 149件、RANS 264件）に比べ極端に低かった。
また `select_papers.py` の厳格ルール（標題基点＋コア語2件以上）が
表記揺れ（"position based" / "XPBD" / "extended position based dynamics"）を
別物として扱い、单一手法の論文束を薄めた。

**影響**: 指定サブトピックを根拠に実装判断をする場合、該当4領域は本レジストリからは
一次ソースを引けない（他カテゴリへ分散している可能性を含む）。

**次の手順**:
1. `search/tools/paper_taxonomy.py` の `core` 重みを見直し、SPH/MPM・SoA/ECS・KD-Tree の
   重みを 1.5 程度へ下げ、PBD 表記ゆれ（`position based` / `XPBD` / `position-based dynamics`）を
   同義語グループ化する。
2. 上記サブトピック専用クエリを追加し再収集する:
   `python search/tools/collect_papers.py --categories 01_aerodynamics_cfd --rows 150`
3. `python search/tools/select_papers.py --per-category 400` で再選定（他カテゴリの順位変動を確認）。
4. `python search/tools/translate_titles.py` → `build_reports.py` → `validate_paper_collection.py` を再実行。

**前提**: 対象4サブトピック均有 1件以上の論文が存在する（Crossref/DataCite 上に十分量あることは
収集クエリで仮確認済み）。再選定時に全体順位が変動し得るため、差分レポートを別途作成する。

---

## P0-2 近重複タイトル 11 組（同一論文の重複掲載）

**現状**: 正規化タイトル（記号除去・小文字化）での完全重複は 0 組だが、
Jaccard 係数 ≥0.8 の近重複が 11 組検出された。代表例:

- `assessment of reynolds averaged navier stokes simulations …` ／ `correction assessment of reynolds averaged navier stokes simulations …`（0.94）
- `hypersonic flow separation in shock wave boundary layer interactions` ／ `flow separation in shock wave boundary layer interactions`（0.89）
- `a viscoelastic viscoplastic constitutive model for polymer bonded explosives …` ／ `publisher correction a viscoelastic viscoplastic constitutive model …`（0.87）
- `an energy conserving integrator for conservative hamiltonian systems …` ／ `energy conserving integrator for conservative hamiltonian systems …`（0.92）
- `iccd interactive continuous collision detection between deformable models …` ／ `interactive continuous collision detection between deformable models …`（0.92）

**原因**: 重複排除は「正規化タイトル完全一致」のみで実装され、
①出版社 Correction/Erratum の先頭接辞、②arXiv 版と出版版のプレフィックス差
（"ICCD " 等）、③語順差のいずれにも追随できない。

**影響**: 2,000件中の数件〜十数件が同一研究の重複掲載であり、
「2,000件 = 2,000の独立研究」という解釈が厳密には成立しない。

**次の手順**:
1. `paper_taxonomy.py` のタイトル禁止語に `publisher correction`, `correction `（先頭）,
   `erratum for`, `corrigendum to` を明示的に追加（現状の禁止語は `correction to` のみで先頭接辞形が漏れる）。
2. `select_papers.py` に Jaccard ≥0.85 かつ年差≤2年の近重複検出を追加し、
   出版版（journal-article）優先で1件に統合。
3. 近重複統合は「統合元DOI/arXiv ID」を `progress_tracker.json` の `dedup_index` に
   `merged_duplicates` として記録し、追跡可能性を確保する。

---

## P1-3 抄録未登録 800 件（40%）→「核心要約」が標題・媒体からの推定

**現状**: 抄録（レジストリ公開分）を持つのは 1,200/2,000 件。

| カテゴリ | 抄録あり | 抄録なし |
|---|---:|---:|
| 01_aerodynamics_cfd | 270 | 130 |
| 02_mechanics_multiphysics | 254 | 146 |
| 03_numerical_solvers | 301 | 99 |
| 04_collision_geometry | **135** | **265** |
| 05_gpu_realtime_sim | 240 | 160 |

抄録が無い 800 件は Markdown 上で「抄録未登録（標題と掲載媒体から要約）」と明記し、
「支配方程式・手法は標題と分類情報からの推定である」旨を併記済み（虚偽記載の回避は担保済み）。
ただし「主要な支配方程式・アルゴリズムの概要」としての情報量は タイトル同名反復に留まる。

**原因**: Crossref に抄録を預ける出版社が限定的（ACM/Springer/Elsevier/MDPI/Frontiers 等は
abstract を含むが IEEE・Taylor&Francis・AIAA・和文誌は概ね未登録）。カテゴリ04が最悪なのは
ACM/IEEE 比率が高いことによる。

**次の手順**（優先順）:
1. `doi.org` コンテンツネゴシエーション（CSL-JSON）での回復を再試行する。
   実測では 904 件中 **4 件**しか回復できず、回収効果は限定的。
2. 出版社別アブストラクト取得（Crossref の `link` フィールド→出版社 XML ライ传感）を使う。
   SSRF 遮断（`validate_url_safe`）を適用し、1出版社1件ずつ段階導入する。
3. arXiv 版を持つ論文は arXiv 摘要（abs ページ）を採用する。DataCite の
   `descriptions` は既に利用済みで、版が一致するものは本作業内で自動統合済み。

**注意**: 抄録の LLM 要約生成は「出典脚的引用」に反するため本タスクでは採用しない。
未登録のまま標題由来要約を明示する運用を維持する。

---

## P1-4 外部抄録・引用情報ソースの遮断（arXiv / OpenAlex / Semantic Scholar = HTTP 429）

**現状**: 本実行環境（共有 IP）では下記が全滅し、代替として Crossref + DataCite のみを使用。

| API | 応答 | 代替 |
|---|---|---|
| `api.crossref.org` | 200 OK | —（本体） |
| `api.datacite.org` | 200 OK | —（arXiv 接頭辞 10.48550） |
| `export.arxiv.org/api/query` | 429 "Rate exceeded" | DataCite で arXiv 相当を代替 |
| `api.openalex.org` | 429 "Insufficient budget"（共有 IP の日次予算枯渇） | Crossref + DataCite |
| `api.semanticscholar.org` | 429 "Too Many Requests" | 同上 |
| `api.core.ac.uk` / `dblp.org` | 未検証（Core は要キー、DBLP は bot 遮断） | — |

**結果として残る欠落**:
- arXiv プレプリントの被引用数（DataCite は引用数を持たない）→ 引用数タイブレークで
  arXiv 側の不利が競合評価で効き、preprint 240件が 2,000件中 12% に留まる。
- OpenAlex の concept/topic による分類ラベル（未使用）。
- Semantic Scholar / NTRS / NASA の試験レポート（タスクが許容した NTRS ソースは 0 件）。

**次の手順**:
1. 別 IP（CI ランナー、 Mise失调ネットワーク）または API キー取得後に
   `collect_papers.py` の DataCite パスへ arXiv / OpenAlex 優先順位を追加。
2. OpenAlex 復旧時は `is-referenced-by-count` を arXiv にも付与でき、preprint の順位付けが改善する。
3. NTRS（NASA Technical Reports Server）の追加は、
   `https://ntrs.nasa.gov/api/citations/search` を新ソースとして `paper_common.py` に実装する。

---

## P1-5 手法タグ未判定 87 件（適用値がカテゴリ汎用テンプレートのみ）

**現状**: `TECHNIQUES` の手法キーで照合できなかった論文 87 件は、
カテゴリ別フォールバック文（例:「既存の CuPy GPU 経路（および CPU フォールバック）の
並列化・メモリ配置・近似モデル化に適用できる。」）のみが適用値となる。

| カテゴリ | 手法タグなし |
|---|---:|
| 01_aerodynamics_cfd | 44 |
| 03_numerical_solvers | 35 |
| 05_gpu_realtime_sim | 5 |
| 04_collision_geometry | 2 |
| 02_mechanics_multiphysics | 1 |

**原因**: `paper_taxonomy.py` の `TECHNIQUES` は約 120 キーの手動典であり、
機械学習・推定・モデル予測制御など 2024〜2026 年の新規手法が未登録。
一例: `rank 39 Physics-informed fourier neural operators…` は
支配方程式（PINN/FNO）は判定できたが手法タグが一致せず、フォールバックへ落ちている。

**影響**: 87 件の適用価値は「その論文固有の数値手法」ではなくカテゴリ一般論であり、
タスク要件の「3Dシミュレーションへの応用案」の具体性が残る。

**次の手順**:
1. 87 件のタイトル・抄録を一覧化し、`TECHNIQUES` へ新キーを追加（キー設計は「原文に現れる語」単位）。
2. `build_reports.py` の `detect_techniques` は AND/OR の一致判定だが、
   複数語の結合（例: "graph neural network" と "gnn"）を同一視するエイリアス表を增设。
3. 追加後は `build_reports.py` のみ再実行すれば反映される（再収集・再検証は不要）。

---

## P2-6 著者名未登録 20 件

**現状**: `authors` が空の論文 20 件（Crossref の著者要素欠落。主に DataCite の
少数レコードと一部出版社）。
Markdown 上は「著者情報なし」と明記し、虚偽の氏名を創作していない。

**次の手順**: `doi.org` コンテンツネゴシエーション（CSL-JSON の `author` 配列）または
DataCite の `creators` から補完。既存 `backfill_abstracts.py` に
「著者・抄録同時回復」モードを追加すれば 1 パスで済む。

## P2-7 タイトル機械翻訳の用語辞書・校正が未実施

**現状**: 日本語訳は Google 公開エンドポイントによる機械翻訳（原文テキストの変換のみ、
要約生成ではない）。「日本語訳（機械翻訳）」と明記し原題も併記。
航空・数値解析の定型語（例: "boundary layer" → 「境界層」、"wake" → 「後流」、
"trim" → 「トリム」）は訳出が一貫しない。固有名詞の一部は原語のまま（例: "GE-SpMM"）。

**次の手順**:
1. 航空/数値解析/計算幾何の用語辞書（100〜200語）を作成し訳前に適用（置換方式）。
2. 固有名詞・記号（Re, C_L, C_D, CFL, SPH, XPBD, GJK）は原語保持ルールを明示。
3. 校正は人手または LLM レビューが前提。現状は自動生成のまま。

## P2-8 候補プール 62,010 件が成果物に未保存（再選定の再現性）

**現状**: 収集した候補は 62,010 件（Crossref 58,301 + DataCite/arXiv 3,709）を
SQLite キャッシュ（`search/.cache/paper_collection.db`、63MB、Git 管理外）に保持。
Git に入るのは最終選定 2,000 件のみ。キャッシュを消すと
「2,000 件がどう選ばれたか」の再現は再収集（~40分・ネットワーク要）を要する。

**次の手順**:
1. 選定の再現性を担保するため、カテゴリ別の候補上位（例: 上位 1,000件/カテゴリ）の
   DOI・タイトル・スコアを `search/selection_candidates.jsonl`（約 5,000 行）として保存する。
2. キャッシュ削除時は `collect_papers.py` の再実行で再構築可能である旨を
   `docs/PAPER_REGISTRY_PIPELINE.md` §3 に追記。

## P2-9 ローカルテスト 10 件失敗（2 件再現）— 同時進行中の物理リファクタ由来

**現状**: ローカル `pytest tests -q` は 735 passed / 10 failed。再現するのは 2 件:
- `tests/test_advanced_physics_audit.py::test_fixed_timestep_convergence`
- `tests/test_flight.py::test_free_fall_matches_the_analytic_trajectory`

いずれも `physics/flight_sim.py`（560行差分）・`physics/aero_domain.py`（289行差分）・
`tests/test_flight.py`（14行差分）の同時進行中の変更が対象で、本作業の追加ファイル
（`search/**`、`tests/test_paper_collection.py`）とは無関係。master の CI は green。

**次の手順**: 物理リファクタの着地後に `python -m pytest tests -q` を再実行し、
残存失敗の有無で切り分ける（本タスクの範囲外のため未着手）。

## P3-10 ローカルブランチ・worktree の未整理

**現状**: 本作業由来の.Push 後残置:

- ローカル統合ブランチ `integration/paper-collection-2000`（b2cbfc4、origin/master より 13 遅れ）
- ローカル統合ブランチ `integration/paper-registry-docs`（97ee7b8、11 遅れ）
- 参照用 worktree `C:/Users/theor/AppData/Local/Temp/kilo/head_check`（detached a578f08）
- 作業ブランチ `feat/paper-collection-2000`（a578f08、origin と同期済み）

**次の手順**: 統合ブランチ 2 件と head_check worktree は役割を終えたため削除する。
`git branch -D integration/paper-collection-2000 integration/paper-registry-docs` /
`git worktree remove <path>`。他セッションの worktree（baseline_master, remediation_wt,
medieval-terrier）は触らない。

## P3-11 `.agents/Project.md` の Loop State 未更新

**現状**: 自律ループ規約（Phase 5）に従い Findings を `.agents/Knowledge.md` の
`## [Paper Registry Pipeline]` セクションへ追記済み（master 反映・CI green）。
`Project.md` の Loop State は他セッションが物理ループで進行中のため、
本タスクの書き込みで上書きすると所有権が衝突する意図から**意図的に未更新**とした。

**次の手順**: ループ統括者（または物理ループの負責セッション）が
`Project.md` の Loop State に本タスクの完了位置を追記する。
記載すべき内容: 2,000件レジストリ master 反映済み、CI green、残課題は本ファイル参照。

## P3-12 メモリ／VRAM 実測ピーク値の未取得

**現状**: タスク条件「RAM/VRAM 使用率 90% 未満」は、**設計上**は満たしている
（候補は SQLite へストリーミング格納、1リクエスト最大100件×4スレッドのみ常駐、
最終レポートも1カテゴリ単位で逐次生成）。ただし実測ピーク値は記録していない。

**次の手順**: `collect_papers.py` / `build_reports.py` 実行時に
`psutil` 相当の RSS サンプリングを 1 行追加し、
「最大 RSS < 基準値の 90%」を `validation_report.json` に記録する。
VRAM は本タスクが GPU を使用しないため該当なし（CuPy 経路の検証は既存 smoke_test の領域）。

## P3-13 レジストリの定期更新機構が無い

**現状**: 本レジストリは 2026-10-20 時点のスナップショット。差分更新・再検証の
運用は未定義（`collect_papers.py` は再実行可能だが、既存 DOI を INSERT OR IGNORE する
ため更新された論文の抄録差し替えは反映されない）。

**次の手順**:
1. `select_papers.py` に `--refresh` モードを追加し、既存 `verified` テーブルと
   差分比較して変更論文のみ再検証する。
2. 運用周期（例: 四半期）を決め、`docs/PAPER_REGISTRY_PIPELINE.md` に追記する。
3. 更新時は `progress_tracker.json` の `generated_at` を更新し、
   `validation_report.json` に世代番号を持たせる。

---

## 附: 意図的に「やめた」こと（課題ではなく設計判断）

- **LLM による論文メタデータ生成**: 全面禁止。`core_theory.summary_sentences` は
  レジストリ公開抄録からの文抽出、`formulations` は語彙マッピング、`title_ja` は
  原文テキストの機械翻訳のみ。記憶由来_DESCRIPTORは一切含まれない。
- **出版社ページのスクレイピング**: SSRF 遮断と利用規約の不確実性のため不採用
  （P1-3 手順2 として再評価対象）。
- **NTRS / Semantic Scholar ソース**: タスク上は許可源だが当該 API が 429 のため
  0 件（P1-4 参照）。
- **同時セッションの作業領域の改変**: 物理リファクタ等の未コミット変更には
  一切触らず、マージは隔離 worktree で実施した。
