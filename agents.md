# AGENTS.md: 3D Paper Airplane High-Performance CFD Simulator

本ドキュメントは、流体力学・航空工学に基づいた「3D紙飛行機シミュレーター」を、Flask (REST API) と Three.js/WebGPU (フロントエンド) を用い、GPUアクセラレーションと並列演算をフル活用して1セッション内で段階的に構築するためのマルチエージェント開発ガイドラインである。

---

## 1. 開発原則 (Core Principles)

1. **Zero-Placeholder Policy (省略・空実装の絶対禁止)**
   - `TODO` コメント、`...` による記述省略、未実装の空関数、スケルトンコードの出力を一切禁止する。
   - すべての物理数式、GPUシェーダー、並列処理、データ保存、Time-Control、APIエンドポイントを 100% 実際に動作・計算されるコードとして記述する。
2. **Small-Step & Verification Protocol (小刻み実行と検証)**
   - モジュール単位でコードを作成後、必ずローカル環境での実行テスト（構文チェック、API導通、GPU/マルチスレッド挙動テスト）を実施し、動作確認後に次のステップに進む。
3. **Git Version Control (Git運用プロトコル)**
   - ローカル検証を通過した実動コードのみを Git コミット対象とする。
   - コミット規約: `feat(scope): message` / `fix(scope): message` / `refactor(scope): message`
4. **Domain-Driven Modular API & Parallel Engine**
   - 物理演算は領域別（空気力学、風場、応力、6DoFシミュレーション）に独立した Flask REST API として実装する。
   - 大規模な風場演算および複数飛行パスのシミュレーションには Python の `concurrent.futures` / `multiprocessing` および GPU（CuPy / PyTorch / CUDA）を活用する（GPU非搭載環境への安全なCPU/NumPyフォールバックも同時実装）。
5. **High-Performance GPU Rendering & Web Workers**
   - フロントエンドの風粒子（数万〜数十万粒子）や応力ヒートマップ描画は Three.js GPGPU / GPU Instanced Mesh / Custom Shaders をフル活用し、描画負荷をGPUへオフロードする。
   - 重いJS処理は Web Workers に委託し、メインUIスレッド（60FPS/120FPS）を阻害しない。
6. **Google Minimalist UI/UX Standard (Google風UIデザイン)**
   - Google Clean / Material Design に完全準拠。白ベース（`#F8F9FA` / `#FFFFFF`）、角丸カード型浮遊パネル、Google Blue (`#1A73E8`) を基調としたデザイン。
7. **作成、完了報告**
   - 作成が開始、終了したら作成、完了タイトル　作成時間、終了時間　修正内容要約(100文字程度)を以下のwebhookに送信する。サブエージェントを起動する時も送信する。サブエージェントはサブエージェントとつける。
   開始、サブエージェント、終了は見た目で分かるように開始は緑サブエージェントはオレンジ終了は青
https://discord.com/api/webhooks/1560152284057567353/2udSqpx3P4ZVhrGi1JnyRbgAY-FyB6cySreOpSOxSi8U9KbTvTj4KtOm91oaMOQCQ8u_
8. **最後に**
   -ユニットテストで必ず検証するように


---

## 2. 拡張機能仕様 (Extended Capabilities)

### 2.1 物理・航空工学的理論モデル
- **空気力学**:
  $$L = \frac{1}{2} \rho v^2 S C_L(\alpha), \quad D = \frac{1}{2} \rho v^2 S C_D(\alpha)$$
  - 迎角 $\alpha$ の非線形失速（Stall）挙動モデル。
  - **地面効果 (Ground Effect)**: 高度 $h$ と翼幅 $b$ による誘導抗力低減:
    $$C_{Di}(h) = C_{Di0} \cdot \left[ \frac{16(h/b)^2}{1 + 16(h/b)^2} \right]$$
  - **エレボン/トリム**: 後縁折り曲げ角 $\delta_{fold}$ によるトリム迎角およびピッチングモーメント調整。
  - **用紙特性と受動的空力弾性モーフィング (Passive Aeroelastic Morphing)**:
    坪量 ($g/m^2$) と剛性に基づく、動圧 $q$ 依存の非線形後縁トリム変形モデル。高動圧投擲域（$q \gg q_{ref}$）での頭上げループ抑制と、頂点・滑空域（$q \to 0$）でのトリム自動復元を両立する:
    $$\delta_{eff}(q) = \delta_{trim} \cdot \left[ \frac{1}{1 + (q / q_{aeroelastic})^{n_{morph}}} \right]$$
    （変形角を固定値でクランプせず、材料非線形性に基づく滑らかなシグモイド減衰として実装する）。
