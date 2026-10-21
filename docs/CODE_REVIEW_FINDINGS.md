# コードレビュー指摘事項記録 (Code Review Findings)

- 対象: `paper_airplanes/simulation/v3` (Flask + Three.js 3D 紙飛行機 6DoF シミュレータ)
- 方式: コンテキストを持たない独立サブエージェントによる反復レビュー (各ラウンドは前ラウンドの情報を与えない)
- 開始: 2026-10-19 / ベースコミット: `a578f08`〜`d4a9486`（レビュー中にリポジトリが進行中）
- サブエージェント: **13名**（R1: 4名 / R2: 5名 / R3: 2名 / R4: 2名）、**374件の指摘**（重複排除後 69件）
- 状態: **未修正** (ユーザー指示により指摘のみ記録)

---

## サマリ

| ラウンド | サブエージェント | 領域 | Critical | High | Medium | Low | 計 |
|---|---|---|---|---|---|---|---|
| R1 | backend-physics | `physics/` 物理エンジン | 0 | 8 | 10 | 12 | 30 |
| R1 | frontend | `static/js/`, `templates/` | 1 | 6 | 9 | 11 | 27 |
| R1 | api-security | `api/`, `app.py` | 0 | 5 | 10 | 11 | 26 |
| R1 | tests-ci-deps | `tests/`, CI, 依存, 衛生 | 2 | 6 | 13 | 9 | 30 |
| R2 | paper-conformance | 論文一致度（Helmbold/Polhamus/Hoerner/Viterna/ISA/Sutherland/Glauert） | 1 | 5 | 8 | 14 | 28 |
| R2 | frontend-a11y | フロント品質・アクセシビリティ・情報設計 | 3 | 7 | 29 | 13 | 52 |
| R2 | config-contract | 設定/データ契約（UI↔スキーマ↔サーバ） | 0 | 6 | 9 | 8 | 23 |
| R2 | ops-tooling | 運用/自動化/可観測性（tools, bridge, MCP, CI, 文書） | 2 | 8 | 20 | 8 | 38 |
| R2 | performance | 性能/スケーラビリティ/並行性 | 5 | 6 | 7 | 7 | 25 |
| R3 | test-effectiveness | テストスイート有効性 | 4 | 5 | 8 | 6 | 23 |
| R3 | e2e-journey | E2Eプロダクト・ユーザジャーニー | 6 | 6 | 22 | 6 | 40 |
| R4 | origami-geometry | 折り畳み/幾何層と空力結合 | 1 | 4 | 5 | 6 | 16 |
| R4 | wind-stress | 風・応力・大気 | 1 | 2 | 8 | 5 | 16 |
| **合計** | **13名** | | **26** | **74** | **158** | **116** | **374** |

**優先度別（重複排除後）**: P0 = 14件 / P1 = 21件 / P2 = 23件 / P3 = 11件 → **計 69件**
（内訳と優先順は本ファイル末尾「全体トリアージ（優先度順）」を参照）


### 横断的パターン（複数エージェントが独立に言及）

1. **物理3系統の分裂** — 正規6DoF RK4 / GPUアンサンブル3DoF / GPU最適化バッチ3DoF が同一機体を別々に計算。GPU側のHelmbold式・失速角・応力許容・風ベクトル規約がCPU側と不一致（R1-backend #1〜#4、R1-tests #10）。
2. **フロントエンドJSが `index.html` に存在しないDOM id を大量参照** — 約35個。エラー表示バナー、飛行ステータス行、クイック投入ドック、チャンピオン採用カード等が死んだまま（R1-frontend #1〜#6, #16, #21）。
3. **設定の单一ソース violated** — `pyproject.toml` vs `pytest.ini`/`ruff.toml`/`mypy.ini`、`requirements.txt` vs `pyproject.toml`、`docs/` vs `wiki-docs/` vs `archive/`、`PHYSICS_*_PLAN.md` の重複。
4. **設定・認証の未読込** — `load_dotenv()` が `app.py` で呼ばれず `API_KEY` は常に空、`.env` はデッド設定。
5. **セキュリティ検証の欠落** — 認証ミドルウェアのテスト 0件、`pip-audit || true` でゲートが無効、`/api/v1/errors` がスタックトレースを返す。

---

# Round 1

## R1-backend-physics — `physics/` 物理エンジン (30件)

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | GPU風ベクトル符号規約が正準と90°ずれ、GPU2経路間は180°乖離 | `physics/parallel_engine.py:842-843`, `:1503-1504` | 正準は `-(sinθ, cosθ)`。ensemble は `(-cosθ,-sinθ)`、batch は `(+cosθ,+sinθ)`。`optimizer_domain.py:415-417` は全候補を batch 経路へ送るため、CuPy環境では最適化が回転した風場で行う |
| 2 | GPUバッチの応力正規化係数が 120 kPa 固定 | `physics/parallel_engine.py:107`, `:1639`, `:1643`, `:1662` | `allowable_pa` を計算して捨て、`_STRESS_LIMIT_PA=1.2e5` を使用。6DoF は `stress_domain.paper_allowable_stress`≈2.2 kPa。約54倍のスケール差。`crashed` 判定も無効化され応力クラッシュが死に分岐化 |
| 3 | GPUバッチが失速角・トリムAoAをハードコード | `physics/parallel_engine.py:1541`, `:1602`, `:1604` | `0.244 rad` 固定。`AircraftConfig.stall_alpha_deg` は GPU系で一切参照されず、UIでの失速角設定が最適化に無効 |
| 4 | GPU経路のHelmbold式がCPU経路と不一致 | `physics/parallel_engine.py:866`, `:1447` vs `physics/flight_sim.py:555-560` | GPU は `2πAR/(2+√(AR²+4))`、CPU は正準 `2πAR/(2+√(AR²(1+λ)+4AR/π))`。+8〜12% の揚力傾き差 |
| 5 | `records_domain.py` に U+FFFD 276個 / 93行 | `physics/records_domain.py:175`, `:438`, `:448`, `:777`, `:1262`, `:1853` (+87行) | UTF-8→cp932→UTF-8 往復で破壊された日本語。`/api/v1/records/presets` でユーザに露出。**修正済み**: 往復で消滅した元文字は復号せず、ギネス記録の原文出典から日本語93行を再記述。`tools/check_mojibake.py` と `tests/test_no_mojibake.py` が再発を検出 |
| 6 | CPUフォールバックが「発散」を「着地成功」と報告 | `physics/parallel_engine.py:1366-1368` vs `physics/flight_sim.py:145-167` | `EVALUATE_KEYS` に `landed`/`crashed` が無く既定式が採用。`status=="diverged"` でも `landed=True` になり最適化のランキングに混入 |
| 7 | 最適化がエラー/発散評価を正当な 0m 飛行として採点 | `physics/optimizer_domain.py:185-201`, `:304-305`, `:429` / `physics/flight_sim.py:3289-3303` | `objective_value()` が `status` を見ない。`flight_sim` は「0m として報告してはならない」と明記しているのに、最適化側は 0.0 として Pareto archive に投入 |
| 8 | 最適化1回内で2つの物理モデルが混在 | `physics/optimizer_domain.py:399`, `:415-417` | 候補1個=6DoF RK4、2個以上=3DoF 近似。比較不能なスコアを同一トーナメントで比較 |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 9 | `wind_vector` が風速ソルバに誤った高さ参照を渡す | `physics/flight_sim.py:833` / `physics/wind_domain.py:909` | 絶対高度ではなく地上高を渡すため `ground_level_m` だけ低く評価。docstring と実契約の3者的不一致 |
| 10 | `flight_sim._local_aero` と `aero_domain.evaluate_aero` の操作権限が4倍乖離 | `physics/flight_sim.py:934,943` vs `physics/aero_domain.py:998,1037` | ロール `0.0035` vs `0.015`、ヨー `0.0025` vs `0.010`。上流 import 可否で挙動が静かに変わる |
| 11 | `aeroelastic_morph_exponent` / `aeroelastic_pressure_scale_pa` が宣言のみ | `physics/config_schema.py:328-329` vs `physics/flight_sim.py:2194,2196`, `physics/parallel_engine.py:878` | 実計算は `160.0`/`3.0` のリテラル。較正不能という説明と実態が乖離 |
| 12 | Pareto ソートが評価アーカイブ全体で O(n²) 純 Python | `physics/optimizer_domain.py:613-649` | 5000評価で 2500万ペア比較。リクエストスレッドで数十秒 |
| 13 | `flight_phase_metrics` が O(n²) 且つ1飛行に2回 | `physics/flight_sim.py:3343`, `:3077`, `:3436` | 300サンプルで 18万反復/回。5000評価で 10^9 級 |
| 14 | `investigate` 系が適応サブステップを無効化し 50ms 固定RK4 | `physics/investigate/tuning.py:31,109-111,237-239` / `physics/flight_sim.py:2061-2066` | 安定性限界 ~1.8e-4 s に対し 280倍 oversized。1サブステップで最大21回の空力評価、4秒宇宙に 10^5 回の評価 |
| 15 | 進捗コールバック第3引数の意味が生産者ごとに違う | `physics/optimizer_domain.py:347,386` vs `physics/parallel_engine.py:1691` / `physics/job_manager.py:323-328` | CPU経路は float(best_score)、GPU経路は str(backend)。`JobManager` が `record.backend` にbest scoreが入る |
| 16 | JSONLエラーログが回転ハンドラを迂回 | `physics/error_domain.py:126-133` vs `:152` | `stream.write` 直書きで `emit` を通らないため `logs/error.jsonl` が無制限増加。書き込み失敗は握りつぶし |
| 17 | `mesh_domain.__all__` に未定義名の2件 | `physics/mesh_domain.py:73`, `:76` | `PANEL_INCIDENCE_DRAG_K`, `paper_sheet_thickness_m` が存在せず `import *` が AttributeError。ruff F822 |
| 18 | 対気速0でも非零の空力モーメント | `physics/aero_domain.py:1091,1094,1105` vs `physics/flight_sim.py:1880-1886` | `max(speed,0.8)**2` により動圧が床打ち。6DoF は「無風ではモーメントゼロ」不変条件を侵犯 |

### Low（12件・要点のみ）

| # | 場所 | 内容 |
|---|---|---|
| 19 | `physics/flight_sim.py:709-713` | `atmosphere_state` がキャッシュ済みdictをコピーせず返却。将来の in-place 変更で全セッション汚染 |
| 20 | `physics/flight_sim.py:1543` | `resolve_throw` の `np.linalg.solve` が無防備。`inertia_xy_kgm2` で特異にすると LinAlgError→HTTP 500 |
| 21 | `physics/flight_sim.py:826-829` | 「無風」短絡の前提 `thermal_strength==0.0` が既定 0.50 と不整合。既定でも常に風クエリ4回/段 |
| 22 | `physics/flight_sim.py:3020-3024,2898,2095` | テレメトリのみで1サブステップあたり空力評価2〜3回追加（全体の約40%） |
| 23 | `physics/flight_sim.py:1606,1470` | `throw_speed_source` が `"impulse"` と報告するが `throw_style=="direct"` では impulse 適用外。ラベルと実挙動が不一致 |
| 24 | `physics/flight_sim.py:755-758` | 動粘性係数キーの大文字小文字不一致で常にフォールバック定数 1.7e-5 を使用（Sutherland 値 1.86e-5 Ignored） |
| 25 | `physics/flight_sim.py:170,218,235` | `FlightDivergenceError` はどこからも raise されず、except 分岐が到達不能。`MAX_CONTACT_RATE_JUMP_RADPS` 等も死定数 |
| 26 | `physics/flight_sim.py:3315,3352` | docstring は pitch rate ≤20 deg/s だが実装は 35.0 |
| 27 | `physics/storage_domain.py:9` | 編集痕 `-codex approval` がモジュール docstring に残存 |
| 28 | `physics/stress_domain.py:235,281` | `from physics import aero_domain` の絶対 import + `except Exception: return 0.0` により、意図せず pitch 項が欠落 |
| 29 | `physics/records_domain.py:892-943` | `release_speed_for_apex` が 48×6000 歩の純 Python バイセクション/回 |
| 30 | `physics/mesh_domain.py:961-975` | `is_closed_mesh` が純 Python エッジ数え。10万三角形メッシュで数秒、mesh import 毎に実行 |

---

## R1-frontend — `static/js/`, `templates/` (27件)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | 調査ツールの「推奨設定を採用」が反映されない | `static/js/investigator.js:298,304` | `ConfigStore.set(section, key, value)` に配列 `["aircraft", k]` を渡しており、`store.data` に `aircraft,elevon_deg` という新キーが作られるだけ。トーストは「適用しました」と表示するが実機体パラメータは不変 |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 2 | チャンピオン「採用」がハードコード値 10m/s/12°/0° を適用 | `static/js/app.js:1146-1157,1214-1230` | 参照するスライダー要素 `#quick-slide-*` が `index.html` に存在せずフォールバック定数が適用される。トーストは实际问题を表示するため「表示と適用が不一致」 |
| 3 | 滑空比・飛行フェーズ表示が恒久的に死んでいる | `static/js/app.js:2367-2378,2406-2432` | `frame.velocity_mps` は API 応答に存在しない（実際は `vx_mps/vy_mps/vz_mps`）。`tp-glide` は常に `—`、ビューポートのフェーズピルは「待機中」のまま |
| 4 | シミュレーションエラーのバナーが完全に死 | `static/js/app.js:1763-1764,1789-1796` | `#sim-error-banner` 等7つのidが `index.html` に無く早期 return。`RUN=ERROR` 表示・発散ピル・error_id 提示が全て発生しない |
| 5 | 3Dモデル取り込みが no-op | `static/js/app.js:2623` vs `:183` | 実体は `this.meshPanel`、判定は `this.meshpanel`。glb/obj/stl 選択で何も起きない |
| 6 | 飛行ステータス行が 6箇所で死 | `static/js/app.js:1095-1101` (呼出 812, 2311, 2478) | `#status-dot`/`#status-text` が存在せず早期 return |
| 7 | フォーカスカメラが即座に平均機追従へ戻される | `static/js/viewport.js:1272-1299` vs `:1707-1710`, `:1935` | `focusTrajectory()` 後に毎フレーム `_updateCameraMode` が `mean` モードで再ターゲット。フォーカスが数フレームで無効化 |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 8 | `setEnsembleFleet` が二重定義 | `static/js/viewport.js:1349-1428`（死） / `:1450-1547`（実効） | 後勝ち。80行の死コード＋改修トラップ |
| 9 | 「既定から変更」マーカーが機体セクションで点灯しない | `static/js/state.js:36-41` + `static/js/panels.js:321-323` | `store.schema.aircraft` は fields マップ自体を保持し `.fields` が undefined。`simulation` セクションは `/meta/schemas` 自体に存在せず reset 不可能 |
| 10 | `release_pitch_deg` のスライダー重複＋DOM id 重複 | `static/js/panels.js:43,59,256` | `id="param-launch-release_pitch_deg"` が2つ存在。`label[for]` と `getElementById` が破綻 |
| 11 | 対地速度ゲージが恒久 `—` | `static/js/app.js:2352` | `frame.ground_speed_mps` は API に存在しない（NaN） |
| 12 | 取り込み3Dモデルの応力ヒートマップが no-op | `static/js/viewport.js:817-822` | `meshObject` は `THREE.Group` で `geometry` undefined。また `setHeatVisible` が `airframeMaterial` のみ操作し取り込み材質は常冷たい青のまま |
| 13 | 投入ボタン無効化でid指定ミス | `static/js/app.js:2224-2231` | 実 id は `btn-rail-gpu-ensemble`。実行中は押せる→再入ガードで無反応（toast 無し） |
| 14 | 「単機/100機一括」がモード切替ではなく即時実行 | `static/js/app.js:1424-1443` | `active` クラスだけが状態。選択表示と次回挙動が乖離 |
| 15 | テレメトリストリップチャートが飛行を暗黙に切り詰め | `static/js/telemetry.js:139` (capacity 3000), `static/js/app.js:2320` | 60秒飛行=6000サンプル。古い3000が上書きされ時間軸が30秒開始。`#rec-count`(3000)とスクラブ(6000)が矛盾 |
| 16 | クイック投入ドック約260行が死んだDOMに結線 | `static/js/app.js:1104-1343` | 20+ id 欠如。チャンピオンカードclose とEscapeハンドラを内包しているため単純削除不可 |
| 17 | プリセット削除に確認なし、削除ボタンが常時有効 | `static/js/datapanel.js:158-164,166-191` | 誤クリックで不可逆。選択なしでも押せる |
| 18 | 「既定へ」がサーバ設定の消去（確認なし） | `static/js/app.js:1554,1887-1900` | 空バンドル送信でサーバ側 aircraft/launch/environment が工場既定に |
| 19 | `notify(..., "success")` のトーンが未定義 | `static/js/investigator.js:185,308` | CSS は `error`/`warn`/`ok` のみ定義 |
| 20 | 外部CDN 2種依存（アイコン31個 + three.js） | `templates/index.html:10-18` | オフラインで起動不可、供給網リスク、Chart.jsのみSRI |

