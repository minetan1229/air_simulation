# Knowledge Base (On-Demand Reference)
<!-- 警告: 全文読み込み禁止。grepやキーワード検索で必要な箇所のみ部分抽出すること。 -->
<!-- 記述形式: ## [Category] Topic / 検証済みの確定事実のみを簡潔に記述 -->

## [Architecture & Specs]
- ルート `agents.md` は「常駐コア（規約・ガードレール）+ 参照索引」であり、詳細技術仕様は `docs/SPECIFICATION.md` に集約・遅延読み込み化されている。過去知見・現在地・定型手順の正本は `.agents/` 配下（`Knowledge.md` / `Project.md` / `skills/`）が持ち、`agents.md` へ重複長文を常駐させない。
- MCP実行基盤は `.kilo/mcp/`（`launch.mjs` が各サーバーのエントリポイントをcwd非依存に解決）、登録先は `.kilo/kilo.jsonc` の `mcp` キー。外部APIキーは不要。`fetch` のみ専用venv (`.kilo/mcp/venv`) で Python 実行。
- GPUバッチシミュレーション (`gpu_batch_evaluate_cases`) は非GPU環境（CI/Linux）で `parallel_evaluate` を安全なフォールバックとし、同一のスキーマ（`landed`, `crashed`, `status`, `horizontal_range_m`）を返却する。 飛行シミュレータの世界座標系はNED (+z下向き) であり、風場 (+z上向き) との鉛直風変換（$v_{rel} = v_{aircraft} - wind_{ned}$）を整合させている。外部メッシュ取得の `validate_url_safe` はプライベートIP/ループバック/クラウドメタデータ/非標準ポート/リダイレクトを遮断する。APIエラーハンドラはクライアントにスタックトレースを返さず、一意な `error_id` のみを返却する。

## [Verified Solutions & Troubleshooting]
- CI環境（GPU非搭載環境）でGPUバッチを呼び出すテストは、暗黙的例外送出ではなく安全なNumPy/CPUフォールバックとスキーマ互換性が必要。GitHub Actions の Windows runner では PowerShell が既定シェルのため、bash スクリプトを実行するワークフローには `defaults: run: shell: bash` の指定が必須。 全監査課題（#28〜#37）は対応完了し、新規監査テスト10件および既存700+件のテストが全パスして master (82d48ea) に反映済み。自律ループプロトコルに Phase 6 [Finalize & Terminate]（正常終了）、リトライ上限3回、不一致時ロールバック規約を導入し、ループ制御の完全性を担保。
- MCP導入（2026-10-19）: context7 / sequential-thinking / fetch / memory / playwright の5サーバーを local stdio で登録し、`kilo mcp list` で全5件 `connected`、`tools/verify_mcp_servers.py` で initialize→tools/list→実ツールコールまで全件PASS。playwright は既存Chromiumを再利用し、成果物は `.kilo/mcp/playwright/` へ出力される。

## [Paper-Conformance Audit] (2026-10-19, commit 60fdef6)
- 正本は `PHYSICS_PAPER_CONFORMANCE_AUDIT.md`（高20 / 中34 / 低30 / 非論文系8）。**修正前の実測値**であり、物理計算経路の3系統分裂（正規6DoF RK4 / GPUアンサンブル / GPU最適化バッチ。実測で `max_stress_pa` が 1261 倍乖離）、Helmbold式4系統分裂、応力許容3系統分裂、接地の75%強制散逸、風符号3経路不一致などが記されている。 正規6DoF経路自体は健全（真RK4・Quaternion積・Euler方程式・SO(3)整合・CCDSLERP・エネルギー収支3%以内・真空放物線1e-4一致）。修正は GPU 代理経路と接地・応力・慣性の散了箇所に限定された。