- **風場・流体 (Fluid Dynamics)**:
  - 3D空間ベクトル場および Simplex/Perlin ノイズによる乱気流、熱上昇気流 (Thermal)。
- **構造負荷・応力 (Structural Stress)**:
  - 動圧 $q = \frac{1}{2} \rho v^2$ および Gフォースによる翼根 (Wing Root) 曲げモーメントと頂点応力ヒートマップ値 (0.0:青 〜 1.0:赤)。

### 2.2 時間軸制御 (Time Control & Playback System)
- **タイムコントロール**: 一時停止 (Pause)、再生 (Play)、倍速変更 (0.25x, 0.5x, 1x, 2x, 5x, 10x)、コマ送り (Step Forward/Backward)。
- **タイムライン・シーク (Timeline Scrubbing)**: 飛行履歴をリングバッファ構造で記録し、スライダー操作で過去・現在の任意フレームに戻って状態や応力分布を巻き戻し確認可能。

### 2.3 データ保存・永続化・エクスポート (Persistence & Export)
- **ローカルストレージ/IndexedDB 保存**: 機体パラメータ、環境設定、カスタムプリセットの保存と復元。
- **データエクスポート/インポート**:
  - `JSON`: 設定パラメータ・全シミュレーション状態の入出力。
  - `CSV`: 飛行ログ（時間、位置 $X,Y,Z$、速度、迎角、高度、Gフォース、翼面最大応力）の即時ダウンロード。

### 2.4 GPU・並列計算アーキテクチャ (GPU & Parallelization)
- **バックエンド並列・GPU加速**:
  - Python `multiprocessing` / `ThreadPoolExecutor` による多変数パラメータの一括並列シミュレーション。
  - CuPy / PyTorch 連携による 3D 風場グリッド配列の一括ベクトル計算（CUDA GPU利用、非搭載時は NumPy へ自動フォールバック）。
- **フロントエンド GPU 加速**:
  - Three.js Custom Vertex & Fragment Shaders による動的ヒートマップ（頂点ごとのリアルタイム応力変形）。
  - GPGPU / Transform Feedback パティクルシステムによる 100,000+ 個のリアルタイム風流線粒子描画。

---

## 3. エージェント役割定義 (Agent Roles)

1. `Git-Integration Agent`: リポジトリ初期化、`.gitignore` / `requirements.txt` 管理、起動確認、Gitコミット制御。
2. `Aero-Physics Agent`: `physics/aero_domain.py` （揚力・抗力・失速・地面効果・トリム・坪量）。
3. `Fluid-Wind Agent`: `physics/wind_domain.py` （GPU/NumPy対応 3D風場・乱気流ベクトル演算）。
4. `Structural-Stress Agent`: `physics/stress_domain.py` （動圧・Gフォース・翼根曲げモーメント・全頂点応力計算）。
5. `Parallel-Sim-Engine Agent`: `physics/flight_sim.py` & `physics/parallel_engine.py` （6DoF RK4積分・時間制御タイムバッファ・マルチプロセッシング並列処理）。
6. `Data-Storage Agent`: `physics/storage_domain.py` （JSON/CSVエクスポート・状態保存・復元API）。
7. `Google-UI & GPU-Visualization Agent`: Three.js GPUシェーダー描画・GPGPU風線粒子・時間制御UI・Google Clean デザイン構築。

---

## 4. REST API 仕様 (API Specifications)

