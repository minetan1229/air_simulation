# no-finish.md — 未完課題台帳（全体）

> 本ファイルは、本リポジトリで **意図的に未解決とした全残課題** を、依存関係・優先度順にまとめた台帳である。
> 2 系統を収録する。
>
> - **第一部 — コード / 規約準拠**: `docs/SPECIFICATION.md`（物理 §1 / GPU §2 / REST §3 / セキュリティ §4 /
>   構成 §7 / 責務境界 §8 / 検証 CI §9）に対する実装乖離。
> - **第二部 — 学術論文レジストリ**: 2,000 件の収集・検証・データベース化（`search/` 配下）の残課題。
>
> 収録時点: 2026-10-20 ／ 基準 commit: master `42d80e8`
> 検証状態: master は CI 全グリーン（compileall / ruff / mypy / pytest 860 件 / smoke_test 13 ステージ）

### 担当分割（重要）

残課題は **2 系統** に分かれる。同一ファイルに二重に書かないこと。

| 系統 | 収録場所 | 対象 |
|---|---|---|
| **仕様準拠** | 本ファイル「第一部」 | `docs/SPECIFICATION.md`（物理 §1 / GPU §2 / REST §3 / セキュリティ §4 / 構成 §7 / 責務境界 §8 / 検証 CI §9）への実装乖離、API・セキュリティ・運用・テスト・衛生 |
| **物理コア再構成** | `genetic-project` ブランチの `no-finish.md` ＋ `no-finish-physics-supplement.md` | `physics/aero_kernel.py` / `physics/gpu_dynamics.py` 導入に伴う据え置き・繰延（`genetic-project` の 866 PASS 基準） |

> **統合上の注意**: `genetic-project` ブランチもルート `no-finish.md` を持つため、マージ時に
> add/add 競合する。解消方針は **本ファイル（統一版）を正とする**。`genetic-project` 側の
> 物理バックログは `no-finish-physics.md` へリネームして取り込み、物理セクションの
> 詳細（E-1 GPU/CPU、Polhamus、Helmbold、失速外挿、`_local_*` 重複 等）はそちらを正とする。

---

## 0. 全体索引

### 第一部（コード / 規約準拠）— 依存関係順

| # | 区分 | 残課題 | 影響 | 着手可否 |
|---|---|---|---|---|
| A-1 | 物理 §1 | Viterna-Corrigan 深失速モデル未実装 | 深失速・回復飛行 | → `no-finish-physics.md` F-2 |
| A-2 | 物理 §1 | Helmbold 式が仕様式と不一致 | 全機体の揚力傾斜 | → `no-finish-physics.md` E-1 |
| A-3 | 物理 §1 | Polhamus 係数・空力弾性が既定で不活性 | 受動空力弾性 | → `no-finish-physics.md` E-2 |
| A-4 | 物理 §1 | 低速 pushover 閾値 3.0（仕様 2.0）・機構不活性 | 発進直後 | 前提 B-1 完了後 |
| A-5 | 物理 §1 | 構造応力が迎角 α を受けない | 翼根応力 | 前提 B-1 完了後 |
| A-6 | 物理 §1 | ABL に power law が無い | 低空風速 | 前提 B-1 完了後 |
| A-7 | 物理 §1 | 接地散逸上限が主機体バウンス未適用 | 地上衝突 | 前提 B-1 完了後 |
| B-1 | 責務 §8 | 物理モデルの重複実装 8 種 / 10 組が不一致 | 物理の一意性 | → `no-finish-physics.md` F-5 |
| B-2 | 責務 §8 | 受動空力弾性が §8 担当外 | 責務境界 | A 群と同一コミット |
| B-3 | 責務 §8 | `records_domain` が物理モデルを含む | 検証の二重化 | A 群と同一コミット |
| C-1 | REST §3/§4 | `api_error()` が 4xx/5xx で本文を返す | 内部情報の露出 | 即着手可 |
| C-2 | REST §3 | 一部 POST の入力範囲検証が無い | 500 化・過負荷 | 即着手可 |
| C-3 | REST §3 | `/wind/field` が座標系タグを返さない | 座標系取り違え | 即着手可 |
| C-4 | REST §3 | API prefix がリテラル 52 箇所 | 変更漏え | 即着手可 |
| C-5 | 構成 §7 | `create_app()` がファクトリでない | 状態漏れ | 即着手可 |
| C-6 | REST §3 | Suzanne / Dart プリセット不在 | 機体形状検証 | 即着手可 |
| D-1 | セキュリティ §4 | DNS rebinding（TOCTOU）余地 | SSRF 残存 | 即着手可 |
| D-2 | セキュリティ §4 | `API_KEY` 未設定時は公開 | 匿名での副作用実行 | 方針決定要 |
| D-3 | セキュリティ §4 | `/errors/report` の `url` 無制限 | 監査ログ汚染 | 即着手可 |
| D-4 | セキュリティ §4 | `.env` の平文 Discord 認証情報 | 端末漏えい | → H-1 |
| E-1 | 運用 §9 | `check_runs.py` がジョブ単位を列挙不可 | 手順の実行可能性 | 即着手可 |
| E-2 | 運用 §9 | `read_issues.py` に GitHub 認証が無い | レート制限 | 即着手可 |
| E-3 | 運用 §9 | `notify_discord.py` の例外出力が URL を含む | CI ログ漏えい | 即着手可 |
| E-4 | 構成 §7 | `cupy-cuda12x` が必須依存 | 導入障壁・CI 時間 | 即着手可 |
| E-5 | 構成 §7 | `search/tools/` が lint 対象 | 品質ゲートの混在 | 方針決定要 |
| E-6 | 構成 §7 | `send_webhook.py` が孤児 | 二重通知経路 | 即着手可 |
| F-1 | 検証 §9 | HTTP 層テスト未到達 8 ルート | 契約違反の検出漏れ | 即着手可 |
| F-2 | 検証 §9 | `investigate/`・`storage_domain` の直接テスト不足 | 回帰ガード不足 | 即着手可 |
| F-3 | 検証 §9 | 設定エクスポート CSV 経路が検証を迂回 | 不正設定の流通 | 即着手可 |
| G-1 | 構成 §7 | 死んだ設定フィールド 3 件 | 未機能な knob | 即着手可 |
| G-2 | 物理 §1 | `wind_domain` docstring の z 規約不一致 | 二重換算再発 | 即着手可 |
| G-3 | 物理 §1 | コメントと実態の乖離 | 誤読 | 即着手可 |
| G-4 | 構成 §7 | `.gitignore` のメッシュ拡張子が過広 | 追跡不能化 | 即着手可 |
| G-5 | 構成 §7 | `requirements.txt` と `pyproject.toml` の二重化 | 陳腐化 | 即着手可 |
| H-1 | 運用 | Discord トークンのローテーション | 漏えい範囲 | オペレータのみ |