### Low（11件・要点のみ）

| # | 場所 | 内容 |
|---|---|---|
| 21 | `static/js/api.js` 等 | 死んだコード多数（`health`,`wind`,`step`,`history`,`frame`,`batch`,`meshMetrics`,`recordThrowTechniques`, `resetSection`, `clearGhost`, `reseed`, `sampleAt`）。`MAX_STEP_CALLS_PER_SECOND` は未使用（comment は60/s制限と説明） |
| 22 | `static/js/telemetry.js:54,84-90` | `showPeak: false` 固定で全ゲージのピーク表示が恒久無効、`resetPeaks()` が no-op |
| 23 | `static/js/viewport.js:1710,1738` | カメラ更新で毎フレーム `THREE.Vector3` を2つ確保（60/s→120/s） |
| 24 | `static/js/viewport.js:221-224` | `webglcontextlost` で `running=false` のみ。`webglcontextrestored` ハンドラ無しで3D表示が恒久停止 |
| 25 | `templates/index.html` / `static/js/app.js:1557-1565,1202-1213` | ARIA tabs は `aria-controls`/矢印キー移動なし。Escape で閉じないモーダルあり |
| 26 | `static/js/transport.js:225-231` | `play()` が `launch()` を await/catch せず unhandled rejection _Addendum |
| 27 | `static/js/app.js:3291-3305` vs `static/js/telemetry.js:100` | 同一物理量（応力）が計器では kPa、レジェンド/チャートでは Pa 表示。1000倍乖離 |

---

## R1-api-security — `api/`, `app.py` (26件)

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | `.env` が読まれず API_KEY が常に空 = API 無認証 | `app.py:133-135`, `:142` / `.env.example:19-27` | `load_dotenv()` が `app.py` に無い。`API_KEY` 設定しても全保護エンドポイントが到達可能。Docker は `0.0.0.0` バインド |
| 2 | `/api/v1/errors` がスタックトレース・絶対パス・クライアントIPを返す | `api/routes/records.py:210-223` / `physics/error_domain.py:44-46,101-104` | `asdict(record)` で `traceback_str`/`path`/`client_ip` を含む。#1 と複合で偵察オラクル化 |
| 3 | 全クライアントが1つのシミュレータを共有し launch がロック外で積分 | `api/core.py:240-250,267` / `api/routes/simulation.py:120-122,137-147` | `SESSION` モジュールシングルトン。`with SESSION.lock` が147行で終わる前に数秒の積分が走る。並行 launch で軌跡が混入・`/simulation/step` が launch 中の状態を破壊 |
| 4 | 最適化ジョブの params 無制限 + 入場判定 race + タイムアウト無し | `api/routes/jobs.py:108-113,123-124,135-136,143-151,182-184` / `physics/optimizer_domain.py:333-334` | `sim_duration_s`/`dt_max` 無検証。`active_count()>=4` は check-then-act race。4リクエストでジョブ枠が恒久消費され最適化APIが全て拒絶される |
| 5 | `/api/v1/guinness/simulate` の `duration_s` 無上限・clamp バイパス | `api/routes/records.py:177` / `physics/records_domain.py:2079-2082` | `simulation.py:140` は [0.1,120] で制限するがrecordsは未制限。`clamp_ranges()` も通さず `duration_s=1e9` でCPU数時間固定 |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 6 | mesh import にファイルサイズ上限なし（64MB/リクエスト） | `app.py:45`, `api/routes/mesh.py:32,58-64`, `physics/mesh_domain.py:2116-2145,2182-2187` | 上限は `MAX_CONTENT_LENGTH` のみ。モデル250件×64MB≈16GB のディスク枯渇経路 |
| 7 | `/api/v1/system/force-gpu-render` が APIキー作用域外・既定有効 | `app.py:142` / `api/routes/meta.py:205-290` | `ENV=="production"` のみ無効化。`.env` 未読込のためローカルでは常時有効。レジストリ書換＋ブラウザ起動。`remote_addr` チェックは proxy 越しで無効化 |
| 8 | CORS が localhost/127.0.0.1 の全ポートを許可 | `app.py:163-178,194-199` | 任意のローカルポートのページから API 応答を cross-origin 読み取り可能 |
| 9 | SSRF: DNS リバインディング窓 | `physics/mesh_domain.py:1643-1678,1688-1699` | 検証時と実取得で別々に名前解決。TTL 0 のホストで public↔loopback 切替で検査を迂回 |
| 10 | レート制限が全エンドポイント不在 | `requirements.txt:1-15` 等 | あるのはリクエスト単位予算のみ。1クライアントで CPU 飽和可能 |
| 11 | 不明ID が 404/409 でなく 400 | `api/routes/jobs.py:193,202,213,322,338,365`, `api/routes/mesh.py:104,117,130` | `NotFound` 例外は records のみ使用。job は未完了結果を 200、investigate は 400 と不整合 |
| 12 | グローバル ValueError/OverflowError ハンドラが内部障害を 400 化 | `app.py:95-110`, `api/routes/records.py:188-189`, `api/core.py:169-170` | 内部例外文字列をそのままクライアントへ返す。`{"error":"invalid input: <内部例外>"}` |
| 13 | ジョブ枠のリーク（スレッド生成前に running 化） | `api/routes/jobs.py:143-151,182-184,255-263,302-304` / `physics/job_manager.py:313-315,571` | `thread.start()` 失敗時 `running` のまま永久。`BaseException` は自前ワーカーで捕捉されずスレッド死亡。削除APIも無し |
| 14 | 一覧系にページング/上限なし、リングバッファ10万フレーム可 | `api/routes/simulation.py:197-205`, `physics/config_schema.py:990` 等 | `/simulation/history` が全件返却で数十MB。`ring_buffer_capacity` 最大100000 |
| 15 | `/api/v1/errors/report` が任意文字列をサーバログへ書込可能 | `api/routes/records.py:226-253` / `physics/error_domain.py:114-151` | 改行や偽のタイムスタンプでログ偽造・回転ハンドラ枯渇 |
| 16 | `/api/v1/jobs/join` がスレッドを最大30秒占有 | `api/routes/jobs.py:355-368` / `physics/job_manager.py:831-858` | Flask スレッドが食いつぶされる。フロントは未使用 |

### Low（11件・要点のみ）

| # | 場所 | 内容 |
|---|---|---|
| 17 | `app.py:44` | `JSON_SORT_KEYS` は Flask 2.3 で廃止。死んだ設定 |
| 18 | `app.py:276,286` | `--debug` で Werkzeug 対話デバッガ有効。Docker も同じバイナリ |
| 19 | `api/routes/records.py:213-214` | `limit` を素の `int()` で解析。`as_int` ヘルパー未使用でエラー形状が他と不一致 |
| 20 | `api/routes/meta.py:220-221` | JSON 配列ボディで `.get()` → AttributeError → 500 |
| 21 | `app.py:152-155` | API キーをクエリ文字列で受領（ログ漏え）＋非定数時間比較 |
| 22 | `api/routes/physics.py:201-210` | 手動 gzip が JSON provider を迂回、`Vary: Accept-Encoding` 欠落。`allow_nan` 既定で NaN 出力 |
| 23 | `app.py:186-193` | CSP が `unsafe-inline` と jsDelivr 許可、`connect-src` が `ws:`/`wss:` 全部許可 |
| 24 | `app.py:49-54` | GPU ウォームアップ失敗を `except Exception: pass` で握りつぶし |
| 25 | `api/routes/mesh.py:33` vs `:68` | multipart 経路は `uploaded.filename` を無加工（base64 経路は `secure_filename`） |
| 26 | `api/routes/physics.py:127-128` / `api/core.py:257-264` | `current_snapshot()` をロック外で読む。設定更新と競合して torn snapshot |

**コード的に問題なしと確認された点（記録）**: `eval`/`exec`/`os.system`/`yaml.load`/ユーザー制御 `pickle.loads` なし、`subprocess.Popen` の引数列は完全ハードコード、路径 traversal は `secure_filename`+`os.path.basename`+`storage_domain.sanitise_artefact_name`+親ディレクトリ解決チェックでProperに処理、CSV injection 対策済み、SSRF の策（スキーム/ポート制限・redirect禁止）は有効、フロントが実際に呼ぶAPIのキー名・型不一致なし。

---