1. `POST /api/v1/aerodynamics/calculate`: 揚力・抗力・モーメント・失速判定
2. `POST /api/v1/wind/field`: 空間風速ベクトル場取得（GPU/並列ベクトル計算）
3. `POST /api/v1/stress/analysis`: 翼面動圧・Gフォース応力ヒートマップデータ取得
4. `POST /api/v1/simulation/step`: 6DoF 1ステップ位置・姿勢更新
5. `POST /api/v1/simulation/batch`: パラメータスイープ・並列複数シミュレーション実行
6. `POST /api/v1/data/export`: 飛行履歴データの CSV / JSON フォーマット変換・ダウンロードデータ生成

---

## 5. UI/UX デザイン仕様 (Google Clean Standard)

- **カラー**: メイン背景 `#F8F9FA` / カード `#FFFFFF` / Accent `#1A73E8` / 状態 `#34A853` (正常), `#FBBC04` (高負荷), `#EA4335` (破断)
- **レイアウト**:
  - **Header Bar**: Google スタイルロゴ、風洞モードトグル、データ保存/復元/CSV出力ボタン。
  - **Left Panel**: 機体（質量, 坪量, トリム角, 二面角）・環境（初速, 発射角, 風速, 乱気流）調整スライダー。
  - **Bottom Time Control Dock**: 再生/一時停止/倍速/コマ送りボタン、シーク可能なタイムラインスライダー。
  - **Right Overlay**: 飛行ステータス（高度, 速度, 迎角, L/D比, Gフォース, 最大応力）＆ Chart.js リアルタイムグラフ。

---

## 6. 段階的実行手順 (Execution Plan)

1. **Step 1**: Git初期化 (`git init`), `.gitignore`, `requirements.txt` (Flask, numpy, scipy, cupy-cuda12x/torch等) の作成。
2. **Step 2**: `physics/` モジュール群 (`aero_domain.py`, `wind_domain.py`, `stress_domain.py`, `flight_sim.py`, `parallel_engine.py`, `storage_domain.py`) の完全実装と単体検証。
3. **Step 3**: `app.py` (Flask REST API 全エンドポイント) の完全実装と API 通信テスト。
4. **Step 4**: 計器コンソール UI (`index.html`, `static/css/console.css`) および GPUアクセラレーション対応 Three.js 3D ビューア (`static/js/` の `app.js` / `viewport.js` / `airframe.js` / `flowfield.js` / `transport.js` / `telemetry.js` / `panels.js` / `optimizer.js` / `meshpanel.js` / `datapanel.js`) の完全実装と統合テスト。

> **v4 では上記の工程を §8.6 の Phase 0〜3 によるサブエージェント並列プロトコルで実行する。** 契約（`config_schema.py` / `geometry_utils.py`）を Manager が先に確定し、残りを §9 の分担表で排他並列に委譲する。

## 7. 追加拡張仕様 (Advanced Features)

1. **通信最適化 (Streaming & Batching)**:
   - WebSocket または 1/10秒ごとのバッチAPIレスポンスによる滑らかな 60FPS 描画。
2. **数値安定性 (Adaptive Sub-stepping)**:
   - 高速・高gフォース状態における可変 $\Delta t$ 制御および数値発散のクランプ処理。
3. **機体形状パラメトリック化 (Parametric Folding)**:
   - ダーツ型/グライダー型のメッシュ切り替え、およびウイングレット/先端折りの動的変形。
4. **比較・ゴースト飛行モード (Ghost Flight / Multi-Trajectory)**:
   - 前回飛行の軌跡を半透明で同時再生する比較オーバーレイ機能。
5. **物理ユニットテスト (Physics Verification)**:
   - `pytest` による揚力・抗力・失速・地面効果の数式整合性自動テスト。

---

## 8. 拡張仕様 v4 (Extended Capabilities v4)

### 8.1 大気・流体パラメータの全面可変 (Configurable Atmosphere)
- すべての物理パラメータは `physics/config_schema.py` の dataclass で定義し、UI / REST API から実行時に変更可能とする。
- **ISA 標準大気**: $T = T_0 - L h$、$p = p_0 (T/T_0)^{g/(L R)}$、$\rho = p/(R T)$。
- **局所大気**: `temperature_K`, `pressure_Pa`, `humidity_rel` から混合比の補正により密度を算出（$R_{air}=287.05$, $R_{v}=461.5$）。
- **動粘性 (Sutherland)**: $\mu = \mu_0 (T/T_0)^{1.5}(T_0+S)/(T+S)$、$\nu = \mu/\rho$、$Re = Vc/\nu$。
- **抗力スケーリング**: `drag_scale`（0.0〜5.0）で全抗力係数を可変。加えて `gravity`, `ground_friction`, `gust_intensity`, `thermal_strength`, `wind_speed`, `wind_direction_deg`, `ground_effect_enabled`, `compressibility_enabled`。
- 変更は `POST /api/v1/environment/atmosphere` 経由で即時反映。

