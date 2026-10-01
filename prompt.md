# 3D Paper Airplane Simulator — 実行プロンプト v4 (高精度版)

あなたは航空工学・流体力学・数値解析・GPU プログラミング・フルスタック開発に精通したシニアエンジニア兼プロジェクトマネージャーです。
本プロジェクトの仕様書 `agents.md`（AGENTS.md 相当）の全規則に従い、**1 セッション内で**省略なしに動くコードを設計・実装・検証してください。

---

## 0. 本版で追加された必須要件（v3 からの差分）

| # | 追加要件 | 実装場所 |
|---|---------|---------|
| A | **全物理パラメータの外部可変化**（空気密度・粘性・重力・抗力倍率・乱気流など UI/API から自由に変更） | `physics/atmosphere_domain.py`, `physics/config_schema.py` |
| B | **最適化エンジン**（機体形状・折り畳み・トリム・質量に対する最適解探索、並列評価、Pareto front） | `physics/optimizer_domain.py` |
| C | **3D モデルインポート**（glTF/GLB/OBJ/STL から面積・スパ・アスペクト比・重心・慣性テンソルを自動算出） | `physics/mesh_domain.py`, `static/js/model_importer.js` |
| D | **完了予定時刻（ETA）表示**（ジョブ単位の進捗・残時間・推定完了時刻をサーバー側で管理し UI に表示） | `physics/job_manager.py`, `static/js/eta_display.js` |
| E | **サブエージェントによる並列実装**（分担表に従い、ファイル衝突を避けて並列実行） | 全工程 |

**上記 A〜E は省略・後回し・疑似実装を一切許可しない。全ステップで同時に完成させる。**

---

## 1. 厳格な実行原則（違反禁止）

1. **Zero-Placeholder Policy**
   - `...` による省略、`TODO` / `FIXME`、未実装の空関数、`pass` のみの本体、スタブ API を一切書かない。
   - 物理式・GPU シェーダー・並列処理・最適化アルゴリズム・進捗管理・DOM 操作・UI イベントまで 100% 実行可能なコードとする。
2. **Contract-First（依存関係の固定）**
   - 全エージェントが共有するデータ構造（`physics/config_schema.py` の dataclass / JSON スキーマ）を**最初にあなたが 1 人で確定**させる。以後の全実装はこの型に厳密に従う。型の変更による他エージェントの破綻を防ぐ。
3. **Small-Step & Verification**
   - 1 モジュールごとに実行して結果を確認してから次へ進む。最低でも以下を毎回実行し、**実測出力**を報告する。
     - `python -m pytest tests -q`（物理・最適化・メッシュの数値検証）
     - `python -m compileall physics app.py`（構文検証）
     - `python smoke_test.py`（API 導通 + 最適化ジョブ + ETA 取得 + メッシュ解析の通し確認）
4. **No Silent Fallback**
   - GPU（CuPy / torch CUDA）が無い環境では NumPy にフォールバックして**正常動作すること**をテストで証明する。フォールバック時は応答に `backend: "numpy"` を明示する。
5. **Git Protocol**
   - 検証通過後のみコミット。形式は `feat(aero): ...` / `feat(optimizer): ...` / `feat(mesh): ...` / `feat(ui): ...` / `fix(<scope>): ...`。
   - `.gitignore` に `__pycache__/`, `*.pyc`, `.venv/`, `node_modules/`, `*.glb`, `*.gltf`, `*.stl`, `*.obj`, `runs/`, `jobs/` を含める。
6. **物理的妥当性（精度の核心）**
   - 解析解が存在する領域（線形揚力 $C_L = C_{L\alpha}\alpha$、失速前の誘導抗力 $C_{D0} + C_L^2/(\pi e AR)$、レイノルズ数依存、ISA 大気、面積・重心・慣性テンソルの幾何計算）は **pytest で解析解と 1e-6〜1e-3 の相対誤差**を検証する。検証できない部分は「検証済み / 未検証」を明示する。

---

## 2. 物理モデルの高精度化要件（省略不可の式）

