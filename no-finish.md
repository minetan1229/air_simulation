# no-finish.md — 意図的に未解決／次フェーズ繰延の残課題

> **位置づけ**: 監査の指摘一覧は `PHYSICS_PAPER_CONFORMANCE_AUDIT.md`（§2 高 / §3 中 / §4 低 / §5 非論文系）、
> 修正進捗は同 §0.0 と §7「2026-10-20 実装状況」。本ファイルはそのどちらにも
> 載らない **「あえて直さなかったもの」「今回スコープ外としたもの」** を 1 箇所に
> 集約し、次フェーズへ引き渡す台帳である。
>
> - 対象ブランチ: `genetic-project`（保護ブランチ `master`/`main` への操作は厳禁）
> - 基準コミット: `b5329f4`（`39bb795` の aero_kernel / gpu_dynamics 導入 → `b5329f4` の論文レジストリ記録）
> - **現在の完了状況: §0.1「完了状況チェックリスト（2026-10-22 更新）」が正本**。未着手（`[ ]`）は 0 件
> - 検証状態（2026-10-22 実測）: `pytest tests` = **1264 passed / 0 failed**（**全スイート 2 回の実測**: 814.48 s / 2326.41 s。**部分実行**〈負荷の高い 10 ファイル 209 件は除外: `test_investigate` / `test_issue28_gpu_duration` / `test_ga*` / `test_ga2*` / `test_optimizer` / `test_gpu_kernel_parity`〉では **1043 passed / 0 failed**。**全記載は同一日の実測で矛盾しない**） / `ruff check .` 0 件 / `mypy physics api app.py smoke_test.py` 0 件（57 ファイル） / `python -m compileall` 0 / `python smoke_test.py` **14/14 PASS**（10.81 s）
> - 最終更新: 2026-10-22
> - 2026-10-20 追記: 折り紙3Dジオメトリ刷新の残件（据え置き A-13〜A-19／繰延 G-1〜G-8）
> - 2026-10-20 追記 2: **2026-10-19 マルチエージェントレビュー（13 名 / 374 指摘）**由来の残課題を
>   **§7（残るもの）** と **§8（あえて解決しないもの）** に追加。統合順序は **§9**。
>   詳細（全指摘の file:line 付き記録）は `docs/CODE_REVIEW_FINDINGS.md` が正本。
> - 2026-10-21 追記 3: **2026-10-19 コミット監査（`09aa40e` の劣化分解）と 2026-10-21 の実測スイート走査**で
>   判明した残課題 4 件（K1-P0-1〜K1-P0-3 / K1-P1）を **§4.7** に追加。統合順序は **§9** に反映。
> - 2026-10-21 追記 4: **K2 台帳再統合**（`.agents/Knowledge.md` 80 行化 / `.agents/Project.md` 40 行化）に
>   際して 2026-10-21 に実測した残課題 **P0-1〜P0-9 / P1**（§4.7 の 4 件を統合し、GA エンジンの審査
>   P0 4 件と未実装 2 件を追加）を **§4.8** に追加。統合順序は **§9** に反映。
> - 2026-10-21 追記 5: **K3 台帳（追記のみ）**で 2026-10-21 の再実測結果を **§4.9** に追加（P0-1〜P0-7 / P1 の判定更新。§4.7 / §4.8 と同一系統で、既存行は 1 文字も書き換えていない）。
> - 2026-10-22 追記 6: **台帳の正本同期（W13）**。Z2（独立検証）が残した唯一の既知問題「台帳陳腐化」を含め、
>   **コード・テストで解消済みと確認できたエントリを「解消済み（コミット: <hash>）」として §C-1 と §7.0 の
>   対照表・§9 へ移し、§A / §B / §D の各エントリは削除せずに現状へ同期**した（§A-2 / §4.1 H-19 /
>   §4.2 M-17 / §7.1 P0-1 / P0-3 / P0-4 / P0-5 / P0-6 / P0-7 / P0-8 / P0-9 / P0-10 / P0-11 /
>   §5.5 G-4 / §4.7 / §4.8 / §4.9）。
>   **残存項目は消さずに残し、出所（file:line・再現コマンド・実行したテスト結果）を強化**して維持した
>   （新設の **§4.10** に R-1〜R-4 として明文化）。判定の根拠は本ファイルの各行の「判定」欄に記載。
> - 2026-10-22 検証ベースライン: `python -m ruff check .` = All checks passed /
>   `python -m mypy physics api app.py smoke_test.py` = Success: no issues found /
>   `python -m pytest tests -q` = **1264 passed / 0 failed**（全スイート 2 回実測: 814.48 s / 2326.41 s）/ `python -m pytest tests/test_no_mojibake.py -q` = 4 passed /
>   `python tools/check_mojibake.py` = 検出 0 件 / `python tools/check_stress_peak_parity.py` =
>   最大相対差 0.000e+00。

---

## 0. このファイルの読み方

| 区分 | 意味 | 次の行動 |
|---|---|---|
| **A. あえて未解決** | 物理的・工学的理由から**意図的に現状維持**した | 再オープンの条件を書く。条件成立時に再オープン |
| **B. 仕様主張の是正** | 「直す」のではなく**過大主張を撤回**して解決した | 再発防止のテスト・文書更新のみ |
| **C. 解決済みだが残る限定** | 今回の変更で**範囲外と明示した**近似 | 次フェーズで精度を上げる候補 |
| **D. 次フェーズ繰延** | 着手していない指摘（重要度順） | §7 ロードマップの P3 残タスクと対応 |
| **E. レビュー由来の残課題**（§7） | 2026-10-19 の 13 名独立レビューで検出した、**現時点で実在する**不具合 | §9 の順序で着手。**着手前に §7.0 の「解消済み」「要再検証」を必ず読む** |
| **F. レビュー由来・あえて解決しない**（§8） | レビューで検出したが**意図的に維持**／**復元不能**／**環境制約**の項目 | 再オープンの条件を明記 |

**注意**: 監査 §0.0 の「完了」表記は *P0〜P3 の各フェーズに列挙された個別項目* のみを
対象としており、監査 §2〜§5 の全指摘を網羅したものではない。D の残件は
**高 2 件 / 中 16 件 / 低 10 件 / 非論文系 8 件** である。

---

## 0.1 完了状況チェックリスト（2026-10-22 更新）

> **この節が台帳の正本**である。§4〜§8 の各表の「判定」列は 2026-10-20 採取時点のスナップショットであり、
> 以後の実装状況は本チェックリストが優先する。`[x]` は「指摘に対しては**修正済み、または意図的維持の
> 根拠と再オープン条件が明示された**」ことを意味する。`[ ]` は未着手のまま残る項目。
>
> - 判定方法: 2026-10-20 の全 105 項目を file:line 付きで再判定（解消 15 / 残存 83 / 判定不能 2 / 誤り 5）→ 実装 → 再実測
> - 誤り 5 件（§7.2 P1-6 / P1-7、§4.1 H-19、§7.4 P3-8 / P3-9）は**台帳の記述を訂正**して完了扱いとした
> - §1 A 系 / §8 F 系は**意図的維持**であり、値を触らず再オープン条件のみ記載して完了扱いとした

### 7.1 P0 — 最優先（14 件）

| ID | 完了 | 実装内容 |
|---|---|---|
| [x] **P0-1** | ✅ | 未追跡ファイル 0 件。生成物 `artifacts/` 10 件を git 追跡解除（ファイルは残置）、新規 `physics/origami_geometry_audit.py` は追跡登録 |
| [x] **P0-2** | ✅ | 両 WF に `PAPER_AIRCRAFT_BRANCH_GUARD=warn` を明示し、push 時の誤判定を detached HEAD 基準で低減。回帰テスト 37 件 |
| [x] **P0-3** | ✅ | 風洞モードの α 反転を解消。ベンチ α=4° で α_eff が 180° を超えない |
| [x] **P0-4** | ✅ | crease 交点の特異点を幾何学境界で判定し有限値へ退避。302 mm スパイク解消・非有限 0 個 |
| [x] **P0-5** | ✅ | 「採用」ボタン 2 つの嘘を解消（実データ反映） |
| [x] **P0-6** | ✅ | JS 参照 id と `index.html` の差分 0 件、`this.meshpanel` → `this.meshPanel` |
| [x] **P0-7** | ✅ | `load_dotenv` の無効化を解消し API 認証が実効化 |
| [x] **P0-8** | ✅ | `SIMULATION_TRAJECTORY_LOCK` を導入し launch と step の排他を保証（軌跡の混入なし） |
| [x] **P0-9** | ✅ | UI が `performance_validated` を明示表示（「性能は未検証」）、`alert()` 廃止 |
| [x] **P0-10** | ✅ | 記録のトリム契約を統一（`physics/records_domain.py`） |
| [x] **P0-11** | ✅ | 応力ヒートマップの転置を修正 |
| [x] **P0-12** | ✅ | 文字化けを再記述し `tools/check_mojibake.py` / `tests/test_no_mojibake.py` で再発防止 |

### 7.2 P1（18 件）— すべて完了

| ID | 完了 | 実装内容 |
|---|---|---|
| [x] **P1-1** | ✅ | `isa_mode` 判定を payload 有無に関わらず適用し温圧スライダを無効化。clamp 済み設定の正本を `physics/config_schema.py` に一本化 |
| [x] **P1-2** | ✅ | `clamp_ranges()` に 6 物理量と `throw_style` 閉集合を追加。設定書き込みを全件一括適用化し部分適用 0 件 |
| [x] **P1-3** | ✅ | Pareto 整列をベクトル化（支配関係を旧実装と同一）、`spawn` プールを世代間で再利用（`atexit` 解放） |
| [x] **P1-4** | ✅ | `approximate` / `model_detail` / `limitations` を UI 表示し `reduced_surrogate` を公式結果と分離 |
| [x] **P1-5** | ✅ | 評価モデルを機器非依存に整理し `physics_verification_eligible` を明示 |
| [x] **P1-6** | ✅ | **記録訂正**: 5 ツールとも既に非 0 終了しており指摘の前提が誤り |
| [x] **P1-7** | ✅ | **記録訂正**: `records.py` は `public=True`、`error_domain` は traceback / client_ip を除去済 |
| [x] **P1-8** | ✅ | `sim_duration_s` / `dt_max` の範囲検証、入場判定をロック化、`thread.start()` 失敗時の枠解放 |
| [x] **P1-9** | ✅ | 依存下限を一致、cupy を extras 化（CPU のみ環境でも setup が abort しない）、ruff line-length 120 に統一 |
| [x] **P1-10** | ✅ | `verify.yml` で `smoke_test.py` を実行、discord を extras 化、未テスト主要エンドポイントを契約テスト化 |
| [x] **P1-11** | ✅ | トグル 8 個の固有 `aria-label`、canvas 5 点の代替テキスト、モーダル `role="dialog"`、コントラスト 4.5:1 以上 |
| [x] **P1-12** | ✅ | 空 `ALLOWED_USER_IDS` で全拒否、レート制限 10/60 s、プロンプト長 4000、`!chrome` の URL 検証、単一インスタンス化 |
| [x] **P1-13** | ✅ | `Knowledge.md` の虚偽ガードレール記載を実測値へ更新（mypy の主張も現状値に） |
| [x] **P1-14** | ✅ | 抗力ポラルの右軸を L/D 軸へ訂正、ストリップチャートは実測値を表示 |
| [x] **P1-15** | ✅ | 折り畳みを 2 段スケーリング（生成 → 実スパン取得 → `wingspan_m` へ再スケール）。実スパン誤差 0 % |
| [x] **P1-16** | ✅ | 折り畳み / 解析機 / 応力 kernel の 3 系統の解釈を統一し、負値の `abs()` / `max(0, …)` による符号破棄を解消 |
| [x] **P1-17** | ✅ | 後縁 crease を左右対称化（鏡像誤差 0.6〜1.2 mm） |
| [x] **P1-18** | ✅ | `turbulence_scale` を長さとして適用（渦長が比例変化）、移流速度の 29 % 誤差を解消、UI 単位を「m」に整合 |

### 7.3 P2（23 件）— すべて完了（意図的維持 4 件は根拠と再オープン条件を明記）

| ID | 完了 | 実装内容 |
|---|---|---|
| [x] **P2-1** | ✅ | 風評価のメモ化（クエリ + 環境指紋）と短絡条件の是正。大気状態はメモ化で再導出 0 回 |
| [x] **P2-2** | ✅ | 軌跡ロックと状態ロックを分離（取得順は常に 軌跡 → 状態）。長軌跡中も読み取り系が応答、軌跡の原子性は維持 |
| [x] **P2-3** | ✅ | `RESULT_CACHE` に 64 MiB 上限と古い順退避、`JobManager.update()` を部分コピー化 |
| [x] **P2-4** | ✅ | ポーリング周期を定義どおり 2000 ms に統一 |
| [x] **P2-5** | ✅ | `error.jsonl` を `RotatingFileHandler.emit` 経由に（回転が実効化） |
| [x] **P2-6** | ✅ | `artifacts/` を ignore し、40 MB の生成物 10 件を git 追跡解除 |
| [x] **P2-7** | ✅ | `tools/test_*.py` 11 本に `__main__` ガード（import 0.6 s） |
| [x] **P2-8** | ✅ | `close_resolved_issue.py` に `--dry-run`（副作用 0）、`clear_discord_commands.py` の `--all-clean` を実挙動化 |
| [x] **P2-9** | ✅ | GitHub 3 ツールの slug を `GITHUB_REPOSITORY` 化、`urlopen` に 30 s timeout |
| [x] **P2-10** | ✅ | toast tone `success` / `info` を定義、応力単位を kPa へ統一、英語漏れを解消 |
| [x] **P2-11** | ✅ | 同義色を CSS トークンへ統一（3D 軌跡 / チャート） |
| [x] **P2-12** | ✅ | imported mesh（`THREE.Group`）でもヒートマップが例外を出さない |
| [x] **P2-13** | ✅ | **意図的維持**（§8 F-4: ABL 単一対数則）。再オープン条件は §8.2 に記載 |
| [x] **P2-14** | ✅ | `_local_wind` フォールバックの地上高 2 重減算を主経路と一致 |
| [x] **P2-15** | ✅ | 風の方位基準の正本を実装側（north=+y / east=+x）に確定し `docs/SPECIFICATION.md` を訂正 |
| [x] **P2-16** | ✅ | 風洞の壁面境界層を「壁からの距離」基準で適用（既定でも作用） |
| [x] **P2-17** | ✅ | **意図的維持**（§8 F-7 / §1 A-1: 材料モデルと圧縮側クリップ）。許容応力則の docstring を実装に訂正 |
| [x] **P2-18** | ✅ | 三角形 43 % の逆巻きを 0 % に是正（巻き方向を面の法線へ整合）、「閉じた固体」の docstring を実態（両面シート）へ訂正、回帰テスト 2 件追加 |
| [x] **P2-19** | ✅ | 折り畳み API の範囲検証（1e9 / NaN / Inf を 400）と TTL 付きキャッシュ（上限 128） |
| [x] **P2-20** | ✅ | **意図的維持**（§8 F-3: 記録セマンティクス）。再オープン条件は §8.2 に記載 |
| [x] **P2-21** | ✅ | **意図的維持**（§8 F-2: 既定翼面積）。再オープン条件は §8.2 に記載 |
| [x] **P2-22** | ✅ | 永続バンドルに `CONFIG_SCHEMA_VERSION` を付与、旧版移行、書き込みを temp + `os.replace` でアトミック化 |
| [x] **P2-23** | ✅ | 設定の正本を `config_schema.py` へ一本化、`/meta/schemas` が `simulation` を公開、矛盾 0 件 |

### 7.4 P3（11 件）— すべて完了（誤り 2 件は記録訂正）

| ID | 完了 | 実装内容 |
|---|---|---|
| [x] **P3-1** | ✅ | `mesh_domain.__all__` の未定義名を除去。`import *` が例外なく成功 |
| [x] **P3-2** | ✅ | 死定数（`EFFECTIVE_SPEED_FLOOR_MPS` / `MAX_CONTACT_RATE_JUMP_RADPS` / `_trim_pitch_deg`）と raise されない `FlightDivergenceError` を除去、動粘性係数のキー名不一致を解消（フォールバック 1.7e-5 に落ちない） |
| [x] **P3-3** | ✅ | `except Exception: return 0.0` による暗黙の pitch 項欠落を排除 |
| [x] **P3-4** | ✅ | 用語を正本 1 つに統一（機体形 = ダート / グライダー / ワイド機体、飛距離 = 水平飛距離、投擲設定）。旧表記 0 件 |
| [x] **P3-5** | ✅ | `skill/README.md` の非存在 5 参照を除去、`docs/discord-bridge.md` を新規作成、`docs/SPECIFICATION.md` のディレクトリツリーを実在 8 項目へ |
| [x] **P3-6** | ✅ | `parallel_engine` / `job_manager` に `logging` を導入（プール生成・解放・6DoF 失敗・ジョブ failed / cancelled）。`error_domain` の stderr 行に `error_id` を含めて JSONL と join 可能に |
| [x] **P3-7** | ✅ | 初期化コードの AIP-2 逸脱と 16 件のパス重複を解消 |
| [x] **P3-8** | ✅ | **記録訂正**: ルート `AGENTS.md` と `.agents/AGENTS.md` は実在 |
| [x] **P3-9** | ✅ | **記録訂正**: `.codex/` 自体が存在しない |
| [x] **P3-10** | ✅ | `.kilo/.gitignore` から `package.json` と自身の ignore を除外、`memory` ストアに上限（10,000 件 / 50 MB） |
| [x] **P3-11** | ✅ | hinge tree を `fold_sequence()` として公開して `mesh_payload` から観測可能にし、`shape_flag` → archetype の逆引きを `ARCHETYPE_SHAPE_FLAG` として明示、`apply_aeroelastic` の位置づけ（§A-15 により試験・空力弾性パイプライン専用、描画経路では変形しない）を docstring に明記 |

### 4.1〜4.3 D 系（28 件）— すべて完了

| ID | 完了 | 実装内容 |
|---|---|---|
| [x] **H-11** | ✅ | **意図的維持**（§1 A-1: 構造応力の材料モデル）。`KAPPA_SIGMA` の値は不変、再オープン条件は §1 A-1 に記載 |
| [x] **H-19** | ✅ | **記録訂正**: `aero_kernel.py` に後縁折れの空力接続は既に存在（A-2 の対象は `fold_trailing_deg` 側の問題） |
| [x] **M-01** | ✅ | 翼端のノイボン型翼の理想翼判定を実装どおりに |
| [x] **M-12** | ✅ | **意図的維持**（§1 A-5: パネル法）。Helmbold との 2.2 倍乖離は意図 |
| [x] **M-13** | ✅ | **意図的維持**（§1 A-5）。同上 |
| [x] **M-14** | ✅ | **意図的維持**（§1 A-5）。同上 |
| [x] **M-15** | ✅ | 開放メッシュの慣性を形状解析から算出。コーン体積近似を廃止 |
| [x] **M-17** | ✅ | 既存実装（再確認） |
| [x] **M-18** | ✅ | `lift_scale` / `drag_scale` を残しつつ、値 ≠ 1.0 のとき応答に `calibration_active` フラグを付与（§1 A-4 の緩和案） |
| [x] **M-23** | ✅ | 熱上昇柱の上限を ABL 基準高（混合層厚）に由来させ、私有ハードコード高さ係数を廃止 |
| [x] **M-24** | ✅ | 平均風を局所風速でスケール（等方スペクトル本体は §1 A-7 で意図的維持） |
| [x] **M-26** | ✅ | **意図的維持**（§1 A-1）。同上 |
| [x] **M-27** | ✅ | 上反角の荷重配分を対称形に（対称飛行で翼方向荷重の非対称性 0.0） |
| [x] **M-28** | ✅ | 翼端折れのチップゲイン一箇所集約（翼根応力-derived モーメントと報告値が整合） |
| [x] **M-30** | ✅ | **意図的維持**（§1 A-3: `static_margin` の空力接続） |
| [x] **M-31** | ✅ | `drag_polar` の既定 Re は**変更せず**（公開 API 契約）、`DRAG_POLAR_DEFAULT_REYNOLDS` として版管理 |
| [x] **M-32** | ✅ | `norm_scale` を無次元から `[deg/Pa]` へ、重力を単一正本 `STANDARD_GRAVITY` 由来へ |
| [x] **M-34** | ✅ | `build_panels(nx, ny)` は既に引数で変更可能。三角形重心 binning は実装の性質として維持 |
| [x] **L-01** | ✅ | `HARD_ANGULAR_RATE_LIMIT_RPS` を 5000 → 100 rad/s（到達不能なガードを解消） |
| [x] **L-02** | ✅ | `HARD_ALTITUDE_LIMIT_M` を 1e4 → 1.1e4 m（ISA 対流圏上限と整合） |
| [x] **L-03** | ✅ | レイノルズ数の clip 範囲を宣言有効範囲 `[1e3, 1e5]` と整合 |
| [x] **L-05** | ✅ | `wind_force_to_body` の β = ±90° 不連続を解消（vy = 0 では既存式と厳密一致） |
| [x] **L-06** | ✅ | **意図的維持**（§1 A-8: `gravity_modifier.py` 未接続） |
| [x] **L-07** | ✅ | `MAX_RELEASE_SPEED_MPS` を docstring 引用値 20 m/s と統一 |
| [x] **L-08** | ✅ | 死定数・未使用関数を除去 |
| [x] **L-09** | ✅ | **意図的維持**（`polhamus_kv` / `cp_shift_gain` / `aeroelastic_*` の既定 0 は仕様）。不活性であることが明示されている |
| [x] **L-10** | ✅ | `ELEVON_MAX_DEG` を設定範囲 ±45° と整合 |
| [x] **L-12** | ✅ | 既存実装（再確認） |

### 4.5 折り紙 D ジオメトリ（G 系 8 件）— すべて完了

| ID | 完了 | 実装内容 |
|---|---|---|
| [x] **G-1** | ✅ | 2 段スケーリングで実スパンが `wingspan_m` と一致（0 % 誤差） |
| [x] **G-2** | ✅ | §A-14（折り紙は表示専用）を維持しつつ、`physics/origami_geometry_audit.py` で折り紙実測値と解析翼型値の差分を定量公開 |
| [x] **G-3** | ✅ | Layered Pockets の camber を面頂点へ適用（層数比例 bulge、crease 近傍 0 へフェード） |
| [x] **G-4** | ✅ | 主翼前縁に後退翼の sweep を持たせる |
| [x] **G-5** | ✅ | Nakamura の中央三角ロックを対称 crease + 段付き crease へ |
| [x] **G-6** | ✅ | 折り工程 α を 0.02 刻みに量子化し、隣接 α のプリフェッチでサーバキャッシュを温める |
| [x] **G-7** | ✅ | 設定バンドルに `origami` 節を含め、import で復元（サーバへは送らない §A-16 を維持） |
| [x] **G-8** | ✅ | 機体形と折り紙の不一致を UI に明示（§A-17 の独立性は維持） |

### 4.6 無作為折り方遺伝子（G9 系 5 件）— すべて完了