### 8.2 空力翼型の高精度化
- 線形域 $C_L = C_{L0} + C_{L\alpha}\alpha$、Viterna 相当の非線形失速遷移。
- 抗力 $C_D = C_{D0}(Re) + C_L^2/(\pi e AR)$。
- **Drag Polar 出力**: $C_{D,\min}$ と $\alpha_{L/D,\max}$ を数値的に求めて API で公開。
- 三角翼理論: $AR = b^2/S$、$c_{mac}$、$C_{L\alpha} = 2\pi AR/(2+\sqrt{AR^2(1+\lambda)+4AR/\pi})$。
- Elevon 折り角 $\delta$ による $\Delta C_m$ / $\Delta C_L$ / $\Delta\alpha_{trim}$。
- **極低速頂点遷移力学 (Apex Low-Speed Pushover Dynamics)**:
  上昇端（$V < 2.0\,\text{m/s}$）における風圧中心 $x_{cp}$ の後退移動および質量中心偏心による自然な機首下げモーメントモデル。動圧消失時のモーメント特異点を防ぎ、重力と微小相対風による自然なプッシュオーバー（滑空姿勢への遷移）を再現する。
- 揚力係数の数値積分で翼循環を検証する pytest を含める。

### 8.3 最適化エンジン (Optimizer Domain)
- **目的関数**: `max_distance`, `max_flight_time`, `max_ld_ratio`, `min_max_stress`, `max_stall_margin`, `min_launch_speed`, `max_stability`（重み付き複数指定可）。
- **アルゴリズム**: Coarse Grid Sweep / Nelder-Mead / Differential Evolution / **NSGA-II**（非支配ソート + クラウディング距離 + SBX 交叉 + 多項式変異、Pareto front 出力）/ **Surrogate**（k-NN・RBF 近似 + 期待改善量 EI）。
- **設計変数**: 翼幅, 翼面積, テーパ比, AR, 二面角, 形状フラグ, 4 種の折り角, Elevon 角, 坪量, 紙剛性, 質量, 発射角, ロール角, 初速。
- **並列実行**: `multiprocessing.Pool`（Windows `spawn` 対策のモジュールレベル評価関数）、`parallel_engine.py` と共通設定を使用。

### 8.4 3D モデルインポート (Mesh Domain)
- 対応形式: **glTF 2.0 / GLB / OBJ / STL**（STL はバイナリ / ASCII 両対応）。
- 読込経路: ファイル選択 / ドラッグ&ドロップ / URL 指定の 3 通りを同一パイプラインで処理。
- **自動抽出物理量**: 表面積 $S$、翼面積、スパン $b$、平均翼弦 $\bar{c} = S/b$、テーパ比 $\lambda$、$AR = b^2/S$、重心 $x_{cg}$（体積 / 面積モード切替）、慣性テンソル $\mathbf{I}$（四面体積分）、質量 $m = \rho_{paper}\cdot\text{gsm}\cdot S_{sheet}$。
- **空力パネル分割**: $N_x \times N_y$ パネルの法線・面積・空力ラムダ。局所 $\alpha$ ごとの $C_L$ と回転揚力 $C_{L,\mathrm{rot}}$ を含む静的パネル法。
- **単位補正** (`scale_hint`: mm / cm / m) と **手動上書き** (`override` フラグ) に対応。
- `trimesh` 依存を許容し、無ければ純 NumPy 実装へフォールバックする。
- 取込モデルはサーバー側で `models/` に UUID 命名でキャッシュし、一覧・選択・物理量取得の API を提供する。