### 2.1 大気・流体パラメータ（要件 A：全可変）
- **ISA 標準大気**: 対流圏で $T = T_0 - L h$、$p = p_0 (T/T_0)^{g/(L R)}$、$\rho = p/(R T)$。
- **局所大気の上書き**: `temperature_K`, `pressure_Pa`, `humidity_rel` から密度を計算（湿潤空気の混合比による補正、$R_{air}=287.05$, $R_{v}=461.5$）。
- **動粘性**: Sutherland 式 $\mu = \mu_0 (T/T_0)^{1.5} (T_0+S)/(T+S)$、$\nu = \mu/\rho$。
- **レイノルズ数**: $Re = V c / \nu$ を毎ステップ算出。
- **抗力などの環境スケール**（UI でスライダー化）:
  - `drag_scale`（0.0〜5.0, 既定 1.0）: 全抗力係数の乗数
  - `gust_intensity`（0.0〜1.0）、`thermal_strength`（m/s）、`wind_speed`（m/s）、`wind_direction_deg`（0〜360）
  - `gravity`（m/s², 既定 9.80665）、`ground_friction`（0.0〜2.0）
  - `ground_effect_enabled`、`compressibility_enabled`（$M = V/a$ で Prandtl-Glauert 補正）
- すべて `POST /api/v1/environment/atmosphere` で即時変更でき、**UI 変更 → サーバー → 次のステップ計算に反映**されることをテストで証明する。

### 2.2 空力翼型（要件 A の機体側）
- 線形域: $C_L = C_{L0} + C_{L\alpha}\,\alpha$
- 失速: Viterna 相当の非線形遷移（sigmoid 型で $C_{L,\max}$ から滑らかに低下させ、post-stall では $C_L$ を線形減少させる）。
- 抗力: $C_D = C_{D0} + C_L^2/(\pi e AR)$、$C_{D0} = f(Re)$（Re が小さいほど増加）。
- **抗力極線（Drag Polar）の API 公開**: $C_{D,\min}$ と $\alpha_{L/D,max}$ を数値的に求めて返す。
- 三角翼（テーパ比 $\lambda$）の空力諸元: $AR = b^2/S$、$c_{mac}$、$C_{L\alpha} = 2\pi AR \big/ \big(2 + \sqrt{AR^2(1+\lambda) + \tfrac{4}{\pi} AR}\big)$。
- 総揚力 $L = \tfrac12 \rho V^2 S C_L$、抗力 $D = \tfrac12 \rho V^2 S C_D$、ピッチングモーメント $M = q S c C_m$。
- **Trim / Elevon**: 後縁折り角 $\delta$ が $\Delta C_m$、$\Delta C_L$、$\Delta\alpha_{trim}$ を与える。
- **数値積分検証**: 揚力係数を $\alpha$ で数値積分した翼の循環 $\Gamma = 2 q S C_L/(\rho V)$ と、1 枚板の Helmbold 極値を比較するテストを pytest に含める。

### 2.3 6DoF 積分（高精度）
- 状態: 位置 $\mathbf{r}$、速度 $\mathbf{v}$（機体座標系と慣性座標系の変換を明示）、姿勢四元数 $\mathbf{q}$、角速度 $\boldsymbol{\omega}$。
- 力: 空力（揚力・抗力・ピッチング / ヨーイング / ロールモーメント）、重力、姿勢安定化の復元トルク。
- **RK4 積分 + Adaptive Sub-stepping**: $\Delta t$ を局所誤差推定（角速度と流速に基づく制限条件）と CFL 風条件から可変に決め、$\Delta t \in [10^{-5}, 2\times10^{-2}]$ s にクランプ。数値発散検出（NaN / Inf / 速度上限超過）で前ステップへロールバック。
- **慣性結合**: $I\dot{\omega} = M - \omega \times I\omega$。慣性テンソルは 3D メッシュから自動算出。
- 検証: 抗力なしの自由落下で $h(t) = h_0 - \tfrac12 g t^2$、抗力 0 時に全エネルギー変化 < 1e-6。

### 2.4 風場
- 3D ベクトル場: 平均風 + Simplex 風乱流 + 熱上昇気流（Plume 式）+ 地面境界層プロファイル（対数則 $\bar{u}/u_* = (1/\kappa)\ln(z/z_0)$）。
- **GPU 実行**: CuPy / torch によるグリッド一括計算、非対応時は NumPy。応答に `backend` フィールドで明示。

---

## 3. 最適化エンジン（要件 B：最適解探索）