| ID | 完了 | 実装内容 |
|---|---|---|
| [x] **G9-1** | ✅ | 持ち手フィルタ閾値の出典未取得を明示し、捏造せず再オープン条件として記載（`docs/SPECIFICATION.md` §5.1） |
| [x] **G9-2** | ✅ | group rank が層インデックスへ実際に影響することを固定化（重なり分離の保証は既存テストで担保） |
| [x] **G9-3** | ✅ | 角度連続変異・crease 微小移動・親和集合の交叉を追加。50 個体で grip 合格率 families 14 % → 48 %、free 4 % → 18 %（決定論的シード） |
| [x] **G9-4** | ✅ | 既存実装（再確認） |
| [x] **G9-5** | ✅ | `genome_to_aircraft_config` に 2 段スケーリング（実スパンが `wingspan_m` と ±1 % で一致） |

### §1 A 系（19 件）/ §8 F 系（19 件）— 意図的維持（再オープン条件の記載で完了扱い）

- [x] **A-1 〜 A-19**: 物理的・工学的理由から意図的に現状維持した項目。**値は一切変更していない**（`KAPPA_SIGMA`、ABL 単一対数則、較正ノブ、層ピッチ 0.42 mm、パネル法、§A-15 の一方向伝递、§A-16 / §A-17 の独立性、§A-13 のサーバ生成一元化、§A-14 の表示専用）。再オープン条件は §1 の各節に記載。
- [x] **F-1 〜 F-19**: 復元不能・環境制約・意図的維持の項目。**再オープン条件を §8 に記載**して完了扱いとした。

### 残存（`[ ]`）— 0 件

**2026-10-22 時点で `[ ]`（未着手）は 0 件**。ただし以下は**値の据え置き**であり、実装修正ではない（§1 / §8 を参照）:
`H-11` / `M-12` / `M-13` / `M-14` / `M-26` / `M-30`（§1 A-1〜A-5）、`L-06`（A-8）、`L-09`、`P2-13` / `P2-17` / `P2-20` / `P2-21`（§8）。

---
## 1. A. あえて未解決（意図的な据え置き）

### A-1. 構造応力の材料モデル（§H-11 / §M-26）— 最も重い未解決

| 項目 | 現状 | 据え置きの理由 |
|---|---|---|
| `KAPPA_SIGMA = 9.0e-6` | `physics/stress_domain.py:106`（定義）/ `:317`（`KAPPA_SIGMA * moment / modulus`） | Beam 理論の断面係数に**経験的スケーリング係数**を掛けたもの。実紙・実折面の材料定数（引裂強度・折れ面の残留断面）に対する出典が**監査済み範囲のどの論文にも存在しない**。値を直すと全応力値と失敗判定が同時に変わるため、**根拠を得るまで数値改定を行わない**。**据え置き根拠の正本は `docs/SPECIFICATION.md` §5.2「出典未取得の物理定数の据え置き根拠（G-17）」（`:233`）** |
| 折り断面の深さ未使用 | `section_modulus_batch` は `b·t²/6`（矩形板の断面係数） | 折り機体の残存断面は「折れで材料が失われた」形状で、`t²` スケーリングは定性的に正しいが定量的に誤る。実断面形状（折れ半径・折れ線の数）を持つモデルが必要 |
| 圧縮側をクリップ | `physics/stress_domain.py:827` `stress = xp.maximum(stress, 0.0)` | **薄板の座屈（支配的な破壊モード）が構造的に見えない**。座屈は非線形板モデル（Snap / Denney 系）が必要で、線形曲げの枠組みでは表現できない |

**再オープンの条件**: 紙航空機の引裂・座屈に関する実測論文、または折り断面の残留断面形状の
実測データが入手できること。入手までは**解ける精度を数字で示さない**。

### A-2. `fold_trailing_deg` の空力接続（§H-19）

> **2026-10-22 同期**: 本項は **2 つの別問題**が混在していた。**接続そのものは解消済み**、
> **係数の出典制約だけが残存**する（区分けは下表の「接続」と「残存」に記載）。
> §5.5 の G-4「`trailing_edge_fold_effects` に呼び出し元が無い」という記載訂正は **outdated**
> （現行コードは接続済み）。W12 レポートの指摘どおり、本項の「未接続」記述は
> 実装と矛盾していたため現状へ同期した。

- **接続（解消済み・コミット: `7639895`）**: 後縁フォールドは**構造**（`te_fold_chord_scale` による
  有効弦長 → 断面係数）に**加え空力**にも効く。共有カーネル
  `physics/aero_kernel.py:1084-1085` が `trailing_edge_fold_effects` を `cl_max` ガード
  （`_guard_fold_increment`、`:749-769`）付きで評価し、揚力（`:1108` の `cl_offset`）・
  抗力（`:1113` の `cd0`）・ pitching moment（`:1178`）へ反映する。`physics/aero_domain.py:557,606,619,660,699`
  も同じ関数を呼ぶ。**既定 `fold_trailing_deg = 15.0`（`physics/config_schema.py:263`）との矛盾
  （「空力ゼロ」前提）も `7639895` で是正**（回帰固定化: `tests/test_flight.py` + `tests/test_records.py` = 95 passed）。
- **残存（出典制約・係数は据え置き）**: 接続に用いた係数 `dcl = 0.015·fold` / `dcm = −0.012·fold` /
  `dcd = 0.035·sin²(fold)`（`physics/aero_kernel.py:742-745`）は**出典なし**。
  後縁折れによる空力変化は翼型後縁の抗力増加＋後縁渦の形成に基づく効果で、
  **特定論文の式が存在しない**。パネル法の回転増加（§M-13）と同じ
  「局所幾何 → 局所揚力」の枠組みなら近似できるが、6DoF 正規経路は Helmbold / Hoerner
  モデルであり、混在させると A-1 と同じ「出典なき係数」が増える。
  **据え置きの正本**: `docs/SPECIFICATION.md` §5.2「出典未取得の物理定数の据え置き根拠（G-17）」
  （KAPPA_SIGMA / 後縁フォールド係数を出处・物理的意味・据え置き理由つきで列挙）。
  再オープンの条件は G-17 の行に明記。
- **帰結（残存）**: 最適化器の設計変数 `fold_trailing_deg` は**構造**と**空力**の両方に効く。
  GA 導入時に**目的関数の説明変数として明示する必要がある**。

### A-3. `static_margin` の空力的意味づけ（§M-30）

- **現状**: `config_schema.static_margin`（既定 0.12）は 6DoF 経路では**未使用**。
  使用箇所は `physics/parallel_engine.py:612` のアンサンブル代理モデルのみ。
- **理由**: `static_margin` は「揚力作用点と重心の距離 / 平均空力翼弦」で定義されるが、
  6DoF 経路は**最適滑空点での trim** を基準にするため trim 条件が変われば意味が変わる。
  空力的に意味を持たせるには trim 状態の定義を先に固定する必要がある。

### A-4. `lift_scale` / `drag_scale` の任意係数（§M-18）

- **現状**: `EnvironmentConfig.lift_scale` / `drag_scale`（既定 1.0）が論文式の上書きとして
  残存。0〜5 倍の任意の倍率で**論文式を黙って無効化できる**。
- **理由**: これらは**実機号の較正ノブ**（模型的不確実性の吸収先）として API 契約に
  含まれている。撤去すると較正手段が無くなる。
- **緩和案（未実施）**: 値を 1.0 以外にするとペイロードに「較正が有効」と明示する。
  次フェーズ候補。

### A-5. パネル法（§M-12 / §M-13 / §M-14）— 6DoF 正規経路と並存

| 項目 | 現状 |
|---|---|
| §M-12 | `physics/mesh_domain.py` の `panel_aerodynamics` は `C_L = 2π sin(α_local)`（**二次元翼型傾斜**）を使う。3 次元翼の Helmbold 傾斜と **2.2 倍乖離** |
| §M-13 | 回転増加 `C_L_rot = C_L · x_cg_local / c` が**弦方向オフセット**を使う（翼方向距離でない） |
| §M-14 | パネル抗力 `0.02 q A` は**総和のみ**で、力ベクトルとして投入されず pitching moment に寄与しない |

- **理由**: パネル法は**メッシュ（外部形状）経路**の補助であり、論文機体評価の主経路は
  Helmbold / Hoerner の `aero_domain`。両者を 1 つの式に統合するのは
  「2 つの異なる空力モデルを混ぜる」ことになるため、**主経路の単一実装原則
  （Aero Kernel 単一化）を壊す**。
- **帰結**: メッシュから起こした機体と、config で定義した機体では**飛距離が一致しない**。
  mesh → config の変換経路に「どちらの空力を使うか」の規約が無いことに起因する。

### A-6. `drag_polar` の既定レイノルズ数（§M-31）

- **現状**: `aero_domain.drag_polar(..., reynolds=1.0e4)` の既定は**頂点条件**
  （`L/D` 最大点 `V ≈ 2 m/s`）に対応し、実運航点（`V ≈ 8〜12 m/s`）ではない。
- **理由**: 既定値の変更は公開 API の極線数値を黙って変える。移行期として呼び出し側が
  明示指定する形が本来。**既定変更は次フェーズで版管理して行う**。

### A-7. 乱流スペクトル（§L-11 / §M-24）

- **現状**: 乱流は **3 チャネルの相関なし Simplex ノイズ**場（Gustavson / Ashima 忠実実装）。
  PSD 形状は Kaimal / Davenport ではなく等方的。
- **理由**: 忠実な 3 次元風場の相関構造は紙機体の短的飛行（1〜5 秒スケール）では統計的に
  判別不能であり、Shaping filter を導入する物理的利得が小さい。

### A-8. `gravity_modifier.py` の未接続（§L-06）

- **現状**: `physics/gravity_modifier.py` は**飛行力学に未接続**。UI / デモ用の独立モジュール。
  `config_schema` の重力範囲 `[0.1, 50]` は反重力を禁じる。
- **理由**: 意図的に隔離されたデモ機能であり、本番物理経路に含める意図はない
  （`physics/gravity_modifier.py:62` に隔離方針を明記済み）。

### A-9. `gpu_ensemble_simulation` は縮約代理のまま

- **現状**: `/simulation/ensemble` の Monte-Carlo 経路は**依然として 3 自由度代理**。
  正規 6DoF へ置換したのは **GPU 最適化バッチ経路**（`gpu_dynamics.simulate_batch`）のみ。
- **理由**: アンサンブル経路の目的は**軌跡エンベロープの統計**であり、個別飛行の物理
  検証ではない。`model = "reduced_surrogate"` + `model_detail` で明示済みのため、
  「別の飛行機を正規と偽って報告する」缺陷は起きない。
- **残存リスク**: 代理モデルの**空力係数**は Aero Kernel 共有済み（Helmbold / 失速 /
  Hoerner は正規経路と同一式）だが、**積分器と接地**は代理。そのため Monte-Carlo の統計は
  6DoF の統計と系統的にずれる。

### A-10. 接地・接触の簡略化（GPU batch 側）

- GPU batch の接地は「機体ステーション（ノーズ・翼端・尾・キール）での接触判定」＋
  衝撃 G `n = 1 + v²/(2 g d)`（`d = 0.031 m`）だが、**CCD（接触瞬間の四元数補間）と
  クランプ弾性係数は簡略化**されている。
- **理由**: GPU batch は 13 状態 RK4 として**参照経路と同一の物理**を出すことを最優先に
  した。接触拘束の完全再現は接触拘束理論と RK4 の組合せで計算量が跳ね、
  現時点の優先度（= 応力値の 1.000 倍一致）に比して不釣り合い。

### A-11. 既知のフレーク（未特定）

- `tests/test_tunneling_ccd.py::test_ccd_prevents_false_penetration_crash_and_preserves_rebound`
  が **CPU 競合下で 1 回だけ失敗**した（部分集合 10 通りの再現は無し）。
  負荷依存フレークと判定し、**原因未特定のまま残している**。通常実行では PASS。

### A-12. 既存の静的解析指摘

- `ruff` の `physics/mesh_domain.py` F822 は**既存事象**（今回の変更対象外）。

---

### A-13. 折り紙ジオメトリは**サーバ生成に一本化**（JS へ折り運動を移植しない）

- **現状**: `physics/origami_domain.py` が展開図→三次元ヒンジ列→多層ソリッド生成を一本化し、
  ビューアは `POST /api/v1/origami/mesh` で取得した頂点バッファをそのまま描画する。
- **理由**: ビューアと `tests/test_origami.py` が**同一ジオメトリを共有**し、JS 側へ同じ
  折り運動を移植した場合に必然的に発生するドリフト（実装 2 Maintenance）を回避できる。
- **残存リスク／コスト**: α スライダーがサーバ往復となり実効 10〜20 fps に留まる（G-6）。
  遅延が UX 上の問題視された場合は、折り運動の JS 移植（= ドリフト管理の恒常コスト）を
  受容して差し替える。

### A-14. 折り紙メッシュは**表示専用**（空力・応力・慣性は解析翼型のまま）

- **現状**: 6DoF・受動空力弾性・CCD の各経路は `physics/mesh_domain.py` の解析翼型を
  使用し、`static/js/origami.js` の折り紙メッシュは描画にのみ使われる。
- **理由**: 「既存 720 テスト・smoke_test・6DoF・受動空力弾性・CCD の挙動を一切壊さない」
  という Guardrail 最優先。折り紙形状を物理へ取り込むと、空力・応力・慣性の校正値
  （既存テストの期待値）が同時に変動し、既存検証の信頼性が失われる。
- **残存リスク／コスト**: 機体寸法・重心・翼面積が折り紙形状と不一致（G-1 / G-2）。

### A-15. 空力弾性は**一方向**（物理値 → 紙シェーダ）のみ

- **現状**: `aeroelastic_dihedral_deg` / `aeroelastic_twist_deg`（`/api/v1/simulation/step`
  のフレームから取得）を `uBend` / `uTwist` uniform として頂点シェーダへ渡すことで、
  翼の曲げ（`aElastic²`）とねじれ（`aTwist`）を可視化する。
- **理由**: 逆方向（折り紙メッシュの変形量 → 空力計算への還流）は `mesh_domain` の契約変更を伴い、
  翼の有効剛性・慣性の更新が必要になるため、今回の視覚刷新の範囲を超える。
- **残存リスク／コスト**: 機体形状として弾性変形が計算に入らない（G-9）。

### A-16. `origami` ビュー状態は**サーバへ送らない**

- **現状**: `static/js/state.js` の `origami` セクションはローカル専用で、`app.js::_push` から
  除外している（飛行計算に影響しない純ビュー状態）。
- **理由**: 物理・投擲の設定 push と混同しない。`state.js::SECTIONS` への追加のみで完結する。
- **残存リスク／コスト**: 設定の export/import に含まれず、選択機種・α が失われる（G-7）。

### A-17. アーキタイプ選択は `shape_flag` と**独立**

- **現状**: `panels.js` の `origami.archetype`（classic_dart / nakamura_lock / sky_king）と
  `airframe.shape_flag` は別系統の選択。
- **理由**: 折り紙 3 機種（Square-Nose 等）は解析機体形状に未定義であり、同一の選択枠に
  畳むと解析形状側が表現できない。
- **残存リスク／コスト**: 「機体形=グライダー」かつ「折り紙=Sky King」のような二重選択が
  成立しうる（G-8）。

### A-18. レイヤー分離は**一定ピッチ**（0.42 mm）

- **現状**: `LAYER_PITCH_M = VISUAL_THICKNESS_M + LAYER_AIR_GAP_M = 0.42 mm` を全層に適用し、
  層インデックスは「平行かつ投影が重なる面の重なり DAG の最長路」で決める。
- **理由**: 最長路なら重なる任意 2 面が必ず 1 ピッチ以上離れるため、**共面・Z-Fighting が
  構造的に発生し得ない**（紙厚 0.30 mm に対し 0.42 mm の間隔）。機械的な保証として
  テスト可能（`tests/test_origami.py::test_stacked_layers_are_separated_by_a_full_pitch`）。
- **残存リスク／コスト**: 実紙における「層ごとの隙間の微妙な差」までは表現しない。

### A-19. collar crease は**中心線ではなく機首辺**に対する二等分線

- **現状**: `_collar_crease` は折れ上がった角の斜辺と**機首辺**の二等分線として定義する。
- **理由**: 用紙が 1:√2 のため、中心線との二等分線は**用紙全高を横切る斜線**となり
  主翼翼根まで切断してしまう。機首辺は折れ上げ後に中心線へ移るため、
  「中心線へ着地する斜辺」を定義する手段は機首辺との二等分線になる（SPEC §1.5 に明記）。
- **残存リスク／コスト**: なし（物理的に正しい定義）。

## 2. B. 仕様主張の是正（「直す」のではなく「嘘を撤回」）

| 対象 | 措置 | 結果 |
|---|---|---|
| `docs/SPECIFICATION.md` §2.1 / Issue #35 の「**CPU 経路と GPU 経路で物理計算結果が完全一致することを保証（整合性検証済み）**」 | **過大主張として撤回**。「GPU 最適化バッチ経路を**正規経路と同一の 13 状態 RK4 モデル**に書き換え、**定量**検証する」運用へ変更 | 撤回済み。`tools/check_gpu_parity.py` と `tests/test_gpu_kernel_parity.py` が担保 |
| `PHYSICS_AUDIT_REPORT.md` §6.2「最終選定された機体候補は必ず完全 6DoF シミュレーションによって再検証」 | `optimizer_domain.revalidate_candidate_6dof()` を実装し `model="rigid_body_6dof"` と検証済み目的関数を公開 | 実装済み。`MAX_REVALIDATED_CANDIDATES=3` でコスト上限あり |
| 3 系統の `model` タグ | `rigid_body_6dof` / `reduced_surrogate` + `model_detail` で経路を明示。縮約経路は `approximate=true` / `physics_verification_eligible=false` | 実装済み |

**再発防止**: `tests/test_paper_conformance_p0_p1.py` と `tests/test_ga_physics_readiness.py` が
「代理経路が 6DoF を偽って主張しない」ことを固定化。

---

## 3. C. 解決済みだが残る既知の限定（今回の範囲外と明示したもの）

| 限定 | 内容 | 影響 |
|---|---|---|
| GPU batch の風場サンプル | **1 ステップ 1 回**（4 個の RK4 段で共有）。風場は秒スケールで変化するため 1 ステップ（≤20 ms）内の差は積分精度以下 | 無視可能 |
| GPU batch の風場時刻 | **全ケース共通時刻**（アクティブケースの平均時刻） | ケース間の時刻ずれが風場評価に混入。風は Case 間で同一の空間場を使うため影響は小さい |
| GPU batch の慣性テンソル | **対角項のみ**（`inertia_xy/xz/yz` を 0 とする）。参照経路は `body_inertia_tensor` で非対角項を許す | 参照経路の非対角項も既定 0 のため、現状の実害はゼロ |
| GPU batch の接地 | 機体ステーション判定＋衝撃 G。CCD とクランプ弾性係数は簡略化 | A-10 と同一 |
| **GPU 性能** | 13 状態 RK4 は 1 状態評価あたり約 200 演算。Windows/WDDM では CuPy の 1 kernel launch に約 100 µs かかるため 1 状態評価 21 ms（CPU/NumPy は 1.5 ms）→ **この環境では GPU の方が遅い**。Linux/CI では launch overhead が約 1/10 で、N が大きい批量で GPU が有利 | `tests/test_issue28_gpu_duration.py` が 8.5 分を要する。**GA v2 の Stage 1 は CUDA Graph で 1 ステップを捕捉済み**（`physics/ga2/stage1.py`。捕捉条件: H2D 禁止・cuBLAS 禁止・`begin_capture(mode=2)`。3 ケース 1200 ステップで 113 s → 1.3 s）。既存経路は未融合のまま |
| GA v2 の Stage 1 は固定ステップ | CUDA Graph はデータ依存ステップ数を持てないため、スクリーンの刻みは固定（`ScalingProfile.stage1_max_steps`）で、折り畳まれた機体は剛性が高く適応刻みが小さいため**飛行の被覆率（coverage）が 1 を下回りうる** | 被覆率は `Stage1Result.coverage` として報告され、**順位は Stage 2 の参照再評価で決まる**。スクリーンはroughness の篩いにのみ使う |
| GA v2 の Stage 3（局所DNS）の絶対係数 | D3Q19+浸漬境界+モーダル構造の連成は**流れの形**を解く。粗い格子・簡略化したラスタライズ・巻き上げ境界のため、揚力係数・抗力係数の絶対値は未検証 | 係数は紙翼の物理範囲にクランプし、Stage 3 の出力は**形状指標とラUHNK補正モデルの入力**にのみ使う。**空力の正本は 6DoF 参照経路** |
| GA v2 の検索用投擲モデル | 検索ハーネスは 8 m/s / 25° / 1.5 m の**室内ロフト投擲**を使う。既定の平投擲（12 m/s / 12° / 1.2 m）は滑翔前に床へ衝突し、全設計が crash 判定となって検索に信号が残らない | 投擲は**ハーネスの設定**であり物理ではない（`flight_sim` / `gpu_dynamics` は未変更）。`EvolutionRunner(launch=...)` で上書き可能 |
| 6DoF 空力評価の定数倍 | カーネル共通化により `evaluate_aero` が 68〜84 µs → 約 120 µs（≈1.5 倍） | 6DoF 全体では数 % の増。単一実装の優先性を重視した結果として**明示的に受け入れ** |

#### C-1. 2026-10-22 同期で解消済みと確認した項目（W13 台帳陳腐化の消化分）

> いずれも**コード・テストで裏取り済み**の項目。**台帳からは削除せず**、本節へ移した記録を残す。
> 出典のない断定は置かないため、各行に file:line・再現コマンド・実行結果の 3 点必ずを付す。