### 第二部（学術論文レジストリ）— 優先度順

| # | 優先度 | 残課題 | 影響範囲 | 実測値 |
|---|--------|--------|----------|--------|
| 1 | P0 | 指定サブトピック 4 件が未充足（SPH/MPM・SoA/ECS・PBD/XPBD・KD-Tree） | カテゴリ 01/02/04/05 | 0 / 0 / 8 / 4 件 |
| 2 | P0 | 同一論文の近重複タイトル 11 組が残存 | カテゴリ横断 | Jaccard≥0.8 で 11 組 |
| 3 | P1 | 抄録未登録 800 件（40%） | カテゴリ 04 が最悪 | 800/2000 |
| 4 | P1 | 外部抄録ソースが HTTP 429 で遮断 | 抄録・被引用 | 遮断 3 API |
| 5 | P1 | 手法タグ未判定 87 件 | カテゴリ 01/03 が偏重 | 87/2000 |
| 6 | P2 | 著者名未登録 20 件 | 各カテゴリ | 20/2000 |
| 7 | P2 | タイトル翻訳の用語辞書・校正が未実施 | 全 2,000 件 | — |
| 8 | P2 | 候補プール 62,010 件が成果物に残らない | 選定工程の再現性 | 62,010 → 2,000 |
| 9 | P2 | ローカルテスト 10 件失敗（並行物理リファクタ由来） | ローカル開発 | master の CI は green |
| 10 | P3 | ローカルブランチ / worktree の未整理 | Git 衛生 | 2 ブランチ + 1 worktree |
| 11 | P3 | `.agents/Project.md` の Loop State 未更新 | 自律ループ | — |
| 12 | P3 | メモリ / VRAM 実測ピーク値未取得 | 資源制約 | — |
| 13 | P3 | レジストリの定期更新機構が無い | 運用 | スナップショット 2026-10-20 |

---

# 第一部: コード / 規約準拠の残課題

**基準**: `.agents/Project.md` → `docs/SPECIFICATION.md`（§1 物理 / §2 GPU並列 / §3 REST API / §4 セキュリティ / §5 引用 / §7 構成 / §8 責務境界 / §9 検証CI）
**対象時点**: master `24af076`（2026-10-19 反映済み・CI 全グリーン）
**記述の単位**: 各項目に「現状（証拠）/ 影響 / 完了条件 / 前提」を必ず付す。着手禁止の項目は理由を明記する。

---

## 0. 現況サマリ

| 区分 | 件数 | 状態 |
|---|---|---|
| A. 物理モデル仕様準拠（SPEC §1） | 7 | 3 件は `no-finish-physics.md` へ担当移動、4 件は残存 |
| B. 物理モジュール責務境界（SPEC §8） | 3 | 1 件は `no-finish-physics.md` へ担当移動、2 件は残存 |
| C. API / 契約の残差 | 6 | 未着手（影響小） |
| D. セキュリティの残差 | 4 | 未着手（影響中） |
| E. 運用ツール / CI の残差 | 6 | 未着手（影響小） |
| F. テストカバレッジの残差 | 3 | 未着手（影響中） |
| G. 設定 / 衛生の残差 | 5 | 未着手（影響小） |
| H. オペレータのみ実施可能 | 1 | **保留**（要ユーザー操作） |

直前の是正作業（Phases 0–4, 7）で解消済みの項目は本台帳に含めない。既に `docs/SPECIFICATION.md` §6/§7/§8 と `.agents/Knowledge.md` へ反映済み。

---

## 1. 着手順序と前提（最重要）

本台帳の A / B 群は、**別セッションが進行中の物理コア再構成と全面的に重複する**。
`physics/aero_kernel.py` / `physics/gpu_dynamics.py` / `physics/origami_domain.py` の追加と、
`physics/flight_sim.py`（861 行）/ `physics/aero_domain.py`（525 行）/ `physics/parallel_engine.py`（657 行）/
`physics/optimizer_domain.py`（234 行）/ `physics/stress_domain.py`（133 行）/ `physics/wind_domain.py`（157 行）/
`physics/atmosphere_domain.py`（90 行）の改変が未コミットのまま存在する。