### 3.1 目的関数（複数同時対応、UI で重み設定）
- `max_distance` 水平飛行距離最大化
- `max_flight_time` 滞空時間最大化
- `max_ld_ratio` 最大 $L/D$
- `min_max_stress` 翼面最大応力最小化（構造安全）
- `max_stall_margin` 失速マージン最大化
- `min_launch_speed` 必要発射初速最小化
- `max_stability` 静的安定性最大化（中立点位置の最適化）

### 3.2 探索アルゴリズム（すべて実装、選択可能）
- **Coarse Grid Sweep**: 離散グリッド全評価（並列）。
- **Nelder-Mead**（scipy）: 単一目的・低評価回数向け。
- **Differential Evolution**（`scipy.optimize.differential_evolution`）: 単一目的・既定エンジン。
- **NSGA-II**: 自前実装（非支配ソート + クラウディング距離 + SBX 交叉 / 多項式変異）→ **Pareto front** を出力。多目的（例: 距離と応力のトレードオフ）。
- **Surrogate 高速化**: 評価済みサンプルで $k$-NN / RBF 近似モデルを fit し、期待改善量 EI で次の評価点を選ぶ（評価回数の削減）。

### 3.3 探索対象の設計変数
- 形状: 翼幅 $b$、翼面積 $S$、テーパ比 $\lambda$、$AR$、二面角、ダート型 / グライダー型の形状フラグ
- 折り畳み: 前縁 / 後縁 / 翼端 / 中央の折り角（4 変数）、Elevon 角
- 材料: 坪量 (g/m²)、紙剛性、質量
- 射出: 発射角、ロール角、初速

### 3.4 実行機構と ETA（要件 D と統合）
- ジョブは `physics/job_manager.py` の `JobManager`（スレッドセーフ、`threading.RLock` + `dict`）で管理。
- 各ジョブのフィールド: `job_id`（UUID4）、`status`（`queued|running|completed|failed|cancelled`）、`progress`（0.0〜1.0）、`evaluated_count`、`total_evaluations`、`started_at`（ISO8601）、`updated_at`、`eta_seconds`、`estimated_completion`（ISO8601）、`backend`（`multiprocessing|threading|serial`）、`elapsed_seconds`、`evals_per_second`。
- **ETA 計算式**（実装必須）:
  - $\overline{t_{eval}} = \dfrac{\text{elapsed\_seconds}}{\max(\text{evaluated\_count}, 1)}$
  - $\text{eta\_seconds} = (\text{total\_evaluations} - \text{evaluated\_count}) \cdot \overline{t_{eval}}$
  - $\text{evals\_per\_second} = 1 / \overline{t_{eval}}$、$\text{estimated\_completion} = \text{now} + \text{eta\_seconds}$
- `evals_per_second` は指数移動平均（$\alpha = 0.3$）で平滑化し、開始直後の過大評価を防ぐ。進捗は 0.5 秒間隔で更新。
- `POST /api/v1/optimizer/run` は**即座に** `job_id` を返す（ブロッキング禁止）、`GET /api/v1/optimizer/status/<job_id>` で進捗 / ETA を取得、`POST /api/v1/optimizer/cancel/<job_id>` で中断、`GET /api/v1/optimizer/result/<job_id>` で Pareto front・最適パラメータ・目的関数値を JSON / CSV で取得。
- 最適化は `multiprocessing.Pool`（Windows の `spawn` 対策で評価関数はモジュールレベルに置く）で並列評価し、`parallel_engine.py` と共通の worker 設定を使う。

---

## 4. 3D モデルインポート（要件 C）

### 4.1 対応形式と読込経路
- **glTF 2.0 / GLB**（`GLTFLoader`）、**OBJ**（`OBJLoader`）、**STL**（`STLLoader`、バイナリ / ASCII 両対応）。
- 読込方法: ブラウザのファイル選択、ドラッグ＆ドロップ、URL 指定の 3 通り。いずれも同一パイプラインに流す。