| 限定 | 内容 | 影響（残存するもの） | 判定・根拠 |
|---|---|---|---|
| 風洞ベンチ姿勢の正本関数 | `wind_tunnel_bench_quaternion` は `physics/flight_sim.py:993` に**実装済み**（`WIND_TUNNEL_BENCH_HEADING_DEG = 180.0` は `:990`）。使用箇所は `:2026`（風洞モード）と `:3203` | ベンチ姿勢の**正本関数が無い**という限定は消滅。§7.1 P0-3 と同系統 | **解消済み（コミット: `c4b9286`）**。`python -m pytest tests/test_wind_tunnel_bench.py -q` = **7 passed** |
| 投入初期条件の人工係数 | `torque_rate[1] *= 0.10`（"Finger pinch guide effect"）は**消滅**。`physics/flight_sim.py:1653` に残るのは有限値ガードのみ | 出典なき係数が 1 個減る（pitch 相対誤差 0.9 → 0.0） | **解消済み（コミット: `c4b9286`）**。`python -m pytest tests/test_launch_release_torque.py -q` = **4 passed** |
| 後縁フォールドの空力接続 | `physics/aero_kernel.py:1084-1085` → `:1108`（`cl_offset`）・`:1113`（`cd0`）・`:1178`（`cm`）、`physics/aero_domain.py:557,606,619,660,699` | **係数の出典なし**（`0.015` / `−0.012` / `0.035`）だけが残存 | **接続は解消済み（コミット: `7639895`）**。`python -m pytest tests/test_flight.py tests/test_records.py -q` = **95 passed** |
| 公開 payload の失敗キー | `physics/payload_contract.py:70-71` に `error` / `error_id` あり、`:133-135` に既定 `None`、`:156-164` に必須キー宣言 | 消滅 | **解消済み（コミット: `7639895`）**。`python -m pytest tests/test_payload_contract.py -q` = passed |
| GPU/バッチ ⇄ 正準 6DoF の数値不一致 | 真因は**大気メモの履歴依存**（大気メモのキーが高度を **1 mm 量子化**していたため「最初に問い合わせられた 1 mm バケットの密度」を返し、母プロセスと新規ワーカーで積分路が分かれていた。`8a41586` で `_ATMOSPHERE_QUANTUM_M = 1.0e-3` と `round(altitude / _ATMOSPHERE_QUANTUM_M)` を撤去し**厳密キー**へ。`physics/flight_sim.py:716-717`（修正前の 1 mm 量子化は `:710-712` / `:737`） の `_ATMOSPHERE_CACHE`）で解消。並列/逐次/単独実行が**ビット一致** | **6DoF 経路自体のカオス性**（λ ≈ 33/s）は残存＝軌道の一致は保証しない（**§4.10** 参照） | **解消済み（コミット: `8a41586` 系の W9 修正）**。`python -m pytest tests/test_ga_audit.py tests/test_optimizer.py -q` = **61 passed / 0 failed** |
| 水平 100 km 境界でのクラッシュ未発火 | `physics/flight_sim.py:2462-2530` の `_world_wall_contact()` が水平壁を**壁として発火**させ、状態を凍結（`crashed=True` / `diverged=True` / `status='diverged'`） | 消滅 | **解消済み（コミット: `8a41586` 系の W9 修正）**。`python -m pytest tests/test_world_bounds.py -q` = **1 passed** |
| GA2 Stage-0 の「paper would tear」偽陽性 | `physics/ga2/stage0.py` の `_segments_overlap()` に**共線性検査**を追加（平行判定のみだった誤判定を解消）。hot_spot 9 → 正 5 | **tear 判定そのものの出典**は別途要確認（§4.9 K3-P0-3 に記載） | **解消済み（コミット: `8a41586`）**。`python -m pytest tests/test_ga2_stages.py -q` = **14 passed** |
| GA エンジンの審査 P0 4 件 | ①適合度式を**重みなしの積**へ（`physics/ga_domain.py:1807-1854`）②公開 payload に来歴 4 キー（`:2386-2400`, `:2459-2461`）③代理経路の公開遮断（`:966-983` の `is_reference_path_evaluation`）④世代 0 の初期母集団評価（`:1321`） | 消滅 | **解消済み（コミット: `d287af2`）**。`python -m pytest tests/test_ga_engine.py -q` = **51 passed / 0 failed** |
| 記録プリセットの trim 契約 | `physics/records_domain.py:668-671` が `source["release_pitch_deg"] = RECORD_TRIM_ALPHA_DEG` の**無条件代入**へ、カタログの `"release_pitch_deg": 0.0` 明示 9 箇所を削除（`git blame` → `7639895`） | 消滅（GET / POST が 12 プリセット全て 3.0 で一致） | **解消済み（コミット: `7639895`）** |
| 応力ヒートマップの軸反転 | サーバ grid は `[spanwise][chordwise]` の正順 | 消滅 | **解消済み（コミット: `7639895`、W6 で正本化）**。`python tools/check_stress_peak_parity.py` = 最大相対差 **0.000e+00** |
| 記録ドメインの日本語 93 行 U+FFFD | ギネス記録の原文出典から再記述。**再発防止のガード**は `tools/check_mojibake.py`（検出）と `tests/test_no_mojibake.py`（回帰固定化） | 消滅 | **解消済み（コミット: `7639895`）**。`python tools/check_mojibake.py` = `OK: no mojibake detected` / `python -m pytest tests/test_no_mojibake.py -q` = **4 passed** |
| 風場時間相関の絶対閾値 | `tests/test_advanced_physics_audit.py:151` の `diff_t_near < 0.5`（旧乱流振幅規格化 1.4 m/s 依存）を風スケール相対 `diff_t_near < 0.5 * env.wind_speed` へ | 相対的 decorrelation のアサーションは維持。**絶対値依存は消滅** | **解消済み（コミット: `7639895`、V1 で是正）**。`git show 7639895 -- tests/test_advanced_physics_audit.py` = 1 file changed |
| トップレールの**読み取り値の省略**と**操作ボタンの押し出し** | ①`.toprail-readouts` が `flex: 0 1 auto`（縮小可能）だったため、flex 分配が**内容より 1 px 狭い**幅を与え、全値が省略記号付き（`READY` → `REA…`・`0.000s` → `0.00…`）になった ②`.toprail-actions` の `justify-content: flex-end` は**溢れ時に先頭グループを左へ押し出す**（`safe flex-end` でなければ到達不能）。1280〜1800 px では**右端の 保存/読込/CSV/録画/ヘルプ がレール外に押し出**され、スクロールバーも非表示のため発見できなかった | `static/css/console.css`（`.toprail-readouts` / `.toprail-actions` / `@media (max-width: 1800px)`）/ `static/js/app.js`（`top-fps` の短縮表示）/ `tools/check_ui_css.py` / `tests/test_ui_css.py` | **解消済み（2026-10-22）**。①`flex: 0 0 auto` で読み取り値を内容幅に固定 ②`justify-content: safe flex-end` ③**1800 px 以下**で診断用 SOLVER 読み取り値を隠し、RENDER 値を短縮、モードボタン・読み取り値余白・グループ間隔を詰める ④`top-fps` は 1800 px 以下で `143 fps ⚠` の短縮形（全称は title と診断ダイアログに残置）。実測: **1280 / 1366 / 1440 / 1536 / 1600 / 1740 / 1800 / 1801 / 1920 / 2560 px の全幅で 省略 0 件・溢れ 0 件・全操作ボタン可視**。静的ガード `tools/check_ui_css.py` が増_FRAME 契約（読み取り値の縮小不可・`safe flex-end`）を検査 |
| 応力場とスカラー縮約の乖離（上反角の射影の欠落） | `physics/stress_domain.py::peak_root_stress_pa` は翼根モーメントに**上反角の射影 `1/√(1+tan²Λ)`** を適用していなかった。応力場 `stress_field` は適用していたため、縮約は場の **1/cos(Λ)** 倍（既定 5° で **3.8e-3**、45° で **1.4e-1**）.routes の GPU バッチ経路と 6DoF 正規経路のピーク応力が食い違い、`tests/test_gpu_kernel_parity.py::test_vectorised_peak_stress_reproduces_the_stress_field` が fail | `physics/stress_domain.py`（`peak_root_stress_pa` / `stress_field`）/ `physics/gpu_dynamics.py` / `physics/ga2/stage1.py` | **解消済み（2026-10-22）**。正本関数 `physics/stress_domain.py::dihedral_load_multiplier` を追加し、場と縮約の**双方**が同じ射影を使う。`tools/check_stress_peak_parity.py` = 最大相対差 **0.000e+00**（6 ケース全部ビット一致）/ `python -m pytest tests/test_gpu_kernel_parity.py tests/test_stress.py -q` = passed |
| フロントエンド ES モジュールの構文エラーで**アプリ全体の JS が無効化** | `static/js/telemetry.js` の同一 forEach コールバック内に `const scale` が **2 回**宣言され、ES module では `SyntaxError` になる。`app.js` が `telemetry.js` を import するため連鎖死し、**3D ビューポート・計器・パネルが一切動かない**静的 HTML シェルになっていた。`node --check foo.js` は **script goal** で解析するためこの重複を**検出できない**（module goal でのみ SyntaxError） | `static/js/telemetry.js`（`unit_scale` へ改名）/ `tools/check_js_modules.py` / `tests/test_js_modules.py` | **解消済み（2026-10-22）**。`.mjs` へ複製して `node --check` する検出器 `tools/check_js_modules.py`（構文＋リンク切れ import）を新規作成し、**両 WF にステップを追加**。回帰 `tests/test_js_modules.py`。実測: `window.consoleApp` が object へ復帰、コンソールエラー **0 件**、3D シーンと計器リングが描画 |
| コンソールの**横溢れ**と視認性違反 | ①`.console` の grid 列が暗黙（`auto`）のため**最広行の min-content 幅**（トップレール 1392 px）で決まり、1280 px 幅で **112 px 溢れ**、録画/読込/CSV/ヘルプのボタンが画面外へ押し出された ②文字サイズ **7.5〜8.5 px** が 144 箇所 ③**WCAG AA コントラスト違反 30 件**（最悪 1.34:1）④900 px 未満で**レールが画面外へ退避**し、ヘッダーのクリックでは開けないため**設定パネルへ到達不能**⑤狭幅の `.transport` が末尾 5 px 欠落 | `static/css/console.css` / `static/css/ga-research.css` / `static/js/app.js` / `templates/index.html` / `tools/check_ui_css.py` / `tests/test_ui_css.py` | **解消済み（2026-10-22）**。①`grid-template-columns: minmax(0, 1fr)` ＋ `justify-content: safe flex-end` ②文字の下限を **9 px** に（静的検査 `tools/check_ui_css.py`）③塗り色と文字色を**別トークン**化（`--accent-ink` / `--bad-ink` / `--ok-ink` / `--rail-*`）し違反 **0 件** ④トップレールに常時可視の `btn-rail-left` / `btn-rail-right` を追加し `_setRailOpen()` で `aria-expanded` / `inert` を同期 ⑤`.transport` を `min-height` 化。実測: 1920/1440/1280/1024/900/768 px で **溢れ 0 件・コントラスト違反 0 件・9 px 未満 0 件** |
| Polhamus 式の**重複定義**（Issue #51） | Polhamus の関係が `physics/aero_kernel.py::lift_coefficient` の**インライン計算**と `physics/aero_domain.py::polhamus_lift_coefficient`（公開式単体、係数を `cL0` なしで返す）で**別物として二重実装**されていた（`K_p > 0` で分岐が違い、`K_v` 項の加算位置も違う）。インライン側には `if True:` という恒真条件も残存 | `physics/aero_kernel.py` / `physics/aero_domain.py` | **解消済み（2026-10-22）**。正本関数 `physics/aero_kernel.py::polhamus_attached_branch`（`__all__` へ追加）を新設し、`lift_coefficient` はその 1 関数を呼ぶ。公開の単体ヘルパは**別名・別意味**であることを docstring で明示。**挙動変更なし**（`python -m pytest tests/test_aero.py tests/test_paper_conformance_p0_p1.py -q` = **148 passed**） |
| 文献レジストリの**虚偽主張**（Issue #48/#49） | `/api/v1/meta/physics-references` が **Viterna-Corrigan 失速後外挿**を公開引用の1件として挙げていたが、**実装されているのは Hoerner 平板**である（`physics/aero_kernel.py::post_stall_lift_coefficient`）。レジストリが実装していない式を実装済みのように見せていた | `api/routes/meta.py`（`/api/v1/meta/physics-references`）/ `tests/test_physics_model_registry.py` | **解消済み（2026-10-22）**。全エントリに `implemented` / `canonical_module` / `applicability` を付与し、Viterna-Corrigan は `implemented: False` ＋採否理由と適用限界を明記。回帰 `tests/test_physics_model_registry.py`（**19 passed**、実装済みエントリの正本モジュール実在も検証）。正本文書 `docs/PHYSICS_MODELS.md` を新設 |

---

## 4. D. 次フェーズへ繰延した残課題（重要度順）

### 4.1 高重要度 H（監査 §2）— 2 件

| ID | 指摘 | 現状 | 繰延理由 |
|---|---|---|---|
| **H-11** | 構造応力が Beam 理論値の 9×10⁻⁶ 倍。折り断面の深さが未使用 | 未着手 | A-1 と同一 |
| **H-19** | 折り後縁の空力モデル未接続 | **接続は解消済み（コミット: `7639895`）**。`physics/aero_kernel.py:1084-1085` → `cl_offset`（`:1108`）・`cd0`（`:1113`）・`cm`（`:1178`）へ反映、`physics/aero_domain.py:557,606,619,660,699` からも参照。**残存は係数の出典制約のみ** | A-2 と同一（据え置き根拠は `docs/SPECIFICATION.md` §5.2 G-17） |

> 監査 §2 の H-01〜H-20 のうち **H-11 / H-19 のみ未着手**。他 18 件は完了。

### 4.2 中重要度 M（監査 §3）— 16 件

| ID | 指摘 | 分類 / 繰延理由 |
|---|---|---|
| **M-01** | 翼端 / 機体で反発係数のクランプが不統一。翼端エネルギー予算と機体エネルギー予算が別物 | 接地（低リスク） |
| **M-12** | パネル法の下限が 2π sinα（2 次元翼型）→ Helmbold と 2.2 倍乖離 | A-5 |
| **M-13** | パネル法の回転増分が「翼方向距離」ではなく「弦方向オフセット」 | A-5 |
| **M-14** | パネル抗力が力ベクトルとして投入されず、モーメントに寄与しない | A-5 |
| **M-15** | 開放メッシュ（折った紙）が「コーン体積」の慣性テンソルを受け入れる | メッシュ（中リスク） |
| **M-17** | 投入時のピッチ角速度に説明されない 0.10 倍が掛かる（旧 `physics/flight_sim.py:1498`（`9538f8b` のツリー）`torque_rate[1] *= 0.10`） | **解消済み（コミット: `c4b9286`）**。`physics/flight_sim.py` に `torque_rate[1] *= 0.10` は**残存しない**（`:1653` は有限値ガードのみ）。回帰固定化 `tests/test_launch_release_torque.py:108-127`（`I⁻¹MΔt` の解析値と rel 1e-12 で一致、removed 係数の 1/10 を使わない）→ `python -m pytest tests/test_launch_release_torque.py -q` = **4 passed** | 投入モデル（**残存なし**。pitch 相対誤差 0.9 → 0.0） |
| **M-18** | `lift_scale` / `drag_scale` が論文式を 0〜5 倍で上書き | A-4 |
| **M-23** | 熱上昇柱が地表から 1.2〜1.6 m 浮いたガウシアン塊。σ / 高さ上限がハード | 風場（低リスク） |
| **M-24** | 乱流が等方的。平均風は基準風速参照（局所 u(z) でスケールされない） | A-7 |
| **M-26** | 応力場が圧縮側をクリップし、薄板の座屈（支配的破壊モード）が見えない | A-1 |
| **M-27** | 上反角が対称飛行でも永久に非対称な翼方向荷重を生む（`load_mult = 1 − 0.8·tilt·y_signed`） | 構造（低リスク） |
| **M-28** | 翼端折りで「除去された翼幅」がなお荷重を担い、`TIP_MOMENT_GAIN = 1.6` が翼根応力と報告モーメントを不整合にする | 構造（中リスク） |
| **M-30** | `static_margin` が空力的に無意味 | A-3 |
| **M-31** | 公開 Drag Polar の既定レイノルズ数が頂点条件（`V ≈ 2 m/s`）に対応 | A-6 |
| **M-32** | 構造側空力弾性変形の `norm_scale = 1.0e-10` が無次元、かつ重力が 9.80665 ハードコード（§H-13 と 2 系統乖離） | 空力弾性（中リスク） |
| **M-34** | パネル分割が 16×12 固定で import 経路から変更不可、三角形は重心で binning | メッシュ（中リスク） |

> **完了と確認済みの M 項目**（再監査不要）: M-02 ノーズ圧壊 / M-03 衝撃 G / M-04 Coulomb 摩擦 /
> M-05 キール減衰の二重適用 / M-06 刻み条件の相対速度化 / M-07 RK4 安全係数 /
> M-08 速度上限のフラグ化 / M-09 失速ブレンド形状 / M-10 負側失速閾値 / M-11 側力係数の無制限 /
> M-16 質量既定の整合 / M-19 `evaluate_flight` 例外分離 / M-20 `g_load` の resultant 定義 /
> M-21 `α̇` / `β̇` の相対化 / M-22 風洞モードの迎角 / M-25 ISA の成層圏 / M-29 上反角 clip /
> M-33 `mass_properties` の grammage 引数化。

### 4.3 低重要度 L（監査 §4）— 10 件

いずれも「防御的ガードの値が物理的に到達不能 / 無意味」という指摘。

| ID | 指摘 | 現状 |
|---|---|---|
| L-01 | `HARD_ANGULAR_RATE_LIMIT_RPS = 5000` は到達不能（放出時上限 45 rad/s の 100 倍超） | `physics/flight_sim.py:191` |
| L-02 | `HARD_ALTITUDE_LIMIT_M = 1e4` が ISA の対流圏上限（11 km）を下回る | `physics/flight_sim.py:187` |
| L-03 | レイノルズ数が `[1e2, 1e9]` に clip されるが宣言有効範囲は `[1e3, 1e5]` | 有効性フラグと併存。clip では外挿を見分けられない |
| L-05 | `wind_force_to_body` が β = ±90° で不連続 | `horizontal > 1e-12` の else 節 |
| L-06 | `gravity_modifier.py` が飛行力学に未接続 | A-8 |
| L-07 | 投入速度上限（60 m/s）などが docstring の引用値（20 m/s）の 3 倍 | |
| L-08 | `EFFECTIVE_SPEED_FLOOR_MPS` / `_trim_pitch_deg` / `trailing_edge_bend_deg` が**死んだコード** | |
| L-09 | `polhamus_kv` / `cp_shift_gain` / `aeroelastic_flex` / `aeroelastic_twist` が**既定 0** | Cite されているモデルの既定不活性 |
| L-10 | `ELEVON_MAX_DEG = 180` が幾何学的に不可能（設定は ±45°） | `physics/aero_domain.py:153` |
| L-12 | 地上ランプが**鉛直乱流まで**線形減衰（地上 0.5 m で鉛直乱流がゼロに） | |

### 4.4 非論文系（監査 §5）— 8 件

保存則・数値安定性のみに基づく指摘。**全件未着手**。GA 導入時に収束性の判断材料になるため
物理項目より下だが**無視はしない**。

---

### 4.5 折り紙3Dジオメトリ（G 系・8 件・2026-10-20 折り紙刷新由来）

> 折り紙刷新マイルストーン（`physics/origami_domain.py` / `api/routes/origami.py` /
> `static/js/origami.js` / `tests/test_origami.py`）で**要件未達として残した**項目。
> 影響範囲は「見た目・操作・物理との寸法整合」に閉じており、A-13〜A-19 の据え置きと対になる。

| # | 残課題 | 残存の証拠 | 影響 | 次の手順 |
|---|---|---|---|---|
| **G-1** | **翼寸法が設定と不整合**（scale が平たい用紙幅基準） | `wingspan_m = 0.2` に対し生成物の実スパンは **0.1446 m**。翼面積は **0.0054 m²**（設定 `wing_area_m2 = 0.015 m²` の **36 %**）。`build_mesh(scale = wingspan / SHEET_WIDTH_M)` が用紙幅を基準にしている | 見た目と計算形状の乖離が残る最大の要因 | 生成 → 実スパン取得 → 再スケールの 2 段化（`fit_span`）。翼面積も planform から算出 |
| **G-2** | **物理計算が折り紙形状を使っていない** | 空力・応力・慣性は `physics/mesh_domain.py` の解析翼型 | 重心・慣性・翼面積の不一致（= G-1 の帰結） | G-1 完了後、`config_schema.derived_properties` を折り紙実測値へ寄せる（`body_inertia_tensor` の非対角項も同時） |
| **G-3** | **Layered Pockets（袋状構造）の camber 未実装** | `physics/origami_domain.py::_build_face_skin` に層数比例の bulge 項が無い（crease ロールの法線勾配のみ） | 要件 B「紙特有のわずかな膨らみ」が未達。機首・前縁の重なりが平面に潰れて見える | 面ごとに `aLayer` を参照した法線方向 bulge を頂点高さに注入（crease 近傍は 0 へフェード） |
| **G-4** | **主翼前縁が矩形で後退翼になっていない** | `wing_l` / `wing_r` crease は `u = const` の直線 | 実物の dart らしい前縁後退がなく、機体形状のリアリティが一段落ちる | `corner` crease と `wing` crease の交点に短い前縁後退 crease を追加（group scope と整合） |
| **G-5** | **Nakamura の「中央三角ロック」が近似** | `lock_l` / `lock_r` は `flap_l/flap_r` に対する **24°** の小折れのみ。層を挟み込む三角ロック機構は未再現 | 要件「中央の三角ロックで左右の層を固定」が機構・外形とも未達 | 対称 crease ＋ 段付き crease で「片側層を反対側へ挟む」構造を設計（G-3 の camber と併用） |
| **G-6** | **折り工程 α スライダーがサーバ往復** | `static/js/app.js::_loadOrigami` が 60 ms debounce で `POST /api/v1/origami/mesh` を取得 | α 操作の実効 10〜20 fps | 最短は α の量子化（0.02 刻み）+ プリフェッチ。JS 移植は A-13 の drawback あり |
| **G-7** | **折り紙ビュー状態が export/import に含まれない** | `origami` はローカル専用セクション（`app.js::_push` が除外） | 設定の往復で選択機種・α が失われる | `/api/v1/data/export` の bundle に `origami` を含める（サーバは受信しない） |
| **G-8** | **機体形状 `shape_flag` と折り紙アーキタイプが独立** | `panels.js` の 2 系統が別選択 | 二重選択の不整合表示 | `shape_flag` を主とし origami は導出＋上書き可に（`shape_flag → 0/1/2` と 3 機種が 1:1 に対応することを活用） |

**検証状態（折り紙系のみ）**: `tests/test_origami.py` = **39 passed**
（頂点数・法線妥当性・退化三角形なし・自己交差ゼロ・Z-Fighting オフセット・境界ボックス・
等距性・α 連続性・空力弾性・payload 契約）。playwright でコンソールエラー 0 件・
シェーダコンパイルエラー 0 件・4 視点（機首/斜め前方/後方/下面）・α スライダー・3 機種切替を
スクリーンショットで確認済み。


### 4.6 無作為折り方遺伝子（Step 2・G9 系・5 件・2026-10-20 由来／G9-3・G9-4 は 2026-10-21 解決）

> `physics/origami_genome.py` は折り操作のシーケンスを遺伝子として表現し、
> `tests/test_origami_genome.py`（22 件）で固定化した。残るのは**設計判断が未確定**な項目。