## R1-tests-ci-deps — `tests/`, CI, 依存, リポジトリ衛生 (30件)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | master への CI が全滅（branch guard がセッション全体abort） | `conftest.py:34-53` / `tools/branch_guard.py:119-193` / `.github/workflows/ci.yml:4-5`, `verify.yml:4-5` | `PROTECTED_BRANCHES=("master","main")`, 既定 `severity="error"`。`actions/checkout` は push 時にローカル branch master を作るため必ず fatal。両WFとも `PAPER_AIRCRAFT_BRANCH_GUARD=warn` 未設定。`smoke_test.py:841-847` も同問題。毎回 master push で偽の「CI Failure」Issue が起票される<br>**→ 解消済み（2026-10-20）**: 両 WF に workflow レベル `PAPER_AIRCRAFT_BRANCH_GUARD: warn` を設定。`tools/branch_guard.py` は CI イベント checkout（`GITHUB_REF`）を識別し、push checkout のローカル branch 名を違反根拠にしない（detached HEAD 基準）。ローカル clone の master 保護は維持 |
| 2 | git に未追跡だが import 必須のモジュール5件 → クローン不能 | `physics/aero_domain.py:78`, `conftest.py:42`, `app.py:36`, `smoke_test.py:841`, `tools/notify_discord.py:86`, `tools/verify_mcp_servers.py:30` | `physics/aero_kernel.py`, `physics/origami_domain.py`, `tools/branch_guard.py`, `tools/work_report.py`, `tools/mcp_call.py`, `tools/verify_mcp_servers.py` が未追跡。新クローンで `import physics` が ImportError。部分コミットは master を恒久的に破壊 |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 3 | ツール設定4重ソース、3つが死んでいる | `pyproject.toml:37-54`, `pytest.ini:1-9`, `ruff.toml:1-25`, `mypy.ini:1-34` | pytest.ini/ruff.toml/mypy.ini が優先され pyproject ブロックは死。line-length 120 vs 100、target py311 vs py310、ruff `select` に isort(`I`) が欠落 → isort 無効 |
| 4 | `requirements.txt` と `pyproject.toml` の依存が乖離 | `requirements.txt:1-15` vs `pyproject.toml:15-32` | Flask/scipy/trimesh の下限不一致。dev系が runtime ファイルに混入。`discord.py`・`playwright` は両方に未宣言。`torch` extra は片方のみ。setup は pyproject、CI は requirements、Docker は requirements で3系統 |
| 5 | `cupy-cuda12x` がハードランタイム依存 | `pyproject.toml:19`, `requirements.txt:5`, `Dockerfile:11`, `setup.sh:47-53`, `setup.ps1:47-53` | コードは CPU フォールバックを持つのに CUDA 必須。`setup.sh` は `set -euo pipefail` で GPU 無し環境だとabort。`setup.ps1` は `$LASTEXITCODE` 未検査で成功表示。2スクリプトの失敗セマンティクス不一致 |
| 6 | APIキー認証ミドルウェアのテスト 0件 | `app.py:130-157` / `tests/` 全体 | `API_KEY`/`X-API-Key`/`Authorization`/`401` の参照が suite 内に存在しない。比較反転 regression が全テスト通過 |
| 7 | security監査ジョブが失敗できない | `.github/workflows/ci.yml:87-94` | `pip-audit --desc on \|\| true` が exit status を破棄。`security-events` 権限も成果物保存もなし |
| 8 | 40MB 生成物がコミット済み、`artifacts/` が未 ignore | `artifacts/physics_repair/after.json` (40,269,444 B), `baseline.json`, `cli_smoke/after.json` / `.gitignore:23-30` | `.git` だけで 41.7MB。`search/` がさらに 73MB |
| 9 | API契約テストが2系統に分散、8エンドポイントと全エラー経路が未テスト | `api/routes/*.py` (47 routes) / `tests/` (14 endpoints) / `smoke_test.py` (~35) | 未テスト8件: `/errors/clear`, `/guinness/simulate`, `/guinness/verification`, `/jobs/join/<id>`, `/meta/physics-references`, `/system/gpu`, `/system/force-gpu-render`, `/simulation/launch`。`smoke_test.py` は `tools/run_ci_tests.py` から実行されず CI の失敗検知に未接続 |
| 10 | GPU経路が CI で一度も実行されず、GPU↔CPU スキーマ一致テストも無し | `tests/test_issue28_gpu_duration.py:13-54`, `tests/test_ensemble.py:11-74`, `tests/test_optimizer.py:695-701` | GitHub runner に GPU なし → 名前に反して NumPy fallback を検証。ベクトル GPU 実装の CI カバレッジ 0%。`agents.md` §2 の「返却スキーマ完全一致」要求が未検証 |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 11 | テストがプロセスグローバル singleton を変更し順序依存 | `tests/test_security_and_frames_audit.py:98`, `tests/test_records.py:73-97` / `api/core.py:267-270` | `app.config["TESTING"]=True` が復元されない。`SESSION` が `test_records` の POST で書き換わる。collection 順序で結果が変わる |
| 12 | `discord.py` 未宣言 → Discordブリッジのテストが全スキップ | `tests/test_discord_bridge.py:12-26`, `tools/discord_bridge.py:1154` | 両依存ファイルに未宣言。51KB のtrackedモジュールが CI で無カバレッジ |
| 13 | `tools/test_*.py` 11本が import 時に 35 秒シミュレーションを実行 | `tools/test_75deg_launch.py:7-18` 他8本 | `if __name__=="__main__"` ガード無し。`pytest .` や IDE の全テスト実行で 11×35秒=6.5分の無駄計算 |
| 14 | CI WF 2本が重複、coverage 成果物なし、concurrency なし | `.github/workflows/ci.yml:14-72`, `verify.yml:9-53` | push 1回で pytest 約7回+smoke 6回。`--cov` 未使用。`upload-artifact` 0件。`concurrency:` 未定義 |
| 15 | mypy が最大3モジュールを免除、CI の型検査範囲が AGENTS.md と不一致 | `mypy.ini:15-22`, `ci.yml:37`, `verify.yml:36`, `agents.md:54` | `ignore_errors=True` が `mesh_domain`/`flight_sim`/`auto_investigate`。`smoke_test.py` と `tools/` は型検査されない |
| 16 | 本番 Docker イメージにテストツールチェーンと100MB超の非ランタイムデータ | `Dockerfile:11,25`, `.dockerignore:1-15`, `requirements.txt:10-15` | runtime に pytest/mypy/ruff。`search/`(73MB) `artifacts/`(41MB) `docs/images/`(12MB) `tests/` を COPY。runtime/dev 分割なし |
| 17 | ツール3本が誤ったリポジトリを参照 | `tools/check_runs.py:27`, `check_issues.py:21`, `read_issues.py:6` | `minetan1229/air_simulation` をハードコード。実際は `minegishi/paper_airplanes/simulation/v3`。`agents.md` が CI/Issue 確認に指示しているツール群が無関係リポジトリを報告する |
| 18 | 失敗通知スクリプトが全エラーを握りつぶして exit 0 | `tools/create_failure_issue.py:121-127`, `close_resolved_issue.py:83-84`, `check_runs.py:44-45`, `check_issues.py:36-37` | Issue 起票失敗でも緑。ラベル欠落や token 権限制御でも検知不可 |
| 19 | `.env.example` が不完全＋未使用変数を文書化 | `.env.example:1-27` / `tools/check_runs.py:21` 等 | `GITHUB_TOKEN`/`GH_TOKEN` が欠落（6ツールが読む）。`SECRET_KEY` はコードから一切参照されない死設定。`.env` 自体は正しく gitignore 済み |
| 20 | 孤立・重複ドキュメント群 | `archive/PHYSICS_REPAIR_PLAN.md` vs ルート版 / `wiki-docs/` vs `docs/` | archive 版はルート版と SHA-256 同一（16,217B）。PROGRESS 版は既に乖離。`wiki-docs/` は `docs/` と内容が別に存在的で README からリンクなし |
| 21 | ルート直下のスクラッチファイルが未 ignore | `tmp_conv.py`, `tmp_diag.py`, `tmp_diag2.py` / `.gitignore:1-42` | `git add -A` で混入。`tmp_*` ルール自体が無い |
| 22 | setup スクリプト2本の Python バージョン判定・失敗意味が不一致 | `setup.sh:23-24,46-53`, `setup.ps1:17-18,46-53` | 3.10 未満を拒否しない。`setup.sh` は abort、`setup.ps1` は「完了」表示 |
| 23 | `Dockerfile` の `ENV=production` は「変数名ENVにproduction代入」 | `Dockerfile:30` | 意図した `ENV FLASK_ENV=production` ではなく、偶然 `meta.py:217` が読む `ENV` として機能。typo に近い状態 |
| 24 | GPU起動ヘルパーの2実装が乖離、絶対パスハードコード | `launch_gpu_render.bat:10-22`, `launch_gpu_render.ps1:10-61` | bat は Chrome 全プロセスを強制終了。WF/setup/README から未参照 |
| 25 | 自明なアサーション | `tests/test_work_report.py:92` | `assert "owner/repo" not in summary or True` は常に真 |
| 26 | 追跡されているエージェント設定が承認/sandbox を無効化 | `.codex/config.toml:1-2` | `approval_policy="never"`, `sandbox_mode="danger-full-access"` |
| 27 | `tools/capture_ui.py` が未宣言依存・CWD相対パス・追跡PNGを上書き | `tools/capture_ui.py:3,6,30-31` | playwright 未宣言。`docs/images/*.png` を無警告で上書きし巨大binary diff。readiness check なし |
| 28 | `agents.md` は大文字小文字を区別するFSで解決不能 | `agents.md:1` | 追跡名は小文字、ハーネスは `AGENTS.md` 参照。Linux CI では `AGENTS.md` が存在しない |
| 29 | `.gitignore` にツールキャッシュと pytest 要約が未記載 | `.gitignore:32-36`, `tools/run_ci_tests.py:92` | `.mypy_cache/`/`.ruff_cache/` はツールの自己 ignore に依存。`pytest_failure_summary.txt` は CI でリポジトリ直下に出力 |

### Low（1件・他は上記Mediumに含む）

| # | 場所 | 内容 |
|---|---|---|
| 30 | `agents.md:58,76` / `tools/verify_mcp_servers.py:30` / `.kilo/kilo.jsonc` | `agents.md` が指示する `tools/verify_mcp_servers.py`・`tools/mcp_call.py` と `.kilo/kilo.jsonc` が未追跡。AGENTS.md 記載コマンドが開発者以外で失敗 |

---

# Round 2

> R2 は R1 の情報を与えず、**新規の視点**で5名のコンテキスト無しサブエージェントが独立実施。

## R2-paper-conformance — 論文一致度監査 (28件: C1/H5/M8/L14)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| F-01 | GPU経路が**ラジアン**のAoAを**度**解釈カーネルへ渡す | `physics/parallel_engine.py:163,215` vs `:1137,:1744` | 実効αが57.3倍過小。GPU機体は失速せず、Hoerner剥離抗力が死に、C_D≈0.023のまま。数値実測: α=14°で GPU C_L=0.132 vs 正準 0.810 |
| F-03 | 最適化の**評価モデルがハードウェアで切り替わる** | `physics/optimizer_domain.py:458-466` | GPU→3DoF近似、CPU→6DoF RK4。Pareto front・実行可能性マスクが機器依存 | 

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| F-02 | Viterna–Corrigan が API と README で公開されているが**実装ゼロ** | `api/routes/meta.py:76-87` / `README.md:22` | grep で `viterna` 実装なし。失速後は Hoerner平板 moreover レジストリ自体の C_D 式も誤記（正: `B₁sin2α+B₂cos²α/sinα`） |
| F-04 | `ground_level_m ≠ 0` で風場が**恒久ゼロ** | `physics/flight_sim.py:839-875` + `physics/wind_domain.py` | 地上高を2重減算。実測: `ground_level_m=100` で \|u\|=6.615→0.000 |
| F-05 | GPUバッチが**独自の応力モデル**を使用 | `physics/parallel_engine.py:1843-1855` vs `physics/stress_domain.py:211+` | 6DoFは梁理論、GPUは独自式、surrogateは第三の式 |
| F-06 | `isa_air_density` が**相対湿度**にvirtual温度補正を適用 | `physics/parallel_engine.py:441-465` | 正しくは水蒸気質量比。実測 0.9935 vs 1.2211 kg/m³ = **18.6% の密度不足**（GPU両経路） |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| F-07 | GPUバッチの失速角ハードコード 0.244 rad | `physics/parallel_engine.py:1808,1810` | 同一関数が `stall_alpha_arr` を受け取るのに使っていない。`stall_alpha_deg` 設定が無効 |
| F-08 | `_local_aero` が**第2の非合流空力モデル** | `physics/flight_sim.py:886-1008` | Hoerner抗力欠落・失速後揚力線形ランプ・ailside権限4.3倍差・cm_q 2倍差・pushover/減衰なし |
| F-09 | GPU経路は `aero_kernel` を使わず**独自再実装** | `physics/parallel_engine.py:130-224` | docstring は「reference path と厳密に一致」とclaiming。実際の `gpu_dynamics.simulate_batch`（正しい6DoF GPUカーネル）は製品経路から未接続 |
| F-10 | Polhamus 分岐の失速アンカーが Polhamus 式でない | `physics/aero_kernel.py:360-363` | kp>0 で曲線が失速点で不連続。`:356` に `if True:` 残留 |
| F-11 | メッシュパネル法が**第4の空力モデル** | `physics/mesh_domain.py:1512,1519,1532` | 2次元揚力傾斜 6.28/rad（正準の2.22倍）、`2sinαcosα`、`0.02qA`（SPEC は `cos²α`）。`panel_aerodynamics` は呼び出し元なし |
| F-12 | 既定機体の翼面積が文献値より**3〜7倍小さい** | `physics/config_schema.py:237-239` | S=0.015 m²（翼荷重 333 g/m² vs 文献 50-100）。高速dart化。設計変数レンジも同.Identity |
| F-13 | 記録プリセットが**非物理的AR/CD0**で_duration に合わせ込む | `physics/records_domain.py` | AR 7.0-7.4（文献 2-3）、L/D 19-20（物理上限 4-10）。29-31秒は「導出」ではなく「合わせ込み」 |
| F-14 | GPU経路が圧縮性・Reynolds補正・ABL/突風/熱流を**全て欠落** | `physics/parallel_engine.py` | Mach補正なし、cd0 の Re 補正なし（Re≈4e4 で15%抗力誤差）、風は定数ベクトル |
| F-15 | `max_ld_ratio` は**瞬間ピーク**であり滑空比ではない | `physics/flight_sim.py:3137-3138` | _MAIN KPI が構造的に楽観的。実滑空比（水平距離/高度損失）が未計算 |

### Low（要点のみ）

| # | 場所 | 内容 |
|---|---|---|
| F-16 | `README.md:23` | Hoerner の**法線力**を「直交剥離抗力」と誤ラベル（コードは揚力） |
| F-17 | `aero_kernel.ground_effect_factor:191` | φ_GE は「低減係数」ではなく**保持係数**（地上で0→自由大気で1） |
| F-18 | `config_schema.py:277-278` | 既定は `polhamus_kp=kv=0` → README の Polhamus 項は**オプトイン** |
| F-19 | `atmosphere_domain.py` docstring vs `meta.py:124` | Sutherland 年号 1895 vs 1893 の内部不整合 |
| F-20 | `docs/SPECIFICATION.md:5` vs README | Wieselsberger 年号 1921 vs 1922（正: 1922） |
| F-21 | `config_schema.py:481` | SPEC §4.4 の「OSエントロピー由来」乱流は実際には**決定的**（seed固定） |
| F-22 | `flight_sim.py:1607-1613` | SPEC §1.2 の「2秒で昇降終了」ルールが存在しない |
| F-23 | `flight_sim.py` / `parallel_engine.py:1698` | SPEC §1.2 の「1000フレーム」予算と実値（20000/3000）が不一致 |
| F-24 | `stress_domain:557` / `flight_sim.py:129-137` / `parallel_engine.py:1845` | 許容応力則が**3重に重複**定義（数値は一致） |
| F-25 | `flight_sim.py:2691-2700` | 着地衝撃時のみ動圧を差し替えるため、heatmap の応力indexと `max_stress_index` が非比較 |
| F-26 | `mesh_domain.py:1532` vs SPEC §3.3 | パネル抗力式が SPEC と不一致、かつ力ベクトルとして未適用 |
| F-27 | `aero_kernel.py:181` vs `flight_sim.py:597` | Mach キャップが 0.85 と 0.99 の2値（係数 1.897 vs 5.0） |
| F-28 | `parallel_engine.py:984,1691` | 風規約のヘルパーが重複実装（符号は一致、SPEC の「共有」主张は偽） |

### 既存監査 `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` への判定

- **反証（现已修复）13件**: H-03, H-04, H-06, H-07/08/09, H-12, H-15, H-16, H-17, H-18, H-20, M-03, M-04, M-07, M-10, M-11, M-25
- **確認（未修正）8件**: H-14(`_local_aero`乖離), H-19(`fold_trailing_deg` 接続なし), M-12, M-13/M-14, M-16(質量/坪量/用紙面積不整合), M-30(`static_margin` 無効), M-31(drag polar Re 不整合), M-26
- **部分解決 3件**: H-10(応力index 3系統), H-13(モーフ), M-06(刻み規則)
- **旧監査の脱漏 10件**: F-01(deg/rad), F-03(機器依存モデル切替), F-06(湿度補正), F-04(風高度原点), F-07, F-02, F-12/F-13(次元妥當性), F-15, F-24, `gpu_dynamics` 未接続

---