このため A-1 / A-2 / A-3 / B-1 の 4 項目は **担当を `no-finish-physics.md` へ移し**、
本台帳には仕様との差分のみを残した。残る A-4 / A-5 / A-6 / A-7 / B-2 / B-3 は
`genetic-project` のバックログに該当項目が無いため、本台帳が正本である。

したがって:

1. **A / B 群は、並行作業の統合完了後に着手する。** 先に着手すると同一箇所を二重実装し、統合時に全面衝突する。
2. **C〜G 群は並行作業と非競合のため即着手可能**（`api/` / `tools/` / `.github/` / `tests/` / `docs/` / 設定ファイルを扱う。ただし `physics/storage_domain.py`・`physics/records_domain.py`・`physics/config_schema.py` には競合しうる変更を含むため、変更前に差分確認を行う）。
3. 着手前に必ず `git fetch origin` し、対象ファイルの未コミット差分が存在しないことを確認する。

---

## A. 物理モデル仕様準拠（SPEC §1）— 未着手

> 影響: 物理計算結果そのもの。着手のたびに全シミュレーション結果の基準値が変わるため、
> **必ず特性テスト（characterization test）を先に追加してから**変更する。

### A-1. Viterna & Corrigan 1982 の深失速モデルが未実装

> **担当移動**: `genetic-project` ブランチの `no-finish-physics.md` **F-2**（= `no-finish-physics-supplement.md` §2 F）が
> 正本。ここでは仕様との差分だけ記す。
>

- **現状**: `physics/aero_domain.py:358` は Hoerner の完全剥離平板揚力 `2 sin a cos^2 a` による近似。
  失速後は `physics/aero_domain.py:382` `_post_stall_lift_coefficient` が
  `POST_STALL_BLEND_START_DEG = 45.0`（`:373`）から `post_stall_slope` による線形外挿＋smoothstep ブレンド。
  Viterna-Corrigan の `A1/A2/B1/B2` 外挿係数と `CD_max → 1.8〜2.0` の関係式は存在しない。
- **仕様**: SPEC §1.1:19「失速後抗力 `CD_max` 1.8〜2.0 へ Viterna-Corrigan で外挿し Hoerner 平板抗力へブレンド」。
  現状は `CD_max` の数値目標に**到達**するが方式が別物であり、§5:135 が Helmbold/Polhamus/Viterna を
  「準拠」と明記している以上、方法の不一致は正本違反。
- **影響**: 失速後挙動・回復飛行・着陸分散の予測精度。紙飛行機の主検証。
- **完了条件**: Viterna-Corrigan の 4 係数を実装し Hoerner 平板抗力へブレンド。
  `CD_max` が 1.8〜2.0 域に到達すること、深失速域の `CL(α)` が連続であることを
  `tests/test_aero.py` に**数値区間で**固定する（現状は外挿の式そのものを test が固定化していない）。
- **前提**: A-2（揚力傾斜式）と同時に行うと差分が混在するため、A-2 → A-1 の順で着手する。

### A-2. Helmbold 揚力傾斜式が仕様式と不一致

> **担当移動**: `genetic-project` ブランチの `no-finish-physics.md` **E-1**（GPU/CPU backend 統合）が
> 揚力傾斜式の正本化を含む。差分のみ記す。
>

- **現状**: `physics/aero_domain.py:213` `helmbold_cl_alpha` は
  `2πAR / (2 + sqrt(AR² (1 + taper) + 4AR/π))`。
  仕様式は `2πAR / (2 + sqrt(AR² (1 + tan²(Λ_c/2)) + 4))`。
  相違は 3 点：(a) 定数項が `4AR/π`（仕様は `4`）、(b) `1 + taper_ratio`（仕様は `1 + tan²(Λ_c/2)`、
  後退角 Λ_c が input として存在しない）、(c) `AR ≤ 4` の適用条件が無い。
- **影響**: 全機体の揚力傾斜。低 AR 機（紙飛行機は AR 1〜3）で誤差が最大。
  さらに `tests/test_aero.py:213-217` は**実装式を固定化**しているため、仕様準拠へ変えると
  意図せずテストが PASS する形になる（真の検証が無い）。
- **完了条件**: 後退角 `Λ_c` を `AircraftConfig` に追加し、仕様式どおりの実装へ置換。
  `AR ≤ 4` 条件と `AR > 4` の外挿挙動を分岐として明示。`tests/test_aero.py` を
  「仕様式の実装」に対する検証へ書き換え、`AR` 走査で実測値を固定する。

### A-3. Polhamus 渦揚力係数が既定で不活性

> **担当移動**: `genetic-project` ブランチの `no-finish-physics.md` **E-2**（Polhamus 係数）が正本。
> 差分のみ記す。
>

- **現状**: `physics/config_schema.py:316` `polhamus_kv: float = 0.0`（既定 0 = 渦揚力 항等的に不活性）。
  `Kp` は Helmbold 傾斜へ焊接されており、Polhamus 係数として独立していない。
  `cp_shift_gain`（`:318`）、`aeroelastic_compliance`（`:321`）、`cg_offset_x`（`:313`）も既定 0。
- **影響**: SPEC §1.1 の受動空力弾性モーフィング（§5:132 / §8 row-1 = aero-physics 担当）が**既定では動かない**。
  仕様上は主要機構だが、標準プリセットではその挙動が観測されない。