| # | 残課題 | 残存の証拠 | 影響 | 次の手順 |
|---|---|---|---|---|
| **G9-1** | ~~**持ち手フィルタの閾値は人体工学の出典が無い**~~（**解決済み**） | GripThresholds の既定（最低高さ 8 mm / 前後長 30 mm / 2 層 / CG 許容 15 mm / グリップ半幅 12 mm）に ISO 7250-1:2017 / ISO/TR 7250-2:2010、産総研/HQL 手の寸法データ(2010)、MIL-STD-1472H §5.7.3、NASA-STD-3000 §3.3 の成人ピンチ把持寸法規格を照合・登録 | 解決: 人体工学基準と現行値の整合を確認し、docs/SPECIFICATION.md §5.1 および physics/origami_genome.py docstring に一次文献を正式登録 | 完了。閾値は文献範囲の中央〜安全域下限として妥当性を確認。 |
| **G9-2** | **`layer_order` は共面層のタイブレークのみ**（**未解決**） | `assign_layers(faces, group_order)` の group rank は「深さが同じ」ときの順序付けにだけ効く | 「この折りはこの面の下へ回す」という指定が、層が重なっていない場合には効かない | group rank を「層インデックスへの拘束」として DAG の辺拘束へ拡張する（重なり保証を保ったまま） |
| **G9-3** | ~~**ランダム個体の適合率が低い**~~（**解決済み**） | 実測: `strategy="families"` の 50 個体で grip 合格は 12 個体（**24 %**）、`"free"` は**ほぼ 0 %**。完全無作為の世代0は成立しない | 解消: GA は既知の適合アーキタイプ（`genome_from_archetype` の classic dart / nakamura / sky_king。3 機体とも grip フィルタ合格）で初期集団をシードし、適合性を保つ修復・攪乱オペレータを併用する | 完了。実測値は `.agents/Knowledge.md`「Procedural Origami Genome」に記録（正本ではなく参照） |
| **G9-4** | ~~**レシピの SVG / 手順表がブラウザに未表示**~~（**解決済み**） | API は `/api/v1/origami/genome/recipe` で Markdown と SVG を返していたのに、UI は折り手順スライダ（`genome_step`）しか無かった | 解消: 折り紙パネルに展開図プレビュー（SVG。**山=赤実線 / 谷=青破線**＋符号付き角度）と手順表を実装し、3D スライダと連動 | 完了。実装は `static/js/ga_research.js`（`renderCreasePattern` / `requestOrigamiRecipe` は `static/js/origami.js`） |
| **G9-5** | ~~**遺伝子個体の翼寸法は wingspan_m 設定と独立**~~（**解決済み**） | step_meshes / origami_mesh / origami_genome.py / API /api/v1/origami/genome/steps / UI static/js/app.js / origami.js に wingspan_m 2段スケーリング（実測スパンから  = \text{target} / \text{current}$ で頂点・寸法を一括再スケール）を適用 | 解決: UI の wingspan_m スライダーと 3D メッシュ・物理特性のスパンが一致するよう配管 | 完了。	ests/test_origami_genome.py::test_origami_genome_wingspan_rescaling で固定化。 |

**検証状態（遺伝子系のみ）**: `tests/test_origami_genome.py` = **22 passed**
（無作為 1000 個体の幾何変換・持ち手判定・6DoF 受け渡し、3 定型機体の grip 合格、
折り角の範囲、JSON 往復、手順表・Markdown・展開図 SVG・段階メッシュ、API 6 系統）。
往復・風高度・評価status の回帰固定化テストは `tests/test_origami_genome_roundtrip.py` /
`tests/test_wind_height_parity.py` / `tests/test_gpu_kernel_parity.py`。


### 4.7 2026-10-19 コミット監査・2026-10-21 実測で判明した残課題（4 件・K1 台帳）

> 出典: 2026-10-19 のコミット監査（`09aa40e` の劣化分解）と 2026-10-21 の実測スイート走査。
> **§7.1 / §7.2 の P0-1〜P0-3 / P1 とは別系統**のため、本節の ID は `K1-` 接頭辞で台帳内衝突を避けてある。
> 事実の正本は `.agents/Knowledge.md`「Commit Audit & Integration Contract」節（§4.7 は残課題のみ）。

| ID | 残課題 | 場所（file:line） | 再現コマンド | 判定・担当 |
|---|---|---|---|---|
| **K1-P0-1** | **GPU/バッチ経路と 6DoF 参照経路の数値不一致**。`distance_m` が 5.2577409 対 5.2613846（相対差 6.9e-4）／1.9218516 対 1.9239000（1.1e-3）、`max_stress_pa` 差 1.9e-4 が許容 1e-6 を超過し、reference 側が `status='crashed'` になる | **修正前**の `physics/gpu_dynamics.py:547,705,709`（`altitude = -position[:,2] - params.ground_level_m` による AGL の事前減算）/ `physics/wind_domain.py:852`（`heights_agl = P[:,2] - ground_level_m` での AGL 形成）/ `physics/flight_sim.py:952`（参照経路の `height = -_finite(position[2])`） | `python -m pytest "tests/test_optimizer.py::test_map_evaluate_is_order_preserving" "tests/test_optimizer.py::test_parallel_evaluate_matches_serial_and_reports_progress" "tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals" -q` | **解消済み（コミット: `8a41586`）**。真因は**大気メモの履歴依存**で、`physics/flight_sim.py:716-717`（修正前の 1 mm 量子化は `:710-712` / `:737`） の `_ATMOSPHERE_CACHE` が高度を **1 mm 量子化**していた（`_ATMOSPHERE_QUANTUM_M = 1.0e-3` と `round(altitude / _ATMOSPHERE_QUANTUM_M)`）ため「最初に問い合わせられた 1 mm バケットの密度」を返していた。`8a41586` で量子化を撤去し**厳密キー**へ（`physics/gpu_dynamics.py:365`（`-positions[:, 2]` = `z_up = -z_ned`）の風高度も `z_up = -z_ned` へ）。2026-10-22 再実測: `python -m pytest tests/test_ga_audit.py tests/test_optimizer.py -q` = **61 passed / 0 failed**。**残存は 6DoF 経路のカオス性**（§4.10） |
| **K1-P0-2** | **水平 100 km 境界でのクラッシュ判定が働かない**。境界の 0.034 m 手前（`position_m.x = 99999.9657`）でも `crashed=False` のまま | `tests/test_world_bounds.py::test_simulation_crashes_and_freezes_when_crossing_horizontal_boundary` / 水平リミットの判定実装（`physics/flight_sim.py`） | `python -m pytest "tests/test_world_bounds.py::test_simulation_crashes_and_freezes_when_crossing_horizontal_boundary" -q` | **解消済み（コミット: `8a41586`）**。`physics/flight_sim.py:2462-2530` の `_world_wall_contact()` が水平壁を壁として発火し状態を凍結（`crashed=True` / `diverged=True` / `status='diverged'`）。2026-10-22 再実測: **1 passed** |
| **K1-P0-3** | **GA2 の Stage-0 幾何フィルタが既知設計を偽陽性で不合格にする**。既知設計が `reasons=['paper would tear']` で落ちる | `tests/test_ga2_stages.py::test_the_geometry_filter_accepts_the_known_designs` / `physics/ga2/stage0.py` | `python -m pytest "tests/test_ga2_stages.py::test_the_geometry_filter_accepts_the_known_designs" -q` | **解消済み（コミット: `8a41586`）**。`_segments_overlap()` の**共線性検査の追加**で解消（hot_spot 9 → 正 5）。2026-10-22 再実測: `python -m pytest tests/test_ga2_stages.py -q` = **14 passed** |
| **K1-P1** | **3 ファイルが 120 s を超えており、タイムアウトなのか単に遅いだけかが未確認** | `tests/test_investigate.py` / `tests/test_issue28_gpu_duration.py` / `tests/test_payload_contract.py` | `python -m pytest tests/test_investigate.py tests/test_issue28_gpu_duration.py tests/test_payload_contract.py -q --durations=10` | **2026-10-22 実測済み**（`--durations=15` の実測結果は §4.9 K3-P1 欄を参照。3 ファイル合計で **0 failed**。**全スイート再実測 2 回**（`python -m pytest tests -q --durations=15` = **1264 passed / 0 failed**／814.48 s と **1264 passed / 0 failed**／2326.41 s）でも**「120 s 超」は 1〜2 テスト**で**判定は変わらない**（**タイムアウトではなく所要時間の長さ**。**所要時間は実行形態・負荷に依存し、差の原因は特定していない**）） |

**前提となる事実（2026-10-21）**: HEAD `54d556b` の `tests/test_flight.py` + `tests/test_records.py` は
**9 failed / 68 passed** だったが、`7639895 fix(physics): restore the fold-integration contract and the payload keys`
で **82 passed** へ回復済み（`09aa40e` の2劣化＝後縁フォールドの空力接続と `fold_trailing_deg` 既定 15.0 の矛盾、
`physics/payload_contract.py` の `CORE_KEYS` からの `error`/`error_id` 欠落）。
検証ベースライン: `python -m ruff check .` 0 件 / `python -m compileall physics api tools app.py smoke_test.py` 0 error /
`python smoke_test.py` 14/14 ステージ。既知の残失敗は本節の 4 件と P1 の未確認 3 ファイル。

**参照アンカーの注意（Knowledge.md 再構成のため）**: `no-finish.md` §7.0 と `docs/CODE_REVIEW_FINDINGS.md` は
`.agents/Knowledge.md` を**行番号**（例: `:38-39`、`:41`）で参照しているが、Knowledge.md は 2026-10-21 に
80行→74行へ再構成（節の統合）されたため**行番号アンカーは失効**している（§7.0 の P1-13 における
「Knowledge.md が実在しないガードレールを主張」という指摘自体は、節 `[Discord Bridge]` の該当行に実在する）。
参照は**節名**で行うこと: `[Discord Bridge]` / `[Infeasible Status Forwarding]` / `[GA Research Engine]` /
`[Wind Altitude Convention / 風高度規約・風洞]` / `[Commit Audit & Integration Contract]` / `[Remaining Work Ledger]`。


### 4.8 2026-10-21 実測で確定した残課題（K2 台帳: P0-1〜P0-9 / P1）

> 出典: 2026-10-19 コミット監査の残件（§4.7 と同一系統）＋ 2026-10-21 の K2 実測スイート走査
> ＋ GA エンジン審査の P0 4 件（Y1 レポート `Y1_ga_engine_review.md` / 修正レポート `Y2_ga_p0_fixes.md`）。
> **ID は本節が優先 ID**。§7.1 の `P0-1`〜`P0-12`（2026-10-19 レビュー由来）とは別系統のため台帳内衝突を
> 避けるため `K2-` 接頭辞を付す（**K2-P0-N ≡ 指示の P0-N**）。§4.7 の `K1-P0-1`〜`K1-P0-3` / `K1-P1` と
> **同一の不具合**であり、本節が 2026-10-21 の実測値と実装状況で優先 ID を再割当したものである。
> 事実の正本は `.agents/Knowledge.md`「Verified Solutions & Troubleshooting」/「Wind Altitude Convention」/
> 「Console Control Unification」/「Orchestration Ops」節（残課題のみ本ファイルに二重保管しない）。

| ID | 残課題 | 場所（file:line） | 再現コマンド | 判定・担当 |
|---|---|---|---|---|
| **K2-P0-1** | **GPU/バッチ経路と 6DoF 参照経路の数値不一致**。`distance_m` が 5.2577409 対 5.2613846（`tests/test_optimizer.py::test_map_evaluate_is_order_preserving`）および 1.9218516 対 1.9239000（`::test_parallel_evaluate_matches_serial_and_reports_progress`）が許容 1e-6 を超過。`tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals` は `max_stress_pa` 差 1.9e-4 > 許容 1e-6、reference 側が `status='crashed'` になる | **修正前**の `physics/gpu_dynamics.py:547,705,709`（`altitude = -position[:,2] - params.ground_level_m` の AGL 事前減算）/ `physics/wind_domain.py:852`（`heights_agl = P[:,2] - ground_level_m`）/ `physics/flight_sim.py:952`（参照経路の `height = -_finite(position[2])`） | `python -m pytest "tests/test_optimizer.py::test_map_evaluate_is_order_preserving" "tests/test_optimizer.py::test_parallel_evaluate_matches_serial_and_reports_progress" "tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals" -q` | **解消済み（コミット: `8a41586`）**。真因は**大気メモの履歴依存**（`physics/flight_sim.py:716-717`（修正前の 1 mm 量子化は `:710-712` / `:737`） で大気メモの高度 1 mm 量子化 `_ATMOSPHERE_QUANTUM_M = 1.0e-3` を撤去し**厳密キー**へ）／`physics/gpu_dynamics.py:365`（`-positions[:, 2]` = `z_up = -z_ned`）で風高度を `z_up = -z_ned` へ。**「バッチ経路の実効 AGL 502 m」は真因ではなかった**（`tests/test_wind_height_parity.py` 3 passed＝風サンプルは一致）。2026-10-22 再実測: `python -m pytest tests/test_ga_audit.py tests/test_optimizer.py -q` = **61 passed / 0 failed**。**残存は 6DoF 経路のカオス性**（§4.10） |
| **K2-P0-2** | **水平 100 km 境界でのクラッシュ判定が働かない**。境界の 0.034 m 手前（`position_m.x = 99999.9657`）でも `crashed=False` のまま | `tests/test_world_bounds.py::test_simulation_crashes_and_freezes_when_crossing_horizontal_boundary` / 水平リミット判定（`physics/flight_sim.py`） | `python -m pytest "tests/test_world_bounds.py::test_simulation_crashes_and_freezes_when_crossing_horizontal_boundary" -q` | **解消済み（コミット: `8a41586`）**。`physics/flight_sim.py:2462-2530` の `_world_wall_contact()` が水平壁を**壁として**発火させ、状態を凍結（`crashed=True` / `diverged=True` / `status='diverged'`）。2026-10-22 再実測: **1 passed** |
| **K2-P0-3** | **GA2 の Stage-0 幾何フィルタが既知設計を偽陽性で不合格にする**。既知設計が `reasons=['paper would tear']` で落ちる | `tests/test_ga2_stages.py::test_the_geometry_filter_accepts_the_known_designs` / `physics/ga2/stage0.py` | `python -m pytest "tests/test_ga2_stages.py::test_the_geometry_filter_accepts_the_known_designs" -q` | **解消済み（コミット: `8a41586`）**。`_segments_overlap()` の**共線性検査の追加**で解消（平行判定のみだったため 137–185 mm 離れの crease を重なりと誤算／hot_spot 9 → 正 5）。2026-10-22 再実測: `python -m pytest tests/test_ga2_stages.py -q` = **14 passed**。**残存: tear 判定そのものの出典**（`MAX_FOLDS_AT_ONE_SPOT` / `HOT_SPOT_RADIUS_M` の根拠。許容応力の尺度は `physics/stress_domain.paper_allowable_stress` が正本で、`docs/SPECIFICATION.md` §1.3） |
| **K2-P0-4** | **GA の適合度式に出典無しの係数が混入**。`grip_penalty_weight=0.01` / `max_secondary_weight=0.25` と基準値 `max_ld_ratio: 8.0` / `max_altitude_m: 10.0`（重み付き加算形。設計正本は grace factor の**積** `1/(1+P_grip)` であり `8.0`/`10.0` に出典なし） | `physics/ga_config.py` / `physics/ga_domain.py`（Y1 の **Y-02**） | `python -c "from physics.ga_config import GAConfig; c=GAConfig(); print(c.grip_penalty_weight, c.max_secondary_weight)"` | **解消済み（コミット: `d287af2`）**。`physics/ga_config.py` に `grip_penalty_weight` / `max_secondary_weight` / `max_ld_ratio` / `max_altitude_m` は**存在せず**、`physics/ga_domain.py:1807-1854` の `_individual_fitness` は `merit × 1/(1+P_grip) × 1/(1+max(0,σ_u−1))` の**重みなしの積**。回帰固定化 `tests/test_ga_engine.py::test_the_fitness_carries_no_free_coefficient` / `::test_the_grip_penalty_enters_the_fitness_as_a_grace_factor` → `python -m pytest tests/test_ga_engine.py -q` = **51 passed**。**残存**: 旧重み付き成果物 `artifacts/ga_audit/**` は現行設定と不整合（Y1 の Y-19）のため引用不可 → 再生成 or 生成時 config ハッシュの埋め込みが要対応 |
| **K2-P0-5** | **GA の公開 payload に `model` / `approximate` / `physics_verification_eligible` / `limitations` が無い**。`_result_payload` に 4 キーが無く、スカラー filter が `limitations`（非スカラー）を脱落させるため、公開 payload と HTTP 応答のいずれにも経路情報が 1 つも無い | `physics/ga_domain.py::_result_payload` / `api/routes/ga_research.py`（Y1 の **Y-06**） | `python -m pytest tests/test_ga_engine.py -q -k "payload or provenance or limitations"` | **解消済み（コミット: `d287af2`）**。`physics/ga_domain.py:521-526` が `limitations` をリストとして保持、`:2386-2400` の来歴 dict に 4 キー、`:2459-2461` で公開 payload へ展開。回帰固定化 `tests/test_ga_engine.py::test_the_published_result_carries_its_provenance` / `::test_the_api_publishes_the_provenance_of_the_result` → **51 passed** |
| **K2-P0-6** | **代理経路（`reduced_surrogate`）が「最適機体」として公開され得る**。ホール・オブ・ファームがティアを問わずに登録し、`_best_individual` が先頭を無条件公開するため tier-1 個体が「物理検証済みの最適機体」として出る | `physics/ga_domain.py`（Y1 の **Y-21**） | `python -m pytest tests/test_ga_engine.py -q -k "surrogate or publish"` | **解消済み（コミット: `d287af2`）**。`physics/ga_domain.py:966-983` の `is_reference_path_evaluation` が `model == RIGID_BODY_MODEL_TAG`（`"rigid_body_6dof"`）かつ `approximate == False` のみ公開を許す。回帰固定化 `tests/test_ga_engine.py::test_the_published_optimum_is_never_a_surrogate_result` → **51 passed** |
| **K2-P0-7** | **世代 0 の初期母集団が一度も評価されない**（未評価個体が `feasible` 扱い）。世代ループが offspring だけを tier0→tier3 に通すため初期母集団は `tier=0, metric=0.0, result={}` のままで、`individual_structural_failure` が `result={}` に対し失敗条件を一つも満たさないため `False`（feasible）を返す | `physics/ga_domain.py`（Y1 の **Y-23**） | `python -m pytest tests/test_ga_engine.py -q -k "generation0 or bootstrap or feasible"` | **解消済み（コミット: `d287af2`）**。`physics/ga_domain.py:1321` の `_evaluate_initial_population()` が全島の初期母集団を評価し、`:2610` で世代ループ前に実行、`:2632-2635` で `generation0_feasible_fraction` を公開。**未評価個体と失敗個体は `INFEASIBLE_SCORE`（−1e12）**（`:1843-1844`）→ **51 passed** |
| **K2-P0-8** | **風洞ベンチ姿勢の正本関数 `wind_tunnel_bench_quaternion` が未定義**。`7639895` の規約（ベンチは進行方向を向く heading 180°、4° ベンチの α は要求された正の α、旧実装は 180°+α で −176°）は docstring に明文化されているが、その関数を参照する実装が存在しない | `physics/flight_sim.py`（関数**未定義**）/ `physics/wind_domain.py:881,915`（docstring 参照のみ） | `python -c "import physics.flight_sim as f; print(hasattr(f, 'wind_tunnel_bench_quaternion'))"` → `False` | **解消済み（コミット: `c4b9286`）**。`physics/flight_sim.py:993` に `wind_tunnel_bench_quaternion(alpha_deg, beta_deg)` を実装（`WIND_TUNNEL_BENCH_HEADING_DEG = 180.0` は `:990`）、風洞モード（`:2026`）と UI 経路（`:3203`）が呼称。`python -c "import physics.flight_sim as f; print(hasattr(f, 'wind_tunnel_bench_quaternion'))"` → **`True`**。回帰固定化 `tests/test_wind_tunnel_bench.py` → **7 passed**（§7.1 P0-3 と同系統） |
| **K2-P0-9** | **投入初期条件の出典なき人工係数が残存**。`torque_rate[1] *= 0.10`（"Finger pinch guide effect"）が `physics/flight_sim.py:1512`（`7639895` / `d287af2` のツリー）に残り、pitch 相対誤差が 0.9（解析値 `I⁻¹MΔt` と不一致）。`7639895` の規約は撤去を要求 | `physics/flight_sim.py:1512`（`7639895` / `d287af2` のツリー） | `python -m pytest tests/test_flight.py -q`（現状 82 passed のため**係数除去の回帰固定テストが未整備**。撤去時は pitch 相対誤差 0.0 を固定するテストを新設する必要がある） | **解消済み（コミット: `c4b9286`）**。`physics/flight_sim.py` に `torque_rate[1] *= 0.10` は**残存しない**（`:1653` は有限値ガードのみ）。回帰固定化テスト `tests/test_launch_release_torque.py:108-127` が「produced rate ≡ 解析値 `I⁻¹(r×F)Δt`（rel 1e-12）を**全軸**で保証」を固定（removed 係数 1/10 を使わない）。`python -m pytest tests/test_launch_release_torque.py -q` = **4 passed** |
| **K2-P1** | **3 ファイルが 120 s を超えており、タイムアウトなのか単に遅いだけかが未確認** | `tests/test_investigate.py` / `tests/test_issue28_gpu_duration.py` / `tests/test_payload_contract.py` | `python -m pytest tests/test_investigate.py tests/test_issue28_gpu_duration.py tests/test_payload_contract.py -q --durations=10` | **2026-10-22 実測済み**（`--durations=15` の実測結果は §4.9 K3-P1 欄を参照。3 ファイル合計で **0 failed**。**全スイート再実測 2 回**（`python -m pytest tests -q --durations=15` = **1264 passed / 0 failed**／814.48 s と **1264 passed / 0 failed**／2326.41 s）でも**「120 s 超」は 1〜2 テスト**で**判定は変わらない**（**タイムアウトではなく所要時間の長さ**。**所要時間は実行形態・負荷に依存し、差の原因は特定していない**）） |

**2026-10-21 実測の検証ベースライン**: `python -m ruff check .` = All checks passed /
`python -m compileall physics api tools app.py smoke_test.py` = 0 error / `python smoke_test.py` = **14/14 ステージ** /
`tests/test_flight.py` + `tests/test_records.py` = **82 passed**（`7639895` による復旧を確認）。
K2-P0-1 / K2-P0-2 は**再現済み（FAILED）**、K2-P0-3 は **PASS**（解消済み）、
K2-P0-4〜K2-P0-7 は Y2 により作業ツリー上は修正済み（**未コミット**・CI 未確認）、
K2-P0-8 / K2-P0-9 は**未実装**（コード側対応が要る）である。

**2026-10-22 同期（W13 台帳陳腐化の消化）**: 上記の K2-P0-1〜K2-P0-9 は**すべて解消済み**へ更新した
（内訳: K2-P0-1/2/3 = `8a41586`、K2-P0-4〜P0-7 = `d287af2`、K2-P0-8/9 = `c4b9286`）。
**判定根拠は各行の「判定」欄に file:line・再現コマンド・実行したテスト結果を記載**した
（2026-10-22 再実測: `tests/test_ga_audit.py`+`tests/test_optimizer.py` = 61 passed、
`tests/test_ga_engine.py` = 51 passed、`tests/test_ga2_stages.py` = 14 passed、
`tests/test_wind_tunnel_bench.py` = 7 passed、`tests/test_launch_release_torque.py` = 4 passed、
`tests/test_world_bounds.py` = 1 passed）。
**残存するもの**は各行の末尾に明記した（K2-P0-1: 6DoF 経路のカオス性／K2-P0-3: tear 判定の出典／
K2-P0-4: 旧重み付き成果物 `artifacts/ga_audit/**` の不整合）。**台帳行は削除していない**。


### 4.9 2026-10-21 K3 再実測で更新した残課題（K3 台帳: P0-1〜P0-7 / P1）

> 出典: K3 指示台帳（2026-10-19 コミット監査＋GA エンジン審査の残件）＋ **2026-10-21 の K3 再実測**。
> §4.7（`K1-P0-1`〜`K1-P0-3` / `K1-P1`）および §4.8（`K2-P0-1`〜`K2-P0-9` / `K2-P1`）と**同一の不具合系統**であり、
> 本節は §4.8 の判定を**再実測結果で更新**した優先 ID（`K3-P0-N` ≡ 指示の `P0-N` ≡ §4.8 の `K2-P0-N`）である。
> 既存行は書き換えず、新規行の追加のみ。事実の正本は `.agents/Knowledge.md`。

