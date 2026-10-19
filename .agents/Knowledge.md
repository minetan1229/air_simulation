# Knowledge Base (On-Demand Reference)
<!-- 警告: 全文読み込み禁止。grepやキーワード検索で必要な箇所のみ部分抽出すること。 -->
<!-- 記述形式: ## [Category] Topic / 検証済みの確定事実のみを簡潔に記述 -->

## [Architecture & Specs]
- ルート `agents.md` は「常駐コア（規約・ガードレール）+ 参照索引」であり、詳細技術仕様は `docs/SPECIFICATION.md` に集約・遅延読み込み化されている。過去知見・現在地・定型手順の正本は本ディレクトリ側（`Knowledge.md` / `Project.md` / `skills/`）が持ち、`agents.md` へ重複して長文を常駐させない。
- MCP実行基盤は `.kilo/mcp/`（`launch.mjs` が各サーバーのエントリポイントをcwd非依存に解決）、登録先は `.kilo/kilo.jsonc` の `mcp` キー。外部APIキーは不要。`fetch` のみ専用venv (`.kilo/mcp/venv`) で Python 実行。
- GPUバッチシミュレーション (`gpu_batch_evaluate_cases`) は非GPU環境（CI/Linux）で `parallel_evaluate` を安全なフォールバックとし、同一のスキーマ（`landed`, `crashed`, `status`, `horizontal_range_m`）を返却する。
- 飛行シミュレータの世界座標系はNED (+z下向き) であり、風場 (+z上向き) との鉛直風変換（$v_{rel} = v_{aircraft} - wind_{ned}$）を整合させている。
- 外部メッシュ取得の `validate_url_safe` は、プライベートIP/ループバック/クラウドメタデータ/非標準ポート/リダイレクトを遮断する。
- APIエラーハンドラはクライアントにスタックトレースを返さず、一意な `error_id` のみを返却する。
- 学術文献・理論的根拠レジストリは `/api/v1/meta/physics-references` エンドポイントで公開され、Helmbold/Polhamus/Viterna/Wieselsberger/Hoerner/Sutherland/AeroSandboxの典拠を網羅。

## [Verified Solutions & Troubleshooting]
- CI環境（GPU非搭載環境）でGPUバッチを呼び出すテストは、暗黙的例外送出ではなく安全なNumPy/CPUフォールバックとスキーマ互換性が必要。
- GitHub Actions の Windows runner では PowerShell が既定シェルのため、bash スクリプトを実行するワークフローには `defaults: run: shell: bash` の指定が必須。
- 全監査課題（#28〜#37）は対応完了し、新規監査テスト10件および既存700+件のテストが全パスして master (82d48ea) に反映済み。
- 自律ループプロトコルに Phase 6 [Finalize & Terminate]（正常終了）、リトライ上限3回、不一致時ロールバック規約を導入し、ループ制御の完全性を担保。
- MCP導入（2026-10-19）: context7 / sequential-thinking / fetch / memory / playwright の5サーバーを local stdio で登録し、`kilo mcp list` で全5件 `connected`、`tools/verify_mcp_servers.py` で initialize→tools/list→実ツールコールまで全件PASS。playwright は既存Chromium（`%LOCALAPPDATA%\ms-playwright`）を再利用し、成果物は `.kilo/mcp/playwright/` へ出力される。

## [Paper-Conformance Audit] (2026-10-19, commit 60fdef6)
- 監査レポート: `PHYSICS_PAPER_CONFORMANCE_AUDIT.md`（高20 / 中34 / 低30 / 非論文系8、付録にハードコード定数一覧）。**本節は修正前の実測値**であり、正本は同レポート。
- **物理計算経路は3系統**存在し同一機体を3種類の飛行機として計算していた: (A) 正規6DoF RK4=`flight_sim`、(B) GPUアンサンブル、`(C) GPU最適化バッチ`（**NSGA-II/DEの既定評価経路**）。B/Cは3自由度・陽的オイラー・大量クランプ。実測（`duration_s=3.0`、既定機体）: A は `flying`、C は `crashed`、`max_stress_pa` 281.8 vs 355388（**1261倍**）で SPEC §2.1/Issue #35 の「CPU/GPU完全一致」は**偽**。CuPy/RTX4060 は本環境で**有効**のため B/C は到達不能な退行ではなく**稼働中の経路**。
- 失速後揚力ノッチ（α∈[41°,45°] で `C_L ≡ 0`）／失速抗力の `C_D0 cosα` 欠落（α=30° で 5.1倍過小）／慣性テンソルの平行軸項欠落（λ=0 で −25%）／Helmbold式4系統分裂（GPU式が +11.3%）／応力許容3系統分裂（11.4倍・54.5倍）／接地の速度非依存インパルスと `max(rest²,0.25)` の75%強制散逸／風符号3経路不一致（90°回転＋符号反転）。
- `PHYSICS_AUDIT_REPORT.md` §7 の「付加質量未実装」もコードと矛盾（`added_mass_enabled` は部分実装）。
- 正規6DoF経路は健全: 真RK4、Quaternion積・Euler方程式（ジャイロスコープ項含む）・SO(3)整合・CCDSLERP・抗力非正仕事・エネルギー収支3%以内・真空放物線1e-4一致。**修正は GPU 代理経路と接地・応力・慣性の散了箇所に限定すべき**。