## R2-frontend-a11y — フロント品質/アクセシビリティ (52件: C3/H7/M29/L13)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | 設定トグル8個すべてが **"有効"** と同一のアクセシブル名 | `static/js/panels.js:230` (`:213,227-231`) | `spec.hint` 未定義のため既定 "有効"。可視ラベルは `<span class="field-key">` で `<label>` 関連付けなし。スクリーンリーダでは8個区別不能 |
| 2 | 「推奨設定を採用」が**何も適用せず成功表示** | `static/js/investigator.js:296-308` vs `static/js/state.js:52-58` | `store.set(["aircraft",k], recAir[k])` と配列を渡す → `data["aircraft,k"]` に新規作成。`store.data.aircraft` は不変 |
| 3 | エラーバナーと飛行ステータス行の**DOMが存在しない** | `static/js/app.js:1763-1764,1095-1098,1782,1793` / `static/css/console.css:887-971` | `_cacheDom` の202 id中 **35個が `index.html` に不在**。88行の精心されたバナーCSS が永久に死 |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 4 | フレームカウンタが文字列 "undefined" を表示、スクラブが動かない | `static/js/app.js:2441,2448` / `static/js/transport.js:20-45` | `Transport` に `scrubIndex` が存在せず `String(undefined)`。`aria-valuenow` も未更新 |
| 5 | 抗力ポラルの右軸が **C_D** だが描かれているのは **L/D** | `static/js/telemetry.js:506` vs `:438-454` | 右軸目盛 0.0-0.3 の横に L/D 0-8 の曲線。数値の誤読を誘発 |
| 6 | 狭幅で左右レールが**キーボード到達不能**かつ invisible なままフォーカス可能 | `static/css/console.css:1695-1717` / `static/js/app.js:470-478` | 開閉は `<div class="rail-head">` のクリックのみ（tabindex/role/キー処理なし）。`transform` .off-canvas なので60個のコントロールが tab order に残る |
| 7 | トグル状態が**色のみ**で表現、aria 状態なし | `static/css/console.css:800-804` | `.chip[data-on="1"]` にアイコン/下線/`aria-pressed` なし。`.chip.active` は CSS ルール 0 件 |
| 8 | モーダルに `role="dialog"` なし・フォーカス罠なし・focus 復元なし・Escape 不可 | `templates/index.html:763,886,946,966` / `static/js/app.js:1596-1601,1202-1213,2597` | Escape は `#help`/`#gpu-modal` のみ。自動表示される `#flight-result-modal` は不可 |
| 9 | 5つの canvas/WebGL ビューに**テキスト代替なし** | `templates/index.html:233,339,389,403,563` / `static/js/telemetry.js:283` | チャート値は `ctx.fillText` で描画されるのみ。`aria-live` は `#toasts` のみ |
| 10 | ゲージが**色のみ**で重症度を表現し、AAコントラスト違反 | `static/css/console.css:649-651,13-21` | warn `#fbbc04`=**1.71:1**, ok `#34a853`=3.06:1, bad `#ea4335`=3.92:1（4.5:1 必要） |

### Medium（要点のみ・全29件のタイトルと場所）

| # | 場所 | 内容 |
|---|---|---|
| 11 | `console.css:13` | `--muted #80868b` が全二次テキストで AA 違反（3.49/3.68/3.31:1） |
| 12 | `telemetry.js:202-215,274-276` | ストリップチャートが**軸上限パディング値**をデータ値として表示（高度5.00を5.90と表示） |
| 13 | `index.html:583,445-448,273,281` / `panels.js:192,238` / `optimizer.js:78-89` | `aria-label` が可視ラベルを上書き（WCAG 2.5.3）、多数の control にアクセシブル名なし |
| 14 | `templates/index.html` + 13 JS | **i18n層が全く無い**（CJK 3,372字＋JS 4,200字がすべてハードコード）。`i18n|locale|gettext|Intl.` の grep 0件 |
| 15 | `airframe.js:21` vs `panels.js:16` vs `index.html:128-130` | 同じ機体形の名称が3系統で異なる（"glider (suzanne)" / "グライダー" / "ペーパーグライダー"） |
| 16 | `optimizer.js:13-19` | 目的関数方向が `" maximise"`（先頭空白・英単語）で英語漏れ |
| 17 | `console.css:1587-1589` | toast tone `info`/`success` が未定義。推奨適用トーストが既定色 |
| 18 | `app.js:860` vs `api.js:173` | `api.export` は未定義 → `TypeError` が catch され常に**ブラウザ側 Blob フォールバック**。サーバ側 export は到達不能 |
| 19 | `telemetry.js:11-33` vs `app.js:3291-3313` | `GAUGE_SPECS`/`AIR_SPECS` が二重定義で**内容が乖離**（応力 Pa vs kPa）。telemetry 側が死コード |
| 20 | `console.css` 全体 | `!important` 52個 / inline style 属性101個 / 未定義トークン `--radius-sm` 等 |
| 21 | `console.css:35-41,471,818,832` | z-index スケール（`--z-banner:40`）を magic number 50 が**貫通** |
| 22 | `console.css` | CSSセレクタ11個が重複定義（後勝ち）。`:root` が `:7` と `:3148` の2箇所 |
| 23 | `console.css` | マークアップ/JS が使う class 10個に**CSSルール皆無**（`.metric-row`, `.btn-outline`, `.hud-phase-pill` 等）。ギネス諸元表が無スタイル |
| 24 | 全体 | `prefers-reduced-motion` 対応ゼロ。`rec-blink` 1.2s **無限**アニメーション |
| 25 | `console.css:59,85-86` | `overflow:hidden` + `100dvh`、**印刷スタイルなし** |
| 26 | `console.css:1621-3200` | ブレークポイント10個が2系統の矛盾した順序。レールドロワーが2実装（`.is-open` 274px と `.mobile-open` 320px、JSは `is-open` のみ使用） |
| 27 | `console.css:558-573` 等 | タッチターゲットが最小24px未満（range thumb 9×13px、`.chip` 19px、プリセット 15px） |
| 28 | `console.css:269,396,574,805,1294,1865` | focus-visible 未定義の control 多数。`:1294`/`:1865` は `outline:none` のみ |
| 29 | `index.html:423-433` / `app.js:481-504` | tablist 構造不正（`aria-controls` なし、roving tabindex なし、矢印キーなし） |

### Low（要点のみ・全13件）

| # | 場所 | 内容 |
|---|---|---|
| 30 | `console.css:1-5` | ヘッダ宣言「No card shadows, no pill buttons, no icon fonts」に対し 20+ box-shadow・12/16/20/28px ピル・iconify アイコン |
| 31 | `telemetry.js:53,84-90` | `showPeak` が恒久 false → 全ゲージの `pk` 表示が空 |
| 32 | `lib.js:58,4` | 到達不能分岐 `fixed === "-0"`。`$$` は未使用 export |
| 33 | `lib.js:62-71` | `sig()` が 0<v<1 で桁数不揃い（"0.000500" vs "25.0k"） |
| 34 | `app.js:1017-1086` | ランク表の `<tr>` がマウス専用（tabindex/role/キー処理なし） |
| 35 | `index.html:868-876` | ランク表ヘッダに単位なし。`td` に等幅指定で日本語/絵文字も等幅 |
| 36 | `meshpanel.js:291-373` / `optimizer.js:512-541` / `index.html:619-625` | 同じ「諸元表」が table と無スタイル div grid で2実装 |
| 37 | `index.html:118,169,178,186` | `<section open>` は不正 HTML（`open` は `<details>` のみ） |
| 38 | `console.css:1431,2776,2968,1422,1990,1997` | 未定義 CSS 変数（`--radius-sm` は fallback なし → `border-radius:0`） |
| 39 | `optimizer.js:98,128` | `const row` 宣言前の参照。ウェイトスライダが `.obj` grid 外に append |
| 40 | `index.html:6` | `color-scheme: light` 宣言。上部レール/トランスポートは暗色ハードコードで dark 変種なし |
| 41 | `templates/index.html` / JS | 日本語 UI 文字列の**英語混入**と表記ゆれ |
| 42 | `telemetry.js:506` | 抗力ポーラ凡例が**皆無**（C_D/L/D 曲線の区別不能） |
| 43 | `viewport.js:893-913` vs `telemetry.js:97-100` | 3D軌跡は応力ランプ色、チャートは4つの別色 → 色的意味が衝突 |
| 44 | `viewport.js:1050-1057` vs `telemetry.js:97-100` | 3Dシーンとチャートで同義色が異なる値 |
| 45 | `app.js:2435-2438` / `transport.js:216-217` | スクラブ範囲が sample 数と1つずれ、2つの異なる分母を混用 |
| 46 | `app.js:1897`, `datapanel.js:201,226` | ストアの公開APIを4箇所迂回。ストアは module-level singleton |
| 47 | `app.js:55` vs `transport.js:34` | `tunnelMode` が2箇所に二重所有 |
| 48 | `panels.js:6-7` vs `:164-166` | docstring は「サーバ範囲が勝つ」とするが実装は常に手書きテーブル |
| 49 | `transport.js:323,363`, `optimizer.js:287,382`, `meshpanel.js:196` | ネットワーク/ポーリング失敗を**無言で握りつぶし** |
| 50 | `app.js:1019-1078,3022` / `lib.js:17` | サーバ値を `innerHTML` に直接埋め込み。`escapeHtml` は2箇所のみ。`make()` に未使用の html シンク |
| 51 | `app.js:860` / `api.js:173` | `api.export` 未定義（上記 #18 と重複のため本表では一本化） |

---

## R2-config-contract — 設定/データ契約 (23件: H6/M9/L8)

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | 温度/気圧スライダが `isa_mode` ON（既定）で**無効** | `static/js/panels.js:65-66` → `api/core.py:177-180` → `physics/atmosphere_domain.py:397-410` | サーバの自動無効化は `payload` に `isa_mode` が無い場合のみ発動するが、UIは常に全 environment を送るため到達不能。UIも `isa_mode` を落とさない |
| 2 | サーバの clamp 済み設定が**クライアントで破棄**される | `api/routes/physics.py:143` → `static/js/app.js:1879` | `_push()` がレスポンスを捨てる。UI は物理が使わない値を表示（mass 5g → 物理 1.0kg） |
| 3 | 危険な係数4件に**範囲検証なし** | `physics/config_schema.py:289,279,345-347` vs `clamp_ranges():389-453` | `post_stall_slope`, `cLalpha_per_rad`, `ref_area_m2/length/span` が clamp 対象外。`post_stall_slope=-0.35` で40°時に C_L≈-8.2（重量の2200倍） |
| 4 | バンドル取り込みが clamp を**完全バイパス** | `api/routes/records.py:82-87` vs `api/core.py:165-167` | aircraft/launch/simulation は `from_dict` のみ。`validate_config_bundle` は範囲検証を一切行わない（docstring は逆を主張） |
| 5 | 設定書き込みが**部分適用**される | `api/routes/physics.py:129-143` | 4セクションを順次適用しロールバックなし。launch の検証失敗で aircraft/environment は適用済み・クライアントは旧値保持 |
| 6 | クイック調整ドックが**インパルス投擲モードで無視**される | `static/js/app.js:1155-1157` vs `static/js/panels.js:308-312` | パネルスライダは `throw_speed_source="direct"` を再宣言するが、ドックはしない |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 7 | `release_pitch_deg` が二重登録、前方は「迎え角 (AoA)」と誤ラベル | `static/js/panels.js:43,59,305-307,330-354` | 2つのスライダが1値を奪い合う。実 AoA は `flight_sim.py:1684` で導出 |
| 8 | `dt_min`/`record_interval_s` が**ミリ秒表示だが単位は "s"** | `static/js/panels.js:105,108` vs `physics/config_schema.py:960,964` | `dt_min=1e-5 s` が "0.01 s" と表示。隣接の `dt_max` は実秒で同一単位表記 |
| 9 | サーバ公開範囲が**UIで読まれない**（二重正本） | `config_schema.py:1471-1509` vs `static/js/panels.js:14-120,164-166` | `LAUNCH_RANGES` 3値が UI と不一致既に乖離 |
| 10 | `/meta/schemas` に `simulation` 節が無く、`aircraft` の展開方法が他と別 | `api/routes/meta.py:32-46` / `static/js/state.js:36-44` | `store.schema.aircraft.fields` が undefined → 変更マーカー不点灯、`resetSection("aircraft")` 無効 |
| 11 | 部分セクション payload が**セクション全体を既定へリセット** | `api/core.py:157-170` | `build()` は既定から構築。environment のみ `merge_environment` でパッチ。他3節は置換。同一エンドポイントで意味が混在 |
| 12 | `throw_style`/`flight_mode` が**未検証**、未知styleでimpulse有効化 | `config_schema.py:888,891,919-946,948-954` / `flight_sim.py:1429-1439` | `flight_sim` は direct にフォールバック警告を出すが `impulse_active` は生文字列を判定 → 警告と逆の挙動 |
| 13 | 記録プリセットが**自前のトリム不変条件に違反** | `physics/records_domain.py:1009,1023-1029,670` vs プリセット群 | 9/12プリセットが `release_pitch_deg=0.0` を明示 → `setdefault` 発火せず、`RECORD_TRIM_ALPHA_DEG=3.0` が死 |
| 14 | 永続バンドルに**schema version がなく**、1項改名で全不能 | `physics/storage_domain.py:92-97,717-746,904-910` | `strict=True` で未知キーが ValueError。UI実体gosは localStorage（版管理なし、24件上限） |
| 15 | 成果物書き込みが**非アトミック** | `physics/storage_domain.py:882-888` | `open(path,"w")` で truncate→stream。クラッシュで切れた JSON が残り `load_config` が例外 |
| 16 | 設計変数のカタログが**3箇所に重複**し範囲も不一致 | `api/routes/meta.py:42-43`, `config_schema.py:1460-1462,1178-1201` | `wingspan_m` 等で UI/clamp と不一致 |
| 17 | `shape_flag` は**描画のみ**、物理から未読 | `static/js/panels.js:16` / `physics/config_schema.py:245` | clamp 対象外。`shape_flag=99` も受理 |
| 18 | `_airframe(trim_alpha_deg=…)` が **TypeError** | `physics/records_domain.py:1030-1036` | pop が payload 構築後に走るため `AircraftConfig(**aircraft)` が未知引数で失敗 |
| 19 | プリセット質量**坪量と矛盾** | `physics/records_domain.py:949-952,1138,957-960` | implied 80.2 g/m² vs 宣言 99.0。`gwr_compliance` は "legal: true" のまま |
| 20 | `step_trip_ratio` の文書範囲(0..0.02)と強制範囲(0..0.05)不一致 | `config_schema.py:325` vs `:420` | UI は 0-0.02、API は 0.04 を受理 |
| 21 | 整数項目が**切り捨て**（拒否でない） | `config_schema.py:189-190` vs `api/core.py:132-143` | `int(64.9)=64`。他エンドポイントは `as_int` で拒否 |
| 22 | 単一グローバルセッション・revision なし・ETag なし | `api/core.py:267,192-264`, `app.py:200` | `X-Simulation-Session` は CORS 許可のみで未使用。タブ2つで last-write-wins |
| 23 | 幾何サマリが `aspect_ratio`/`ref_*` を無視 | `static/js/airframe.js:35-51` vs `config_schema.py:1342-1440` | 3D は常に b²/S、ソルバは `aspect_ratio` を使用。最適化「反映」後に描画と計算が不一致 |