### 8.5 完了予定時刻 (ETA) 管理
- `physics/job_manager.py` の `JobManager`（`threading.RLock` + `dict`）で全ジョブを管理する。
- ジョブフィールド: `job_id`(UUID4), `status`(`queued|running|completed|failed|cancelled`), `progress`, `evaluated_count`, `total_evaluations`, `started_at`, `updated_at`, `eta_seconds`, `estimated_completion`, `backend`, `elapsed_seconds`, `evals_per_second`。
- **ETA 計算式**:
  $$\overline{t_{eval}} = \frac{\text{elapsed}}{\max(\text{evaluated},1)},\quad \text{eta} = (\text{total}-\text{evaluated})\cdot\overline{t_{eval}},\quad \text{estimated\_completion} = \text{now} + \text{eta}$$
  - `evals_per_second` は指数移動平均（$\alpha=0.3$）で平滑化し、開始直後の過大評価を防ぐ。更新間隔 0.5 秒。
- `POST /api/v1/optimizer/run` はブロッキングせず即 `job_id` を返す。UI は `estimated_completion` を `HH:MM:SS` で常時表示する。

### 8.6 サブエージェント並列開発プロトコル
- **Phase 0（-manager）**: `physics/config_schema.py` と `physics/geometry_utils.py` で契約を確定（全依存の共通基盤）。
- **Phase 1（並列 worker）**: §9 の各役割をファイル排他で `Task` ツールから同時委譲。競合ファイルを発生させない。
- **Phase 2（-manager）**: `app.py`・UI・統合テスト・Git 操作。
- **Phase 3（並列 worker）**: 各 worker へ pytest 強化を `task_id` 指定の再開で依頼。
- worker への指示には必ず「担当ファイル限定変更」「config_schema 準拠」「Zero-Placeholder」「実 pytest 出力の報告」「GPU フォールバック確認」を含める。

---

## 9. エージェント役割定義 v4 (Agent Roles v4)

`physics/config_schema.py` / `physics/geometry_utils.py` は Manager が Phase 0 で作成し、以下はファイル排他の並列実装とする。