## [Paper-Conformance Fix Pass] (2026-10-19, commit 60fdef6 → 未コミット)
- 修正実施記録: `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` 付録B。高重要度20件すべてに着手、中重要度から17件を実施。
- **失速後揚力ノッチは解消**（旧 `max(raw,0)` のゼロ床を廃し Hoerner平板極限への単調遷移へ。α=41/45° の `C_L≡0` は消滅、α≥45° でHoernerと完全一致。旧ブレンドは30°で谷を生んでいたので**固定ターゲット**へ）と **翼端/負側の失速は対称化**（`neg_stall = max(stall+15,30)` は廃止。`stall_blend_weight` は失速角以下で**厳密に0**、上方は logistic^4 を0.0625で正規化。付着分岐は `cl_max` でクリップ）。
- **接地は接触インパルスへ**: `v += J/m`, `ω += I⁻¹(r×J)`。法線 `J_n=-(1+e) m_eff v_n`、Coulomb円錐 `|J_t|≤μ|J_n|`。角運動量が構成的に保存され、翼端カートホイールは幾何と衝突エネルギーに比例。**キール線接触（重心直下）を追加**。エネルギー上限は `E_post ≤ E_pre`（`max(rest²,0.25)` の下限は削除）、翼端の反発係数クランプ0.5も廃止し `tip_restitution` を新設。
- **応力尺度は単一化**: `paper_allowable_stress` のみ。heat-map は `σ/σ_allow`（failure_index と同一）。`flight_sim` の[0,1]クランプ・GPUの120kPa尺度を廃止 → **過応力クラッシュ判定が蘇生**。
- **慣性テンソルに平行軸項を復元**: 正解 `b²[(1+3λ)/(6(1+λ)) − (1+2λ)²/(9(1+λ)²)]`（三角翼 b²/18、矩形 b²/12）。旧式はλ≠1で過小。
- **GPU経路**: `gpu_dynamics.simulate_batch`（6-DoF GPU積分器）へ移行、旧3-DoF代理は `model: "reduced_surrogate"` で明示。GPU/CPUの係数カーネルは `aero_domain` と同一式（Helmbold/失速/Hoerner）。
- **CuPy固有の非互換2件**: `np.asarray(cupy_array)`（gpu_dynamics.wind_batch）と `xp.where(python_bool, ...)`（gpu_dynamics.atmosphere_batch）は CuPy で例外。`positions.get()` / Python `if` に変更。
- テストで更新した契約: 翼端反発の旧0.5クランプ前提、側力の線形定義、非対称失速閾値、`g_load` のresultant定義、`error`/`error_id` キー、optimizer の `verified` キー、front の重複設計除去。
- **残課題**: GPU/CPUの数値一致の定量照合（SPEC §2.1 の「完全一致」宣言の検証）、折り断面の実Zと実材料定数による応力再構成、Polhamus全揚力式、回転付加慣性、パネル法の2π傾斜（2.2倍乖離）。

## [Discord Bridge] (2026-10-19, commit 9e4b24c → master 50f5330)
- `tools/discord_bridge.py` は `agy` CLI を `--output-format stream-json` で駆動する。会話 ID は UUID で brain ディレクトリ名と一致し、`--print-timeout` 超過時は stderr に "print timeout" を出して部分出力を返す（終了コード0の正常終了扱い）。
- ガードレール: stderr 常時ドレイン（パイプ詰まりによる子プロセス停止を防止）/ CLI と監視の二重タイムアウト / チャンネル・全体の同時実行上限 / タスク ID 一意化 / プロンプト長・URL・モデル・セッション ID 検証 / 内部例外は Error ID のみ Discord 送出 / 起動時に永続ビュー再登録。
- `discord.py` 2.7 では `Select`/`Button`/`TextInput` への `callback` 直接代入は型エラーとなるため**サブクラス化**が正（`discord.ui.Select[discord.ui.View]`）。同ファイルは mypy 52件 → 0件。
- POSIX でプロセスツリーを kill する時は子プロセスが専用セッション（`start_new_session=True`）に属することが前提。親と同じグループのまま `killpg` するとテストランナーごと死ぬ（`terminate_process_tree` はgroup 判定で保護）。
- 正本仕様は `docs/discord-bridge.md`、テストは `tests/test_discord_bridge.py`（57件・スタブCLI結合テスト込み）。