### 4.2 物理量の自動抽出（`physics/mesh_domain.py`）
- 読み込んだジオメトリから次を**完全自動**で算出する（`trimesh` 依存を許容、無い場合は純 NumPy 実装にフォールバック）。
  - 全頂点座標から表面積 $S$、翼面積、スパン $b$、平均翼弦 $\bar{c} = S/b$、テーパ比 $\lambda$
  - アスペクト比 $AR = b^2/S$
  - 重心 $x_{cg}$（体積中心 / 面積中心を `cg_mode` で切替）
  - 慣性テンソル $\mathbf{I}$（四面体積分）、質量 $m = \rho_{paper} \cdot \text{gsm} \cdot S_{sheet}$
  - 空力パネル分割: $N_x \times N_y$ パネル、**パネル法線・面積・空力ラムダ**（局所 $\alpha$ ごとの $C_L$ と、局所洗出しによる回転揚力 $C_{L,\mathrm{rot}}$ を含む静的パネル法）
- **単位補正**: mm / cm / m を指定して正規化（`scale_hint`）。
- **手動上書き UI**: 自動算出値を上書き可能（`override` フラグ）。

### 4.3 モデル管理
- 取込済みモデルをサーバー側でキャッシュ（`models/` 配下、UUID 命名）。`GET /api/v1/mesh/models` で一覧、`POST /api/v1/mesh/select` で選択、`GET /api/v1/mesh/metrics/<model_id>` で物理量を取得。
- 3D ビューアには選択モデルのメッシュを反映し、応力ヒートマップは頂点属性として動的更新する。
- モデルごとに最適化を実行し、**モデル単位の Pareto front** を比較できる。

---

## 5. サブエージェント並列実装（要件 E）

**あなたの役割は「実装者」ではなく「オーケストレーター兼統合者」**。以下の方針で `Task` ツールによりサブエージェントを起動する。

### 5.1 実装順序
1. **Phase 0（自分で実施）**: `physics/config_schema.py` の全 dataclass / JSON スキーマ、`physics/geometry_utils.py`（四元数・Euler 変換・外積）、ディレクトリ構成、`requirements.txt`、`.gitignore` を確定してコミット。ここが全エージェントの依存の要。
2. **Phase 1（並列）**: 独立モジュールを各サブエージェントへ同時に委譲する。
3. **Phase 2（自分の担当）**: `app.py`（全 API 統合）、UI 統合、統合テスト、Git 操作。
4. **Phase 3（並列・仕上げ）**: 各サブエージェントへ担当モジュールの pytest 強化・数値検証を、返答（`task_id` を指定した再開）として依頼する。

### 5.2 Phase 1 サブエージェント分担表（ファイル排他・競合なし）