### Low

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 24 | `physics/records_domain.py` | `records_domain.py:1030` | `trim_alpha_deg` を override に含むプリセットは TypeError（潜在） |
| 25 | `records_domain.py:1034` | 同上 | pop が no-op |
| 26 | `records_domain.py:1138` | 同上 | 質量 5.0g vs 坪量 99 g/m²・A4面積 0.0624 m² → 6.24g |
| 27 | `api/routes/meta.py:42-43` | 設計変数カタログ重複（上記16と重複） |
| 28 | `static/js/app.js:2749` / `records_domain.py:1859-1920` | ロータークラフト記録（27.9s/17s）が「飛距離」カテゴリで表示される |
| 29 | `app.js:2758` vs `api/routes/records.py:135-139` | 「投げ方は変更せず」のコメントだがサーバは `set_launch` を実行 |
| 30 | `api/routes/jobs.py:112` vs `static/js/optimizer.js:213` | 最適化は 6 秒flight、コンソールは 25-60秒。`max_distance` が別物 |
| 31 | `api/routes/records.py:27` | `POST /api/v1/data/export` が UI から未到達（`api.export` 未定義） |
| 32 | `static/js/datapanel.js:8-28` | localStorage プリセットに version なし。schema 変更で全不能 |
| 33 | `api/core.py:257-264` | `current_snapshot()` をロック外で読む → torn snapshot |
| 34 | `physics/config_schema.py:245` | `shape_flag` が `clamp_ranges()` に無い |

---

## R2-ops-tooling — 運用/自動化/可観測性 (38件: C2/H8/M20/L8)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | branch guard が **master への全 CI を abort** | `conftest.py:34-50` / `tools/branch_guard.py:66-69,119-122` | `actions/checkout` は push 時にローカル branch を作るため `master` と判定。両WFが `PAPER_AIRCRAFT_BRANCH_GUARD=warn` 未設定。AGENTS.md Phase6 の "master push" が必ず失敗し、偽の CI Failure Issue が起票され自動クローズも不可<br>**→ 解消済み（2026-10-20）**: 両 WF に workflow レベル `PAPER_AIRCRAFT_BRANCH_GUARD: warn` を設定。guard 側は CI イベント checkout（`GITHUB_REF` ベース）を検出し、push checkout の branch 名を違反根拠にしない |
| 2 | Discord bridge が**既定で全許可**、無認証で特権 agent を実行 | `tools/discord_bridge.py:379-385,742-748,1004-1010,1138` / `.env.example:7-8` | `ALLOWED_USER_IDS` 空なら全員許可。チャットが `agy -p <prompt> --dangerously-skip-permissions` に素通し。`!chrome <url>` も無制限 |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 3 | Knowledge.md が**実在しないガードレール5件を主張** | `.agents/Knowledge.md:38` vs `tools/discord_bridge.py:736,818-819,894,1038-1046,1056-1059,904-906` | stderr ドレイン/二重タイムアウト/同時実行上限/入力検証/error-id 送出のいずれも未実装。stderr 未ドレインは**デッドロック**、タイムアウト未設定は**無限ハング** |
| 4 | プロトコルが指示する**6ツールが未追跡** | `agents.md:56,58` / `tools/*` / `.kilo/kilo.jsonc` | `git commit -a` で `notify_discord.py` が未追跡 `work_report.py` を import → `verify.yml:40` が ImportError |
| 5 | CI/Issue 検査ツールが**失敗時も exit 0** | `tools/check_runs.py:44-45`, `check_issues.py:36-37`, `read_issues.py:15-16`, `close_resolved_issue.py:83-86`, `create_failure_issue.py:121-129` | 「CI 緑」の判定が空出力として通る |
| 6 | `logs/error.jsonl` が**回転ハンドラを迂回** | `physics/error_domain.py:126-133` vs `:152` | `stream.write` 直書きで `emit` を通らないため無制限増加（`error.log` のみ回転） |
| 7 | Knowledge.md の「mypy 52→0件」主張が**現時点で 52件** | `.agents/Knowledge.md:39` vs `tools/discord_bridge.py:244,277` | `Select` のサブクラス化未実施。`tools/` は CI の型検査対象外 |
| 8 | 「正本仕様」「AGENTS.md パス」が**実在しないファイルを指す** | `.agents/Knowledge.md:41`, `Knowledge-important.md:3`, `.agents/skills/loop-handover.md:6`, `docs/SPECIFICATION.md:168,170,172` | `docs/discord-bridge.md` 不在。`.agents/AGENTS.md` 不在。`Knowledge-important.md` は 3行のアーカイブ |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 9 | Project.md の Loop State が**実リポジトリ状態と矛盾** | `.agents/Project.md:23-27` vs `git status` | 「P0 未着手」と記載だが reality は 40ファイル +2477/-1257 の未コミット変更と新規5モジュール |
| 10 | MCP ツールが**サーバ不在時に生トレースバック** | `tools/verify_mcp_servers.py:216-217`, `tools/mcp_call.py:86,96-97,118` | `MCPStdioClient` 構築が `try` の外。`JSONDecodeError` が `MCPError` を逃す |
| 11 | `notify_discord.py` が webhook 未設定で exit 1（プロトコルは正常スキップ規定） | `.agents/skills/notify-webhook.md:12` vs `tools/notify_discord.py:210-211,279` | 環境起因の失敗を無限リトライすることになる |
| 12 | **Webhook URL がトレースバックに漏出** | `tools/notify_discord.py:216-220` | `Request` 構築が `try` の外 → ValueError でURL（秘匿トークン含む）が stderr へ |
| 13 | CI失敗Issueの**自動クローズが PR で成立しない** | `tools/close_resolved_issue.py:58-60`, `create_failure_issue.py:71`, `verify.yml:4-7` | `GITHUB_REF_NAME` が push では `master`、PR では `42/merge`。完全一致比較で永不クローズ |
| 14 | 破壊的自動化に **dry-run も確認も無い** | `close_resolved_issue.py:69-81`, `create_failure_issue.py:113-119`, `clear_discord_commands.py:45-70` | コマンド全削除。`--all-clean` フラグは表示文のみ変化（挙動同一） |
| 15 | 3ツールが**リポジトリ slug をハードコード** | `tools/check_runs.py:27`, `check_issues.py:21`, `read_issues.py:6` | `GITHUB_REPOSITORY` 非対応。fork で別リポジトリの Issue を操作し得る |
| 16 | **タイムアウト無し**のネットワーク呼び出し | `check_runs.py:30`, `check_issues.py:24`, `read_issues.py:9` | `urlopen` に `timeout=` なし。無限ハングで自律ループが停止 |
| 17 | bridge に**単一インスタンス制御・レート制限が無い** | `tools/discord_bridge.py:477-491,171-219,1012-1031,1138` | 二重起動で全応答重複。`/chats` が他セッションのプロンプトを漏出 |
| 18 | 未知 task_id の abort が**全タスクを kill** | `tools/discord_bridge.py:406-428` | 該当なしでも `channel_id is None` 分岐で全 kill |
| 19 | MCP エントリポイントが **CWD 相対** | `.kilo/kilo.jsonc:6,12,18,24,30`, `.kilo/mcp/launch.mjs:4-7` | README は「cwd 非依存」と主張するが launcher 自身は CWD 依存 |
| 20 | Discord bridge の**CI カバレッジ 0** | `tests/test_discord_bridge.py:12-26`, `requirements.txt`, `pyproject.toml` | `discord.py` 未宣言 → 11件全スキップ |
| 21 | 依存の乖離 | `requirements.txt:4,6,7` vs `pyproject.toml:16-19` | Flask/scipy/trimesh の下限不一致。CI と開発で別セット解決 |
| 22 | `pip-audit` が**失敗できない** | `.github/workflows/ci.yml:87-94` | `\|\| true` が exit status を破棄 |
| 23 | lint/type 失敗時に**Issue が起票されない** | `.github/workflows/verify.yml:42-47` | `if: failure() && steps.pytest_step.outcome == 'failure'`。ruff 失敗時は pytest がスキップされ条件不成立 |
| 24 | stderr と永続ログの**相関IDが無い** | `app.py:39-42,122-126`, `api/core.py:79-95` | ファイルハンドラ未設定。error_id がログlinking できない |
| 25 | GPU経路と**ジョブワーカーが無ログ** | `physics/parallel_engine.py:41-48`, `physics/job_manager.py:40-43` | `logging` import なし。GPU 失敗が `except Exception: pass` で消える |
| 26 | エラーログ dir が **CWD 相対・import 副作用で生成** | `physics/error_domain.py:52-58,65-67,224-225` | `import physics` で CWD に `logs/` を作る。systemd の `WorkingDirectory=/` で `/logs/` に書く |
| 27 | `/healthz/ready` の `max_jobs=4` が**3箇所重複**、キュー深度報告なし | `app.py:239-253`, `api/routes/jobs.py:135,236` | _BUSY 判定が HTTP 400（429/503 が適切） |
| 28 | UI検証ツールが**トレースバックで落ち**、作業ツリーを汚染 | `tools/capture_ui.py:3,6,26`, `tools/verify_repair_ui.py:44-45,190-201` | 追跡済み PNG を無警告上書き。`artifacts/` は未 ignore |
| 29 | `tools/test_*.py` 9本が**import時に 35秒シミュレーション** | `tools/test_*.py` 9件 | `__main__` ガード無し。`pytest .` で約5分の無駄計算 |
| 30 | `.env.example` が **GITHUB_TOKEN を欠落**、未使用変数を文書化 | `.env.example:1-27` | 5ツールが `GITHUB_TOKEN` を読む。`SECRET_KEY` は**どこからも読まれない** |
| 31 | `agents.md` のコマンド表が `tools/` 接頭辞を**1つ欠落** | `agents.md:57` vs `verify-and-test.md:17` | `close_resolved_issue.py` が解決不能 |

### Low

| # | 場所 | 内容 |
|---|---|---|
| 32 | `.kilo/.gitignore:2,7` | `package.json` と `.gitignore` 自身を ignore → MCP 実行系を clone から再構築不能 |
| 33 | `.kilo/mcp/launch.mjs:53-57` | memory ストアが無制限成長。protocol の「検索索引」という説明と実装が乖離 |
| 34 | `skill/README.md:42-46` | スキルカタログが**存在しない5ファイル**を索引 |
| 35 | `agents.md:64`, `docs/SPECIFICATION.md:172` | 行数上限は充足（75/49/27）。ただし `Knowledge-important.md` の50行制限が no-longer-tracked |
| 36 | `tools/run_ci_tests.py:92` | `pytest_failure_summary.txt` が repo root に出力され未 ignore |
| 37 | `docs/CODE_REVIEW_FINDINGS.md` | 本レビュー記録自体が未追跡（clone で失われる） |
| 38 | `tools/read_issues.py:5` | `sys.stdout.reconfigure` 無防備 → 非標準 stdout で AttributeError |

---

## R2-performance — 性能/スケーラビリティ/並行性 (25件: C5/H6/M7/L7)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | `aero_kernel._finite` がスカラーに `np.nan_to_num` → **3.6倍ホットパス減速** | `physics/aero_kernel.py:155` ← `:575` | 1評価あたり81回 `nan_to_num`（0-d配列で9µs）。実測 1195µs → 328µs。**シミュレーション1秒あたり 2.30s → 0.63s CPU** |
| 2 | GPUバッチが**毎ステップ風場をホスト往復** | `physics/gpu_dynamics.py:309,318` | `positions.get()` → ホスト風ソルバ → `xp.asarray`。平静時の短絡も無し。GPU因為は CPU風ソルバと PCIe |
| 3 | 最適化アーカイブの Pareto ソートが **O(n²) 純Python** | `physics/optimizer_domain.py:824`（呼出 `:639`） | 実測 n=100→0.091s, 400→1.83s, 1600→**25.4s**。n=5000 で**約248秒**。`_result_from_front` も O(front×archive) |
| 4 | **NSGA-II の世代ごとに spawn プロセスプールを作り直し** | `physics/parallel_engine.py:790-792`, `:382` | 125世代 → 125プール生成。実測 4ケースで 3.17s（うち2.97sが子プロセス import）。約6分のオーバーヘッド |
| 5 | 単一グローバル `RLock` が**全シミュレーション API を直列化**、かつ launch 時は**ロック外で状態変更** | `api/core.py:194`, `api/routes/simulation.py:54,137-147` | スループット上限=機体1機同時。240ステップ要求で**約20秒ロック保持**。launch は `simulate()` がロック外 → `/simulation/step` と同一シミュレータを破壊的に競合 |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 6 | 1評価 **約9秒** → 5000評価ランが**約12.5時間** | `physics/optimizer_domain.py:588` → `parallel_engine.py:682` → `flight_sim.py:3161` | 実測 0.5s→1.87s, 1.0s→2.74s, 2.0s→3.99s。既定 `sim_duration_s=6.0` で 9s/評価 |
| 7 | `step()` が**捨てるテレメトリパケット**を構築 | `physics/flight_sim.py:2994`→`:2831`→`:2868` | `derived_timestep()` が有限差分2回を追加。telemetry-only 3.2%、`simulate()` 経路では 0.8% が**確実に無駄** |
| 8 | 風場を**空力評価1回につき1回**評価、既定では短絡が**発動しない** | `physics/flight_sim.py:860-863` vs `config_schema.py:479` | 既定 `thermal_strength=0.50` により短絡条件が偽。961風評価/0.5秒=1:1。0.17s CPU/シミュレーション秒 |
| 9 | `aero_domain.evaluate_aero` が**毎呼び出しで大気とカーネル係数を再導出** | `physics/aero_domain.py:930,955` | キャッシュ済み大気 (`flight_sim.py:698-755`) を破棄。`params_from_config` 16.9µs × 1922 = 32ms/秒。機体 consta 却在に |
| 10 | 最適化ジョブが **GIL 制約スレッド**で、4ジョブ制限は TOCTOU race | `api/routes/jobs.py:135,182`, `physics/job_manager.py:784` | 4ジョブ並列でも1ジョブ速度。ワーカープールもキューも無し |
| 11 | GPUアンサンブル: **毎ステップ CUDA同期2回**、終端で **1600回の個別D2H**、O(S·N log N) の percentile ループ | `physics/parallel_engine.py:1213,1237,1253-1256,1288` | 2000ステップで4000回のパイプ停止。`sample_tracks` は 100×400=40000点 ≒ **3.8MB JSON** |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 12 | `parallel_evaluate` の直列経路が**O(n²)** | `physics/parallel_engine.py:767-780` | `emit()` が毎回全結果列を再走査。n=5000 で1250万反復 |
| 13 | `/api/v1/jobs` ポーリングが**実際は700ms**、256件 deep copy | `static/js/optimizer.js:266-274` / `api/routes/jobs.py:342-352` | `POLL_MS=700` が `setTimeout` で再武装。`JOBS_MS=2000` は初回のみ |
| 14 | `JobManager.update()` が**評価ごとにレコード全体 deep copy** | `physics/job_manager.py:594-612` | `as_dict`+`from_dict` 往復を5000回。グローバル RLock 内で |
| 15 | `RESULT_CACHE` が**最大32件の完全結果をRAM保持** | `api/core.py:269-271`, `api/routes/jobs.py:170-173` | 5000評価なら16-32MB常駐。バイト数ではなく個数でFIFO退避 |
| 16 | メッシュ領域の Python レベル O(F) ループ、`np.unique(axis=0)` が STL の崖 | `physics/mesh_domain.py:961,1359,392` | 100万三角形 → 300万行 = 72MB+ ワークスペース、数秒 |
| 17 | launch / ensemble の**ペイロードが巨大** | `api/routes/simulation.py:180`, `static/js/transport.js:93` | 1サンプル 515B → 25秒で1.26MB、20000フレームで10MB |
| 18 | `FlightSimulator.step()` が**シミュレータ自身のロックを取らない** | `physics/flight_sim.py:2939` vs `:1755` | `history()`/`metrics()` のみ取得。2リクエストが1 RK4 サブステップ内に割り込み得る |
| 19 | `evaluate_coefficients` が**GPU 1ステップで6回**（RK4 なら4回） | `physics/gpu_dynamics.py:832,855` | ステップサイズ制御とサマリで余分な2回 |
| 20 | 単発サマリは**スナップショット毎に9回 `np.percentile`** | `physics/parallel_engine.py:1288` | S=400, N=4096 で約2×10^8 比較（`np.percentile(axis=1)` 1回で足りる） |
| 21 | `stress_domain.py:855` の色マップが**Python ネスト内包**、220ms ポーリング | `static/js/transport.js:13` | 256×256 で65536回の Python 呼び出し/リクエスト |
| 22 | 風場ポーリング 900ms で**設定全体を再送** | `static/js/transport.js:285` | 11×11×7=847点グリッドごと |