## [Paper Registry Pipeline] (2026-10-20, commit a578f08 → master b2cbfc4)
- 学術論文2,000件（5カテゴリ×400件）を `search/` に構造化して収録。仕様は `docs/PAPER_REGISTRY_PIPELINE.md`、検証結果は `search/validation_report.json`（レジストリ再照合 2000/2000、URL解決 2000/2000、構造エラー 0）。
- 出典は Crossref（DOI・抄録・媒体）と DataCite（arXiv 接頭辞 10.48550、240件のプレプリントを含む）。**記憶情報での論文生成は禁止**し、全件をレジストリ API で実在確認した。
- 本環境では arXiv / OpenAlex / Semantic Scholar API が共有IP由来で HTTP 429 となる。代替として Crossref + DataCite + `doi.org` コンテンツネゴシエーション（CSL-JSON）で抄録を回復できる（回復率は低いため抄録未登録件は標題・媒体からの要約と明記）。
- 抄録カバレッジは Crossref 由来で約 25%、arXiv 経由を含めると 60%。抄録の無い論文は `抄録未登録（標題と掲載媒体から要約）` と明示し、支配方程式・手法はタイトルと分類情報からの推定である旨を併記する。
- 選定は「標題にコア語 + （コア語2件以上 or 補完語2件以上）」の標題基点ルール。標題ベースで選別するため、抄録のみに依存した緩い選別による雑多な論文を排除できる。機械翻訳（Google 公開エンドポイント）で標題のみを日本語化し、原題は保持。翻訳は原文テキストの変換であり要約の生成ではない。

## [Origami Geometry] (2026-10-20)
- 正本実装は `physics/origami_domain.py`（純幾何、飛行力学に非依存）。展開図→三次元ヒンジ列→多層ソリッド生成までを一括で持つ。
- **用紙は正方形でない**ため、(u,v) 平面の法線は三次元で折り目に直交しない。折り目は実寸の紙面（210×297 mm）で定義し、side normal は紙面内で三次元直交Directions として再構築する（flat そのまま使うと折れ目の向きが 45°ずれる）。
- 折り目は**現在位置**を軸に回す（`T' = F ∘ T`）。flat 定義のまま回すと、folded 済み層で crease がずれ、層が千切れる。
- 折り目定義は「その折り目が実際に切る面」を単位とする group スコープで分離する。スコープ無しだと、先に折った flap を後続 crease が貫通する。
- **Z-Fighting 排除**: 面インデックスは「平行かつ投影重なり」クラスタの最長路（重なりDAG）で決める。重なった任意2面は ≥1 レイヤーピッチ（0.42 mm）離れるので、紙厚 0.30 mm に対して構造的に共面になり得ない。オフセットは α に比例（α=0 は平たい紙）。
- 折れ目は 3 次ベジエのロール（両パネルに接線連続）で閉じ、境界が skin と roll で完全に一致する。flat 面法線をそのまま使うと法線が不連続になる。
- A4 斜辺は 45° ではない（1:√2 のため）。45° 相当は**紙面（三次元）での角度**で定義する。
- テストは `tests/test_origami.py`（39件）。Z-Fighting 判定は「重なり面の中間面距離 + 層差×ピッチ > 紙厚」、自己交差判定は 3D 線分距離 + 共線/端点接触を除外。