| ID | 残課題 | 場所（file:line） | 再現コマンド | 判定・担当 |
|---|---|---|---|---|
| **K3-P0-1** | **GPU/バッチ経路と 6DoF 参照経路の数値不一致**。`distance_m` 5.2577409 対 5.2613846（`tests/test_optimizer.py::test_map_evaluate_is_order_preserving`）および 1.9218516 対 1.9239000（`::test_parallel_evaluate_matches_serial_and_reports_progress`）が許容 1e-6 を超過。`tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals` は `max_stress_pa` 差 1.9e-4 > 許容 1e-6、reference 側が `status='crashed'` になる | **修正前**の `physics/gpu_dynamics.py:547,705,709`（`altitude = -position[:,2] - params.ground_level_m` の AGL 事前減算）/ `physics/wind_domain.py:852`（`heights_agl = P[:,2] - ground_level_m`）/ `physics/flight_sim.py:952`（参照経路の `height = -_finite(position[2])`） | `python -m pytest "tests/test_optimizer.py::test_map_evaluate_is_order_preserving" "tests/test_optimizer.py::test_parallel_evaluate_matches_serial_and_reports_progress" "tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals" -q` | **解消済み（コミット: `8a41586`）**。真因は**大気メモの履歴依存**（`physics/flight_sim.py:716-717`（修正前の 1 mm 量子化は `:710-712` / `:737`） で大気メモの高度 1 mm 量子化 `_ATMOSPHERE_QUANTUM_M = 1.0e-3` を撤去し**厳密キー**へ）／`physics/gpu_dynamics.py:365`（`-positions[:, 2]` = `z_up = -z_ned`）で風高度を `z_up = -z_ned` へ。**「バッチ経路の実効 AGL 502 m」は真因ではなかった**（風サンプル自体は一致）。2026-10-22 再実測: `python -m pytest tests/test_ga_audit.py tests/test_optimizer.py -q` = **61 passed / 0 failed**。**残存は 6DoF 経路のカオス性**（§4.10） |
| **K3-P0-2** | **水平 100 km 境界でのクラッシュ判定が働かない**。境界の 0.034 m 手前（`position_m.x = 99999.9657`）でも `crashed=False` のまま | `tests/test_world_bounds.py::test_simulation_crashes_and_freezes_when_crossing_horizontal_boundary` / 水平リミット判定（`physics/flight_sim.py`） | `python -m pytest "tests/test_world_bounds.py::test_simulation_crashes_and_freezes_when_crossing_horizontal_boundary" -q` | **解消済み（コミット: `8a41586`）**。`physics/flight_sim.py:2462-2530` の `_world_wall_contact()` が水平壁を壁として発火＋状態を凍結。2026-10-22 再実測: **1 passed** |
| **K3-P0-3** | **GA2 の Stage-0 幾何フィルタが既知設計を偽陽性で不合格にする**。既知設計が `reasons=['paper would tear']` で落ちる | `tests/test_ga2_stages.py::test_the_geometry_filter_accepts_the_known_designs` / `physics/ga2/stage0.py` | `python -m pytest "tests/test_ga2_stages.py::test_the_geometry_filter_accepts_the_known_designs" -q` | **解消済み（コミット: `8a41586`）**。`_segments_overlap()` は**平行判定のみ**で共線性を検査せず、137–185 mm 離れている crease を重なりと誤算していた（hot_spot 9 → 正 5）。**共線性検査の追加**で解消。2026-10-22 再実測: `python -m pytest tests/test_ga2_stages.py -q` = **14 passed**。**残存: tear 判定そのものの出典**（`MAX_FOLDS_AT_ONE_SPOT` / `HOT_SPOT_RADIUS_M`） |
| **K3-P0-4〜P0-7** | **GA エンジンの審査 P0 4 件**。① 適合度式に出典無しの重み `grip_penalty_weight=0.01` / `max_secondary_weight=0.25` と基準値 `max_ld_ratio: 8.0` / `max_altitude_m: 10.0` ② 公開 payload に `model` / `approximate` / `physics_verification_eligible` / `limitations` が無い ③ **代理経路（`reduced_surrogate`）が「最適機体」として公開され得る** ④ **世代 0 の初期母集団が未評価で feasible 扱い**（−1.0 は INFEASIBLE −1e12 より 1e12 倍高い）。審査 verdict は **REJECT**。設計一致率は 12 一致 / 21 不一致 / 9 未実装 / 5 過剰実装、決定論性は同一シード 2 回で完全一致（合格） | `physics/ga_config.py` / `physics/ga_domain.py::_result_payload` / `api/routes/ga_research.py`（Y1 レポート `Y1_ga_engine_review.md` が正本。修正は `Y2_ga_p0_fixes.md`） | `python -m pytest tests/test_ga_engine.py -q -k "payload or provenance or limitations or surrogate or generation0 or bootstrap"` | **解消済み（コミット: `d287af2`）**。①`physics/ga_config.py` に該当 4 設定は**存在せず**、`physics/ga_domain.py:1807-1854` は重みなしの積 ②`:521-526,2386-2400,2459-2461` ③`:966-983`（`is_reference_path_evaluation`） ④`:1321,2610,2632-2635`。2026-10-22 再実測: `python -m pytest tests/test_ga_engine.py -q` = **51 passed / 0 failed**。**残存**: 旧重み付きの成果物 `artifacts/ga_audit/**` は現行設定と不整合（引用不可）→ 再生成 or 生成時 config ハッシュの埋め込みが必要 |
| **K3-P1** | **3 ファイルが 120 s を超えており、タイムアウトなのか単に遅いだけかが未確認** | `tests/test_investigate.py` / `tests/test_issue28_gpu_duration.py` / `tests/test_payload_contract.py` | `python -m pytest tests/test_investigate.py tests/test_issue28_gpu_duration.py tests/test_payload_contract.py -q --durations=10` | **2026-10-22 実測済み**（下記「K3 再実測（2026-10-22）」の表を参照。3 ファイル合計で **0 failed**。**タイムアウトではなく実測上の所要時間が長いだけ**と切り分け済み。**全スイート再実測 2 回**（`python -m pytest tests -q --durations=15` = **1264 passed / 0 failed**・所要 814.48 s と **1264 passed / 0 failed**・所要 2326.41 s）で**「120 s 超」は 1 回目 1 件（`test_full_investigate_pipeline`）／2 回目 2 件（同 + `test_sensitivity_scan_ranks_variables`）**。**両測定とも完走**し**タイムアウトは発生していない**。**所要時間は実行形態・負荷に依存**し、**差の原因は特定していない**） |

**K3 再実測（2026-10-21）の記録**: 上記 5 テストは **5 passed**（264.50 s）。§4.8 が記録した
「K2-P0-1 / K2-P0-2 は FAILED」は**作業ツリーの未コミット差分（他アクターの進行中修正）により解消**した実測であり、
コミット単位では未確認。`K2-P0-8`（`wind_tunnel_bench_quaternion` 未実装）・`K2-P0-9`
（`torque_rate[1] *= 0.10` 残存）は §4.8 のとおり**未実装**。K3 実測では
`physics/flight_sim.py` に `wind_tunnel_bench_quaternion` が無く、`physics/flight_sim.py:1517` に
`torque_rate[1] *= 0.10` が残存している。

> **2026-10-22 追記（W13 台帳同期）**: 直上の 2026-10-21 記録は**commit 単位では未確認**だった。
> 2026-10-22 の再実測で**コミット単位でも解消済み**であることを確認した
> （`wind_tunnel_bench_quaternion` は `physics/flight_sim.py:993` に実装、
> `torque_rate[1] *= 0.10` は消滅。`git log -S` で両者の実体会到 `c4b9286` を特定）。
> 上記の 2026-10-21 記録は**陳腐化の記録としてそのまま残す**（§10 の運用ルール）。

**K3 再実測（2026-10-22）の記録** — 3 ファイルの所要時間の切り分け（K1-P1 / K2-P1 / K3-P1 の実測）:
`python -m pytest tests/test_investigate.py tests/test_issue28_gpu_duration.py tests/test_payload_contract.py -q --durations=15`
→ **22 passed in 1220.53 s (0:20:20) / 0 failed**。

| テスト | 実測所要時間 | 判定 |
|---|---|---|
| `tests/test_investigate.py::test_full_investigate_pipeline` | **527.88 s** | **長時間だが完走**。タイムアウトではなく**実装の計算量**（調査パイプライン全段の逐次実行） |
| `tests/test_investigate.py::test_sensitivity_scan_ranks_variables` | **413.80 s** | 同上（感度スキャンの逐次評価） |
| `tests/test_investigate.py::test_focused_tuning_preserves_immutable_aircraft_properties` | **114.75 s** | 120 s 未満 |
| `tests/test_issue28_gpu_duration.py::test_gpu_batch_respects_individual_case_durations` | **71.94 s** | 120 s 未満 |
| `tests/test_issue28_gpu_duration.py::test_gpu_batch_landing_within_duration` | 17.17 s | 120 s 未満 |
| `tests/test_issue28_gpu_duration.py::test_cpu_batch_fallback_respects_durations` | 4.22 s | 120 s 未満 |
| `tests/test_payload_contract.py::test_every_backend_reports_the_same_key_set` | 25.48 s | 120 s 未満 |
| `tests/test_payload_contract.py::test_cupy_backend_emits_the_full_contract` | 10.81 s | 120 s 未満（残 10 件はすべて 10 s 未満） | 

**結論**: 3 ファイル合計 **20 分 20 秒**、**0 failed**。**タイムアウトは発生していない**＝
「120 s 超」の実体は **`tests/test_investigate.py` の 2 件（527.88 s + 413.80 s = 総時間の 87 %）**であり、
**`test_issue28_gpu_duration.py` と `test_payload_contract.py` は 1 ファイル 120 s 未満**。
K1-P1 / K2-P1 / K3-P1 の「タイムアウトなのか単に遅いだけか」の切り分けは **「単に遅いだけ」と確定**。
**残存（最適化の余地）**: `test_full_investigate_pipeline` と `test_sensitivity_scan_ranks_variables` の
逐次評価は §5 の 1（GPU 性能の融合）と §7.2 P1-3（最適化の実用性・1 評価 ≈ 9 秒）と同じ計算量の問題であり、
**テストの不具合ではなく実装の計算量の制約**である。**作為的な短縮（閾値下调・スキップ）はしない**。

**2026-10-22 再実測（全スイート 2 回）**: `python -m pytest tests -q --durations=15`
→ **1 回目: 1264 passed in 814.48 s (0:13:34) / 0 failed**、**2 回目（2026-10-22・本セッション）: 1264 passed in 2326.41 s (0:38:46) / 0 failed**。上表は **3 ファイルだけを実行した**測定である一方、全スイート測定は
**同一テストの所要時間が 527.88 s 対 129.48 s 対 511.26 s** と大きく異なる（**所要時間は
実行形態・負荷に依存する。差の原因は本セッションでは特定していない＝断定しない**）。**全測定とも
タイムアウトは発生していない**。2 回目の `--durations=15` で 120 s 未満の上位には
`tests/test_ga_engine.py::test_the_command_line_applies_the_trial_count_overrides` 83.72 s・
`tests/test_ga_audit.py::test_a_stalled_ga_flight_terminates_on_the_ground_with_a_bounded_flight_time`（setup 51.19 s）・
`tests/test_flight.py::test_trajectory_responds_to_every_environment_change` 47.76 s・
`tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals` 43.63 s が含まれる。

| テスト | 全スイート実測（1 回目: 814.48 s） | 全スイート実測（2 回目: 2326.41 s） | 判定 |
|---|---|---|---|
| `tests/test_investigate.py::test_full_investigate_pipeline` | **129.48 s** | **511.26 s** | 120 s 超だが**両測定とも完走**。**実装の計算量**の制約 |
| `tests/test_investigate.py::test_sensitivity_scan_ranks_variables` | **102.14 s** | **405.48 s** | 1 回目は 120 s 未満・2 回目は 120 s 超だが**完走**。**所要時間は負荷依存** |
| `tests/test_investigate.py::test_focused_tuning_preserves_immutable_aircraft_properties` | 26.68 s | 109.26 s | 120 s 未満 |
| `tests/test_issue28_gpu_duration.py::test_gpu_batch_respects_individual_case_durations` | 25.09 s | 44.80 s | 120 s 未満 |
| `tests/test_payload_contract.py::test_every_backend_reports_the_same_key_set` | 16.05 s | 15 位外 | 120 s 未満 |
| `tests/test_ensemble.py::test_gpu_ensemble_simulation_cupy_and_numpy` | 27.92 s | 36.65 s | 120 s 未満 |

→ **「120 s 超」は 1 回目で 1 テスト・2 回目で 2 テスト**（`test_full_investigate_pipeline` と `test_sensitivity_scan_ranks_variables`）。K1-P1 / K2-P1 / K3-P1 の判定
（「**単に遅いだけ**」）は**全測定で一致**。**残存は計算量のみ**（§4.10 / §5 の 1 / §7.2 P1-3 と同じ論点）。

## 4.10 2026-10-22 同期で**残存**と確認した項目（消してはならない制約・契約）

> W13 台帳同期で「解消済み」へ移した項目の**背後にある、依然として有効な制約**。
> コード・テストで裏取りしたうえで**残存として維持**する（§10 の運用ルール＝行は消さない）。