### Low

| # | 場所 | 内容 |
|---|---|---|
| 23 | `flight_sim.py:743` | 大気キャッシュのグローバルロックを**キャッシュヒットでも**取得 |
| 24 | `transport.js:229` | `seekTime` が履歴全体の O(n) 線形走査 |
| 25 | `mesh_domain.py:2042` | `MeshLibrary._rebuild_index` が起動時に全モデルファイルをJSON parse |

---

# Round 3

> R3 も R1/R2 の情報を与えず、2名のコンテキスト無しサブエージェントが独立実施（テスト有効性 / E2Eプロダクト）。

## R3-test-effectiveness — テストスイート有効性 (23件: C4/H5/M8/L6)

**規模**: 33ファイル / 653関数 / 803収集項目 / 13,444行 / 2,752 assert（3.43 assert/項目）
**分類**: (a) 真の不変条件 526件 (65.5%) / (b) 配管・契約のみ 242件 (30%) / (c) 構成上真 26件 (3%) / (d) 実質 no-op 9件 (1%)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | テレメトリの Reynolds 恒等式アサーションが**空振り** | `tests/test_flight.py:770-775` | `assert (...) if "kinematic_viscosity_m2s" in packet else True` — 当該キーは payload に**存在しない**ため常に `assert True` |
| 2 | **CuPy/GPU 経路が一度も実行されない** | `tests/test_gpu_kernel_parity.py:222`, `test_ga_physics_readiness.py:379-381`, `test_issue28_gpu_duration.py:36` | すべて `np` を明示。実機では `pytest.skip`。GPU 固有バグは全緑のまま入り得る |
| 3 | API 認証ミドルウェアの**カバレッジ 0** | `app.py:132-159` | `API_KEY`/`Authorization`/`401` の grep 0件。1行の変更で全認証が消えても緑 |
| 4 | 53 HTTP エンドポイント中 **41 が pytest 未テスト**、E2E は pytest に収集されない | `pytest.ini:3-4` | `smoke_test.py` はルート直下で両パターン不一致。`verify.yml` でも実行されない |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 5 | 「風座標変換」監査テストが**テスト内演算のみ** | `tests/test_security_and_frames_audit.py:70-87` | `atan2(2.0,10.0)>0` は定理。製品コード未呼出し。Issue#30 の違反を検出できない |
| 6 | **共有グローバル状態の競合を検証するテストが無い** | `api/core.py:267,257-264` | 並行 HTTP リクエストのテスト 0 |
| 7 | 最適化が**偽の surrogate のみ**をテスト | `tests/test_optimizer.py:72-76` | 11/37 関数が `surrogate_path` fixture 使用。全5アルゴリズムが偽モデル |
| 8 | suite 全体が**git ブランチ名でゲート** | `conftest.py:34-52` | ambient な git 状態で「テストが実行されるか」が決まる |
| 9 | **ブラウザ/DOM テストが一切無い** | `tests/` 全体 | `getElementById|querySelector|document\.|playwright` の grep 0件 |

### Medium / Low（要点）

| # | 場所 | 内容 |
|---|---|---|
| 10 | `test_open_issues_audit.py:32-132` | 13/23件が「現在の挙動」を固定（change-detector）。3閾値が緩すぎ（14倍/1e-9） |
| 11 | `test_atmo_wind.py:838-850` | `cupy-cuda12x` が wheel で入るため `has_cupy=True` → 両分岐未実行、no-op |
| 12 | `test_discord_bridge.py:12-27` | `discord.py` 未宣言 → 11件全スキップ。`--strict-markers` なし |
| 13 | `test_security_and_frames_audit.py:123-166` / `test_mesh.py:1123-1194` | SSRF テストが実 fetch 経路を辿らず。**DNS rebinding 未テスト** |
| 14 | `test_ga_physics_readiness.py:283-291` / `test_paper_conformance_p0_p1.py:163-195` | ソース grep アサーション = change-detector。改名で突破、any 書換で通る |
| 15 | `pytest.ini` / `conftest.py` | **収集項目数 > 0 をアサートする仕組みが無い**。ガードで0件でも緑 |
| 16 | `test_ensemble.py:157-183` | UI の skip-top-1 ロジックを**テスト側で再実装** |
| 17 | `physics/config_schema.py:410-433` | `clamp_ranges()` の境界値を固定するテストが無い（`cd0=5.0` 等） |
| 18 | `test_aero.py:1155-1157` | `hasattr` アサーション（存在のみ） |
| 19 | `test_branch_guard.py:190-193` | リテラルを再宣言しただけの恒等アサーション |
| 20 | `test_physics_audit.py:191-194` vs `:210-214` | ISA 密度の重複テスト |
| 21 | `test_aero.py:758-778` | 同一コード経路を2通りで呼んで比較（自己比較） |
| 22 | `pytest.ini:8-9` | `filterwarnings = ignore::DeprecationWarning` で全 DeprecationWarning を無音化 |
| 23 | `test_flight.py:912-918` | 「時間予算」テストが1回の1秒 flight を測るだけ。O(n²) 回帰は検出不可 |

### 「このテストは検出できるか」判定表（抜粋）

| 欠陥クラス | 検出 | 根拠 |
|---|---|---|
| deg↔rad 単位換算 | **約80%** | 大半は `math.radians` で独立再導出。22件は `config_schema.deg2rad` に依存。`test_flight.py:770-775` が空振り |
| 力・モーメントの符号 | **Yes** | `test_physical_laws_audit.py:74-84`, `test_repair_contracts.py:32-35`, `test_aero.py:674-676` 等、独立フレームで符号再導出 |
| 空力係数の式変更 | **Yes（閉形式）/ No（ブレンド形状）** | Helmbold/PG/Raymer/Hoerner/McCormick/Polhamus は再導出済み。**失速ブレンド関数は単調性+5%幅でしか固定されていない** |
| GPU/CPU 分岐 | **No** | 全 parity テストが `np` 固定 |
| 共有状態競合 | **No** | 並行HTTPのテスト 0 |
| 入力検証欠落 | **部分** | スカラー系は検証済み。**係数の clamp 範囲は未固定** |
| UI/DOM id 不一致 | **No** | ブラウザテスト 0 |
| API 非認証アクセス | **No** | 認証フック未テスト |

### テスト密度の最悪（items/ソース行）

`origami_domain.py` 0.007 / `api/` 0.009 / `storage_domain.py` 0.012 / `records_domain.py` 0.012 / `optimizer_domain.py` 0.023 / `mesh_domain.py` 0.020 / `aero_kernel.py` 0.021 / `gpu_dynamics.py` 0.027 / `flight_sim.py` 0.041 / `wind_domain.py` 0.111 / `aero_domain.py` **0.179**（最良）

**総合判定**: 「物理カーネル実装には、私たちのテストは**本物**。だが**結論がCIバッジに誤って置かれている**」。緑CIバッジは**アプリケーションについての主張ではない**。物理テストは信頼してよい。

## R3-e2e-journey — E2Eプロダクト/ジャーニー (40件: C6/H6/M22/L6)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | 「1位の投げ方を設定に反映」が**ハードコード値を書き込み**セッションを破壊 | `static/js/app.js:1230-1246,1158-1174` | 対象スライダー `quick-slide-*` が `index.html` に不在 → フォールバック 10/12/0 を store へ書き込み**サーバへ push**。トーストは_processorの値を主張 |
| 2 | 「推奨セッティングを機体に適用しました！」が**何も適用しない** | `static/js/investigator.js:296-306` vs `static/js/state.js:53-59` | 4引数 API に2引数で呼び、`undefined === undefined` で `set()` が早期 return |
| 3 | Guinness「公式検証レポート」が**同義反復**（tautology） | `physics/records_domain.py:2226-2292` vs `static/js/app.js:3141-3150` | 適合済み較正レシピで飛行 → 必ず ≈0% 乖離。payload の `method` は "not evidence of validated performance" と明記するが **UI は表示しない** |
| 4 | 日本語の**世界記録名が文字化け**し左レール dropdown に出る | `physics/records_domain.py` (93行/276文字) / `static/js/app.js:2745-2751` | **修正済み**: 日本語93行をギネス記録の原文出典から再記述し、`tools/check_mojibake.py` と `tests/test_no_mojibake.py` が再発を検出する |
| 5 | **クリーンチェックアウトで起動しない** | `physics/origami_domain.py`, `aero_kernel.py`, `gpu_dynamics.py`, `api/routes/origami.py`, `static/js/origami.js`, `tools/branch_guard.py` が未追跡 | モジュールレベル import 済み → `ModuleNotFoundError` |
| 6 | 100機リーダーボードが**3DoF近似を公式結果として提示** | `physics/parallel_engine.py:1448-1454` vs `static/js/app.js:1050-1110` | `approximate`/`limitations` を UI が一切読まない |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 7 | 調査 advice の**エレボン符号とピッチングモーメント方向が逆** | `physics/investigate/advice.py:26-31` vs `physics/aero_kernel.py:462-469` | 「+X°上向きに反らせば上向きモーメント」だが物理はTE-down でノーズダウン。**推奨値は正しく指示が逆** |
| 8 | 最適化が自分の**誠実性メタデータ（approximate/limitations/verified_candidates）を破棄** | `physics/optimizer_domain.py:714-719` vs `static/js/optimizer.js:426-542` | UI は objectives のみ読む |
| 9 | Guinness ペインが「公式生数値 時速約100km」と称しつつ**93.6km/h**・A4 でない0.05m²用紙で飛行 | `templates/index.html:588` vs `records_domain.py:1072-1100`, `config_schema.py:251` | 質量も A4 規定 6.24g に対し 5.00g。`provenance: RECONSTRUCTED` も非表示 |
| 10 | 全記録プリセットの**トリム契約が死んでいる** | `records_domain.py:1002-1009,670` vs プリセット群 | 12プリセット中9個が `release_pitch_deg=0.0` を明示。`GET /presets/ninomiya_n424`=3.0 だが `apply`=0.0（**2エンドポイントが矛盾**） |
| 11 | シミュレーション**エラー表示面が死** | `static/js/app.js:1848` / `templates/index.html` | `sim-error-*` id 不在で早期 return → `RUN=ERROR` も・エラーピルも出ない |
| 12 | 既定最適化アルゴリズムは **DE**、NSGA-II は**UI が常に出す重みを無視** | `static/js/optimizer.js:164,205` vs `physics/optimizer_domain.py:1354-1363` | README は NSGA-II を広告 |

### Medium（要点のみ・全22件）

| # | 場所 | 内容 |
|---|---|---|
| 13 | `app.js:876` vs `api.js:173` | `api.export` 未定義 → 例外が catch され常にブラウザ Blob フォールバック |
| 14 | `app.js:333-334,1290-1300` | チャンピオン JSON/CSV ボタン id が HTML に不在 → エクスポート不可 |
| 15 | `app.js:3141-3150` | 「一括検証マトリクス」が `alert()`。可視パネル `wb-guinness-result-panel` は JS から未書込 |
| 16 | `app.js:2445` | `frame.ground_speed_mps` が API に無い → 対地速度ゲージ恒久 `—` |
| 17 | `telemetry.js:79` | 失速警告が**ハードコード 14°**（実 `stall_alpha_deg` は 4-30°、記録プリセットは 18°） |
| 18 | `app.js:2758` vs `api/routes/records.py:135-139` | 「投げ方は変更せず」コメントだがサーバが `set_launch` を実行 |
| 19 | `optimizer.js:213` | 最適化は 6秒 flight、コンソールは 25-60秒 |
| 20 | `optimizer.js:426-438` | Pareto は objectives[0]×[1] のみ。軸に単位・方向なし。table は snake_case 生表示 |
| 21 | `config_schema.py:1179-1201` | 設計空間は span/area/AR を独立扱い。`aspect_ratio` 指定で AR が span/area から乖離 |
| 22 | `physics/investigate/__init__.py:68` | 前后flight時刻が**5秒で切られ**改善率が常に 0.0% |
| 23 | `physics/investigate/tuning.py:20,76-140` | 感度走査は10変数、チューニングは6変数。影響度の高い変数が advice を出さない |
| 24 | `viewport.js:876-902` | 応力ヒートマップは**glTF/GLB/OBJ で無効**（`target.geometry` undefined）。STL のみ。`H` トグルも無効 |
| 25 | `transport.js:277-292` | 応力場ポーリングは飛行/風洞中のみ |
| 26 | `app.js:2665` / `flight_sim.py:3023,3051` / `parallel_engine.py:1339` | 「飛距離」が3つの異なる量 |
| 27 | `app.js:3126` vs `templates/index.html:588` | ギネススタジオがライブ transport を**上書き**。「完全隔離」表示と矛盾 |
| 28 | `app.js:2749,2970` | ロータークラフト記録が飛距離カテゴリ表示 |
| 29 | `static/js/api.js` | UI 未到達エンドポイント多数: `/simulation/step`, `/simulation/batch`, `/history`, `/meta/health`, `/mesh/metrics`, `/records/throw_techniques`, `/jobs/join`。**README の「60-120FPS リアルタイム対話描画」は未使用** |
| 30 | `records_domain.py:719,731,2017-2027` | `compliance` / `provenance` / `throw_rules` / `design_classes` を算出するが UI は破棄 |
| 31 | `README.md:41` vs `physics/parallel_engine.py:920-931` | README は「CPUフォールバック禁止」と明記、実装は**暗黙フォールバック**。UI も "GPU 並列演算" とハードコード表示 |
| 32 | `docs/SPECIFICATION.md:172-177` | 記載ルート 3件が**実在しない**。実在する 8件を未記載 |
| 33 | `config_schema.py:1179-1200` | 設計変数ラベルが英語のまま UI に出力 |
| 34 | `records_domain.py:756-800` | `gwr_compliance` が**橡皮印**（常に true） |