## [Paper-Conformance Fix Pass] (2026-10-19 → 2026-10-20)
- 修正実施記録: `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` 付録B。高重要度20件すべてに着手、中重要度から17件を実施。
- **失速後揚力ノッチは解消**（旧 `max(raw,0)` のゼロ床を廃し Hoerner平板極限への単調遷移へ。α=41/45° の `C_L≡0` は消滅、α≥45° でHoernerと完全一致。旧ブレンドは30°で谷を生んでいたので**固定ターゲット**へ）と **翼端/負側の失速は対称化**（`neg_stall = max(stall+15,30)` は廃止。`stall_blend_weight` は失速角以下で**厳密に0**、上方は logistic^4 を0.0625で正規化。付着分岐は `cl_max` でクリップ）。
- **接地は接触インパルスへ**: `v += J/m`, `ω += I⁻¹(r×J)`。法線 `J_n=-(1+e) m_eff v_n`、Coulomb円錐 `|J_t|≤μ|J_n|`。角運動量が構成的に保存され、翼端カートホイールは幾何と衝突エネルギーに比例。**キール線接触（重心直下）を追加**。エネルギー上限は `E_post ≤ E_pre`（`max(rest²,0.25)` の下限は削除）、翼端の反発係数クランプ0.5も廃止し `tip_restitution` を新設。 **応力尺度は単一化**: `paper_allowable_stress` のみ。heat-map は `σ/σ_allow`（failure_index と同一）。`flight_sim` の[0,1]クランプ・GPUの120kPa尺度を廃止 → **過応力クラッシュ判定が蘇生**。
- **慣性テンソルに平行軸項を復元**: 正解 `b²[(1+3λ)/(6(1+λ)) − (1+2λ)²/(9(1+λ)²)]`（三角翼 b²/18、矩形 b²/12）。旧式はλ≠1で過小。
- **GPU経路**: `gpu_dynamics.simulate_batch`（6-DoF GPU積分器）へ移行、旧3-DoF代理は `model: "reduced_surrogate"` で明示。GPU/CPUの係数カーネルは `aero_domain` と同一式（Helmbold/失速/Hoerner）。
- **CuPy固有の非互換2件**: `np.asarray(cupy_array)`（gpu_dynamics.wind_batch）と `xp.where(python_bool, ...)`（gpu_dynamics.atmosphere_batch）は CuPy で例外。`positions.get()` / Python `if` に変更。
- テストで更新した契約: 翼端反発の旧0.5クランプ前提、側力の線形定義、非対称失速閾値、`g_load` のresultant定義、`error`/`error_id` キー、optimizer の `verified` キー、front の重複設計除去。
- **残課題台帳の正本は `no-finish.md`**（§A あえて未解決 / §B 仕様主張の是正 / §C 解決済みだが残る限定 / §D 次フェーズ繰延 / §4.5 折り紙 G系 / §4.6 遺伝子 G9系）＋ 追補 `no-finish-physics-supplement.md`。修正後の検証は全866テスト PASS（24分、GPU経路が実6DoF積分器へ移行したため基準比2倍）。

## [Discord Bridge] (2026-10-19, commit 9e4b24c → master 50f5330)
- `tools/discord_bridge.py` は `agy` CLI を `--output-format stream-json` で駆動する。会話 ID は UUID で brain ディレクトリ名と一致し、`--print-timeout` 超過時は stderr に "print timeout" を出して部分出力を返す（終了コード0の正常終了扱い）。
- ガードレール: stderr 常時ドレイン（パイプ詰まりによる子プロセス停止を防止）/ CLI と監視の二重タイムアウト / チャンネル・全体の同時実行上限 / タスク ID 一意化 / プロンプト長・URL・モデル・セッション ID 検証 / 内部例外は Error ID のみ Discord 送出 / 起動時に永続ビュー再登録。 `discord.py` 2.7 では `Select`/`Button`/`TextInput` への `callback` 直接代入は型エラーとなるため**サブクラス化**が正（`discord.ui.Select[discord.ui.View]`）。同ファイルは mypy 52件 → 0件。POSIX でプロセスツリーを kill する時は子プロセスが専用セッション（`start_new_session=True`）に属することが前提（親と同じグループのまま `killpg` するとテストランナーごと死ぬ）。
- 正本仕様は `docs/discord-bridge.md`、テストは `tests/test_discord_bridge.py`（57件・スタブCLI結合テスト込み）。