- **完了条件**: `Kp` / `Kv` を独立係数として実装し、既定値を**物理的に妥当な非ゼロ**へ変更。
  モーフィングの δ_eff(q) 遷移を `tests/test_aero.py` で数値固定する。

### A-4. 低速 pushover 動力学の閾値・機構が仕様相違

- **現状**: `physics/aero_domain.py:1029` は apex 付近の低速条件を `speed < 3.0 m/s` と明記。
  仕様は `V < 2.0 m/s`（SPEC §1.1）。また CP 後退（`cp_shift_gain`）と CoM 偏心（`cg_offset_x`）が
  既定 0 のため、実動は ad-hoc 項に依存している。
- **影響**: 発進直後の physiologically 重要な低空域。Launch シミュレーションの初期姿勢。
- **完了条件**: 閾値 2.0 m/s へ是正し、CP 後退 + CoM 偏心を主機構として実装。
  A-3 と同一コミットで良久、A-3 の「既定値を活性化する」の一部として扱う。

### A-5. 構造応力が迎角 α を入力として受け取らない

- **現状**: `physics/stress_domain.py:780` `analyse(cfg, env, speed_mps, density_kgm3, load_factor, spanwise, chordwise, beta_deg, backend)`
  に `alpha_deg` がない。α は `beta_deg` のみ。受力抵抗は `beta_deg` 経由で影響を受けるが、α 依存の
  翼根曲げモーメントは未考慮。
- **仕様**: SPEC §1.3:39「翼面各頂点に対する動的応力評価値（0.0: 安全 〜 1.0: 破断限界）」が
  「動圧・迎角・Gフォース」を入力とすると明記。
- **影響**: 迎角依存の翼根応力（紙飛行機の主破断モード）。`/api/v1/stress/analysis` の応答精度。
- **完了条件**: `analyse` に `alpha_deg` を追加（既存引数は後方互換の default 付き）、
  α 依存モーメントを実装、REST 層 `api/routes/physics.py:218` まで伝播、
  `tests/test_stress.py` に α 走査による頂点応力の単調性を追加。

### A-6. ABL に power law が無い

- **現状**: `physics/wind_domain.py:605` `boundary_layer_profile` は中立 ABL の log law
  `u(z) = u_ref ln(z/z0) / ln(z_ref/z0)` のみ。power law 表現や層別遷移が無い。
- **仕様**: SPEC §1.2 の境界層記述（power law を含む）。`flight_sim.py:726` `_local_wind` は
  独自実装で `[0, 1]` クランプ하며、`wind_domain` 側は無制限で**同一高度でも値が違う**（B-1 と共通原因）。
- **影響**: 低空（紙飛行機が飛翔する高度帯）の風速予測。
- **完了条件**: power law（粗度長依存指数 α）表現を追加し、log law との切替・ブレンド条件を明記。
  `tests/test_atmo_wind.py` で `wind_domain` と `flight_sim` の高度プロファイル一致を固定する
  （A-6 は B-1 の解消に前提となるため、B-1 と同一コミットで実装する）。

### A-7. 接地エネルギー散逸上限が主機体バウンス経路に未適用

- **現状**: `physics/flight_sim.py:2443` の `max_ratio = max(rest_val * rest_val, 0.25)` による
  散逸上限は**翼端接触（wingtip contact）分岐内**にのみ存在する。
  主機体接地バウンス経路（`flight_sim.py:2470` `if vz > 0.0 and not wingtip_contact:` 以降）は
  散逸上限の適用を受けない。
- **影響**: 地上衝突時のエネルギー保存則制約。`Knowledge-important.md` の常駐制約
  「翼端接地は E_post ≤ E_pre × max(restitution², 0.25)」に対して、主機体接地は同条件を満たさない。
- **完了条件**: 散逸上限を両接地経路へ共通適用（共通ヘルパー化）。
  `tests/test_energy_conservation_ground.py` に主機体接地ケースを追加し、
  散逸上限が接地種別によらず成立することを固定する。

---

## B. 物理モジュール責務境界（SPEC §8）— 未着手

> A 群と同一コミット群で実施する（A 群のモデル統一で重複が解消してから移動する順序が安全）。

### B-1. 同一物理モデルの重複実装（8 重複 / 10 組が数値不一致）

> **担当移動**: `genetic-project` ブランチの `no-finish-physics.md` **F-5**（`_local_atmosphere` /
> `_local_wind` / `_local_stress` の重複）が正本で、4 モジュールの精密な一覧を持つ。
> 全体像のみ記す。
>

- **現状**（正本 → 重複先）:
  | モデル | 正本 | 重複実装 | 不一致内容 |
  |---|---|---|---|
  | Helmbold 揚力傾斜 | `aero_domain.py:213` | `flight_sim.py:506` | 式が 3 系統 |
  | Prandtl-Glauert | `aero_domain.py:224` | `flight_sim.py:514` | 圧縮性補正のクランプ値が別 |
  | ISA / 湿潤大気 | `atmosphere_domain.py:100` | `flight_sim.py:567` | 飽和蒸気圧式が別 |
  | Sutherland 粘性 | `atmosphere_domain.py:244` | `flight_sim.py` 内部 | — |
  | ABL 風速プロファイル | `wind_domain.py:605` | `flight_sim.py:726` | クランプの有無 |
  | ガスト包絡 | `wind_domain.py:565` | `flight_sim.py:726` | 時系列の式が別 |
  | 失速後 CL | `aero_domain.py:382` | `parallel_engine.py:981` | 曲線形状が別 |
  | 許容応力 | `stress_domain.py:456` | `flight_sim.py:1063` | 桁が別 |
  | ISA 密度 | `atmosphere_domain.py` | `parallel_engine.py:325` `isa_air_density` | 3 つ目の大気モデル |