## [Paper-Conformance Fixes] (2026-10-20)
- 監査の修正は `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §0.0 の進捗表が正本。回帰固定化テストは `tests/test_paper_conformance_p0_p1.py`。
- **経路の偽表示を防ぐ 2 つの model タグ**: `rigid_body_6dof`（6DoF RK4。GPU 最適化バッチも 13 状態 RK4 へ書き換え済み）と `reduced_surrogate`（`model_detail` に `gpu_ensemble_3dof` / `analytical_glide_surrogate`）。`approximate=true` の縮約経路は `physics_verification_eligible=false`。
- **応力インデックスの分母は 1 つ**: `stress_domain.paper_allowable_stress(gsm, stiffness)`（既定 2.2 kPa）。25 kPa / 120 kPa / 2.0e4·gsm の 3 系統は廃止。`flight_sim.allowable_stress_pa()` が upstream 取得に失敗した場合のフォールバックも同じ閉形式。
- **風符号は 1 つ**（`wind_domain.wind_flow_components(deg)` = `-(sinθ, cosθ)`、方位角は「風が吹く來的」方位、north=+y / east=+x。旧 GPU 経路の 90°回転（`cos/sin` 混用）は廃止）かつ **Helmbold 式も 1 つ**（`physics/aero_kernel.helmbold_cl_alpha` が正体で、`aero_domain` / `flight_sim` / `stress_domain` / `parallel_engine` は参照のみ。SPEC の `+4` & `tan²(Λ_c/2)` 表記も `4AR/π` へ修正済み）。
- **失速後揚力ブレンドの非対称バグ**（ブレンド目標に `sign * blend_angle` を使うと負側のみ磁力が 0.690→0.553 へ沈む。目標値は `abs(plate(blend_angle))` で左右対称にする）と **Polhamus 公開式** `K_p sinα cos²α + K_v sin²α cosα` の `K_v` 項が偶関数である（α 対称でない）ため「奇関数」テストには使えない（90°超で `K_v` 項が負になること＝copysign 符号強制の禁止のみ検証）。
- **最適化器の実行不可能判定**: `status` が `error` / `failed` の設計は `optimizer_domain.INFEASIBLE_SCORE`（-1e12）＋最悪値目的関数ベクトルで扱い、パレート点・ incumbent に入れない。`evaluate_flight` は例外を `status="error"`（`error` / `error_id` 付き）で返す。

## [Genetic-Project Phase & Branch Guard] (2026-10-20)
- **ブランチ隔離が現在の開発フェーズ**: `genetic-project` ブランチで GA 実装前の物理健全性（P0〜P3）を固めるフェーズ。`master`/`main` へのコミット・マージ・チェリーピック・PR は**厳禁**（`agents.md` §2 Branch Isolation）。
- 保護ブランチ実行停止の正本は `tools/branch_guard.py`。`guard_status()` が `severity` を `error`/`warning`/`none` で返し、`master`/`main` は `error`、他ブランチは `warning`、detached HEAD・非 git ディレクトリは判定不能（`none`）。`PAPER_AIRCRAFT_BRANCH_GUARD=off|warn` で緩和可。CI は検出会うため `warn`（既定で CI 自動検出）。
- 組み込み先: `conftest.py` の session スコープ autouse fixture（保護ブランチなら**pytest 自体の実行を中断**）、`app.py` の `main()`（`--allow-protected-branch` で上書き可）、`smoke_test.py` の `main()`（ステージ実行前、ステージ数は 13 のまま）。
- **`flight_sim._local_aero` は削除済み**（監査 H14）。6DoF 経路の空力は upstream `aero_domain` の単一実装になり、upstream が落ちた場合は `RuntimeError` で**即座に失敗**する（沈黙のフォールバックは「別の物理で飛ぶ」ため致命的）。`active_backends()["aero"]` の値は `upstream` / `unavailable` のみ。
- **固定刻み収束テストは「解決済み領域」で評価する**: 4g ダート（I_pitch=3.5e-6 kg m²）は |ω|≈100 rad/s でカオス的に転がり、点 wise な収束は成立しない。旧刻み段差 0.02/0.01/0.005 s は**すべて**剛性安定限界より大きく、受理却下での半減ステップ集合の比較を測定していた。検証済み刻み段差 0.0025/0.00125/0.000625 s では差分が 3.0e-2 → 8.9e-10 に単調減少し、収束点 ~2.79413 m で一致する。
- **最適化器の最終候補は 6DoF で再検証**（監査 H05 / P3-20）。`optimizer_domain.revalidate_candidate_6dof()` が `flight_sim.evaluate_flight` を直接呼び、`model="rigid_body_6dof"` と検証済み目的関数を返す。縮約経路で評価した run のみ最大 `MAX_REVALIDATED_CANDIDATES=3` 件を再検証し、`verified_candidates` / `best_verified` に公開する。同じベクトルは互いを支配しない（`dominates(a,a)` は False）ため前線に重複し得るが、`_unique_front()` で除去済み。
- **GPU バッチ経路は物理的には正しいが起動オーバーヘッドが支配的**: `gpu_dynamics.simulate_batch` は 13 状態 RK4 の実 6DoF。3 ケース 2.5/6/15 s のバッチで約 690 s、N=32 で 3 s 飛行が約 100 s（1 ケース 3.2 s）。計算量は**ステップ数×カーネル起動回数**で決まるため小バッチでは CPU 6DoF より遅い。GA 導入前に `cupy.fuse` / CUDA Graph による融合かステップ数削減（`_evaluate_state` の二重評価の解消）が必要。
- 固定化テスト: P0/P1 は `tests/test_paper_conformance_p0_p1.py`、P2/P3 は `tests/test_ga_physics_readiness.py`（21件）、ブランチガードは `tests/test_branch_guard.py`（20件）。