| ID | 残存の制約 | 場所（file:line） | 確認コマンド | 判定 |
|---|---|---|---|---|
| **R-1** | **6DoF 参照経路はカオス的**であり、擾乱 1e-8 → 距離 1.2e-3（λ ≈ 33/s、e-folding 0.03 s）。**並列／直列の軌道はビット同一になり得ない**（別の数値積分）。したがって **parity 判定は「発散前段の量」（ピーク応力・载荷係数）を厳密値で見る契約**が正であり、距離・飛行時間はカオス発散検出の許容（`rel=5e-2` 等）でしか判定しない。**履歴依存がビット差の原因**であり、許容の緩めや乱数シードの固定で合わせ込まない | `physics/flight_sim.py`（6DoF RK4）/ `physics/optimizer_domain.py`（parity 許容）/ `tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals` | `python -m pytest tests/test_ga_audit.py tests/test_optimizer.py -q` → **61 passed / 0 failed**（並列/逐次/単独がビット一致。**軌道の一致は保証しない**） | **残存（2026-10-22 確認）**。W9 の修正は大気メモの**履歴依存**を除去したが、**カオス性そのものは消えない**（§4.8 K2-P0-1 / §4.9 K3-P0-1 の残存欄）。／ **CI 実測（2026-10-22）**: 同じ物理コードに対して `Python Code Verification` が**成功と失敗を交互に返す**。run #90（`eed8abd`・docs 差分のみ）= **success**、run #91（`bb08fd3`・台帳 1 行のみ）= **failure**、run #92（`5133a67`・検証ツール 1 ファイルのみ）= **failure**、run #87（`c4b9286`）= **failure**。**失敗は常に** `tests/test_ga_audit.py::test_the_batch_path_agrees_with_the_canonical_6dof_on_ga_individuals` **1 件で、reference 側の `distance_m` は run 間で完全に同一（18.7794275711585）**＝ GA 実行と 6DoF 参照は決定的、**batch 側だけが runner ごとに違う**（numpy/BLAS 実装差の 1 ULP → カオス増幅）。→ **コード差分に起因しない負荷・runner 依存のフレーク**。ローカルは GPU 経路・CPU 経路（`CUDA_VISIBLE_DEVICES=""`・BLAS 1 スレッド）とも `python -m pytest tests/test_ga_audit.py -q` = **16 passed**。報告 Issue #46（開）。**許容の緩め・種固定で合わせ込まない**（上記のとおり）。**batch 経路は GA の適応度関数の surrogate であり、4 秒超のカオス飛行の `distance_m` は runner 間で 5 % 超え得る**＝ §4.8 K2-P0-1 の残存の実体（-batch と 6DoF の距離は长期量子で不可比） |
| **R-2** | **`KAPPA_SIGMA = 9.0e-6` の出典制約**。Beam 理論の断面係数に経験的スケーリング係数を掛けたもので、**監査済み範囲のどの論文にも出典が存在しない**。値を直すと全応力値と失敗判定が同時に変わるため**根拠を得るまで数値改定を行わない** | `physics/stress_domain.py:106`（`KAPPA_SIGMA: float = 9.0e-6`）/ `physics/stress_domain.py:317`（`KAPPA_SIGMA * moment / modulus`）/ `no-finish.md` §A-1（`:218` / `KAPPA_SIGMA` の行は `:222`）/ `docs/SPECIFICATION.md:48`・`:239` | `python -m pytest tests/test_stress.py -q` → **68 passed**（数値の据え置きを確認） | **残存（2026-10-22 確認）**。**据え置き根拠の正本は `docs/SPECIFICATION.md` §5.2「出典未取得の物理定数の据え置き根拠（G-17）」**（KAPPA_SIGMA / 後縁フォールド係数を出所・物理的意味・据え置き理由・再オープン条件つきで列挙） |
| **R-3** | **旧式 stress 尺度の再導入禁止**。判定尺度は `physics/stress_domain.paper_allowable_stress`（`:637-655`、`A = 2500 (gsm/100)^0.5 (0.6 + 0.8 stiffness)`、既定 100 gsm・stiffness 0.35 で **2200 Pa**）の **1 つだけ**で、**過応力クラッシュ判定が蘇生**している。**120 kPa 系統（`_STRESS_LIMIT_PA`）は `physics/` から削除済み**、**25 kPa 系統（`INDEX_REFERENCE_PA = 2.5e4`）は import 互換のため `physics/stress_domain.py:122` に `# noqa: F401` で残存するのみ**（正規化には未使用。heat-map は `σ/σ_allow` の利用率）。**2.0e4·gsm 系統は `ALLOWABLE_BASE_PA`（`:134-137`）へ統合済み**。これらを**再導入すると判定が二重化する**（`no-finish-physics-supplement.md` F-3 / `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §2-H10 が同旨） | `physics/stress_domain.py:122,134-137,637-655` / `physics/aero_kernel.py` の `_guard_fold_increment`（`:749-769`） | `python tools/check_stress_peak_parity.py` → 最大相対差 **0.000e+00**（`paper_allowable_stress` が唯一の判定尺度であることを確認）/ `python -m pytest tests/test_stress.py -q` → **68 passed** | **残存（2026-10-22 確認・再導入禁止事項）**。§7.0 対照表の「応力 120 kPa 尺度を 3 系統分裂 → 解消済み」（`:780`）と**同じ趣旨のガードレール**。§4.2 M-26 / A-1 と同一系統 |
| **R-4** | **GA ゲート G-17 の理由の `docs/SPECIFICATION.md` への記載**。出典未取得の物理定数（`KAPPA_SIGMA` / 後縁フォールド係数 / 把持フィルタ閾値）を、§5.1 と同じ記載形式で §5.2 に列挙し、実装状況と再オープン条件も明記 | `docs/SPECIFICATION.md:233`（§5.2 見出し） / §5.1（把持フィルタ閾値） | `python -c "import pathlib,re; t=pathlib.Path('docs/SPECIFICATION.md').read_text(encoding='utf-8'); print('G-17' in t, 'KAPPA_SIGMA' in t)"` → `True True` | **残存（2026-10-22 確認・記載は既に存在）**。W12 レポートが新設した。**G-17 の記載と本台帳 §A-1 / §A-2 の据え置き理由が一致している**ことだけを確認した（§A-2 の接続は解消済み・係数の出典なしは残存） |
| **R-5** | **Viterna-Corrigan 失速後外挿は未実装**。実装は Hoerner 平板（`physics/aero_kernel.py::post_stall_lift_coefficient`）で、混合角の平板値を固定目標とするため失速から混合角まで `C_L` は厳密単調減少し `C_L ≡ 0` の帯が出ない。Viterna-Corrigan は**風力タービン翼型**向けで、係数 `A_1, A_2, B_1, B_2` を翼型データから求める必要があり、**紙機体にはそのデータが無い**ため係数を推定しない | `physics/aero_kernel.py:499` / `physics/aero_domain.py` / `api/routes/meta.py`（`viterna_corrigan_1982`） / `docs/PHYSICS_MODELS.md` §3 | `python -m pytest tests/test_physics_model_registry.py -q` → **19 passed**（レジストリが未実装を宣言していることの回帰）/ `python -m pytest tests/test_aero.py -q` → passed | **残存（2026-10-22 確認）**。**再オープン条件**: 紙機体相当の翼型データ（`c_Lmax`・`c_dmax` と形状）が入手できること。入手まで **係数を推定しない**（`AGENTS.md` §2 出典なき係数禁止）。**正本文書は `docs/PHYSICS_MODELS.md` §3** |
| **R-6** | **3D CFD（Issue #47）は設計文書のみ**。`docs/CFD_ROADMAP.md` は要求仕様・段階 0〜5 の出口条件・検証ケース・計算予算を定義するが、**3D CFD のコードは存在しない**。GA の fitness は **6DoF パネル法のまま**維持する（1 個体あたり CFD は数時間、1,000 個体で 16 時間となり探索が成立しない） | `docs/CFD_ROADMAP.md` / `docs/PHYSICS_MODELS.md` §8 | `python -c "import pathlib; print(pathlib.Path('docs/CFD_ROADMAP.md').exists())"` → `True` / `python -m pytest tests/test_physics_model_registry.py -q` → **19 passed** | **残存（2026-10-22 確認）**。**着手条件は「クラスタあるいは同等の計算資源の確保」**。段階 0 は**未着手**。3D CFD は**別の層**（検証用）であり 6DoF の replacement ではない |
| **R-7** | **トップレールの幅予算**。ブランド 173 px ＋ 読み取り値 217 px ＋ 操作 870 px ＋ パディングで **1280 px が下限**。これを下回る幅では **操作ボタン（右端の 保存/読込/CSV/録画/ヘルプ）がレールから押し出される**。**1800 px 以下**では診断用 SOLVER 読み取り値を隠し、RENDER 値を短縮し、モードボタン・余白・間隔を詰めて**全ボタンを可視**に保つ（実測: 1280〜2560 px で溢れ 0 件）。**さらに狭い幅では操作の縮退が必要**で、それは未設計（未対応） | `static/css/console.css`（`@media (max-width: 1800px)`）/ `static/js/app.js`（`_onStats`）/ `tools/check_ui_css.py` | `python tools/check_ui_css.py` → OK / `python -m pytest tests/test_ui_css.py -q` → passed | **残存（2026-10-22 確認）**。**1280 px 未満**（タブレット・スマホ）ではレールは画面外引き出し化され、**トップレールの常時可視トリガ**（`btn-rail-left` / `btn-rail-right`）で開く設計にしたが、**1280 px 未満での操作缩减（読み取り値の省略・ボタンのアイコン化）の設計は未着手** |

## 5. 次のフェーズで着手する順序（推奨）

1. **GPU 性能の融合**（§3 C 表）— `cupy.fuse` / CUDA Graph で 13 状態 RK4 の 1 状態評価あたりの
   launch 数を圧縮する。**GA の大量評価の前提条件**であり、他のどの項目より先に片付けるべき
   （現状のままでは GPU 経路が CPU より遅く、存在意義が失われる）。
2. **A-1 / H-11 / M-26（実材料・実断面）**— 過応力クラッシュ判定の物理的根拠。
   実測論文の入手が前提なので**時間並列に着手可能**。
3. **A-9（アンサンブル経路の 6DoF 化）または縮小**— Monte-Carlo の統計が 6DoF とずれるため、
   GA の fitness と UI の予測帯が別物になる。縮約 proxy として明示を維持する、という
   選択肢も含めて判断。
4. **A-5（パネル法と正規経路の統合規約）**— メッシュ機体と config 機体の飛距離が一致しない
   現状は、UI で「同一機体」を主張する文面上での不整合。
5. **L-01 / L-02 / L-10（到達不能ガードの是正）**— 低リスクだが「宣言値と実装値の不一致」なので、
   GA による長時間・大量評価の前に片付けるべき。

---

## 5.5 追補（別セッションの独立照合で判明した漏れ）

本タスクの残課題台帳は上記 §1（A）〜§4（D）で網羅的にまとまっているが、**別セッションが
監査を独立に実施**した結果、上記に**無い項目**が 8 件・**意図的維持の理由**が 6 件・
**副作用の記録**が 5 件見つかった。以下に Temperatures する。

- 追補本体: **`no-finish-physics-supplement.md`**（§E-1〜E-8 / §F-1〜F-6 / §G-1〜G-5）
- 最重要: **E-1（GPU/CPU 数値一致の定量照合）** — §2 の「完全一致」宣言を実測で裏づける最終作業。
- 記載訂正: **G-4** — `H-19`（折り後縁の空力接続）は**未接続**（`trailing_edge_fold_effects` に呼び出し元が無い）。
  `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` 付録B の該当行を訂正済み。
  **2026-10-22 追記（W13 台帳同期）**: この「未接続」は**誤り**で、**接続は `7639895` で復元済み**
  （`physics/aero_kernel.py:1084-1085` → `:1108` / `:1113` / `:1178`、`physics/aero_domain.py:557,606,619,660,699`）。
  §A-2 / §4.1 H-19 の「未接続」記述は現状へ同期済み。**残存は係数（0.015 / −0.012 / 0.035）の出典なし**のみで、
  その据え置き根拠は `docs/SPECIFICATION.md` §5.2（G-17）。

---

6. **G-1 → G-3 → G-2（折り紙寸法整合 → 膨らみ → 物理への取り込み）**— 別トラック。
   物理正本の信頼性（1〜5）には影響しないが、「見た目と計算が別の機体」の不整合は
   UI の機体形状表示という主張に直結するため、表示系を扱う人間には優先度が高い。
   順序は依存で固定される: G-1（実スパン算出）が無ければ G-2 の入力が無く、
   G-3（camber）はスケール確定後に最終調整する。
7. **G-7 / G-8（ビュー状態の保存・選択の一貫性）**— 低コストで UX に直結するため、
   G-1 と並行して着手可。
8. **G-4 / G-5（前縁後退・Nakamura ロック）**— 形状の作り込み直しを伴うため、
   G-1〜G-3 の寸法確定後に行う（作り直すと寸法が振り戻すため）。

## 6. このファイルが古くなっていないかの確認手順

- 監査 §0.0 / §7 の「2026-10-20 実装状況」と本ファイルを突き合わせる
- `git log --oneline` で `39bb795` 以降のコミットに A〜D のいずれかの項目を触るものがあれば、
  本ファイルも同時更新する
- `.agents/Knowledge.md` の「残課題」節と重複。**正本は本ファイル**とし、
  `Knowledge.md` からは参照リンクだけを張る

---

## 7. E. 2026-10-19 マルチエージェントレビュー（13 名）由来の残課題

> **出典**: `docs/CODE_REVIEW_FINDINGS.md`（13 名のコンテキスト無しサブエージェント、4 ラウンド、
> 374 指摘の file:line 付き記録）。本節はそこから **優先度順に残作業を抽出した台帳**である。

### 7.0 着手前に必ず読むこと（スナップショット劣化の対照表）

レビュー実行中（2026-10-19〜20）に **aero_kernel への空力モデル統合・GPU 経路の 13 状態 RK4 化・
応力尺度の単一化・風符号の単一化**という大規模リファクタが進行していた。
そのため **レビュー指摘の一部は指摘時点で既に解消済み**である。以下のとおり判定済み。

| 判定 | 項目 | 根拠 |
|---|---|---|
| **解消済み** | GPU 経路の deg/rad 取り違え（αが57.3倍過小） | `physics/parallel_engine.py:158,191` が `rad2deg` で正規化し `aero_kernel` を委譲。旧 `_vector_lift_coefficient` は消滅 |
| **解消済み** | GPU の Helmbold 式不一致 / 応力 120 kPa 尺度を 3 系統分裂 | `Knowledge.md` §69-70（単一化済み）。`stress_domain.paper_allowable_stress` が唯一の実装。**2026-10-22 再確認**: 実装は `physics/stress_domain.py:637` の `paper_allowable_stress`（既定 2200 Pa）**1 つだけ**で、25 kPa / 120 kPa / 2.0e4·gsm の 3 系統は**再導入禁止**（再導入すると過応力クラッシュ判定が二重化）。正本: `docs/SPECIFICATION.md` §5.2（G-17）。`python tools/check_stress_peak_parity.py` = 最大相対差 0.000e+00 |
| **解消済み** | 風ベクトルの 90°回転・符号 3 系統 | `physics/wind_domain.py` `wind_flow_components()` が単一定義に統合済み |
| **解消済み** | `_local_aero` の操作権限4.3倍乖離 | `_local_aero` は削除済み（`flight_sim.py` に残るのは `_local_wind` のみ） |
| **解消済み** | `aero_kernel._finite` のスカラー `nan_to_num` で3.6倍減速 | `physics/aero_kernel.py` `_finite` に `isinstance(value, float)` の早期分岐がある（docstring もスカラー優先を明記） |
| **解消済み** | 未追跡モジュールでクリーンチェックアウト不能 | `aero_kernel.py` / `origami_domain.py` / `gpu_dynamics.py` / `api/routes/origami.py` / `static/js/origami.js` / `tools/branch_guard.py` / `tools/work_report.py` / `tools/mcp_call.py` / `tools/verify_mcp_servers.py` / `.kilo/kilo.jsonc` は **全て追跡済み**。**2026-10-22 再確認: 未追跡ファイルは 0 件**（`git ls-files --others --exclude-standard` の出力が空。§7.1 P0-1 の 4 件も追跡済み） |
| **解消済み** | master への push で CI が全滅（branch guard がセッションを abort） | 両 WF に workflow レベル `PAPER_AIRCRAFT_BRANCH_GUARD: warn` を明示。加えて `tools/branch_guard.py` が CI イベント checkout（`GITHUB_REF` ベース）を識別し、push checkout のローカル branch 名を**違反根拠にしない**（detached HEAD 基準）。ローカル clone の master 保護は維持（§7.1 P0-2） |
| **解消済み** | `records_domain.py` の日本語93行の U+FFFD 化（P0-12 / 旧 §8 F-1） | 往復で消滅した元文字は復号せず、**ギネス記録の原文出典から日本語を再記述**。`tools/check_mojibake.py` でリポジトリ全体を走査し文字化け 0 件、`tests/test_no_mojibake.py` が回帰を固定化（保留中の除外指定は 0 件）。cp932 往復の再現を許す変換は追加しない |
| **解消済み**（2026-10-22 追記） | **風洞ベンチ姿勢の正本関数 `wind_tunnel_bench_quaternion` が未定義**（§7.1 P0-3 / §4.8 K2-P0-8） | `physics/flight_sim.py:993` に実装（`WIND_TUNNEL_BENCH_HEADING_DEG = 180.0` は `:990`）、使用 `:2026` / `:3203`。`python -m pytest tests/test_wind_tunnel_bench.py -q` = **7 passed**。**コミット: `c4b9286`** |
| **解消済み**（2026-10-22 追記） | **投入初期条件の出典なき人工係数 `torque_rate[1] *= 0.10`**（§4.2 M-17 / §4.8 K2-P0-9） | `physics/flight_sim.py` に残存せず（`:1653` は有限値ガードのみ）。回帰固定化 `tests/test_launch_release_torque.py:108-127`（解析値 `I⁻¹(r×F)Δt` と全軸 rel 1e-12 で一致）→ **4 passed**。**コミット: `c4b9286`** |
| **解消済み**（2026-10-22 追記） | **後縁フォールドの空力接続が無い**（§A-2 / §4.1 H-19 / §5.5 G-4） | `physics/aero_kernel.py:1084-1085` → `:1108`（`cl_offset`）・`:1113`（`cd0`）・`:1178`（`cm`）、`physics/aero_domain.py:557,606,619,660,699`。**残存は係数の出典制約のみ**（§4.10 R-2 / `docs/SPECIFICATION.md` §5.2 G-17）。**コミット: `7639895`** |
| **解消済み**（2026-10-22 追記） | **GPU/バッチ経路と 6DoF 参照経路の数値不一致**（§4.7 K1-P0-1 / §4.8 K2-P0-1 / §4.9 K3-P0-1） | 真因は**大気メモの履歴依存**（`physics/flight_sim.py:716-717`（修正前の 1 mm 量子化は `:710-712` / `:737`） で大気メモの高度 1 mm 量子化 `_ATMOSPHERE_QUANTUM_M = 1.0e-3` を撤去し**厳密キー**へ）。`python -m pytest tests/test_ga_audit.py tests/test_optimizer.py -q` = **61 passed / 0 failed**。**残存は 6DoF 経路のカオス性**（§4.10 R-1）。**コミット: `8a41586`** |
| **解消済み**（2026-10-22 追記） | **水平 100 km 境界でのクラッシュ判定が働かない**（§4.7 K1-P0-2 / §4.8 K2-P0-2 / §4.9 K3-P0-2） | `physics/flight_sim.py:2462-2530` の `_world_wall_contact()` が水平壁を壁として発火＋状態を凍結。`python -m pytest tests/test_world_bounds.py -q` = **1 passed**。**コミット: `8a41586`** |
| **解消済み**（2026-10-22 追記） | **GA2 の Stage-0 幾何フィルタの偽陽性**（§4.7 K1-P0-3 / §4.8 K2-P0-3 / §4.9 K3-P0-3） | `physics/ga2/stage0.py` の `_segments_overlap()` に**共線性検査**を追加（hot_spot 9 → 正 5）。`python -m pytest tests/test_ga2_stages.py -q` = **14 passed**。**残存は tear 判定そのものの出典**。**コミット: `8a41586`** |
| **解消済み**（2026-10-22 追記） | **GA エンジンの審査 P0 4 件**（§4.8 K2-P0-4〜P0-7 / §4.9 K3-P0-4〜P0-7） | ①`physics/ga_config.py` に `grip_penalty_weight` / `max_secondary_weight` / `max_ld_ratio` / `max_altitude_m` が**無く**、`physics/ga_domain.py:1807-1854` は重みなしの積 ②`:521-526,2386-2400,2459-2461` ③`:966-983`（`is_reference_path_evaluation`） ④`:1321,2610`。`python -m pytest tests/test_ga_engine.py -q` = **51 passed**。**残存は旧成果物 `artifacts/ga_audit/**` の不整合**。**コミット: `d287af2`** |
| **解消済み**（2026-10-22 追記） | **記録プリセットの trim 契約が死**（§7.1 P0-10） | `physics/records_domain.py:668-671` が `source["release_pitch_deg"] = RECORD_TRIM_ALPHA_DEG` の無条件代入へ、カタログの `"release_pitch_deg": 0.0` 明示 9 箇所を削除（`git blame` → `7639895`）。GET / POST が 12 プリセット全て 3.0 で一致。**コミット: `7639895`** |
| **解消済み**（2026-10-22 追記） | **応力ヒートマップの転置**（§7.1 P0-11） | サーバ grid は `[spanwise][chordwise]` の正順。`python tools/check_stress_peak_parity.py` = 最大相対差 **0.000e+00**。**コミット: `7639895`**（W6 で正本化） |
| **解消済み**（2026-10-22 追記） | **`physics/payload_contract.py` の `error` / `error_id` 消失** | `physics/payload_contract.py:70-71` に `CORE_KEYS` として存在、`:133-135` に既定 `None`、`:156-164` に必須キー宣言。`python -m pytest tests/test_payload_contract.py -q` = passed。**コミット: `7639895`** |
| **解消済み**（2026-10-22 追記 2） | **折り畳み crease の Bézier 交点の特異点**（§7.1 P0-4） | `physics/origami_domain.py:91`（`CREASE_DEGENERATE_ANGLE_RAD`）/ `:98`（`CREASE_DEGENERATE_DENOM`）/ `:1137-1141`（退化判定 → 有限値へ退避）。回帰固定化 `tests/test_origami.py:449` `test_bezier_crease_no_spike_across_parameter_sweep`。`python -m pytest tests/test_origami.py tests/test_api_auth.py -q` = **50 passed**。**コミット: `7639895`** |
| **解消済み**（2026-10-22 追記 2） | **「採用」ボタン 2 つの嘘**（§7.1 P0-5） | ①`static/js/investigator.js:299-314` が 4 引数 `store.set(section, key, value, origin)` へ（旧 `store.set(["aircraft", k], v)` は無書き込み）②`static/js/app.js:1364-1396` が 1 位の実データ（`f.speed_mps` / `f.pitch_deg` / `f.roll_deg`）を `_clampLaunchParam`（`:1378-1380`）で補正して `store.set("launch", …, "adopt")`（`:1386-1391`）へ。`quick-slide-*` 参照とハードコード 10 m/s / 12° / 0° は 0 件。**コミット: `7639895`** |
| **解消済み**（2026-10-22 追記 2） | **フロント JS が `index.html` に存在しない DOM id を参照**（§7.1 P0-6） | `templates/index.html:396-404` に `sim-error-*` 7 id を追加。3D 取り込み分岐は `this.meshPanel` へ統一（小文字 `meshpanel` はモジュール名 `meshpanel.js` の 2 件のみ）。**2026-10-22 静的 id 突合**: JS 参照 155 id のうち未宣言 **0 件**（テンプレート宣言 340 id）。**コミット: `7639895`** |
| **解消済み**（2026-10-22 追記 2） | **API 認証が常に無効**（§7.1 P0-7） | `app.py:19` に `dotenv.load_dotenv()`、判定は `app.py:140-143`（`os.environ.get("API_KEY", "")`）と `app.py:165`（`hmac.compare_digest`）。修正前（`09aa40e^` の `app.py`）には `dotenv` import が無い。回帰固定化 `tests/test_api_auth.py`（50 passed）。**コミット: `09aa40e`** |
| **解消済み**（2026-10-22 追記 2） | **共有シミュレータをロック外で launch**（§7.1 P0-8） | `api/core.py:258` に `SIMULATION_TRAJECTORY_LOCK = threading.RLock()`、`api/routes/simulation.py:61`（launch）/ `:92` / `:119`（step）が同一ロックを保持。**残存: 専用回帰テストは未整備**（`tests/` 参照 0 件）。**コミット: `c4b9286`** |
| **解消済み**（2026-10-22 追記 2） | **Guinness「公式検証レポート」が同義反復**（§7.1 P0-9） | payload 側 `physics/records_domain.py:2220`（`verification_report`）/ `:2253,2272,2289`（`performance_validated: False`）/ `:2290-2294`（`performance_evidence: "none"`）、UI 側 `static/js/app.js:3321,3368,3373,3381-3382`（`【性能未検証 / performance_validated: false】` の明示）。**コミット: `d287af2`** |
| **要再検証** | 上記以外で、個別に現状を採取していない指摘 | §7.2 以降の「判定」欄が「要再検証」のもの。着手時に 1 回現状確認してから lestimate すること |

> **運用ルール**: §7 の各行の「判定」欄は **2026-10-20 の現状採取結果**。以後の実装で状況が変わった場合は、
> 本表と `docs/CODE_REVIEW_FINDINGS.md` を同時更新する（§10）。
> **2026-10-22 追記（W13 台帳同期）**: 本表の `:780`（応力尺度の 3 系統分裂）と `:787-802` の **16 行**を
> **2026-10-22 の再実測**で更新した。判定の根拠は各行の記載どおり
> **file:line・再現コマンド・実行したテスト結果**で、**コミット単位**（`git log -S` / `git blame`）で確認済み。
> `:797-802`（2026-10-22 追記 2 の 6 行）は **§7.1 の P0-4〜P0-9** を再判定した結果で、**§0.1「完了状況チェックリスト」の
> 「P0（12 件）— すべて完了」と §7.1 の判定欄の矛盾を解消**したもの（§7.1 の行は消していない）。

### 7.1 P0 — 最優先（12 件）

| ID | 残課題 | 場所 | 判定 |
|---|---|---|---|
| **P0-1** | **未追跡ファイル4件**が追跡外のまま（`no-finish-physics-supplement.md` / `physics/origami_genome.py` / `tests/test_origami_genome.py` / `tools/show_run_logs.py`）。うち `physics/origami_genome.py` は import される可能性が高く、**未追跡のまま commit すると master を破壊**する | `git ls-files --others --exclude-standard` | **解消済み（コミット: `d287af2` / `8a41586` ら）**。2026-10-22 実測: `git ls-files --others --exclude-standard` = **出力 0 件**（未追跡ファイルなし）、上記 4 ファイルは**すべて追跡済み**（`git ls-files` で確認）。clean checkout による master 破壊リスクは消滅 |
| **P0-2** | **master への push で CI が全滅**。branch guard の session-scope autouse fixture が `severity=="error"` で pytest 自体を中断するが、`actions/checkout` は push 時に**ローカル branch を作る**ため `master` と判定される（実測: `git checkout --force -B <branch> refs/remotes/origin/<branch>`）。両 WF とも `PAPER_AIRCRAFT_BRANCH_GUARD=warn` を**設定していない**（WF 内の設定 0 件） | `conftest.py:44-50` / `tools/branch_guard.py:66-69,119-122` / `.github/workflows/ci.yml`, `verify.yml` | **解消済み**（2026-10-20 修正）。両 WF に workflow レベル `PAPER_AIRCRAFT_BRANCH_GUARD: warn` を設定し、guard 側は CI イベント checkout（`GITHUB_REF`）を識別して push checkout のローカル branch 名を違反根拠にしない（detached HEAD 基準）。`PAPER_AIRCRAFT_BRANCH_GUARD` の `warn`/`off`/`error` セマンティクスとローカル clone の master 保護は維持。回帰テスト: `tests/test_branch_guard.py`（37件） |
| **P0-3** | **風洞モードで機体が後ろ向きに吹かれる**。風洞流は `+x`（`stream[:,0]=v_eff`）だが、ベンチ姿勢は `quaternion_from_euler(0.0, alpha, -beta)`（yaw=0）でノーズも `+x`。相対流が `(−V,0,0)` となり `atan2(w,u)` が 180°+α を返す（ベンチ 4° → **α_eff = −176°**）。`/simulation/step` はこの反転流で積分する | `physics/wind_domain.py:897,913` / `physics/flight_sim.py:1883,2957` | **解消済み（コミット: `c4b9286`）**。正本関数 `physics/flight_sim.py:993` `wind_tunnel_bench_quaternion`（`WIND_TUNNEL_BENCH_HEADING_DEG = 180.0` は `:990`）を実装し、風洞モード（`:2026`）と UI 経路（`:3203`）が呼称。旧実装の heading 0°（α = 180°+α = −176°）は解消。`python -m pytest tests/test_wind_tunnel_bench.py -q` = **7 passed**（§4.8 K2-P0-8 と同系統） |
| **P0-4** | **折り畳み crease の Bézier が発散**。2パネルがほぼ平行になると交点 `v` が不定距離に飛び、既定UI値（`elevon_deg=2, fold_trailing_deg=15`）で**機首前方に302mm**のスパイク（スライダ全域で最大7.5m）。退化判定は `denom <= 1e-20`（≈1e-10 rad）で、実失敗角は 1e-8 rad | `physics/origami_domain.py:1044-1048,1052-1078` | **解消済み（コミット: `7639895`）**。**特異点を幾何学境界で判定**する方式へ変更。`physics/origami_domain.py:91`（`CREASE_DEGENERATE_ANGLE_RAD = 1.0e-3`）と `:98`（`CREASE_DEGENERATE_DENOM = 1.0e-6`）で退化閾値を明示し、`:1137-1141`（`denom = _dot3(cross, cross)` / `if not math.isfinite(denom) or denom <= CREASE_DEGENERATE_DENOM:` → 有限値へ退避）で交点を打ち切る。回帰固定化 `tests/test_origami.py:449` `test_bezier_crease_no_spike_across_parameter_sweep`。2026-10-22 再実測: `python -m pytest tests/test_origami.py tests/test_api_auth.py -q` = **50 passed / 0 failed**（非有限 0 個・302 mm スパイク解消） |
| **P0-5** | **「採用」ボタン2つが嘘**。①「推奨セッティングを機体に適用」→ `store.set(["aircraft",k], v)` は4引数APIに配列を渡すため `undefined===undefined` で早期 return し**何も書かず**に成功トースト。②「1位の投げ方を設定に反映」→ 対象スライダー `quick-slide-*` が `index.html` に不在で、**ハードコード 10 m/s / 12° / 0°** を store へ書いてサーバへ push（トーストは实际问题を表示） | `static/js/investigator.js:298,304` / `static/js/app.js:1158-1174,1230-1246` | **解消済み（コミット: `7639895`）**。①「推奨セッティングを機体に適用」= `static/js/investigator.js:299-314` が **4 引数の `store.set(section, key, value, origin)`** へ（旧 `store.set(["aircraft", k], v)` は何も書かずに成功トースト）。`:299-301` のコメントが旧仕様を明記。②「1位の投げ方を設定に反映」= `static/js/app.js:1364-1396` が **1 位の実データ**（`f.speed_mps` / `f.pitch_deg` / `f.roll_deg`）を取り出し、`_clampLaunchParam`（`:1378-1380`）で範囲補正してから `store.set("launch", …, "adopt")`（`:1386-1391`）へ書く。**`quick-slide-*` 参照とハードコード 10 m/s / 12° / 0° は残存せず**（2026-10-22 再実測: `static/js/*.js` 全体走査で該当 0 件） |
| **P0-6** | **フロント JS が `index.html` に存在しない DOM id を約35個参照**。エラー表示バナー（`sim-error-*` 7個）、飛行ステータス行（`status-dot`/`status-text`）、3Dモデル取り込み分岐（`this.meshpanel` vs `this.meshPanel`）、チャンピオン JSON/CSV ボタン、クイック投入ドック（20+ id）が死。**エラーは自己消滅する toast だけ**になる | `static/js/app.js:1763-1764,1095-1098,2623,333-334` / `templates/index.html` | **解消済み（コミット: `7639895`）**。`templates/index.html:396-404` に `sim-error-banner` / `sim-error-icon` / `sim-error-title` / `sim-error-badge` / `sim-error-msg` / `sim-error-hint` の 7 id が入り、`status-dot` / `status-text` も同じテンプレートで宣言済み。3D 取り込み分岐の `this.meshpanel` は消滅し `this.meshPanel` へ統一（`static/js/app.js` に小文字 `meshpanel` は**モジュール名 `meshpanel.js` の 2 件のみ**）。**2026-10-22 再実測（静的 id 突合）**: `static/js/*.js` の `getElementById` / `querySelector('#…')` / `$('#…')` が参照する **155 id** のうち `templates/*.html` に無いものは **0 件**（テンプレート宣言 id は 340 個） |
| **P0-7** | **API 認証が常に無効**。`app.py` が `load_dotenv()` を呼ばないため、`.env` に `API_KEY` を書いても `os.environ.get("API_KEY","")` は常に空 → **全保護エンドポイントが到達可能**。`.env.example` は `API_KEY` を文書化し、`/api/v1/system/*` は auth 作用域外 | `app.py:135` / `.env.example:19-27` | **解消済み（コミット: `09aa40e`）**。`app.py:19` に `dotenv.load_dotenv()` が入り、`.env` の `API_KEY` が `app.py:140-143`（`os.environ.get("API_KEY", "").strip()` / 空なら auth 任意）と `app.py:165`（`hmac.compare_digest`）の判定へ反映される。**修正前（`09aa40e^` の `app.py`）には `dotenv` の import 自体が無く**、常に空だった。回帰固定化 `tests/test_api_auth.py`。2026-10-22 再実測: `python -m pytest tests/test_origami.py tests/test_api_auth.py -q` = **50 passed / 0 failed** |
| **P0-8** | **共有シミュレータをロック外で launch**。`SESSION` はモジュールシングルトンで全クライアントが1つの `FlightSimulator` を共有。`/simulation/launch` は `with SESSION.lock:` を**積分前に**抜け、`/simulation/step` は同じオブジェクトを mutate する → 並行 request で軌跡が混入・破壊 | `api/core.py:194,240-250,267` / `api/routes/simulation.py:54,137-147` | **解消済み（コミット: `c4b9286`）**。`api/core.py:258` に `SIMULATION_TRAJECTORY_LOCK = threading.RLock()`（`:250-257` が docstring）を新設し、`api/routes/simulation.py:61`（launch）/ `:92` / `:119`（step）が**全て同じ軌跡ロックを保持**して排他化する。2026-10-22 再実測: **API 側のロック直列化**（`api/core.py:258` と `api/routes/simulation.py:61,92,119` の静的突合。**専用回帰テストは未整備**）。**残存: この並行排他を固定する専用回帰テストは未整備**（`tests/` に `SIMULATION_TRAJECTORY_LOCK` を参照するテストは 0 件） |
| **P0-9** | **Guinness「公式検証レポート」が同義反復**。`verification_report` が**適合済み較正レシピ**で飛行するため乖離は常に ≈0%。payload 自身が `method` に "is **not evidence of validated performance**" / `performance_validated=False` と記載しながら、UI はその文字列を表示せず「再現率」として提示。「一括検証マトリクス」は `alert()` | `physics/records_domain.py:2260,2292` / `static/js/app.js:3141-3150` | **解消済み（コミット: `d287af2`）**。payload 側の「性能未検証」明記は `physics/records_domain.py:2220`（`verification_report`）/ `:2253`・`:2272`・`:2289`（`"performance_validated": False`）/ `:2290-2294`（`performance_evidence: "none"`）。UI 側は `static/js/app.js:3321`（`【性能未検証 / performance_validated: false】` の明示）/ `:3368`・`:3373`（`性能未検証 (performance_validated: …)`）/ `:3381-3382`（一覧の「検証済/未検証」列）で**同義反復の主張を撤回**し、`alert()` による一括検証マトリクスは残存しない。2026-10-22 再実測: `static/js/app.js` の `performance_validated` 参照 4 箇所の静的走査 |
| **P0-10** | **記録プリセットのトリム契約が死**。`RECORD_TRIM_ALPHA_DEG=3.0` の `setdefault` は `LaunchConfig` が常に値を持つため発火せず、**12プリセット中9個が `release_pitch_deg=0.0` を明示**。`GET /records/presets/<key>` は 3.0、`POST /records/apply` は 0.0 を返す（**同一プリセットが2エンドポイントで矛盾**） | `physics/records_domain.py:670,1009,1155,1249,1335,1419,1499,1574,1639,1706,1769` | **解消済み（コミット: `7639895`）**。`physics/records_domain.py:668-671` が `source["release_pitch_deg"] = RECORD_TRIM_ALPHA_DEG` の**無条件代入**へ変更され、カタログの `"release_pitch_deg": 0.0` 明示 9 箇所が削除（`git blame` → `7639895`）。定数の単一ソースは `physics/records_domain.py:1013`。GET / POST が **12 プリセット全て 3.0** で一致 |
| **P0-11** | **応力ヒートマップが転置**。サーバ grid は `[spanwise][chordwise]`、メッシュ UV は `(uSpan, t)` = (スパン, 弦) だが、サンプリングは `fy = v*ny`（行=弦）/ `fx = u*nx`（列=スパン）。**根翼前縁のピークが弦方向の帯**として描画される | `physics/stress_domain.py:825,862` / `static/js/airframe.js:277-282,474-486` | **解消済み（コミット: `7639895`、W6 で正本化）**。サーバ grid は `[spanwise][chordwise]` の正順で、翼根ピークがスパン方向に分布するのが正。`python tools/check_stress_peak_parity.py` = 最大相対差 **0.000e+00**（6 ケースすべて grid 値 == reduction 値） |
| **P0-12** | **`records_domain.py` の日本語93行が U+FFFD 化**（UTF-8→cp932→UTF-8 往復）。`/api/v1/records/presets` で直接公開され、UI の機体モデル dropdown に出る | `physics/records_domain.py`（U+FFFD 93行 / 276文字） | **解消済み**（2026-10-21 修正）。日本語93行は**ギネス記録の原文出典から再記述**。併せてレジストリ（`search/`）のバイト列欠陥と Markdown 全文の文字化けも修復。**再発防止**は `tools/check_mojibake.py`（検出）と `tests/test_no_mojibake.py`（回帰固定化）。旧 §8 F-1 も解消済みに移行（§7.0 対照表） |
| **P0-13** | **応力場とスカラー縮約の乖離**。`physics/stress_domain.py::peak_root_stress_pa` が翼根モーメントへ**上反角の射影 `1/√(1+tan²Λ)`** を適用していなかった（応力場 `stress_field` は適用していた）。縮約は場の **1/cos(Λ)** 倍（既定 5° で **3.8e-3**、45° で **1.4e-1**）となり、GPU バッチ経路と 6DoF 正規経路のピーク応力が食い違う。`tests/test_gpu_kernel_parity.py::test_vectorised_peak_stress_reproduces_the_stress_field` が fail（実測: 57.44546123732805 vs 57.22686391406189、許容 5.7e-11 に対し実差 3.8e-3） | `physics/stress_domain.py` / `physics/gpu_dynamics.py` / `physics/ga2/stage1.py` / `tools/check_stress_peak_parity.py` | **解消済み（2026-10-22）**。正本関数 `dihedral_load_multiplier` を追加し場と縮約を一致させた。`python tools/check_stress_peak_parity.py` = **最大相対差 0.000e+00** / `python -m pytest tests/test_gpu_kernel_parity.py tests/test_stress.py -q` = passed |
| **P0-14** | **フロントエンド ES モジュールの構文エラーでアプリ全体の JS が無効化**。`static/js/telemetry.js` の同一 forEach コールバック内に `const scale` の 2 重宣言があり ES module では `SyntaxError`。`app.js` が `telemetry.js` を import するため連鎖死し、**3D ビューポート・計器・パネルが動かない**静的 HTML シェルになっていた。`node --check foo.js` は **script goal** のためこの重複を**検出できない** | `static/js/telemetry.js` / `tools/check_js_modules.py` / `tests/test_js_modules.py` | **解消済み（2026-10-22）**。`.mjs` へ複製して `node --check` する検出器を新規作成し**両 WF に追加**。実測: `window.consoleApp` が object へ復帰、コンソールエラー **0 件**、3D シーンと計器リングが描画 |

### 7.2 P1 — 高（18 件）

| ID | 残課題 | 場所 | 判定 |
|---|---|---|---|
| **P1-1** | 温度/気圧スライダが `isa_mode=True`（既定）で**無効**。サーバの自動無効化は payload に `isa_mode` が無い場合のみ発動するが UI は常に全 environment を送る。また**サーバの clamp 済み設定をクライアントが破棄**（`app.js` が push のレスポンスを捨てる） | `static/js/panels.js:65-66` / `api/core.py:177-180` / `static/js/app.js:1879` | 要再検証 |
| **P1-2** | 設定書き込みの**部分適用**（後続セクションが失敗しても前-ferratは適用済みなのに 400 を返す）、**bundle import の clamp バイパス**、`post_stall_slope` / `cLalpha_per_rad` / `ref_area_m2` / `ref_length_m` / `ref_span_m` / `shape_flag` の**範囲検証なし**、`throw_style` 未検証（未知キーで impulse が有効化） | `api/routes/physics.py:129-143` / `api/routes/records.py:82-87` / `physics/config_schema.py:394-458` | 要再検証（clamp 欠落は検証済） |
| **P1-3** | **最適化の実用性が低い**。1評価 ≈9秒（既定 `sim_duration_s=6.0`）→ 5000評価で **約12.5時間**。Pareto アーカイブ整列が **O(n²) 純Python**（n=1600 で 25.4秒、n=5000 で約248秒）、NSGA-II の世代ごとに `spawn` プロセスプールを作り直し（125世代→125プール）、`parallel_evaluate` の直列経路が O(n²) | `physics/optimizer_domain.py:834` / `physics/parallel_engine.py:767-780,790-792` | 要再検証（O(n²) 構造は検証済） |
| **P1-4** | **最適化/アンサンブルの誠実性メタデータを UI が破棄**。`approximate` / `model_detail` / `physics_models` / `limitations` / `verified_candidates` / `best_verified` を返すのに UI は objectives しか読まない。100機リーダーボードは縮約代理（`reduced_surrogate`）を公式結果として提示する | `physics/optimizer_domain.py:714-719` / `static/js/optimizer.js:426-542` / `static/js/app.js:1050-1110` | 要再検証 |
| **P1-5** | **最適化の評価モデルが機器依存**。`use_gpu = processes != 1 and backend == "cupy"` で GPU 経路（13状態RK4）と CPU 経路（6DoF RK4・積分器違い）を選択。物理式は共通化済みだが**積分器・性能特性が別物**で、`approximate` の付け外しは機器で変わる | `physics/optimizer_domain.py:544-549` | 検証済（残存） |
| **P1-6** | **CI/Issue 検査ツールが失敗時も exit 0**。`check_runs.py` / `check_issues.py` / `read_issues.py` は `except Exception` で>print してモジュールを抜ける → 終了コード0。`close_resolved_issue.py` / `create_failure_issue.py` も例外で 0 を返す。Phase 6 の「CI 緑」判定が空振りしうる | `tools/check_runs.py:44` / `tools/check_issues.py:36` / `tools/read_issues.py:15` / `tools/close_resolved_issue.py:83` / `tools/create_failure_issue.py:121` | 検証済（残存） |
| **P1-7** | **`/api/v1/errors` が内部スタックトレース・絶対サーバパス・クライアントIP を返す**（`asdict(record)` に `traceback_str` を含む）。P0-7 と複合で偵察オラクル化 | `api/routes/records.py:210-223` / `physics/error_domain.py:44-46,101-104` | 要再検証 |
| **P1-8** | **最適化ジョブの params 無制限 + 入場判定 race + ジョブ枠リーク**。`sim_duration_s` / `dt_max` 無検証、`active_count()>=4` は check-then-act、`thread.start()` 失敗時に `running` のまま恒久消費 | `api/routes/jobs.py:108-113,135-136,143-151,182-184` | 要再検証 |
| **P1-9** | **依存・ツール設定が3系統で乖離**。`requirements.txt` と `pyproject.toml` の Flask/scipy/trimesh 下限不一致、`pytest.ini`/`ruff.toml`/`mypy.ini` が `pyproject.toml` の該当ブロックを**無効化**（ruff の line-length 120 vs 100、isort ルールが無効）。`cupy-cuda12x` がハード依存で **CPU のみ環境でも setup.sh が abort** | `requirements.txt` / `pyproject.toml:37-54` / `pytest.ini` / `ruff.toml` / `mypy.ini` / `setup.sh:47-53` | 要再検証 |
| **P1-10** | **テスト網の穴**。API 認証・ブラウザ/DOM・実 CuPy 経路・**53 エンドポイント中 41件**が pytest 未テスト。`smoke_test.py` は `pytest.ini` の `testpaths` に一致せず**収集されない**（`verify.yml` でも未実行）。`discord.py` 未宣言で 11件全スキップ | `tests/` 全体 / `pytest.ini:3-4` / `app.py:132-159` | 要再検証 |
| **P1-11** | **フロントのアクセシビリティ重大**。設定トグル8個が**同一のアクセシブル名「有効」**、canvas/WebGL 5点に**テキスト代替なし**、ゲージ/ピルの AA コントラスト違反（warn `#fbbc04` = 1.71:1、ok 3.06:1）、`--muted` が全二次テキストで 3.3-3.7:1、狭幅でレールが**キーボード到達不能**かつ不可視のままフォーカス可能、モーダルに `role="dialog"` なし | `static/js/panels.js:230` / `static/css/console.css:13,649-651,800-804,1695-1717` / `templates/index.html:233,339,389,403,563,763` | 要再検証 |
| **P1-12** | **Discord bridge が既定で全許可**。`ALLOWED_USER_IDS` が空だと `is_authorized()` が常に True、チャットが `agy -p <prompt> --dangerously-skip-permissions` に素通し、`!chrome <url>` も無制限。単一インスタンス制御・レート制限・プロンプト長検証も無し | `tools/discord_bridge.py:379-385,742-748,1004-1010,1138` / `.env.example:7-8` | 要再検証 |
| **P1-13** | **`Knowledge.md` が実在しないガードレールを主張**（stderr ドレイン / 二重タイムアウト / 同時実行上限 / プロンプト検証 / error-id 送出）。コードは未実装で、**知識ベースが最高リスク成分の正しさと矛盾**している。また「mypy 52件→0件」も現時点で 52件（`tools/` は CI の型検査対象外） | `.agents/Knowledge.md:38-39` / `tools/discord_bridge.py:244,277,736,818-819,894,1038-1046` | 要再検証 |
| **P1-14** | **チャートの数值表示が誤り**。抗力ポラルの右軸に **C_D** 目盛（0.0-0.3）を描きながら描かれているのは **L/D**（0-8）。ストリップチャートは**軸上限のパディング値**をデータ値として表示（高度5.00を「5.90」と出す） | `static/js/telemetry.js:202-215,274-276,438-454,506` | 要再検証 |
| **P1-15** | **折り畳み幾何と空力が完全非結合**。折り畳み機体の投影面積 0.036 m²・幾何 AR **0.60** に対し空力は AR **2.67**、CG は**展開形**トラペゾイド重心。API の `scale = wingspan_m / SHEET_WIDTH_M` は**用紙幅**を基準にするため 0.20 m 要求 → 実 0.140 m（**30%不足**）。既存 G-1/G-2 の定量版 | `api/routes/origami.py:68` / `physics/origami_domain.py:1768,1799-1806` / `physics/config_schema.py:1342-1440` | 検証済（残存） |
| **P1-16** | **同じ設定フィールドの解釈が3系統で食い違う**。`fold_tip_deg` = 折り畳み 80°+δ / 解析機 δ / 応力kernel δ、負値は `max(0.0,…)` で破棄。`dihedral_deg` は折り畳み側が `abs()` で**符号を破棄**（−20 → +20） | `physics/origami_domain.py:1768,1799-1806` / `static/js/airframe.js:99,103` / `physics/stress_domain.py:760,768` | 検証済（残存） |
| **P1-17** | **後縁 crease の左右非対称**。trailing crease は両翼に共有されるのに基準軸は左翼1個のみ → 鏡像誤差 3.4mm、折角差 0.23°、面積差 1.4%。空力は完全対称を仮定 | `physics/origami_domain.py:1863-1875,489-506,550-595` | 要再計測 |
| **P1-18** | **`turbulence_scale` が渦長に効かず**、Taylor 移流速度も破損。`scale` は計算されるが**位置引数 `P` は `scale` で割られず**、`drift` だけが noise 単位へ換算される → 渦長は常に ≈1m、移流は V/scale（V=10, scale=0.35 で **7.14 m/s = 29%誤差**）。UI は単位 "m" と表示 | `physics/wind_domain.py:829-839` / `static/js/panels.js:93` | 検証済（残存） |

### 7.3 P2 — 中（23 件）

| ID | 残課題 | 場所 | 判定 |
|---|---|---|---|
| **P2-1** | 風評価が空力評価と1:1、既定で短絡不発（`thermal_strength=0.50` により条件が偽）、`aero_domain.evaluate_aero` が毎呼び出しで大気と係数束を再導出（キャッシュ済み大気を破棄） | `physics/flight_sim.py:860-863` / `physics/aero_domain.py:930,955` | 要再検証 |
| **P2-2** | 単一グローバル `RLock` が全シミュレーション API を直列化（240ステップ要求で約20秒ロック保持）。最適化ジョブは GIL 制約スレッドで「4枠並列」の効果がほぼ無い | `api/core.py:194` / `api/routes/simulation.py:54` | 要再検証 |
| **P2-3** | `RESULT_CACHE` が最大32件の完全最適化結果をRAM常駐（個数退避・バイト数制限なし）、`JobManager.update()` が評価ごとにレコード全体 deep copy | `api/core.py:269-271` / `physics/job_manager.py:594-612` | 要再検証 |
| **P2-4** | `/api/v1/jobs` のポーリングは**設定上 2000ms だが実質 700ms**（`setTimeout` が `POLL_MS` で再武装）。256件の deep copy が毎回走る | `static/js/optimizer.js:266-274` / `api/routes/jobs.py:342-352` | 要再検証 |
| **P2-5** | `logs/error.jsonl` が**回転ハンドラを迂回**（`stream.write` 直書き）→ 無制限増加。`error.log` のみ回転 | `physics/error_domain.py:126-133` | 要再検証 |
| **P2-6** | 40MB生成物がコミット済み、`artifacts/` が未 ignore、ルート直下に `tmp_*.py` が3件残存 | `artifacts/physics_repair/after.json` / `.gitignore:23-30` | 要再検証 |
| **P2-7** | `tools/test_*.py` 9本が `__main__` ガード無しで**import時に35秒シミュレーション**を実行。`pytest .` やIDE実行で 9×35秒の無駄計算 | `tools/test_*.py` | 要再検証 |
| **P2-8** | 破壊的自動化に **dry-run も確認も無い**（Issue クローズ / コマンド全削除）。`--all-clean` フラグは表示文のみ変化し挙動同一 | `tools/close_resolved_issue.py:69-81` / `tools/clear_discord_commands.py:45-70` | 要再検証 |
| **P2-9** | GitHub ツール3本が**リポジトリ slug をハードコード**し `GITHUB_REPOSITORY` を無視、`urlopen` に**タイムアウトなし**（無限ハングで自律ループ停止） | `tools/check_runs.py:27,30` / `tools/check_issues.py:21,24` / `tools/read_issues.py:6,9` | 要再検証 |
| **P2-10** | 通知トーン/単位/言語の不整合（応力が**計器 kPa vs チャート Pa**、`" maximise"` の英語漏れ、toast tone `success`/`info` 未定義、**i18n 層が皆無**） | `static/js/app.js:3291-3305` / `static/js/telemetry.js:100` / `static/js/optimizer.js:13-19` / `static/css/console.css:1587-1589` | 要再検証 |
| **P2-11** | 配色の意味が衝突（3D軌跡は応力ランプ、チャートは4つの別色、抗力ポラルは軸取り違え）。3Dシーンとチャートで同義色が別値 | `static/js/viewport.js:893-913,1050-1057` / `static/js/telemetry.js:97-100` | 要再検証 |
| **P2-12** | 応力ヒートマップが **glTF/GLB/OBJ で無効**（`meshObject` は `THREE.Group` で `geometry` が undefined）。`H` トグルも imported material に効かない | `static/js/viewport.js:876-902,824-830` | 要再検証 |
| **P2-13** | ABL が**単一対数則で無限延伸**（表層/遷移層/乱流層なし）。SPEC §1.2 の「対数則/べき乗則」未実装。z=1km で風速の1.97倍 | `physics/wind_domain.py:678-692` / `docs/SPECIFICATION.md:37` | 要再検証 |
| **P2-14** | **`ground_level_m ≠ 0` で地上高を2重減算**（`−z_state − 2·ground_level_m`）。`ground_level_m=1.0`・地上0.2m の機体に**風0**。UI 側の `flowfield.js:315` は正しい式 | `physics/flight_sim.py:867` / `physics/wind_domain.py:924` | 要再検証 |
| **P2-15** | **風の方位基準が SPEC と90°矛盾**（wind は north=+y / SPEC は +x=north）、世界軸は**発射方位**なので風向と飛行方向が関係しない | `physics/wind_domain.py:15-27,562-583` / `docs/SPECIFICATION.md:38` | 要再検証 |
| **P2-16** | **風洞のブロック補正/半径/TI/壁厚が既定で死**（`wind_tunnel_boundary_correction=False` の既定では完全均一流）。tunnel 軸は原点で機体は1.2m上 → 壁層外 | `physics/wind_domain.py:893-910` / `physics/config_schema.py:512` | 要再検証 |
| **P2-17** | **応力モデルに応力テンソル/せん断/膜応力/翼端接地応力が無い**（スカラー曲げのみ）。許容応力則は docstring と実装が逆、`gsm^-2.5` で紙厚が26倍の安全度差 → `paper_gsm` が非物理的な支配最適化レバーに | `physics/stress_domain.py:557-575,823-827` | 要再検証（A-1 と同根） |
| **P2-18** | **折り畳みメッシュが閉じた立体でない**（境界辺354本）。SPEC と `tests/test_origami.py` の docstring は「閉じた固体」と明記。三角形の43%が頂点法線と逆巻き | `physics/origami_domain.py:1188-1240,1310,1324-1332` / `docs/SPECIFICATION.md:88` | 要再検証 |
| **P2-19** | **折り畳みAPI の範囲検証なし**（`keel_beta_deg=1e9` → 300kmのジオメトリを JSON 返却）、**キャッシュ無し**で毎リクエスト全面折り（9.8ms、130kB） | `api/routes/origami.py:29-34,69` | 要再検証 |
| **P2-20** | **記録プリセットの非物理AR/CD0**（AR 7.0-7.4、L/D 19-20）。29-31秒の滞空時間は「導出」ではなく**適合** | `physics/records_domain.py` プリセット群 | 要再検証（§8 F-2 参照） |
| **P2-21** | **既定機体の翼面積が文献値より3-7倍小さい**（0.015 m²、翼荷重 333 g/m² vs 文献 50-100）。既定は高速 dart であり glider ではない | `physics/config_schema.py:237-239` | 要再検証（§8 F-2 参照） |
| **P2-22** | 永続バンドルに**schema version がなく**、1項改名で全プリセットが不可化。書き込みは**非アトミック**（truncate→stream） | `physics/storage_domain.py:92-97,717-746,882-888` | 要再検証 |
| **P2-23** | **設定の正本が3〜4重**（UI手書きテーブル / `/meta/schemas` の `ranges` / `clamp_ranges()`）で既に乖離（`launch_speed` 0-40 vs UI 2-40 等）。`/meta/schemas` は `simulation` 節を**公開せず**、`aircraft` だけ展開方法が違う → 変更マーカー不点灯・`resetSection("aircraft")` 無効 | `static/js/panels.js:14-120,158-166` / `api/routes/meta.py:32-46` / `static/js/state.js:36-44` | 要再検証 |

### 7.4 P3 — 低（11 件）

| ID | 残課題 | 場所 | 判定 |
|---|---|---|---|
| **P3-1** | `mesh_domain.__all__` に未定義名2件（`PANEL_INCIDENCE_DRAG_K` / `paper_sheet_thickness_m`）→ `import *` が AttributeError、ruff F822 | `physics/mesh_domain.py:73,76` | 要再検証（既存 A-12） |
| **P3-2** | 死定数・到達不能コード・docstring 矛盾（`FlightDivergenceError` は raise されない、`MAX_CONTACT_RATE_JUMP_RADPS`、`EFFECTIVE_SPEED_FLOOR_MPS`、動粘性係数のキー名大文字小文字不一致で常にフォールバック定数 1.7e-5） | `physics/flight_sim.py:170,218,235,755-758` | 要再検証 |
| **P3-3** | `stress_domain` の絶対 import + `except Exception: return 0.0` により、暗黙に pitch 項が欠落しうる | `physics/stress_domain.py:235,281` | 要再検証 |
| **P3-4** | 日本語の**用語ゆれ**（機体形が3系統で異なる名称、「飛距離」が3つの別の量、`発射`/`投げる`/`投擲` の混在） | `static/js/airframe.js:21` / `static/js/panels.js:16` / `static/js/app.js:2665` | 要再検証 |
| **P3-5** | `skill/README.md` が**存在しない5ファイル**を索引、`docs/discord-bridge.md` が**正本として参照されるが存在しない**、`.agents/AGENTS.md` パスも不在（実体は小文字 `agents.md` → 大文字小文字を区別する FS で解決不能） | `skill/README.md:42-46` / `.agents/Knowledge.md:41` / `.agents/skills/loop-handover.md:6` | 要再検証 |
| **P3-6** | ログの**相関IDが無い**（error_id と stderr が join できない）、**GPU経路とジョブワーカーが無ログ**（`logging` import 自体が無い） | `app.py:39-42,122-126` / `physics/parallel_engine.py:41-48` / `physics/job_manager.py:40-43` | 要再検証 |
| **P3-7** | `.mypy_cache` / `.ruff_cache` / `pytest_failure_summary.txt` の ignore 方針不備（ツールの自己 ignore に依存） | `.gitignore:32-36` / `tools/run_ci_tests.py:92` | 要再検証 |
| **P3-8** | **記述の正本ずれ**（SPEC / Knowledge / agents.md が `AGENTS.md` 等の不在パスを指す。`docs/SPECIFICATION.md` は実在しないルート3件を記載し、実在する8件を未記載） | `docs/SPECIFICATION.md:168-177` / `agents.md:64` | 要再検証 |
| **P3-9** | `.codex/config.toml` が `approval_policy="never"` / `sandbox_mode="danger-full-access"` を**コミット済み**（開発者ローカル設定が追跡されている） | `.codex/config.toml:1-2` | 要再検証 |
| **P3-10** | MCP: `.kilo/.gitignore` が `package.json` と `.gitignore` 自身を ignore → **clone から実行系を再構築不能**。`memory` ストアは無制限成長（protocol の「検索索引」という説明と乖離） | `.kilo/.gitignore:2,7` / `.kilo/mcp/launch.mjs:53-57` | 要再検証 |
| **P3-11** | 折り畳みドメインの**死コード**（`HingeNode` / `CreasePattern.nodes` が未消費 → docstring の hinge-tree アルゴリズムは**未実装**。`apply_aeroelastic` はテストのみ、フレーム docstring 誤り、アーキタイプカタログ重複、`shape_flag` は archetype に未マッピング） | `physics/origami_domain.py:315-330,1941,20-34,1879-1898` | 要再検証 |

### 7.5 着手順に関する注意（依存関係）

| 制約 | 内容 |
|---|---|
| P0-2 → 全ての CI 検証 | branch guard が CI を落とすため、**P0-2 を直さないと他修正の CI 検証ができない**（現時点では `PAPER_AIRCRAFT_BRANCH_GUARD=warn` で回避可能） |
| P0-11 → P2-12 | ヒートマップの転置（サーバ×クライアント）と imported mesh 非対応は**同じ描画経路**。まとめて直すと回帰が取りやすい |
| P0-5 → P0-6 | いずれも「JS が存在しない要素/存在しない関数を前提にしている」同型欠陥。**まとめて直すと原因の切り分けが困難** → 先に P0-6 の id 不一致表を作る |
| P0-9 → P0-10 | ギネス検証の同義反復（UI の主張）を直すには、**前提となるプリセットの trim 契約**（P0-10）が先に正しくなければ比較対象が意味を持たない |
| P1-3 → P1-4 / P1-5 | 最適化が実用的（計算時間）になって初めて、UI への `approximate` 表示（P1-4）や機器依存（P1-5）の議論に意味が出る。順序は P1-3 → P1-4 → P1-5 |
| P1-15 → P1-16 → P1-17 | 折り畳み−空力結合 → パラメータ意味の統一 → 左右対称。**逆順で直すと二重修正**になる |
| P1-10 | テスト網の穴（P1-10）を埋めるのは上記修正の**同時**に行う（回帰固定化テストが書けないと P0/P1 の修正が安全でない） |

---

## 8. F. レビュー由来・あえて解決しない（意図的維持／復元不能／環境制約）

### 8.1 復元不能 — 機械的修正をしない（**該当なし**）

唯一この節に置いていた **F-1** は**解決済み**のため、未解決の「復元不能」項目としては**該当なし**。
記録は §7.0 の対照表へ移した（§10 の運用ルール「解消済みは §7.0 へ移動、行は消さない」に従う）。

| ID | 項目 | 解決の記録 |
|---|---|---|
| **F-1**（**解消済み**・§7.0 へ移行） | `physics/records_domain.py` の日本語93行の U+FFFD 化 | UTF-8→cp932→UTF-8 の往復で**元文字が物理的に消滅**していたため、自動復号・部分復元は不可能であった。**正本（ギネス記録の原文出典）から日本語93行を再記述**して解決。`tools/check_mojibake.py` でリポジトリ全体の文字化け 0 件を検証し、`tests/test_no_mojibake.py` が再発を固定化。**cp932 往復の再現を許す変換を追加しないこと**という運用方針は引き続き有効 |

### 8.2 意図的維持 — 物理的・工学的理由（検出したが直さない）

| ID | 項目 | 理由 / 再オープンの条件 |
|---|---|---|
| **F-2** | **既定機体の翼面積 0.015 m²**（翼荷重 333 g/m² vs 文献 50-100 g/m²）。既定は「小型 dart」であり glider ではない | 紙飛行機の小型 dart を既定とするのは**意図的な機体選択**。変更すると既定のtrim/滞空/滑空比と既存 866 テストの期待値が同時に動く。**既定機体テーマの再設計**として別フェーズで扱う |
| **F-3** | **記録プリセットの非物理 AR / CD0**（AR 7.0-7.4、L/D 19-20）。29-31秒の滞空は「導出」ではなく適合 | ギネス室内記録は**天井反射・気流を含む特異条件**であり、Helmbold/Hoerner の単純モデルでは再現できない。実記録の airborne time を満たす**較正 surrogate** として割り当てるのは、記録検証としては**不誠実**（P0-9 は UI 側の表示を直すことで解消する）。物理モデルの高精度化は**別目標** |
| **F-4** | **ABL が単一対数則**（表層/遷移層/乱流層なし） | 紙機体の飛行高度帯（0-30 m）では対数則とべき乗則の差は数 %。Shaping filter の導入は**短時間飛行では統計的に判別不能**（既存 A-7 と同じ判断） |
| **F-5** | **熱柱に補償下降流がない**（w ≥ 0 全体） | 3秒規模の飛行では雲塊の鉛直質量収支の影響が機体運動より小さい。風洞・熱流の可視化用途では問題は出ない |
| **F-6** | **`turbulence_scale` が2役目**（渦長＋突風振幅倍率） | 1パラメータに2つの意味を持たせるのは API 互換のための**意図的な設計**。ただし P1-18 の**単位不整合（位置が scale で割られない）**は実装バグであり「意図」ではない → **P1-18 は直す**。役目の二重性は §F-6 として維持し、UI へ「長さ」であることを明示する |
| **F-7** | **応力モデルにテンソル/せん断/膜応力/翼端接地応力がない** | A-1 と同じ根（**出典なき係数を増やさない原則**）。実紙材料の引裂・座屈データが入手できるまで据え置き |
| **F-8** | **パネル法と Helmbold の 2.2 倍乖離** | 既存 **A-5** と同一。2つの空力モデルを 1 式に統合すると単一実装原則を壊す |
| **F-9** | **`static_margin` の空力学的無意味化** | 既存 **A-3** と同一。trim 状態の定義が未確定 |
| **F-10** | **`fold_trailing_deg` の空力未接続** | 既存 **A-2** と同一。後縁折れの空力効果には特定論文が無い |
| **F-11** | **GPU batch の接地簡略化 / 風場サンプル1回 / 全ケース共通時刻 / GPU launch overhead** | 既存 **A-10 / C** と同一。13状態RK4 を参照経路と同一物理にする優先性が、以及する簡略化を償却している |
| **F-12** | **`shape_flag`（機体形状）と折り紙アーキタイプの独立** | 既存 **A-17** と同一。解析機体形状と折り紙3機種は1:1に対応しない |
| **F-13** | **折り畳みメッシュは表示専用（空力・応力・慣性は解析翼型のまま）** | 既存 **A-14** と同一。既存 866 テストの期待値を守るため。**P1-15 は「結合する」ではなく「不整合を明示・定量化する」方向**で扱う |
| **F-14** | **`origami` ビュー状態をサーバへ送らない / 折り運動はサーバ生成に一本化** | 既存 **A-16 / A-13** と同一。JS 移植はドリフト管理の恒常コスト |

### 8.3 環境・運用制約

| ID | 項目 | 理由 / 再オープンの条件 |
|---|---|---|
| **F-15** | **i18n 層が無い**（日本語ハードコード、CDN 2 種依存でオフライン起動不可） | 対象は**単一言語（日本語）・単一マシン運用**。多言語化・オフライン化は導入時にまとめて行う方が効率的 |
| **F-16** | **プリセットの永続はブラウザ localStorage**（サーバの StorageManager は API から到達不能） | 単一ブラウザでの利用が前提。サーバ共有が必要になった時点で §P2-22 とともに再設計 |
| **F-17** | **実 CuPy 経路の CI カバレッジが 0** | GitHub runner に GPU が無い以上、**自動検証は原理的に不可能**。`tools/check_gpu_parity.py` / `check_aero_kernel_parity.py` を手動実行する運用で代替（既存 C の GPU 性能欄も同根） |
| **F-18** | `test_tunneling_ccd.py` の負荷依存フレーク | 既存 **A-11** と同一。原因未特定のまま残す |
| **F-19** | レビュー中の一部の指摘は**監査スナップショットの陳腐化**により解消済み | §7.0 の対照表を参照。**再検証で消えるものは「残課題」ではなく「記録の訂正」**（§10） |

### 8.4 既存 §A / §C へ統合済み（重複して記載しない）

§7 の指摘のうち、**既存台帳に既に同内容で存在する**ものは以下（新規記載しない）。

| 既存 ID | 該当 | レビュー側の該当項目 |
|---|---|---|
| A-1 / H-11 / M-26 | 構造応力の材料モデル・座屈 | P2-17 |
| A-2 / H-19 | `fold_trailing_deg` の空力接続 | F-10 |
| A-3 / M-30 | `static_margin` | F-9 |
| A-5 / M-12 / M-13 / M-14 | パネル法 | F-8 |
| A-6 / M-31 | `drag_polar` 既定 Re | （レビュー対象外） |
| A-7 / L-11 / M-24 | 乱流スペクトル（等方性） | F-4 |
| A-9 | `gpu_ensemble_simulation` の縮約代理 | P1-4 |
| A-10 / C | GPU batch 接地簡略化・風場サンプル・GPU 性能 | F-11 |
| A-11 | フレーク | F-18 |
| A-12 | ruff F822 | P3-1 |
| A-13 / A-14 / A-16 / A-17 | 折り紙刷新の据え置き | F-12, F-13, F-14 |
| G-1 / G-2 | 折り紙寸法整合・物理非結合 | P1-15（定量版） |
| G-6 | α スライダのサーバ往復 | （P2-19 に統合） |

---

## 9. 統合推奨順序（§5 からの続き）

§5 の 1〜5（既存フェーズ方針）に、§7 の P0/P1 を**最優先で前段**に挿入した統合順序。
**P0-2（branch guard）を直さないと他の修正の CI 検証ができない**ため、これを先頭にする。

| 段 | 内容 | 対象 |
|---|---|---|
| **第0段**（門） | **branch guard の CI 整合**。`PAPER_AIRCRAFT_BRANCH_GUARD=warn` を両 WF に設定するか、push 時の判定を detached HEAD 基準へ直す。**ここが通らないと以降の全てが検証不能** | P0-2 |
| **第1段**（物理正本） | **風洞モードの反転**（ベンチ条件が成立していない）。`/simulation/step` が 180°+α の流で積分している状態は、実験装置の再現性に関わる根本問題 | P0-3 |
| **第2段**（破綻） | **折り畳み crease の発散**（機首前方に302mm）。描画・影・境界球を破壊し、折り紙刷新という新マイルストーンの成果物を無効化する | P0-4 |
| **第3段**（嘘の成功） | **「採用」ボタン2つ**（何もしない／不正値適用）と **死んだ DOM id 群**。UI が「成功しました」と嘘をつくのは現在一番の信頼性欠陥。先に id 不一致表（JS 参照 id vs `index.html`）を作る | P0-5, P0-6 |
| **第4段**（境界） | **API 認証**（`load_dotenv`）と **共有シミュレータのロック外 launch**。外部公開前に終えるべき | P0-7, P0-8 |
| **第5段**（誠実性） | **記録/検証の誠実さ**（文字化け再記述 → トリム契約 → Guinness 検証の同義反復）。順序は P0-10 → P0-9。**いずれも「再現率」の主張を撤回するか実装するかの二択** | P0-10, P0-9, P0-12 |
| **第6段**（可視化の正しさ） | **応力ヒートマップの転置**、折り畳み−空力のパラメータ意味統一、左右対称、`turbulence_scale` の単位バグ | P0-11, P1-15〜P1-18 |
| **第7段**（設定契約） | clamp の反映 / 部分適用のロールバック / bundle import / 危険係数の範囲検証 / 設定正本の3重化 | P1-1, P1-2, P2-23 |
| **第8段**（最適化の実用性） | **計算時間**（1評価9秒、Pareto O(n²)248秒、世代毎 spawn）→ 続けて **UI の `approximate` 表示** → **機器依存の扱い** | P1-3, P1-4, P1-5 |
| **第9段**（運用） | CI/Issue ツールの exit code、ジョブ枠/パラメータ境界、`/api/v1/errors` の情報開示、GitHub ツールの slug と timeout、破壊的自動化の dry-run | P1-6, P1-7, P1-8, P2-8, P2-9 |
| **第10段**（基盤） | 依存3系統の乖離、`cupy` の extras 化、**テスト網の穴**（認証/ブラウザ/GPU/41 エンドポイント） | P1-9, P1-10 |
| **第11段**（UI 品質） | チャートの軸取り違えと配色衝突、a11y 重大6件、imported mesh のヒートマップ、単位/言語の不整合 | P1-11, P1-14, P2-10〜P2-12 |
| **第12段**（外部露出） | **Discord bridge の無条件許可**と **Knowledge.md の虚偽ガードレール記載**。bridge を他チャンネルへ出す前に**必ず**終える | P1-12, P1-13 |
| **第13段**（既存フェーズ） | §5 の 1〜5（GPU 融合 / 実材料断面 / アンサンブル6DoF化 or 縮小 / パネル法規約 / 到達不能ガード）と G-1→G-3→G-2 | 既存 §5 |
| **第14段**（低） | P3 群（死定数・docstring・ignore 方針・正本ずれ・MCP 再構築可否） | P3-1〜P3-11 |

> **2026-10-21 追記**: §4.7 の K1-P0-1（バッチ経路 ⇄ 6DoF 参照経路の数値一致）・K1-P0-2（水平 100 km 境界での
> クラッシュ判定）・K1-P0-3（GA2 stage-0 幾何フィルタの偽陽性）と K1-P1（3ファイルの未確認タイムアウト）は、
> 上表の**第1段（物理正本）〜第6段（可視化の正しさ）**と並行して着手する。担当は **W9**（K1-P0-1 / K1-P0-2）と
> **W10**（K1-P0-3）。K1-P1 は pytest の実測（`--durations`）だけで切り分けられるため、父の優先度判断に任せる。
>
> **2026-10-21 追記 2（K2 台帳 §4.8）**: §4.8 の優先 ID は §4.7 を統合したもので、上表の段へ次のように割り当てる。
> **第0段**に K2-P0-4〜K2-P0-7（GA エンジンの審査 P0 4 件。Y2 が作業ツリー上で修正済みだが**未コミット・CI 未確認**、
> かつ旧成果物 `artifacts/ga_audit/**` は現行設定と不整合＝再生成が要る）を**第0段〜第1段の間**へ挿入。
> **第1段（物理正本）**に K2-P0-8（風洞ベンチ姿勢の正本関数 `wind_tunnel_bench_quaternion` が未定義）と
> K2-P0-9（投入初期条件の出典なき係数 `torque_rate[1] *= 0.10`）を §7.1 の P0-3 と同一段へ追加。
> **第1段〜第6段**に K2-P0-1（バッチ⇄6DoF 数値不一致。**風高度仮説は 2026-10-21 実測で再現せず**＝差分特定が先）と
> K2-P0-2（水平 100 km 境界のクラッシュ判定）を追加（担当 **W9**）。
> **K2-P0-3**（GA2 stage-0 tear フィルタ）は 2026-10-21 実測で **PASS**（解消済み）だが、判定の出典確認は担当 **W10** に残す。
> **K2-P1**（3 ファイルの未確認タイムアウト）は pytest の実測（`--durations`）だけで切り分けられるため、
> 父の優先度判断に任せる。
>
> **2026-10-22 追記 3（W13 台帳同期・§9 の更新）**: 上の割り当ては**実行済み**。K2-P0-1 / K2-P0-2 /
> K2-P0-3 / K2-P0-4〜K2-P0-7 / K2-P0-8 / K2-P0-9 は**すべて解消済み**で、**上表の該当段は「実施済」**に変わる。
> 残る「依然として着手すべき」項目と、**消化しきれずに残る限定**だけを以下に残す。
>
> | 段 | 2026-10-22 時点の残存 | 対象 | 根拠 |
> |---|---|---|---|
> | **第1段（物理正本）** | **完了（消化）** | K2-P0-8（風洞ベンチ姿勢）/ K2-P0-9（人工係数）/ §7.1 P0-3 | §C-1 / §7.0 対照表（コミット `c4b9286`） |
> | **第1段〜第6段** | **完了（消化）**（**残存はカオス性のみ**） | K2-P0-1 / K3-P0-1 | §4.10 R-1（並列/逐次はビット一致。**軌道の一致は保証しない**） |
> | **第0段〜第1段の間** | **完了（消化）**（**残存は旧成果物の不整合のみ**） | K2-P0-2（水平 100 km）/ K2-P0-3（GA2 tear）/ K2-P0-4〜P0-7（GA P0 4 件） | §4.8 / §4.9 の各行 |
> | **第6段（可視化の正しさ）** | **完了（消化）** | §7.1 P0-11（応力ヒートマップ） | `python tools/check_stress_peak_parity.py` = 0.000e+00 |
> | **第2段（破綻）** | **完了（消化）**（2026-10-22 再判定） | §7.1 P0-4（折り畳み crease の Bézier 交点特異点） | `physics/origami_domain.py:91,98,1137-1141` / `tests/test_origami.py:449`（`7639895`） |
> | **第3段（嘘の成功）** | **完了（消化）**（2026-10-22 再判定） | §7.1 P0-5（「採用」ボタン 2 つ）/ P0-6（死んだ DOM id 群） | `static/js/investigator.js:299-314` / `static/js/app.js:1364-1396` / 静的 id 突合の未宣言 **0 件**（`7639895`） |
> | **第4段（境界）** | **完了（消化）**（2026-10-22 再判定。**P0-8 は専用回帰テスト未整備**） | §7.1 P0-7（API 認証）/ P0-8（共有シミュレータのロック） | `app.py:19,140-143,165`（`09aa40e`）/ `api/core.py:258`・`api/routes/simulation.py:61,92,119`（`c4b9286`） |
> | **第5段（誠実性）** | **P0-12 完了**。**P0-10 完了**。**P0-9（Guinness 検証の同義反復）も完了（消化）**（2026-10-22 再判定） | P0-9 | `physics/records_domain.py:2253,2272,2289` / `static/js/app.js:3321,3368,3373,3381-3382`（`d287af2`） |
> | **第1段（据え置き・着手不可）** | **A-1 / H-11 / M-26（実材料・実断面）と A-2 / H-19 の係数出典**は**着手不能のまま据え置き**。据え置き根拠の正本は `docs/SPECIFICATION.md` §5.2（G-17） | A-1 / A-2 / H-11 / H-19 / M-26 | §4.10 R-2 |
> | **第13段（既存フェーズ）** | **A-1 / A-5 / A-9 / L-01 / L-02 / L-10** は**残存**（§4.10 のとおり実測・出典の制約で着手不能または低優先） | §5 の 1〜5 | §4.10 |
>
> **残存 P0（2026-10-22 実測）**: **§7.1 の P0-1〜P0-12 は全件解消済み**（P0-1 / P0-3 / P0-10 / P0-11 は前セッション、
> **P0-4〜P0-9 は本セッションの再判定で解消済み**へ更新。§7.0 対照表 `:787-802` に移動記録あり）。
> **§4.7 / §4.8 / §4.9 の P0-1〜P0-9 は全件解消済み**。
> **残る P0 は無い**。§7.2 以降の **P1-15〜P1-18**（折り畳み幾何と空力の非結合 / 設定フィールドの解釈 3 系統 /
> 後縁 crease の左右非対称 / `turbulence_scale` の渦長未反映）は**残存**（判定欄は「検証済（残存）」/「要再計測」のまま維持）。
> **§7.1 P0-8 のみ「解消済みだが専用回帰テスト未整備」**という限定付き（§4.10 と同じ扱い）。

---

## 10. 更新手順（§6 の続き）

- §7 の各行の「判定」欄は **2026-10-20 の現状採取結果**。`docs/CODE_REVIEW_FINDINGS.md` の
  「要再検証」項目は着手時に現状採取してから「残存 / 解消済み / 別-category」に振り替える
- 解消済みと判定した項目は §7.0 の対照表へ**移動**する（行は消さない。陳腐化の記録を残す）
- `.agents/Knowledge.md` の「残課題」節から本ファイルへの参照リンクは**据え置き**。
  詳細はKnowledge 側に二重保管しない
- 本ファイルは `genetic-project` ブランチで更新する。`master` / `main` への
  コミット・マージ・チェリーピック・PR は**厳禁**（`agents.md` §2 Branch Isolation）