- **影響**: 「どちらが正しいか」がコードからは判定できず、CUDA 経路と参照経路が別の物理になる根本原因。
  **A 群のすべての変更が二重実装へ伝播する**ため、A 群着手前に解消すべき。
- **完了条件**: `flight_sim.py` から `helmbold_cl_alpha` / `prandtl_glauert` / `ground_effect_factor` /
  `_local_atmosphere` / `_local_wind` / `_local_aero` / `_local_stress` を削除し、
  `aero_domain` / `atmosphere_domain` / `wind_domain` / `stress_domain` の一本化へ置換。
  依存方向を `flight_sim → *_domain` の一方向に限定する。
  `parallel_engine.py:325` `isa_air_density` は `atmosphere_domain` 参照へ置換。
  **先に A-2/A-3/A-6 で正本の式を確定させてから** 移動する（順序を逆にすると二重実装へ仕様修正を余分に発生させる）。

### B-2. 受動空力弾性モーフィングが §8 の担当外にある

- **現状**: 受動空力弾性（`delta_eff(q)`）は `physics/flight_sim.py:2100` 付近に実装されている。
  SPEC §8 row-1（`aero-physics` 担当 = `physics/aero_domain.py`）の責務外。
  `physics/parallel_engine.py:943` に 3 つ目の実装。
- **影響**: 責務境界違反。SPEC §8 の「担当ファイル」の前提が成立しない。
- **完了条件**: `aero_domain.py` へ移設し、`parallel_engine.py:943` の重複を削除。SPEC §8 row-1 の記載と実装を一致させる。

### B-3. `records_domain` が物理モデルを含む

- **現状**: `physics/records_domain.py:820` `energy_budget`、`:862` `_drag_apex`、
  `:892` `release_speed_for_apex` は物理モデルであり、SPEC §8 の row-9（`records-records`）の
  「記録プリセット・検証マトリクス」という責務に属さない。
- **影響**: 記録プリセットの検証が物理モデル二重実装に依存し、モデル是正時に検証が壊れる。
- **完了条件**: 上記 3 関数を `flight_sim` / `aero_domain` へ移し、`records_domain` は
  データ（プリセット・記録・引用）のみで構成する。SPEC §8 row-9 の責務記述を更新する。

---

## C. API / 契約の残差 — 即着手可（影響小）

### C-1. `api_error()` が 4xx/5xx で本文を返す

- **現状**: `api/core.py:90-95` `api_error()` は常に `{"error": str(message), "status", "timestamp", "error_id"}` を返す。
  SPEC §4.1:118「レスポンスにはユニークな `error_id` のみを返却し、詳細トレースバックはサーバーログにのみ記録する」。
- **影響**: 内部メッセージ（設定名・クラス名・値）がクライアントへ露出する余地。
  トレースバックの秘匿自体は 500 経路で担保済み（`app.py:124`）、4xx の内部情報が主因。
- **完了条件**: production モードで本文を `error_id` のみに縮約し、dev モードでは詳細を返す分岐を実装。
  `tests/test_security_and_frames_audit.py` に production 相当の検証を追加する。

### C-2. 一部 POST エンドポイントで入力範囲の検証が無い

- **現状**: 型・範囲の検証が無い、または事後に例外となる箇所が残存。
  - `api/routes/simulation.py:272` `/simulation/ensemble` … `n_trajectories` 等の範囲検証なし
  - `api/routes/records.py:167` `/guinness/simulate` … `duration_s` に上限なし
  - `api/routes/mesh.py:121` `/mesh/overrides/<model_id>` … オーバーライド値の検証が API 層に無い
  - `api/routes/meta.py:205` `/system/force-gpu-render` … JSON 形状の検証なし
- **影響**: 不正入力が 500 化、または長時間・大量処理につながりうる。
- **完了条件**: 4 箇所に上限・型・許可値の検証を追加し、不正時 400 を返す。
  `tests/test_api_contract.py` に各エンドポイントの不正入力ケースを追加する。

### C-3. `/api/v1/wind/field` が座標系タグを返さない

- **現状**: `api/routes/physics.py:150-212` `wind_field()` は `jsonify(data)` を返すだけで、
  応答に wind ドメインの座標系（+z **up**）を示すタグが無い。
  同じ `/api/v1` プレフィックス配下に +z-down（世界 NED）を返す別エンドポイントがあり、
  クライアントが区別する手段が無い。
- **影響**: 座標系取り違えによる誤った風場適用。`Knowledge-important.md` の座標系常駐制約と関連。
- **完了条件**: 応答に `"frame": "z-up"`（および NED 側の `"frame": "z-down"`）を付与。
  座標系タグの存在を `tests/test_api_contract.py` で固定する。

### C-4. API バージョンの prefix がリテラル 52 箇所に散在

- **現状**: `api/routes/*.py` に `"/api/v1..."` が 52 個のリテラルとして存在。
- **影響**: バージョン変更時の改行漏え。現状は 1 箇所でもずれると route 登録が割れる。
- **完了条件**: 共有定数（例 `api/core.py` の `API_PREFIX`）へ集約し、`url_for` 相当の一元管理にする。