## [Paper Registry Pipeline] (2026-10-20, commit a578f08 → master b2cbfc4)
- 学術論文2,000件（5カテゴリ×400件）を `search/` に構造化して収録。仕様は `docs/PAPER_REGISTRY_PIPELINE.md`、検証結果は `search/validation_report.json`（レジストリ再照合 2000/2000、URL解決 2000/2000、構造エラー 0）。
- 出典は Crossref（DOI・抄録・媒体）と DataCite（arXiv 接頭辞 10.48550、240件のプレプリントを含む）。**記憶情報での論文生成は禁止**し、全件をレジストリ API で実在確認した。
- 本環境では arXiv / OpenAlex / Semantic Scholar API が共有IP由来で HTTP 429 となる。代替として Crossref + DataCite + `doi.org` コンテンツネゴシエーション（CSL-JSON）で抄録を回復できる（回復率は低いため抄録未登録件は標題・媒体からの要約と明記）。
- 抄録カバレッジは Crossref 由来で約 25%、arXiv 経由を含めると 60%。抄録の無い論文は「抄録未登録（標題と掲載媒体から要約）」と明示し、支配方程式・手法はタイトルと分類情報からの推定である旨を併記する。選定は「標題にコア語 + （コア語2件以上 or 補完語2件以上）」の標題基点ルール。機械翻訳は標題のみを日本語化し原題は保持（要約の生成ではない）。

## [Origami Geometry] (2026-10-20)
- 正本実装は `physics/origami_domain.py`（純幾何、飛行力学に非依存）。展開図→三次元ヒンジ列→多層ソリッド生成までを一括で持つ。
- **用紙は正方形でない**ため、(u,v) 平面の法線は三次元で折り目に直交しない。折り目は実寸の紙面（210×297 mm）で定義し、side normal は紙面内で三次元直交Directions として再構築する（flat そのまま使うと折れ目の向きが 45°ずれる）。
- 折り目は**現在位置**を軸に回す（`T' = F ∘ T`）。flat 定義のまま回すと、folded 済み層で crease がずれ、層が千切れる。折り目定義は「その折り目が実際に切る面」を単位とする group スコープで分離する（スコープ無しだと先に折った flap を後続 crease が貫通する）。
- **Z-Fighting 排除**: 面インデックスは「平行かつ投影重なり」クラスタの最長路（重なりDAG）で決める。重なった任意2面は ≥1 レイヤーピッチ（0.42 mm）離れるので、紙厚 0.30 mm に対して構造的に共面になり得ない。オフセットは α に比例（α=0 は平たい紙）。
- 折れ目は 3 次ベジエのロール（両パネルに接線連続）で閉じ、境界が skin と roll で完全に一致する。flat 面法線をそのまま使うと法線が不連続になる。A4 斜辺は 45° ではない（1:√2 のため）。45° 相当は**紙面（三次元）での角度**で定義する。
- テストは `tests/test_origami.py`（39件）、Z-Fighting 判定は「重なり面の中間面距離 + 層差×ピッチ > 紙厚」。残課題の正本は `no-finish.md`（A-13〜A-19 据え置きと D §4.5 の G-1〜G-8 繰延）。