| # | エージェント | 担当ファイル | 必須実装内容 |
|---|-----------|------------|------------|
| 0 | `Manager` (自分) | `physics/config_schema.py`, `physics/geometry_utils.py`, `app.py`, `templates/index.html`, `static/**`, `smoke_test.py` | 契約定義・全 API 統合・UI 統合・統合テスト・Git 制御 |
| 1 | `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | 翼型・失速・Drag Polar・Trim・誘導抗力・Re 補正 |
| 2 | `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | ISA・Sutherland・全可変環境パラメータ・風場・境界層・GPU + NumPy フォールバック |
| 3 | `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | 動圧・G フォース・翼根曲げモーメント・頂点応力場 |
| 4 | `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | glTF/GLB/OBJ/STL 解析・面積/重心/慣性/AR 算出・パネル法・モデルキャッシュ |
| 5 | `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | 6DoF RK4・四元数・adaptive sub-stepping・リングバッファ・発射/着地判定 |
| 6 | `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | Nelder-Mead / DE / NSGA-II / Surrogate / Pareto 並列評価 |
| 7 | `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | JobManager・ETA 式・進捗管理・JSON/CSV エクスポート |
| 8 | `ui-gpu-viz` | `templates/index.html`, `static/css/console.css`, `static/js/*.js` | 計器コンソール UI・Three.js シェーダー・GPGPU 風粒子・時間軸・最適化パネル・ETA 表示・3D モデルローダー |

---

## 10. REST API 仕様 v4 (API Specifications v4)

| メソッド | エンドポイント | 内容 |
|---------|--------------|------|
| POST | `/api/v1/aerodynamics/calculate` | 揚力・抗力・モーメント・失速判定・$C_{D,\min}$・$\alpha_{L/D,\max}$ |
| POST | `/api/v1/aerodynamics/polar` | $\alpha$ スイープの Drag Polar 曲線 |
| POST | `/api/v1/environment/atmosphere` | 環境パラメータ設定 + 密度・動粘性・音速を即時返却 |
| GET | `/api/v1/environment/current` | 現在の環境パラメータ取得 |
| POST | `/api/v1/wind/field` | 空間風速ベクトル場（GPU/並列、`backend` 明示） |
| POST | `/api/v1/stress/analysis` | 翼面動圧・G フォース応力ヒートマップデータ取得 |
| POST | `/api/v1/simulation/step` | 6DoF 1 ステップ位置・姿勢更新（可変 $\Delta t$） |
| POST | `/api/v1/simulation/reset` | シミュレーション初期化 |
| POST | `/api/v1/simulation/batch` | パラメータスイープ・並列複数シミュレーション実行 |
| POST | `/api/v1/mesh/import` | glTF/GLB/OBJ/STL 取込 → `model_id` + 物理量 |
| GET | `/api/v1/mesh/models` | 取込済みモデル一覧 |
| POST | `/api/v1/mesh/select` | 使用モデル選択 |
| GET | `/api/v1/mesh/metrics/<model_id>` | 面積・重心・慣性・AR 取得 |
| POST | `/api/v1/optimizer/run` | 最適化開始 → 即 `job_id`（非ブロッキング） |
| GET | `/api/v1/optimizer/status/<job_id>` | 進捗・ETA・推定完了時刻 |
| POST | `/api/v1/optimizer/cancel/<job_id>` | 最適化中断 |
| GET | `/api/v1/optimizer/result/<job_id>` | Pareto front・最適パラメータ（JSON/CSV） |
| GET | `/api/v1/jobs` | 全ジョブ一覧（状態・ETA） |
| POST | `/api/v1/data/export` | 飛行ログ/最適化結果の CSV / JSON 生成 |
| POST | `/api/v1/data/import` | 設定・プリセット読込 |

全エンドポイントは不正入力に 400 + 明確なメッセージを返し、500 時はトレースをログに残す。

---

## 11. ディレクトリ構成 (Directory Layout)

```
.
├── AGENTS.md                  # 本仕様書
├── prompt.md                  # 実行プロンプト
├── requirements.txt
├── smoke_test.py              # 統合テスト（API 連鎖・最適化ジョブ・3D モデル）
├── app.py                     # Flask 全 API
├── physics/
│   ├── __init__.py
│   ├── config_schema.py       # 全 dataclass / JSON スキーマ（Manager: Phase 0）
│   ├── geometry_utils.py      # 四元数・Euler 変換・外積（Manager: Phase 0）
│   ├── aero_domain.py
│   ├── atmosphere_domain.py
│   ├── wind_domain.py
│   ├── stress_domain.py
│   ├── mesh_domain.py
│   ├── flight_sim.py
│   ├── parallel_engine.py
│   ├── optimizer_domain.py
│   ├── job_manager.py
│   └── storage_domain.py
├── tests/
│   ├── test_aero.py
│   ├── test_atmo_wind.py
│   ├── test_stress.py
│   ├── test_mesh.py
│   ├── test_flight.py
│   ├── test_optimizer.py
│   └── test_jobs.py
├── templates/index.html
├── static/
│   ├── css/console.css
│   └── js/{app.js, api.js, lib.js, state.js, panels.js, viewport.js,
│          airframe.js, flowfield.js, telemetry.js, transport.js,
│          optimizer.js, meshpanel.js, datapanel.js}
└── models/                    # 取込 3D モデルのキャッシュ（gitignore 対象）
```

---

## 12. 検証コマンド (Verification Commands)

各モジュール作成後に以下を実行し、実出力を報告すること。

```powershell
python -m compileall physics app.py      # 構文検証
python -m pytest tests -q                # 物理・最適化・メッシュの数値検証
python smoke_test.py                     # API 導通 + 最適化ジョブ + ETA + メッシュ解析
```

`pytest` では解析解が存在する領域（線形揚力、誘導抗力、ISA、Sutherland、面積・重心・慣性テンソル、翼循環）について相対誤差 1e-6〜1e-3 の検証を行い、検証できない箇所は「未検証」と明示する。
加えて、ギネス滞空記録機ベンチマーク（初速 $26\,\text{m/s}$、投射角 $78^\circ$、初期高度 $1.8\,\text{m}$ からの急上昇・頂点プッシュオーバー・滑空遷移判定）を統合テストに含め、極端個体における非線形遷移の連続性と滞空性能を回帰検証する。