### C-5. `create_app()` がファクトリとして機能しない

- **現状**: `app.py:274` `create_app()` は引数なしの薄いラッパで、modules import 時に
  `app.py:44` でグローバルな `Flask` インスタンスが生成される。
  `api/core.py:267` `SESSION` と `:269` `RESULT_CACHE` はプロセス全体で共有され、
  `create_app()` を複数回呼んでも別アプリが得られるわけではない。
- **影響**: テスト間の状態漏れ、WSGI サーバでの複数 worker 運用を想定した分離が不可。
- **完了条件**: アプリ生成を真のファクトリ化（`SESSION` / `RESULT_CACHE` を app インスタンス配下へ移動）。
  併せて `app.py:45` の `app.config["JSON_SORT_KEYS"]` は Flask ≥ 2.3 で無効なため `app.json.sort_keys` へ置換。

### C-6. プリセットの SPEC 充足（Suzanne / Dart）

- **現状**: `physics/records_domain.py` の `RECORD_PRESET_CATALOGUE` に `toda_29_2s`（`:1358`）等の
  戸田拓夫 Glu.glider 系は存在するが、SPEC §3.6:105 が名指しする **Suzanne** と **Dart** の
  プリセットは存在しない。
- **影響**: SPEC §3.6 の「登録済み機体プリセット（戸田拓夫氏滞空記録機、Suzanne、Dart 等）一覧」の一部が欠落。
  機体形状プリセット换来用途での検証が選べない。
- **完了条件**: Suzanne / Dart の機体形状・質量・投擲条件を `RECORD_PRESET_CATALOGUE` に追加し、
  `tests/test_records.py` で各プリセットの物理パラメータ整合を固定する。

---

## D. セキュリティの残差 — 即着手可（影響中）

### D-1. DNS rebinding（TOCTOU）の余地

- **現状**: `physics/mesh_domain.py` の `validate_url_safe` は解決時に全アドレスを検査するが、
  接続時に urllib 側で再解決する。**検証済み IP へのピニング**がないため、
  検証と接続の間で DNS を差し替える rebinding が可能。
- **影響**: 検証をすり抜けた接続がプライベートネットワークに届く可能性が残る。
- **完了条件**: 検証済み IP への接続固定（カスタム HTTPSHandler / リゾルバ固定）を実装。
  既存の「検証 → 再検証」の二段構えと併存させる。

### D-2. 認証が「API_KEY 未設定時は公開」

- **現状**: `app.py:133-135` は `API_KEY` 未設定なら認証を行わない。
  したがって既定のデプロイでは保護対象エンドポイント（`/api/v1/optimizer/run`、`/api/v1/mesh/import` 等）が無認証で到達可能。
- **影響**: 匿名で最適化ジョブを起動・モデル取込を実行できる。ローカル開発用途としては合理的だが、
  公開デプロイ時の事故リスクが残る。**SPEC §4 は API キー認証を要求していない**ため、
  仕様解釈ではなく運用方針の決定事項。
- **完了条件**: 方針を 1 つ決め実装する。候補: (a) `API_KEY` 未設定時は
  副作用のあるエンドポイントのみ 503 で拒否、(b) 本番起動時に `API_KEY` 必須を fail-fast、
  (c) 現状維持（ローカル専用という運用を明文化）。`.env.example` へ記載する。

### D-3. `POST /api/v1/errors/report` の監査ログ汚染余地

- **現状**: `api/routes/records.py:232` 付近で `message`（2000 文字）、`details`（各 500 文字・20 キー）、
  `stack`（4000 文字）、`level`（許可リスト）、`category`（64 文字）を制限済み。
  残る余地は `url` フィールド（`req_path`）が無制限で、サーバーログに任意の文字列が書き込める。
- **影響**: 監査ログへの注入。深刻度は小。
- **完了条件**: `url` も 512 文字・URL 形式に制限する。`tests/test_error_logging.py` に追加。

### D-4. `.env` 内の平文 Discord 認証情報

- **現状**: リポジトリの作業ツリー（OneDrive 同期領域）の `.env` に
  `DISCORD_WEBHOOK_URL`（完全なベアラートークン）と `DISCORD_BOT_TOKEN` が平文で存在する。
  git 追跡はされていない（`.gitignore` 生效）ため**リポジトリ漏洩ではない**。
  ただし同期フォルダ上に平文であり、`tools/notify_discord.py` が毎回の呼び出しで同値を読み込む。
- **影響**: 端末・同期フォルダの漏えい範囲での webhook 悪用。
- **完了条件**: トークンのローテーション（Discord 側での再発行）＋ OS 資格情報ストア or
  CI Secret への移行。`.agents/skills/notify-webhook.md` の「平文 `.env` を正本とする」規約自体を改訂。
- **備考**: トークン生成・失効はオペレータ操作のため**自動実行できない**。→ 節 H 参照。

---

## E. 運用ツール / CI の残差 — 即着手可（影響小）

### E-1. `tools/check_runs.py` がジョブ単位を列挙できない

- **現状**: `tools/check_runs.py:39-43` は run 単位の `status` / `conclusion` のみを出力し、
  ジョブ（job）単位の列挙を行わない。AGENTS.md §3 の「全ジョブグリーンを確認」が
  手順としては成立しない。