### Low（要点のみ・全6件）

| # | 場所 | 内容 |
|---|---|---|
| 35 | `investigator.js:308` | toast tone "success" 未定義 |
| 36 | `index.html:203-208` vs `app.js:3159-3164` | ギネスモードボタンの id と意味が反転 |
| 37 | `index.html:160-165` / `app.js:2856-2900` | 非表示の旧記録UI に書込み、可視カードは別要素 |
| 38 | `records_domain.py:1030` | `trim_alpha_deg` override は TypeError（潜在） |
| 39 | `README.md:105-128` | アーキテクチャ表に `records_domain`/`origami_domain`/`aero_kernel`/`gpu_dynamics`/`storage_domain` 欠落 |
| 40 | `README.md:30` | 「境界パネル法」を基幹として記載。実際の6DoFは係数ベースでパネル法は**取り込みメッシュ専用** |

### 新規モジュールの統合状況

| モジュール | コンソールから到達可能 | バックエンド接続 | git追跡 | 判定 |
|---|---|---|---|---|
| `physics/origami_domain.py` | **Yes**（折り紙ジオメトリパネル） | Yes（`/api/v1/origami/mesh`） | **No** | 統合済だが**表示のみ**。折り畳み状態は物理pushから**除外**（`app.js:1960-1962`）→3Dと飛行が矛盾しうる |
| `api/routes/origami.py` | Yes | Yes（`app.py:260` で登録） | **No** | 登録済・動作するが未追跡 |
| `static/js/origami.js` | Yes | — | **No** | 完全に配線済（param平坦化も一致）、未追跡 |
| `physics/aero_kernel.py` | Yes（暗示的・全空力計算の核） | Yes | **No** | 核心。**investigate advice が矛盾する正本**。未追跡 |

---

# Round 4

> R4 も新規視点。R1〜R3 で未カバーだった2領域（折り畳み/幾何、風・応力）を 2名のコンテキスト無しサブエージェントが独立実施。

## R4-origami-geometry — 折り畳み/幾何層と空力結合 (16件: C1/H4/M5/L6)

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | crease-roll の Bézier が発散 → **既定UI値で機首前方に302mmのスパイク** | `physics/origami_domain.py:1015-1025`（`denom<=1e-20`）, `:1028-1078`（handle `:1060-1061`） | 2パネルがほぼ平行になると交点 `v` が不定距離に飛び、Bézier中点が数百mm逸脱。実測: 既定 `elevon_deg=2, fold_trailing_deg=15` で x_max=+0.4507（機首は x=+0.1485）。`elevon_deg=0.5` で **+1.9585 m**、`fold_trailing_deg=5` で **+7.477 m** |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 2 | 後縁 crease が両翼に共有され**左翼の基準軸のみ**使用 → 左右非対称 | `physics/origami_domain.py:1863-1875`, `:489-506`, `:550-595` | 実測: 鏡像誤差 3.44-3.52mm、折角 左15.00° vs 右**14.77°**、面積 0.001201 vs **0.001184 m²**。空力は完全対称を仮定 |
| 3 | `fold_tip_deg` が**翼端折り角ではなくハードコード翼端への増分** | `physics/origami_domain.py:1799-1806` | 実測 折角: 折り畳み80° / 解析機0° / 応力 kernel は `fold_tip_deg` 自体。**同じ設定が3つの値**。負値は `max(0.0,…)` で破棄 |
| 4 | ダイヘドラルの**符号が破棄** | `physics/origami_domain.py:1768` `wing_angle = body_half + abs(dihedral_deg)` | `−20` が `+20.000°` の幾何。±20 は bit-identical。解析機・空力側は符号を保持 |
| 5 | 折り畳み幾何が**空力と完全非結合**（絵だけ） | `api/routes/origami.py:44-70` / `physics/config_schema.py:254-258,1342-1440` | 実測 投影面積 0.03605 m²・翼幅 0.1474 m・幾何 AR **0.60** に対し、空力は AR **2.67**。CG は**展開形**トラペゾイド重心。API の `scale=wingspan_m/SHEET_WIDTH_M` により 0.20 m 要求 → 実 0.140 m（**30%不足**） |

### Medium

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 6 | 非コプラナなパネル栈に**基準パネル軸**で crease を適用 | `physics/origami_domain.py:1836-1858,1863-1875,550-595` | 実測: 翼let の trailing strip が **0.43°/0.09°**（期待15°）、winglet elevon が **80.01°**（期待2°）。#1 の直接原因 |
| 7 | メッシュが**閉じた立体でない**（SPEC・テスト docstring と矛盾） | `physics/origami_domain.py:1188-1240` / `docs/SPECIFICATION.md:88` / `tests/test_origami.py:3-11` | 402 v / 382 tri に対し**境界辺 354本**。crease ロールが別シェルとして出力され weld されない |
| 8 | 三角形の **43% が Assigned 頂点法線と逆巻き** | `physics/origami_domain.py:1310,1324-1332` | 164/382 が `dot(winding_normal, mean_vertex_normal) < 0`。`DoubleSide` で隠れるが背面カリング/法線導出で符号反転 |
| 9 | API が**折り畳みパラメータの範囲検証なし** | `api/routes/origami.py:29-34` | `keel_beta_deg=1e9` → bounds x[-0.155, **+300065.737**]（300kmのジオメトリをJSONで返す） |
| 10 | キャッシュ無しで毎リクエスト**全面折り**、90-130kB の非量子化JSON | `api/routes/origami.py:69`, `physics/origami_domain.py:1901-1939` | 実測 9.8ms/回、88.5kB（dart）〜129.9kB（nakamura）。クライアントは60msデバウンスで毎署名変更に再要求 |
| 11 | `depth` が静的部分で**常に 0 にリセット** → 基準面ヒューリスティック崩壊 | `physics/origami_domain.py:579-589` vs `:500` | `parent Face(...)` が `depth` を渡さず既定0。depth 項が常にタイ→面積最大で決定され、trailing crease の基準が誤る |
| 12 | `turbulence_scale` の位置引数が `scale` で割られ**渦スケールが常に≈1m**、 furthermore Taylor 移流速度が V/scale | `physics/wind_domain.py:829-839` | `drift` のみ `scale` で割られ `P` は生座標。実測 移流 7.14 m/s（平均風10 m/s）= **29%誤差**。UIは単位 "m" と表示 |
| 13 | ABL が**単一対数則**で無限延伸（表層/遷移層/乱流層なし） | `physics/wind_domain.py:639-692` / `docs/SPECIFICATION.md:37` | 実測 z=1000m で u=19.69 m/s（風速の1.97倍）。SPEC は対数則**とべき乗則**を要求 |
| 14 | `ground_level_m≠0` で**地上高を2重計算** | `physics/flight_sim.py:867` + `physics/wind_domain.py:924` | `−z_state − 2·ground_level_m` で評価。実測 `ground_level_m=1.0`、地上0.2m → **風0**。UI側の `flowfield.js:315` は正しい式 |
| 15 | ヒートマップに**空間情報が無い**（index は利用率≈0.024、`stress_shape` は未消費） | `physics/stress_domain.py:842-845,862-863` / `static/js/airframe.js:470` | 既定機体で σ_root=52.24Pa / σ_allow=2200Pa → index 0.0237。翼全体がランプ最下位色。サーバ `colours` は別ランプで未読 |
| 16 | 風コンパス座標が **SPEC の世界座標と90°矛盾**、世界軸は発射方位 | `physics/wind_domain.py:15-27,562-583` / `docs/SPECIFICATION.md:38` | wind は north=+y、SPEC は +x=north。`wind_direction_deg` と flight 方向が関係しない |

### Low

| # | 場所 | 内容 |
|---|---|---|
| 17 | `physics/origami_domain.py:1705` | 右襟が group `"collar_l"` に割り当て（`role="collar_r"`） |
| 18 | `origami_domain.py:1687,1694-1710` / `app.js:1741` | `fold_leading_deg` は dart のみ有効。**クライアントの refresh 署名に無く**、スライダを動かしても再要求されない |
| 19 | `origami_domain.py:1941,51,278,1641,315-330,1901-1939` | 死コード多数（`apply_aeroelastic` はテストのみ、`HingeNode`/`CreasePattern.nodes` は未消費 → docstring の hinge-tree アルゴリズムは**未実装**） |
| 20 | `static/js/origami.js:14-18` vs `origami_domain.py:1879-1898` | アーキタイプカタログの重複。クライアントは `GET /api/v1/origami/archetypes` を呼ばない。`shape_flag` は archetype に**マッピングされない** |
| 21 | `origami_domain.py:20-34` | フレーム docstring が誤り（`Rx(+90°)` 適用の主張）。実際は 3-space 正作、pose のみ変換 |
| 22 | `physics/stress_domain.py:823-827,859-882` | 応力テンソル**無し**。スカラー曲げ応力のみ。せん断/膜/翼端接地応力が不在。`SPAN_LOADING_FACTOR` は未使用 |
| 23 | `physics/stress_domain.py:557-575` | 許容応力則の docstring が実装と逆（硬さ↑=強く のは逆）。`gsm^-2.5` の利用率で 3.7倍重い紙が **26倍安全**と判定（`paper_gsm` が非物理的な支配レバーに） |
| 24 | `physics/flight_sim.py:808-836` | `_local_wind` フォールバックが**別の風モデル**（gust則/ABL clamp/粗度床/乱流・熱流なし） |
| 25 | `physics/wind_domain.py:762-775,716,730,763` | 熱柱に補償下降流なし（w≥0 全体）。`core_rel` 無上限（strength=25 で 41.2m） |
| 26 | `physics/wind_domain.py:867-876,129-130,846` | 乱流は壁で減衰（鏡像でなく 5cm層）。水平乱流に壁処理なし。異方性定数 0.707 と docstring の 0.5 が矛盾 |
| 27 | `physics/stress_domain.py:842-845,855-857,867-876` | stress index を2回計算し clip が異なる。`colours` は死ペイロード |
| 28 | `physics/config_schema.py:527-528` | `surface_roughness_m` 最大 1m、`reference_height_m` 最小 0.01m の非現実値 |

## R4-wind-stress — 風・応力・大気 (16件: C1/H2/M8/L5)

> 注: 上記 R4-origami の #12〜#16 は本エージェント（wind/stress）側でも独立に発見された重複項目。うち **#12 turbulence_scale（High）**、**#14 ground_level 2重計算**、**#15 ヒートマップ平坦** は wind/stress 側が独立に「High/Medium」と判定。

### Critical

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 1 | **風洞モードで機体が後向きに吹かれる**（α_eff ≈ 180°+α_bench） | `physics/wind_domain.py:890-914`（`stream[:,0]=speed`）/ `physics/flight_sim.py:1878-1894`（yaw=0）/ `:885-890` | ベンチのノーズも +x、流も +x → 相対流 `(−V,0,0)` → `atan2(w,u)` ≈ 180°。実測: ベンチα=0° → **+180.000°**、4° → **−176.000°**、−5° → **+175.000°**。,/simulation/step はこの反転流で積分 |

### High

| # | タイトル | 場所 | 内容 |
|---|---|---|---|
| 2 | 応力ヒートマップが**転置**（スパン方向と弦方向が逆） | `physics/stress_domain.py:814-826,862` ↔ `static/js/airframe.js:273-283,466-487,509-521` | サーバ grid は `[spanwise][chordwise]`、クライアントは `fy=v·(rows-1)` `fx=u·(cols-1)` で**行に弦方向・列にスパン方向**を引く → 描画パターン `σ(span=弦, chord=スパン)`。根翼前縁のピークが弦方向の帯になる |
| 3 | `turbulence_scale` が**渦長に効かず** Taylor 移流速度を破損 | `physics/wind_domain.py:829-839` | `drift` のみ `scale` 換算、`P` は生座標 → 渦長 ≈1m 固定、移流が V/scale（V=10, scale=0.35 → **7.14 m/s**、29%誤差） |

### Medium（要点のみ・全8件）

| # | 場所 | 内容 |
|---|---|---|
| 4 | `physics/wind_domain.py:678-692` | ABL が単一対数則のみ。SPEC §1.2 の「対数則/べき乗則」未実装。z=1km で u=1.97×風速 |
| 5 | `physics/flight_sim.py:867` + `wind_domain.py:924` | 地上高を2重減算。`ground_level_m=1.0` で地上0.2m の機体に**風0**。UI `flowfield.js:315` は正しい式 |
| 6 | `physics/stress_domain.py:842-845,862-863` | ヒートマップが空間情報ゼロ（index=利用率 0.024）。`stress_shape` は**どのクライアントも読まない**。サーバ `colours` は別ランプで死 |
| 7 | `physics/wind_domain.py:15-27,562-583` vs `docs/SPECIFICATION.md:38` | 風の方位基準が SPEC と90°矛盾。世界軸は発射方位で風向と無関係 |
| 8 | `physics/wind_domain.py:893-910` / `config_schema.py:503-507` | `wind_tunnel_blockage` が「壁境界層」フラグに連動。既定 False では**ブロック補正なし・完全均一流**。tunnel 軸は原点で機体は 1.2m 上 → 壁層外 |
| 9 | `physics/stress_domain.py:823-827,859-882` | 応力テンソル無し（スカラー曲げのみ）。せん断/膜/翼端接地応力なし |
| 10 | `physics/stress_domain.py:557-575` | 許容応力 docstring と実装が逆。`gsm^-2.5` で紙厚が26倍の「安全度」差 → 非物理的な支配最適化レバー |
| 11 | `physics/flight_sim.py:808-836` | `_local_wind` フォールバックが別モデル（ABL clamp≤1、異なるgust則、乱流・熱流なし） |
| 12 | `physics/wind_domain.py:762-775,716,763` | 熱柱に補償下降流なし（w≥0）。`core_rel` 無上限 |

### Low（要点のみ・全5件）