## [Paper-Conformance Fixes] (2026-10-20)
- 監査の修正は `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §0.0 の進捗表が正本。回帰固定化テストは `tests/test_paper_conformance_p0_p1.py`。
- **経路の偽表示を防ぐ 2 つの model タグ**: `rigid_body_6dof`（6DoF RK4。GPU 最適化バッチも 13 状態 RK4 へ書き換え済み）と `reduced_surrogate`（`model_detail` に `gpu_ensemble_3dof` / `analytical_glide_surrogate`）。`approximate=true` の縮約経路は `physics_verification_eligible=false`。**応力インデックスの分母は 1 つ**（`stress_domain.paper_allowable_stress(gsm, stiffness)`、既定 2.2 kPa）。25 kPa / 120 kPa / 2.0e4·gsm の 3 系統は廃止、`flight_sim.allowable_stress_pa()` のフォールバックも同じ閉形式。
- **風符号は 1 つ**（`wind_domain.wind_flow_components(deg)` = `-(sinθ, cosθ)`、方位角は「風が吹く來的」方位、north=+y / east=+x。旧 GPU 経路の 90°回転は廃止）かつ **Helmbold 式も 1 つ**（`physics/aero_kernel.helmbold_cl_alpha` が正体。SPEC の `+4` & `tan²(Λ_c/2)` 表記も `4AR/π` へ修正済み）。
- **失速後揚力ブレンドの非対称バグ**（ブレンド目標に `sign * blend_angle` を使うと負側のみ磁力が 0.690→0.553 へ沈む。目標値は `abs(plate(blend_angle))` で左右対称にする）と **Polhamus 公開式** `K_p sinα cos²α + K_v sin²α cosα` の `K_v` 項が偶関数である（α 対称でない）ため「奇関数」テストには使えない（90°超で `K_v` 項が負になること＝copysign 符号強制の禁止のみ検証）。
- **最適化器の実行不可能判定**: `status` が `error` / `failed` の設計は `optimizer_domain.INFEASIBLE_SCORE`（-1e12）＋最悪値目的関数ベクトルで扱い、パレート点・ incumbent に入れない。`evaluate_flight` は例外を `status="error"`（`error` / `error_id` 付き）で返す。

## [Genetic-Project Phase & Branch Guard] (2026-10-20)
- **ブランチ隔離が現在の開発フェーズ**: `genetic-project` ブランチで GA 実装前の物理健全性（P0〜P3）を固めるフェーズ。`master`/`main` へのコミット・マージ・チェリーピック・PR は**厳禁**（`agents.md` §2 Branch Isolation）。
- 保護ブランチ実行停止の正本は `tools/branch_guard.py`。`guard_status()` が `severity` を `error`/`warning`/`none` で返し、`master`/`main` は `error`、他ブランチは `warning`、detached HEAD・非 git ディレクトリは判定不能（`none`）。`PAPER_AIRCRAFT_BRANCH_GUARD=off|warn` で緩和可。CI は検出会うため `warn`（既定で CI 自動検出）。
- 組み込み先: `conftest.py` の session スコープ autouse fixture（保護ブランチなら**pytest 自体の実行を中断**）、`app.py` の `main()`（`--allow-protected-branch` で上書き可）、`smoke_test.py` の `main()`（ステージ実行前、ステージ数は 13 のまま）。
- **`flight_sim._local_aero` は削除済み**（監査 H14）。6DoF 経路の空力は upstream `aero_domain` の単一実装になり、upstream が落ちた場合は `RuntimeError` で**即座に失敗**する（沈黙のフォールバックは「別の物理で飛ぶ」ため致命的）。`active_backends()["aero"]` は `upstream` / `unavailable` のみ。
- **固定刻み収束テストは「解決済み領域」で評価する**: 4g ダート（I_pitch=3.5e-6 kg m²）は |ω|≈100 rad/s でカオス的に転がり、点 wise な収束は成立しない。旧刻み段差 0.02/0.01/0.005 s は**すべて**剛性安定限界より大きかった。検証済み刻み段差 0.0025/0.00125/0.000625 s では差分が 3.0e-2 → 8.9e-10 に単調減少する。
- **最適化器の最終候補は 6DoF で再検証**（監査 H05 / P3-20）。`optimizer_domain.revalidate_candidate_6dof()` が `flight_sim.evaluate_flight` を直接呼び、`model="rigid_body_6dof"` と検証済み目的関数を返す（縮約経路で評価した run のみ最大3件を再検証し `verified_candidates` / `best_verified` に公開）。同じベクトルは互いを支配しない（`dominates(a,a)` は False）ため前線に重複し得るが、`_unique_front()` で除去済み。
- **GPU バッチ経路は物理的には正しいが起動オーバーヘッドが支配的**: `gpu_dynamics.simulate_batch` は 13 状態 RK4 の実6DoF。3 ケース 2.5/6/15 s のバッチで約 690 s、N=32 で 3 s 飛行が約 100 s。計算量は**ステップ数×カーネル起動回数**で決まるため小バッチでは CPU 6DoF より遅い。GA 導入前に `cupy.fuse` / CUDA Graph による融合かステップ数削減が必要。
- 固定化テスト: P0/P1 は `tests/test_paper_conformance_p0_p1.py`、P2/P3 は `tests/test_ga_physics_readiness.py`（21件）、ブランチガードは `tests/test_branch_guard.py`（20件）。

## [Shared Physics Kernel] (2026-10-20)
- **空力モデルと構造モデルの実装は 1 つに統一された**: `physics/aero_kernel.py`（係数・力・モーメント）と `physics/stress_domain.peak_root_stress_pa`（翼根曲げ応力のスカラー reduction）。`aero_domain` は `aero_kernel` へ、`parallel_engine` の GPU 経路も `aero_kernel` へ委譲する。カーネルは `ScalarMath`（純 `math`）と NumPy/CuPy の2バックエンドを持ち、**式は同一**で `tools/check_aero_kernel_parity.py` が 9856 状態×8 構成で**ビット一致**（rel=0.0）を検証する。
- **NumPy 0-d 配列を経由すると 6DoF 積分器が空力評価で約 30 倍遅くなる**（1 ステップ 4 状態評価）。調査パイプラインが停止する原因。`ScalarMath` は専用にこのため用意した。**共通化のために scalar 経路を Array 版へ戻してはならない**。
- **GPU batch と 6DoF 参照経路の実測一致**: `max_stress_pa` は 5 ケースすべてで**比 1.000**（ビット一致）、`status` 一致、`distance_m` 差 ≤1.4 %、`flight_time_s` 差 ≤0.4 %。検証は `tools/check_gpu_parity.py` と `tests/test_gpu_kernel_parity.py`。
- **参照経路の `max_ld_ratio` は接地インパルスに汚染されていた**: 接地衝突直後のサンプルも L/D 統計に入り、best glide 7.8 の glider が 13.8 を報告していた。`_is_contact_sample()` で接触終了飛行の最終サンプルを除外し、GPU batch と同じ意味に揃えた。
- **GPU 性能は launch overhead 支配**: 13状態 RK4 は 1 状態評価あたり約200演算。Windows/WDDM では CuPy の 1 launch が約100 µs あり 1 状態評価 21 ms（CPU/numpy は 1.5 ms）→ **この環境では GPU の方が遅い**。Linux/CI では launch overhead が 1/10 で N が大きいと GPU が有利。

## [Procedural Origami Genome] (2026-10-20)
- 遺伝子は折り操作の**シーケンス**（`physics/origami_genome.py`）。各 `FoldStep` は `crease_line`（紙面正規化座標）・`fold_angle_deg`（-180..+180、符号で山/谷）・`symmetry`（対称/片側）・`layer_order`（重なり順）・`target_face_ids`（対象パネル）を持つ。折れ線リストが `origami_domain` の折れ線そのものなので、**3D空間で順番に**切断・回転する（頂点座標の変位は無い）。
- 世代0は `strategy="free"`（線・角度・対称とも完全無作為）、`"families"` は折り族群（キール/ノーズ斜線/横折/後縁トリム）だけ選ぶ。折れ線は group スコープで木構造になり、折れない個体は弾かれる。
- **持ち手フィルタ**は中央帯（|z| ≤ 12 mm）を前後方向の station に切り、**2枚以上**の紙が被さる station を grip とする。grip の鉛直高さ・前後長・層数と CG の grip 内包で判定し、不合格は penalty（fitness = 1/(1+penalty)）として 6DoF の前に弾く。閾値は `GripThresholds` で変更可。定型3機体（dart/nakamura/sky_king）はすべて合格する（回帰ガード）。
- **自己貫通の判定は「一方が他方の内部」**（点を含む判定）のみ。折り線が交差するのは同じ紙の折れ線同士なので貫通ではない（classic dart の機首/翼交差で誤検出していた）。層は `LAYER_PITCH` で分離してから判定する。
- レシピ出力: `recipe_steps`（手順表・角度・山/谷・対象パネル）、`recipe_markdown`（印刷用）、`crease_pattern_svg`（山=赤/谷=青の展開図）、`step_meshes`（Step 0 の平らな紙から完成形までの各段階メッシュ）。API は `GET/POST /api/v1/origami/genome`、`/genome/recipe`、`/genome/steps`、UI は折り手順スライダ（`genome_step`）。残課題は `no-finish.md` §4.6（G9系）。
## [Remaining Work Ledger] (2026-10-20)
- **残課題の正本は `no-finish.md`**（ルート）。A=あえて未解決（A-1〜A-19）/ B=仕様主張の是正（3件）/ C=解決済みだが残る限定（6件）/ D=次フェーズ繰延（高2・中16・低10・非論文系8、§4.5 折り紙 G系、§4.6 遺伝子 G9系）/ **E=§7 2026-10-19 マルチエージェントレビュー（13名・374指摘）由来の残課題** / **F=§8 レビュー由来・あえて解決しない** / **§9=統合推奨順序**。`.agents/Knowledge.md` からは参照リンクだけを張り、詳細を二重保管しない。
- D の要点: H-11（実材料の断面モデル）/ H-19（折り後縁の空力接続）/ M-30（static_margin の空力接続）はいずれも**根拠となる論文が無い、または trim 状態の定義が未確定**のため着手できない。A-1 と同じ「出典なき係数」を増やさない原則で据え置いている。**未特定のフレーク**: `test_tunneling_ccd.py` の 1 件が CPU 競合下で 1 回だけ失敗（部分集合 10 通りの再現は無し）。原因特定は未了。