- **影響**: 手順の実行可能性。実運用では「run が success なら全ジョブ green」と読み替える運用で回避している。
- **完了条件**: `/actions/runs/<id>/jobs` を参照し、失敗ジョブと失敗ステップを出力する。
  本作業で実際にこの API Bringing が必要になった。

### E-2. `tools/read_issues.py` に GitHub 認証が無い

- **現状**: `tools/read_issues.py:6` にリポジトリ URL がハードコードされ、
  `GITHUB_TOKEN` を送信せず、`.env` を読まない。`sys.stdout.reconfigure`（`:5`）は例外安全でない。
- **影響**: 大量 Issue で API レート制限に達すると取得失敗。`close_resolved_issue.py` 側も同様に
  リポジトリ slug のハードコードが残存。
- **完了条件**: `GITHUB_TOKEN` 送信・`.env` 読込・`reconfigure` ガード・slug の環境変数化を実装。

### E-3. `tools/notify_discord.py` のエラー出力が URL を含む可能性

- **現状**: `tools/notify_discord.py:119` `print(f"[notify_discord] Failed to send webhook: {exc}")` は
  例外文字列をそのまま出す。`urllib` の `URLError` 系メッセージには URL が含まれる場合がある。
- **影響**: CI ログに webhook の一部が漏れる可能性。深刻度は小。
- **完了条件**: 例外文字列から URL をマスクしてから出力する。

### E-4. `cupy-cuda12x` が必須依存のまま

- **現状**: `requirements.txt:5` `cupy-cuda12x>=13.0.0` は無条件必須。
  CPU 専用環境でも約 1 GB のダウンロードが発生する（`setup.sh` は GPU 不在でも成功するが、インストールは重く）。
- **影響**: CI の実行時間・キャッシュ効率、導入障壁。
- **完了条件**: GPU 依存を extras（`pyproject.toml` の `gpu` extra）へ分離し、
  CI も GPU ジョブを opt-in 化する。SPEC §2.1 の CPU フォールバックは既に実装済みであり、
  依存の分離は設置・運用上の規模の問題。

### E-5. `search/tools/` 14 モジュールが lint 対象

- **現状**: `search/tools/*.py`（論文収集パイプライン）は `ruff.toml` の exclude 対象外で、
  `ruff check .` の検査対象に含まれる。`tools/experiments/` は除外済み。
- **影響**: 研究コーパスのツールが製品コードと同じ品質ゲートに載る。
- **完了条件**: 方針決定 — (a) 品質を維持して lint 対象のまま置く、(b) `search/tools/` を
  検査対象から除外し、リポジトリ固有の lint 設定を使う。

### E-6. `tools/mcp_call.py` / ルート `send_webhook.py` の位置づけ

- **現状**: `tools/mcp_call.py` は AGENTS.md §7 に記載されている（正しい配置）。
  一方ルートの `send_webhook.py` は `tools/notify_discord.py` の薄いラッパで、
  プロトコル文書のどこからも参照されない孤児。
- **影響**: 軽微。二重の通知経路が残る。
- **完了条件**: `send_webhook.py` を `tools/` へ統合するか削除する。

---

## F. テストカバレッジの残差 — 即着手可（影響中）

### F-1. HTTP 層テスト未到達のルートが 8 件

- **現状**: 登録済みルート 52 のうち、HTTP 層テストが無いものは 8 件:
  `/api/v1/environment/config`, `/api/v1/errors/clear`, `/api/v1/guinness/simulate`,
  `/api/v1/investigate/result/<job_id>`, `/api/v1/jobs/join/<job_id>`,
  `/api/v1/mesh/overrides/<model_id>`, `/api/v1/simulation/history/<int:index>`,
  `/api/v1/simulation/launch`。
- **影響**: これらの経路は契約違反が検出されない。
- **完了条件**: `tests/test_api_contract.py` に 8 件の HTTP テストを追加する。

### F-2. `physics/investigate/` と `physics/storage_domain.py` の直接テスト不足

- **現状**: `tests/test_investigate.py` は `auto_investigate` を覆盖するが、
  `physics/investigate/`（`tuning.py` ほか）の直接テストが無い。
  `physics/storage_domain.py` も CSV/JSON エクスポートの検証経路に対する直接テストが無い。
- **影響**: C-2 / C-3 で変更する `storage_domain`（`export_config` の CSV 検証迂回）に
  回帰ガードが無い。
- **完了条件**: `tests/test_storage_domain.py`（有効/無効設定の往復、CSV/JSON の一致）と
  `tests/test_investigate_tuning.py` を新設する。

### F-3. 設定エクスポートの CSV 経路が検証を迂回

- **現状**: `physics/storage_domain.py` `export_config` の CSV ブランチは
  `config_bundle_to_rows(bundle)` を呼ぶが、`validate_config_bundle(bundle)` を通さない
  （JSON ブランチのみ検証）。`validate_config_bundle` は未知のトップレベル鍵を素通しする。
  書き込みバイト数の上限も無い。
- **影響**: 不正な設定がそのままエクスポートされ、返却時に検証される保証が無い。
  大量データでメモリを消费的になりうる。
- **完了条件**: CSV ブランチも検証を通す。未知トップレベル鍵を拒否する。
  出力バイト数の上限を設ける。→ F-2 のテストと同時に実施する。

---

## G. 設定 / 衛生の残差 — 即着手可（影響小）

### G-1. 死んだ設定フィールド