| # | 場所 | 内容 |
|---|---|---|
| 13 | `physics/wind_domain.py:867-876,129-130` | 乱流は鏡像でなく減衰。水平乱流に壁処理なし。異方性定数 0.707 vs docstring 0.5 |
| 14 | `physics/stress_domain.py:842-845,855-857` | stress index 2回計算・clip 差異、`colours` 死ペイロード |
| 15 | `physics/config_schema.py:527-528` | `surface_roughness_m` 最大1m、`reference_height_m` 最小0.01m |
| 16 | `physics/wind_domain.py:321-348,839,951` | `simplex_noise3` はclip するが场内パスはclipせず公開helperと差異。Taylor移流は非突風時の平均風固定 |
| 17 | `physics/config_schema.py:1471-1483` | `ENVIRONMENT_SCHEMA["ranges"]` が `turbulence_scale`/`surface_roughness_m`/`reference_height_m`/`wind_tunnel_*` を欠落（docstring は「UIは範囲をハードコードしない」と主張） |

---

# 全体トリアージ（優先度順・修正禁止）

## P0 — 即時着手すべき（機能・正確性・起動性）

| ID | 問題 | 主な証拠 |
|---|---|---|
| T-01 | **クリーンチェックアウトで起動不可**（未追跡6モジュール） | R1-C-2, R3-C-5, R2-ops-4 |
| T-02 | **master への CI が全滅**（branch guard）→ **解消済み（2026-10-20）** | R1-C-1, R2-ops-1, R3-H-8 |
| T-03 | **「採用」ボタン2つが嘘**（推奨適用 no-op / チャンピオン 10m/s 固定値 push） | R1-C-1, R2-a11y-2, R3-C-1, R3-C-2 |
| T-04 | **GPU経路の deg/rad 取り違え**（αが57.3倍過小、GPU は失速しない） | R2-conf-F-01 |
| T-05 | **GPU密度が18.6%不足**（湿度に virtual温度補正） | R2-conf-F-06 |
| T-06 | **風場が `ground_level_m≠0` で恒久ゼロ** | R1-M-9, R2-conf-F-04 |
| T-07 | **API 認証が常に無効**（`.env` 未読込 → API_KEY 空） | R1-H-1, R2-ops-4 |
| T-08 | **共有シミュレータをロック外で launch**（並行 request で軌跡が破壊される） | R1-H-3, R2-perf-5, R2-perf-18 |
| T-09 | **Guinness「検証」が同義反復**（UI が fitted 値を公式検証として提示） | R3-C-3, R2-conf-F-13 |
| T-10 | **研究者 advice のエレボン符号が逆** | R3-H-7 |
| T-11 | **records_domain の日本語93行が文字化け**（API/UI 露出）→ **解消済み（2026-10-21）** | R1-H-5, R3-C-4 |
| T-12 | **最適化が評価モデルள機器依存**（GPU=近似 / CPU=6DoF） | R1-H-8, R2-conf-F-03, R3-H-6 |

## P1 — 高（正確性・UX・運用）

| ID | 問題 | 主な証拠 |
|---|---|---|
| T-13 | シミュレーションエラー/ステータス/バナーの DOM id 35個が不在 → 機能3つが死 | R1-H-4,6, R2-a11y-3, R3-H-11 |
| T-14 | 温度/気圧スライダが既定で無効 / clamp 済み設定がクライアントで破棄 | R2-contract-1,2 |
| T-15 | 設定書き込みの部分適用 / bundle import の clamp バイパス / 危険係数4件に範囲検証なし | R2-contract-3,4,5 |
| T-16 | 最適化 1評価9秒 → 5000評価12.5時間、Pareto O(n²)248秒、世代毎spawn | R2-perf-3,4,6 |
| T-17 | `aero_kernel._finite` のスカラー `nan_to_num` で3.6倍減速 | R2-perf-1 |
| T-18 | 最適化/アンサンブル結果の `approximate`/`limitations` を UI が破棄 | R3-H-6,8 / R1-H-1,2 |
| T-19 | 記録プリセットのトリム契約が死、GET と apply が矛盾 | R2-contract-13, R3-H-10 |
| T-20 | CI/Issue ツールが失敗時も exit 0（「緑」判定が空振り） | R2-ops-5 |
| T-21 | `/api/v1/errors` がスタックトレース・絶対パス・クライアントIPを返す | R1-H-2 |
| T-22 | 最適化ジョブの params 無制限 + 入場判定 race + ジョブ枠リーク | R1-H-4, R1-M-13 |
| T-23 | 依存3系統（requirements/pyproject/pytest.ini+ruff.toml+mypy.ini）が乖離 | R1-H-3,4 / R2-ops-21 |
| T-24 | `cupy-cuda12x` ハード依存（CPUのみの環境でも導入必須、setup.sh abort） | R1-H-5 |
| T-25 | 認証/ブラウザ/GPU/41エンドポイント がテスト0（緑CIは空虚） | R3-C-1,2,3,4 / R1-H-6,9,10 |
| T-26 | フロント a11y 重大（トグル8個同名、canvas 代替テキストなし、AAコントラスト違反） | R2-a11y-1,9,10,11 |
| T-27 | Discord bridge が既定で全許可＋`--dangerously-skip-permissions` | R2-ops-2 |
| T-28 | Knowledge.md が実在しないガードレールを主張（bridge） | R2-ops-3,7 |
| T-29 | 抗力ポラルの右軸が C_D / チャートが軸上限をデータ値表示 | R2-a11y-5,12 |
| T-30 | ファイル/画像のリンク切れ（`docs/discord-bridge.md` 等）＋AGENTS.md の大文字小文字 | R2-ops-8, R1-L-28 |

## P2 — 中（保守性・性能・品質）

| ID | 問題 | 主な証拠 |
|---|---|---|
| T-31 | `records_domain` 以外の物理3系統の分裂（応力モデル/失速角/風規約/大気/Helmbold） | R1-H-1〜4, R2-conf-F-05,07,08,09,14 |
| T-32 | `_local_aero` が第2の空力モデル（権限4.3倍差等） | R1-M-10, R2-conf-F-08 |
| T-33 | O(n²) `flight_phase_metrics`（1飛行2回） | R1-M-13 |
| T-34 | 風評価が1:1・既定で短絡不発・毎評価で再導出 | R2-perf-8,9 |
| T-35 | 単一 `RLock` 直列化・job 4枠が race | R2-perf-5,10 |
| T-36 | `RESULT_CACHE` 32件保持 / `JobManager.update()` deep copy | R2-perf-14,15 |
| T-37 | `/api/v1/jobs` 700ms ポーリング + 256件 deep copy | R2-perf-13 |
| T-38 | `logs/error.jsonl` が回転迂回で無制限増加 | R1-M-16, R2-ops-6 |
| T-39 | 40MB生成物コミット済み・`artifacts/` 未 ignore・scratch ファイル未 ignore | R1-H-8, R1-M-21 |
| T-40 | `tools/test_*.py` 11本が import 時35秒シミュレーション | R1-M-13(ops) / R2-ops-29 |
| T-41 | 破壊的自動化に dry-run/確認なし（Issue close・コマンド全削除） | R2-ops-14 |
| T-42 | GitHub ツールの slug ハードコード・タイムアウトなし | R2-ops-15,16 |
| T-43 | 通知トーン/単位/言語の不整合（Pa vs kPa、" maximise"、i18n層なし） | R2-a11y-14,16,17,19 |
| T-44 | 配色意味の衝突（3D応力ランプ vs チャート4色、軸C_D/L/D混同） | R2-a11y-42,43,44 / R3-M-26 |
| T-45 | 応力ヒートマップが glTF/GLB/OBJ で無効 | R1-M-12, R3-M-24 |
| T-46 | 記録プリセットが非物理的AR/CD0（AR 7.4、L/D 19） | R2-conf-F-13 |
| T-47 | 既定機体の翼面積が文献値3-7倍小（翼荷重333 g/m²） | R2-conf-F-12 |
| T-48 | `_airframe(trim_alpha_deg=)` が TypeError、`shape_flag` が clamp 外 | R2-contract-18,17 |
| T-49 | 永続バンドルに version なし・書き込み非アトミック | R2-contract-14,15 |
| T-50 | 3D/応力/記録/最適化 Various の単位・範囲の二重正本 | R2-contract-9, R1-M-10 |

## P3 — 低（衛生・記述・体裁）

| ID | 問題 | 主な証拠 |
|---|---|---|
| T-51 | `mesh_domain.__all__` に未定義名2件（ruff F822） | R1-M-17 |
| T-52 | 死定数・到達不能コード・docstring 矛盾（`FlightDivergenceError` 等） | R1-L-25,26 / R1-M-24 |
| T-53 | 絶対 import + `except Exception: return 0.0` の暗黙フォールバック | R1-L-28 |
| T-54 | 日本語の文字化け以外の表記ゆれ（terminology drift 3系統） | R2-a11y-15 |
| T-55 | `__all__`/`skill/README.md` が存在しないファイルを索引 | R2-ops-34 |
| T-56 | ログ/監視の相関ID無し・GPU経路の無ログ | R2-ops-24,25 |
| T-57 | `__pycache__`/`.mypy_cache`/`.ruff_cache` の ignore 方針不備 | R1-L-29 |
| T-58 | `docs/discord-bridge.md` 等 記述の正本ずれ（SPEC/Knowledge/agents.md） | R2-ops-8 |
| T-59 | `.codex/config.toml` が承認/sandbox 無効をコミット | R1-L-26 |
| T-60 | MCP: package.json が ignore  Hur で clone から再構築不能、memory 無制限 | R2-ops-32,33 |

---

## R4 で追加された項目（優先度順）

| ID | 優先度 | 問題 | 主な証拠 |
|---|---|---|---|
| **T-61** | **P0** | **風洞モードで機体が後向きに吹かれる**（α_eff = −176° の反転流）。`/simulation/step` はこの流で積分する | R4-wind-1 |
| **T-62** | **P0** | **折り畳み crease の Bézier が発散**し、既定UI値で機首前方に302mm（スライダ全域で最大7.5m）のスパイク。描画・影・境界球を破壊 | R4-orig-1, R4-orig-6 |
| **T-63** | **P1** | **応力ヒートマップが転置**（スパン/弦が逆）。根翼前縁のピークが弦方向の帯として描画される | R4-wind-2 |
| **T-64** | **P1** | **`turbulence_scale` が渦長に効かず**、Taylor移流速度を V/scale に破損（29%誤差）。UIは単位 "m" と表示 | R4-wind-3, R4-orig-12 |
| **T-65** | **P1** | **折り畳み幾何と空力が完全非結合**（幾何 AR 0.60 vs 空力 AR 2.67、CG は展開形）。`wingspan_m` API は**翼幅ではなく用紙幅**をスケール（30%不足） | R4-orig-5 |
| **T-66** | **P1** | **折り畳み幾何と空力が同じ設定フィールドの解釈が3通り**（`fold_tip_deg` = 折り畳み80°+δ / 解析機 δ / 応力kernel δ）。`dihedral_deg` は折り畳み側で符号を破棄（−20 → +20） | R4-orig-3, R4-orig-4 |
| **T-67** | **P1** | **後縁 crease の左右非対称**（鏡像誤差 3.4mm、折角差 0.23°、面積差 1.4%）。空力は完全対称を仮定 | R4-orig-2 |
| **T-68** | **P2** | **ABL が単一対数則で無限延伸**（SPEC §1.2 の対数則+べき乗則が未実装。z=1km で風速の1.97倍） | R4-wind-4, R4-orig-13 |
| **T-69** | **P2** | **折り畳みメッシュが閉じた立体でない**（境界辺354本）が SPEC とテスト docstring は「閉じた固体」と明記。三角形の43%が法線と逆巻き | R4-orig-7, R4-orig-8 |
| **T-70** | **P2** | **`stress_domain` に応力テンソル/せん断/膜応力/翼端接地応力が不在**（スカラー曲げのみ）。許容応力則は docstring と逆、`gsm^-2.5` で紙厚が26倍の安全度差 | R4-wind-9, R4-wind-10, R4-orig-22, R4-orig-23 |
| **T-71** | **P2** | **風の方位基準が SPEC と90°矛盾**、世界軸が発射方位で風向と無関係。**風洞のブロック補正/半径/TI/壁厚が既定で死** | R4-wind-7, R4-wind-8, R4-orig-16 |
| **T-72** | **P2** | **折り畳みAPIの範囲検証なし**（`keel_beta_deg=1e9` → 300kmのジオメトリJSON）、**キャッシュ無し**で毎リクエスト全面折り（9.8ms、130kB） | R4-orig-9, R4-orig-10 |
| **T-73** | **P3** | 折り畳みドメインの**死コード多数**（`HingeNode`/`CreasePattern.nodes` 未消費→docstring の hinge-tree アルゴリズムは未実装）、**アーキタイプカタログ重複**、`shape_flag` 未マッピング、フレーム docstring 誤り | R4-orig-19,20,21 |
| **T-74** | **P3** | 熱柱に**補償下降流なし**（w≥0 全体）、`core_rel` 無上限。乱流は鏡像でなく減衰、異方性定数と docstring 不一致 | R4-wind-13, R4-orig-25, R4-orig-26 |

---

## 統計サマリ（全13サブエージェント）

| ラウンド | サブエージェント | 領域 | C | H | M | L | 計 |
|---|---|---|---|---|---|---|---|
| R1 | backend-physics | `physics/` 物理エンジン | 0 | 8 | 10 | 12 | 30 |
| R1 | frontend | `static/js/`, `templates/` | 1 | 6 | 9 | 11 | 27 |
| R1 | api-security | `api/`, `app.py` | 0 | 5 | 10 | 11 | 26 |
| R1 | tests-ci-deps | `tests/`, CI, 依存, 衛生 | 2 | 6 | 13 | 9 | 30 |
| R2 | paper-conformance | 論文一致度 | 1 | 5 | 8 | 14 | 28 |
| R2 | frontend-a11y | フロント品質/a11y | 3 | 7 | 29 | 13 | 52 |
| R2 | config-contract | 設定/データ契約 | 0 | 6 | 9 | 8 | 23 |
| R2 | ops-tooling | 運用/自動化/可観測性 | 2 | 8 | 20 | 8 | 38 |
| R2 | performance | 性能/スケーラビリティ/並行性 | 5 | 6 | 7 | 7 | 25 |
| R3 | test-effectiveness | テストスイート有効性 | 4 | 5 | 8 | 6 | 23 |
| R3 | e2e-journey | E2Eプロダクト・ジャーニー | 6 | 6 | 22 | 6 | 40 |
| R4 | origami-geometry | 折り畳み/幾何層と空力結合 | 1 | 4 | 5 | 6 | 16 |
| R4 | wind-stress | 風・応力・大気 | 1 | 2 | 8 | 5 | 16 |
| **合計** | **13名** | | **26** | **74** | **158** | **116** | **374** |

重複を除いた**独立した問題群**は P0=14 / P1=21 / P2=23 / P3=11 の **69件**（トリアージ表 T-01〜T-74。T-61〜T-74 は R4 で追加）。