| エージェント | 担当ファイル | 必須実装内容 |
|-----------|------------|------------|
| `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | §2.2 の翼型・失速・Drag Polar・Trim / 誘導抗力・Re 補正 |
| `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | §2.1 の ISA・Sutherland・風場・境界層・熱上昇・GPU（CuPy / torch）+ NumPy フォールバック |
| `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | 動圧・G フォース・翼根曲げモーメント・頂点応力場（0〜1 正規化ヒートマップ） |
| `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | §4 の glTF / OBJ / STL 解析・面積 / 重心 / 慣性 / AR 算出・パネル法・モデルキャッシュ |
| `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | §2.3 の 6DoF RK4・四元数・adaptive sub-stepping・リングバッファ・発射 / 着地判定 |
| `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | §3 の Nelder-Mead / DE / NSGA-II / Surrogate / Pareto 並列評価 |
| `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | §3.4 の JobManager・ETA 式・進捗管理・JSON / CSV エクスポート |
| `ui-gpu-viz` | `templates/index.html`, `static/css/*.css`, `static/js/*.js` | §6 の Google Clean UI・Three.js シェーダー・GPGPU 風粒子・時間軸・最適化パネル・ETA 表示・3D モデルローダー |

### 5.3 サブエージェントへの委譲指示に必ず含める内容
- 担当ファイルのみを変更すること（他ファイルの改変は禁止）。
- `physics/config_schema.py` で定義された dataclass / 関数シグネチャから逸脱しないこと（逸脱が必要な場合は報告して返答を待つ）。
- Zero-Placeholder を厳守し、全関数に実処理を入れること。
- `pytest tests/test_<module>.py -q` が全て PASS することを確認し、**実際の pytest 出力**を報告すること。
- GPU など実行できない環境依存処理は `backend` フォールバックで代替し、NumPy パスがテストで PASS することを確認すること。

### 5.4 Phase 2 自分の担当
- `app.py`: 全 REST エンドポイント（§7 参照）を `config_schema.py` に従って統合。
- 統合テスト `smoke_test.py`: 全 API を一連で叩き、最適化ジョブ投入 → 進捗取得 → ETA 取得 → 結果取得 → CSV 出力、3D モデル投入 → 物理量取得、まで通しで確認。
- 最終 `pytest tests -q` による Green 確認とコミット。

---

## 6. UI/UX 詳細（要件 A/B/C/D の反映先）

- **カラーパレット**: 背景 `#F8F9FA` / カード `#FFFFFF` / Accent `#1A73E8` / 正常 `#34A853` / 高負荷 `#FBBC04` / 破断 `#EA4335`。角丸 8px、薄い影、Roboto / Google Sans 系フォント。
- **Header Bar**:
  - タイトル、Google Blue ロゴ、風洞モードトグル、3D モデル読込ボタン、`JSON 保存` / `JSON 読込` / `CSV 出力` ボタン。
  - **右端に「完了予定時刻」ウィジェット**: 実行中ジョブの `estimated_completion` を `HH:MM:SS` で常時表示し、残り時間（`mm:ss`）とプログレスバー、ジョブ名（例: `Differential Evolution / 評価 312/1000`）を併記。完了時は `完了 14:32:05` と表示。
- **Left Panel**:
  - 機体セクション: 質量、坪量、剛性、翼幅、翼面積、テーパ比、二面角、前縁 / 後縁 / 翼端 / 中央の折り角、Elevon 角。**すべて数値入力とスライダーの併用**（既定値表示付き）。
  - 環境セクション（要件 A）: 気温 [`°C` / `K`]、気圧 [`Pa` / `hPa`]、湿度 [`%`]、風速、風向、乱気流強度、熱上昇強度、重力、地面摩擦、Drag Scale、地面効果 ON / OFF、圧縮性補正 ON / OFF。変更は即座にサーバーへ送信され次のステップに反映される。
  - 3D モデルセクション: 形式 / ファイル / URL 選択、モデル一覧、単位・スケールヒント、CG 算出モード（体積 / 面積）、算出物理量（$S$, $b$, $c$, $AR$, $\lambda$, $x_{cg}$, 質量, $\mathbf{I}$）と、各値の「自動 / 手動上書き」トグル。
- **Right Overlay**:
  - 飛行ステータス: 高度、速度、迎角 $\alpha$、-slip 角 $\beta$、ロール / ピッチ / ヨー、$L/D$、G フォース、最大応力、状態（正常 / 高負荷 / 破断を色分け）。
  - Chart.js グラフ: 高度・速度・迎角・応力のリアルタイム折れ線。
  - **最適化パネル**: 目的関数チェックボックス（§3.1）、重みスライダー、探索アルゴリズム選択（Grid / Nelder-Mead / DE / NSGA-II / Surrogate）、評価回数、並列プロセス数、`実行` / `中断` ボタン、**進捗バーと ETA 表示**、結果の Pareto フロント散布図（Chart.js scatter）、最適パラメータのテーブル、`この設定を読み込む` ボタン。
- **Bottom Time Control Dock**: 再生 / 一時停止、倍速（0.25x / 0.5x / 1x / 2x / 5x / 10x）、コマ送り（前 / 後）、タイムラインシーク。
- **Wind Tunnel Mode**: トグル ON で風速を固定し、機体を風洞内に固定して空力係数のみ計測するモード。

---

## 7. REST API 一覧（要件 A/B/C/D を反映した確定仕様）

| メソッド | エンドポイント | 内容 |
|---------|--------------|------|
| POST | `/api/v1/aerodynamics/calculate` | $C_L$, $C_D$, $L$, $D$, $C_m$, $L/D$, $C_{D,\min}$, $\alpha_{L/D,\max}$、stall 判定 |
| POST | `/api/v1/aerodynamics/polar` | $\alpha$ スイープによる Drag Polar 曲線 |
| POST | `/api/v1/environment/atmosphere` | 環境パラメータ設定 + 密度・動粘性・音速を即時返却 |
| GET | `/api/v1/environment/current` | 現在の環境パラメータを取得 |
| POST | `/api/v1/wind/field` | 3D 風速場（GPU / NumPy、`backend` を明示） |
| POST | `/api/v1/stress/analysis` | 頂点応力場 + 翼根曲げモーメント + G フォース |
| POST | `/api/v1/simulation/step` | 6DoF 1 ステップ（可変 $\Delta t$、adaptive） |
| POST | `/api/v1/simulation/reset` | シミュレーション初期化 |
| POST | `/api/v1/simulation/batch` | 並列マルチシミュレーション（`multiprocessing`） |
| POST | `/api/v1/mesh/import` | glTF / GLB / OBJ / STL 取込 → `model_id` + 物理量 |
| GET | `/api/v1/mesh/models` | 取込済みモデル一覧 |
| POST | `/api/v1/mesh/select` | 使用モデル選択 |
| GET | `/api/v1/mesh/metrics/<model_id>` | 面積・重心・慣性・AR 等の取得 |
| POST | `/api/v1/optimizer/run` | 最適化開始 → 即 `job_id` 返却（非ブロッキング） |
| GET | `/api/v1/optimizer/status/<job_id>` | 進捗・ETA・推定完了時刻 |
| POST | `/api/v1/optimizer/cancel/<job_id>` | 最適化中断 |
| GET | `/api/v1/optimizer/result/<job_id>` | Pareto front・最適パラメータ（JSON / CSV） |
| GET | `/api/v1/jobs` | 全ジョブ一覧（状態・ETA） |
| POST | `/api/v1/data/export` | 飛行ログ / 最適化結果を CSV / JSON 出力 |
| POST | `/api/v1/data/import` | 設定・プリセット読込 |

全エンドポイントは不正入力に対して 400 と明確なエラーメッセージを返し、500 時はトレースをログに残す。

---

## 8. 実行手順（この順で進めること）

1. **Phase 0**: 契約定義（`config_schema.py`, `geometry_utils.py`, `requirements.txt`, `.gitignore`）を作成し `python -m compileall` で検証、コミット。
2. **Phase 1**: §5.2 の表に従って 8 つのサブエージェントを `Task` ツールで**同時に**起動。担当ファイルは排他であり競合しない。各エージェントの完了報告に実 pytest 出力と実装サマリが含まれることを確認。
3. **Phase 2**: 各サブエージェントの成果物が `config_schema.py` の契約から逸脱していないかを突合検証（`grep` で import と関数シグネチャの整合性を確認）。問題があれば該当エージェントに修正依頼。
4. **Phase 3**: `app.py`（全 API）+ `templates/index.html` + `static/**`（Google Clean UI、GPU シェーダー、GPGPU 風粒子、Time Control、最適化パネル、ETA 表示、3D ローダー）を統合。
5. **Phase 4**: `smoke_test.py` で通し確認（API 連鎖、最適化ジョブの投入 → 進捗 → ETA → 結果、3D モデル取込 → 物理量取得、CSV 出力）。
6. **Phase 5**: `python -m pytest tests -q` が全 GREEN になるまで修正。Google Clean デザイン・レスポンシブ対応・アニメーション性能を確認。
7. **Phase 6**: `git status` を確認し、検証通過分のみコミット。
8. **最終報告**: 実装ファイル一覧、pytest 実出力、API 一覧、UI 構成の説明、既知の制約を報告。

**各ステップの最後で「実行コマンド → 実出力 → 次のステップ」の形で報告し、検証できない箇所があれば修正してから進む。**

---

## 9. 絶対遵守の最終確認

- [ ] §0 の A〜E がすべて実装済み（省略・疑似なし）
- [ ] `physics/optimizer_domain.py` に Nelder-Mead / DE / NSGA-II / Surrogate がすべて実在
- [ ] `physics/job_manager.py` に ETA 計算式が実在し、UI に `estimated_completion` が表示される
- [ ] `physics/mesh_domain.py` が glTF / GLB / OBJ / STL に対応し、$S$, $b$, $c$, $AR$, $\lambda$, $x_{cg}$, $\mathbf{I}$ を自動算出
- [ ] 環境パラメータ（密度・粘性・重力・drag_scale 等）が UI から API 経由で変更可能
- [ ] `config_schema.py` の全 dataclass による型安全性
- [ ] `pytest tests -q` が全 PASS（解析解との比較テスト込み）
- [ ] `smoke_test.py` が全チェーン成功
- [ ] 3D ビューア（Three.js シェーダー + GPGPU 風粒子）が動作
- [ ] Google Clean デザイン準拠
- [ ] Zero-Placeholder 違反ゼロ