- **現状**: `AircraftConfig.shape_flag`（`physics/config_schema.py:245`）と
  `SimulationSettings.time_scale`（`:933`）は定義のみで**どの物理モジュールからも読まれない**。
  `static_margin`（`:312`）も本番モデルでは未使用。
- **影響**: 設定 UI が未機能な knob を公開しうる。
- **完了条件**: 実装するか、削除してスキーマから消す。

### G-2. `wind_domain` の docstring とコードの z 規約が不一致

- **現状**: `physics/wind_domain.py:5-6` の docstring は「z = 地上高さ」を述べるが、
  実装は「z = `ground_level_m` 平面の絶対高度」を使う（`:839`, `:799`）。
  `flight_sim.py:795` は 2026-10-19 の是正で**絶対高度**を渡すよう修正済みだが、
  風場ドメイン側の記述がまだ追従していない。
- **影響**: 誤読による将来の二重換算再発。
- **完了条件**: docstring を実装の規約に一致させ、規約を 1 文で明記する。

### G-3. `physics/records_domain.py` / `parallel_engine.py` のコメントと実態の乖離

- **現状**: `physics/parallel_engine.py:1527` のコメントは「Vectorized RK4/Euler」と記すに対し、
  実装は半陰的 Euler のみ（RK4 ではない）。`physics/records_domain.py` は A 群の relocation 対象。
- **影響**: 誤読の温床。
- **完了条件**: コメントを実装に一致させる（A-1 / B-3 と併せて実施）。

### G-4. `.gitignore` のメッシュ拡張子パターンが過広

- **現状**: `.gitignore` の `*.glb *.gltf *.stl *.obj *.fbx` はリポジトリ全体に影響する。
  将来 `assets/` 等を運用すると追跡できなくなる。
- **影響**: 軽微。`models/` は既に無視済み。
- **完了条件**: 無視対象を実際の配置場所（`models/`, `artifacts/`）に限定する。

### G-5. `requirements.txt` と `pyproject.toml` の依存二重化

- **現状**: `pyproject.toml` の `[project.dependencies]` と `requirements.txt` が併存し、
  内容が二重化されている。CI は `requirements.txt` を、導入手順は `pyproject.toml` を参照する。
- **影響**: どちらかが更新されず第二种が古い状態になりうる。
- **完了条件**: 単一正本を決め、もう一方から生成する（または相互参照させる）。

---

## H. オペレータのみ実施可能 — 保留

### H-1. Discord トークンのローテーション

- **対象**: 節 D-4。
- **理由**: トークンの再発行・失効は Discord の管理操作であり、このセッションからは実行できない。
- **必要な操作**: (1) 現行 webhook / bot トークンを Discord 側で失効、(2) 新規発行、
  (3) 新値を OS 資格情報ストアまたは CI Secret へ格納、(4) `.agents/skills/notify-webhook.md` の規約改訂。
- **完了条件**: 旧トークンが失効済みで、新トークンが平文 `.env` を含まずに供給されること。

---

## 2. 推奨着手順（依存関係順）

```
[前提] 並行物理コア再構成（genetic-project）の統合完了を待つ
   │
   ├─ 1. 物理側（本台帳に残した項目のみ。担当移動した 4 項目は no-finish-physics.md が正本）
   │     B-1 物理モデルの一本化 → A-4 pushover → A-5 応力への α
   │     → A-6 ABL power law → A-7 接地散逸上限 → B-2 / B-3 責務境界の移動
   │     ※ 特性テスト（変更前の数値を固定する回帰テスト）を先に追加してから実装に入る
   │
   └─ 2. 非物理側（並行作業と非衝突。今すぐ着手可）
         F-3 + C-2（ストレージ検証・入力検証）／ C-1・C-3・C-4・C-5・C-6
         D-1〜D-3 / E-1〜E-6 / F-1・F-2 / G-1〜G-5 / H-1（別セッション）
```

- **1** は**物理結果を変える**ため、各コミットで
  `compileall` / `ruff` / `mypy` / `pytest` / `smoke_test` を全通過させ、
  かつ特性テストを先に追加してから実装に入る。
- **2** の各項目は物理結果を変えないため、並行作業と非競合のうちに個別に進行できる。
- **H-1**（Discord トークンローテーション）は本セッションからは実行できず、オペレータ操作が必要。

---

## 3. 本ファイルが古くなっていないかの確認手順

- 本台帳の evidence（`file:line` 参照）は基準 commit `42d80e8` / `defd482` 時点の値。
  物理コア再構成の統合後は、**A 群・B 群の evidence を再採取**してから着手する。
- 物理側の正本は `no-finish-physics.md`（`genetic-project` ブランチ由来）であり、
  そこに A-1 / A-2 / A-3 / B-1 の詳細がある。両ファイルの内容が食い違ったら
  **物理側を正とする**（`genetic-project` が実装会同伴のため）。
- 手順の確認: `python tools/check_runs.py` / `python tools/check_issues.py` で
  CI と Issue の状態を再取得し、本台帳の各「現状」記述と実装が一致するかを確認する。

---

# 第二部: 学術論文レジストリ（2,000 件収集）の残課題

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
上位400件の選定枠から外れた。`paper_taxonomy.py` の `core` 重み（SPH=2.0, PBD 系=3.0 等）と
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

**前提**: 対象4サブトピックいずれも 1件以上の論文が存在する（Crossref/DataCite 上に十分量あることは
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
   複数語の結合（例: "graph neural network" と "gnn"）を同一視するエイリアス表を追加。
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
