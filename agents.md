# AGENTS.md: 3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine

本ドキュメントは、航空力学・構造力学に基づいた「3D紙飛行機シミュレーター」を、Flask (REST API) と Three.js / WebGPU (フロントエンド) を用い、GPUアクセラレーションと並列演算をフル活用して自律的かつ段階的に拡張・運用するための開発ガイドラインおよびアーキテクチャ仕様書である。

---

## 1. 開発原則 (Core Principles)

1. **Zero-Placeholder Policy (省略・空実装の絶対禁止)**
   - `TODO` コメント、`...` による記述省略、未実装の空関数、スケルトンコードの出力を一切禁止する。
   - すべての物理数式、GPUシェーダー、並列処理、データ保存、Time-Control、APIエンドポイントを 100% 実際に動作・計算されるコードとして記述する。
2. **Small-Step & Verification Protocol (小刻み実行と検証)**
   - モジュール単位でコードを作成後、必ずローカル環境での実行テスト（構文チェック、静的解析、API導通、GPU/マルチスレッド挙動テスト）を実施し、動作確認後に次のステップに進む。
3. **Git Version Control (Git運用プロトコル)**
   - ローカル検証を通過した実動コードのみを Git コミット対象とする。
   - 必ずブランチを作成して修正し、テストおよび静的解析の通過を確認した後に master へマージする。
   - コミット規約: `feat(scope): message` / `fix(scope): message` / `refactor(scope): message` / `docs(scope): message` / `test(scope): message` / `chore(scope): message`。
4. **Domain-Driven Modular API & Parallel Engine**
   - 物理演算は領域別（空気力学、風場、大気、応力、6DoFシミュレーション、最適化、メッシュ解析）に独立した Flask Blueprint API として実装する。
   - 大規模な風場演算および複数飛行パスのシミュレーションには Python の `concurrent.futures` / `multiprocessing` および GPU（CuPy / CUDA）を活用する。
   - **GPU非搭載環境への安全なCPU/NumPyフォールバック**: GPUが利用できない環境、またはGPU初期化に失敗した環境では、例外送出や動作停止を招くことなく、安全かつ同一のスキーマと物理モデルでCPU/NumPyフォールバックを実行する（Issue #35 対応）。
5. **High-Performance GPU Rendering & Web Workers**
   - フロントエンドの風粒子（数万〜数十万粒子）や応力ヒートマップ描画は Three.js GPGPU / GPU Instanced Mesh / Custom Shaders をフル活用し、描画負荷をGPUへオフロードする。
   - 重いJS処理は Web Workers に委託し、メインUIスレッド（60FPS/120FPS）を阻害しない。
6. **Google Minimalist UI/UX Standard (Google風UIデザイン)**
   - Google Clean / Material Design に完全準拠。白ベース（`#F8F9FA` / `#FFFFFF`）、角丸カード型浮遊パネル、Google Blue (`#1A73E8`) を基調としたデザイン。
7. **作成・完了報告と Discord Webhook 連携**
   - タスクの開始・終了時には、タイトル、タイムスタンプ、修正内容要約（100文字程度）を Discord Webhook に送信する。サブエージェント起動時にも通知を行う。
   - **Webhook URLの管理**: Webhook URLは環境変数 `DISCORD_WEBHOOK_URL` または `.env` ファイルで設定し、リポジトリへの直書き・コミットを厳禁とする（`.env` は `.gitignore` 対象）。未設定時は送信をスキップして警告を出力する。
     - 通知スクリプト: `python tools/notify_discord.py <start|subagent|end|fail|warning|question> <title> <summary> [extra_info]`
   - **状態カラー規約**:
     - 開始: 緑 (`#2ECC71` / `3066993`)
     - サブエージェント: オレンジ (`#E67E22` / `15105570`)
     - 終了: 青 (`#3498DB` / `3447003`)
     - 失敗: 赤 (`#E74C3C` / `15158332`)
     - 注意点: 黄色 (`#F1C40F` / `15844367`)
     - ユーザーへの質問: ピンク (`#E91E63` / `15277667`)
8. **品質保証・CI/CD・課題完結プロトコル**
   - 単体テスト・統合テスト（`pytest`, `smoke_test.py`）、静的検査（`ruff`, `mypy`）で完全検証する。
   - 変更を GitHub にプッシュした後は GitHub Actions のステータスを監視し、Issue や PR に残存タスクがあればそれらを完全に解決・マージして完了とする。
9. **二層プロトコル構造 (.agents/ OS 連携)**
   - **ルート `AGENTS.md` (本書)**: 本シミュレーター固有の物理モデル、API仕様、アーキテクチャ、および監査基準を規定する。
   - **`.agents/AGENTS.md`**: プロジェクト非依存の自律実行ループOS（Observe & Scope → Hypothesize → Execute → Verify → Distill & Loop）を規定する。作業時は両者を連動させ、テキサスの狙撃兵の誤謬を排除し事前仮説と合否基準に基づき作業を行う。

---

## 2. 物理・航空工学的理論モデル (Physics & Theoretical Models)

本プロジェクトは、体積メッシュ上で連続の式・ナビエ–ストークス方程式を離散化して解く「体積メッシュ Navier-Stokes CFD ソルバー」ではなく、**AeroSandbox 準拠の 6自由度 (6DoF) 飛行力学方程式、Helmbold/Polhamus 三角翼・低アスペクト比経験空力モデル、Viterna 失速遷移、および静的空力パネル法を組み合わせた高精度・高速シミュレーター**である（Issue #29 明記）。

### 2.1 空気力学モデル (Aerodynamics)
- **基礎空気力**:
  $$L = \frac{1}{2} \rho v^2 S C_L(\alpha), \quad D = \frac{1}{2} \rho v^2 S C_D(\alpha)$$
- **揚力傾斜と三角翼ボルテックスリフト (Helmbold & Polhamus)**:
  低アスペクト比（$AR \le 4$）の紙飛行機に対し、線形付着流揚力に前縁剥離渦（Leading-edge vortex）による非線形吸い出し揚力を重畳:
  $$C_{L\alpha} = \frac{2\pi AR}{2 + \sqrt{AR^2 (1 + \tan^2 \Lambda_{c/2}) + 4}}$$
  $$C_L(\alpha) = K_p \sin\alpha \cos^2\alpha + K_v \sin^2\alpha \cos\alpha$$
- **高迎角・失速後遷移 (Viterna-Corrigan Method)**:
  失速迎角 $\alpha_{stall}$ を超える深失速・逆流領域において、平板の剥離流極限（$C_{D,\max} \approx 1.8 \sim 2.0$）へと滑らかに接続。
- **誘導抗力と地面効果 (Ground Effect, Wieselsberger)**:
  高度 $h$、翼幅 $b$、オズワルド効率係数 $e$ に基づく誘導抗力低減:
  $$C_{Di}(h) = \frac{C_L^2}{\pi e AR} \cdot \left[ \frac{16(h/b)^2}{1 + 16(h/b)^2} \right]$$
- **用紙特性と受動的空力弾性モーフィング (Passive Aeroelastic Morphing)**:
  坪量 ($g/m^2$) と剛性に基づく、動圧 $q = \frac{1}{2}\rho v^2$ 依存の非線形後縁トリム変形モデル。高動圧投擲域（$q \gg q_{ref}$）での頭上げ急激ループ（Looping）を抑制し、頂点・滑空域（$q \to 0$）で本来のトリム角へ自動復元する:
  $$\delta_{eff}(q) = \delta_{trim} \cdot \left[ \frac{1}{1 + (q / q_{aeroelastic})^{n_{morph}}} \right]$$
- **極低速頂点遷移力学 (Apex Low-Speed Pushover Dynamics)**:
  急上昇の頂点（$V < 2.0\,\text{m/s}$）において、動圧低下によるモーメント特異点を防ぎ、風圧中心 $x_{cp}$ の後退と質量中心偏心による機首下げモーメントを生成。重力と微小相対風による自然なプッシュオーバー（滑空姿勢への遷移）を実現する。

### 2.2 大気・環境モデル (Atmosphere & Fluid Dynamics)
- **大気モデル**:
  - ISA（国際標準大気）: $T = T_0 - L h$、$p = p_0 (T/T_0)^{g/(L R)}$、$\rho = p/(R T)$。
  - 局所大気: 気温・気圧・相対湿度から水蒸気圧を算出し、乾燥空気と水蒸気の混合比により実効気体定数と密度を算出。
  - Sutherland の式による動粘性係数 $\mu(T)$ 計算およびレイノルズ数 $Re = \rho V c / \mu$ の算出。
- **3D 風場モデル (Wind Field & Turbulence)**:
  - 3D 空間 Simplex ノイズによる空間相関乱流、高度境界層プロファイル（対数則 / べき乗則）、熱上昇気流（Thermal コラム）。
  - **座標系変換の厳密性**: 風場ドメインは鉛直軸を $+z$ 上向きとして定義し、飛行力学ドメイン（世界 NED 座標系: $+x$ 北/前, $+y$ 東/右, $+z$ 下向き）への受け渡し時に $w_{\text{NED}} = -w_{\text{wind}}$ の符号変換を厳密に実行（Issue #30 整合性検証済み）。

### 2.3 構造応力モデル (Structural Stress)
- 動圧 $q$、迎角 $\alpha$、および作用加速度（Gフォース）から翼根曲げモーメントを算出。
- 翼面各頂点に対する動的応力評価値（0.0: 安全 〜 1.0: 破断限界）を算出し、3D ヒートマップとして可視化。

### 2.4 6DoF 運動方程式積分 (Flight Dynamics Engine)
- 四元数（Quaternion）表現による特異点フリーの機体姿勢更新。
- 4次ルンゲ＝クッタ法 (RK4) による数値積分。
- 急激な挙動変化に対応する可変時間刻み適応サブステッピング（Adaptive Sub-stepping）および固定刻み解析の系統的収束性保証（Issue #31 収束性検証済み）。

---

## 3. GPU・並列計算アーキテクチャ (Parallel Engine & GPU Acceleration)

### 3.1 バックエンド並列・GPU加速
- **CuPy / CUDA による一括並列シミュレーション**:
  - 3D 風場グリッド配列の一括ベクトル計算。
  - 複数飛行ケース（モンテカルロ解析、感度解析、最適化母集団）の一括並列 RK4 積分。
  - **ケース別 duration_s の個別反映**: バッチシミュレーション内においてケースごとに異なる打ち切り時間上限 `duration_s` を正確に保持・判定し、タイムアウトと着地時刻を個別算出（Issue #28 解決済み）。
- **安全な CPU / NumPy フォールバック**:
  - GPU 非搭載環境や CUDA 初期化不可環境では、NumPy および `concurrent.futures` / `multiprocessing` による CPU 並列計算へ自動移行。
  - CPU 経路と GPU 経路で出力 JSON スキーマ（`landed`, `crashed`, `status`, `horizontal_range_m` 等）および物理計算結果が完全一致することを保証（Issue #35 整合性検証済み）。

### 3.2 フロントエンド GPU レンダリング
- Three.js Custom Vertex & Fragment Shaders による翼面動的応力ヒートマップ描画。
- GPGPU / Transform Feedback 粒子システムによる 100,000+ 個の風流線パーティクルリアルタイム描画。
- Web Workers によるテレメトリバッファリングと非同期処理。

---

## 4. エージェント役割定義 v4 (Agent Roles v4)

| # | エージェント | 担当ファイル | 必須実装内容 |
|---|-----------|------------|------------|
| 0 | `Manager` | `physics/config_schema.py`, `physics/geometry_utils.py`, `app.py`, `smoke_test.py`, `AGENTS.md` | 契約定義・API Blueprint統合・UI統合・統合テスト・Git制御 |
| 1 | `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | 翼型・失速・Drag Polar・Trim・受動空力弾性・低速頂点遷移 |
| 2 | `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | ISA・Sutherland・全可変大気・風場 Simplex ノイズ・座標変換整合性 |
| 3 | `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | 動圧・Gフォース・翼根曲げモーメント・頂点応力ヒートマップ |
| 4 | `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | glTF/GLB/OBJ/STL 解析・慣性テンソル・パネル法・SSRF防止 |
| 5 | `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | 6DoF RK4・四元数・適応/固定時間刻み収束性・リングバッファ |
| 6 | `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | GPU一括並列・ケース別duration_s・CPU完全一致フォールバック・NSGA-II |
| 7 | `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | JobManager・ETA 指数移動平均・進捗管理・JSON/CSV エクスポート |
| 8 | `ui-gpu-viz` | `templates/index.html`, `static/css/console.css`, `static/js/*.js` | Google Clean UI・Three.js シェーダー・時間軸制御・リアルタイムグラフ |

---

## 5. REST API 完全仕様 (Comprehensive REST API Specification)

全エンドポイントは Blueprint 別に分割管理され、不正入力には HTTP 400 と詳細メッセージ、内部例外には HTTP 500 と追跡用 `error_id` を返却し、スタックトレースを秘匿する（Issue #34）。

### 5.1 Meta API (`api/routes/meta.py`)
- `GET /api/v1/meta/health`: サーバー状態、GPU 利用可否、バックエンド情報
- `GET /api/v1/meta/schema`: 全設定パラメータの dataclass / JSON スキーマ定義
- `GET /api/v1/meta/physics-references`: 学術引用・理論的出典レジストリ（Helmbold, Viterna, Wieselsberger 等の適用範囲と典拠一覧、Issue #32）
- `GET /api/v1/meta/test-fault`: エラーハンドリング・トレースバック秘匿検証用フォールト注入エンドポイント（Issue #34）

### 5.2 Physics API (`api/routes/physics.py`)
- `POST /api/v1/aerodynamics/calculate`: 揚力・抗力・モーメント・失速状態・$C_{D,\min}$・$(L/D)_{\max}$
- `POST /api/v1/aerodynamics/polar`: 迎角 $\alpha$ スイープに対する Drag Polar 特性曲線
- `POST /api/v1/environment/atmosphere`: 環境大気パラメータ設定 + 密度・動粘性・音速を即時返却
- `GET /api/v1/environment/current`: 現在の環境パラメータ取得
- `POST /api/v1/wind/field`: 指定 3D 空間座標群に対する風速ベクトル場取得（GPU / CPU）
- `POST /api/v1/stress/analysis`: 翼面動圧・Gフォース応力ヒートマップデータ取得

### 5.3 Simulation API (`api/routes/simulation.py`)
- `POST /api/v1/simulation/step`: 6DoF 1 ステップ運動方程式更新（可変 $\Delta t$）
- `POST /api/v1/simulation/reset`: シミュレーション状態の初期化
- `POST /api/v1/simulation/batch`: 複数パラメータ一括並列シミュレーション（GPU / CPU フォールバック対応）

### 5.4 Mesh API (`api/routes/mesh.py`)
- `POST /api/v1/mesh/import`: 3D モデルファイル（glTF / GLB / OBJ / STL）または URL 取込（SSRF 防止機構付き、Issue #37）
- `GET /api/v1/mesh/models`: 取込済みモデル一覧
- `POST /api/v1/mesh/select`: シミュレーション対象モデルの選択
- `GET /api/v1/mesh/metrics/<model_id>`: 面積・スパン・重心・慣性テンソル・パネル分割データ取得

### 5.5 Jobs & Optimization API (`api/routes/jobs.py`)
- `POST /api/v1/optimizer/run`: 多目的最適化開始（非ブロッキング、即 `job_id` 返却）
- `GET /api/v1/optimizer/status/<job_id>`: 進捗率・ETA・推定完了時刻（指数移動平均平滑化）
- `POST /api/v1/optimizer/cancel/<job_id>`: 実行中ジョブの中断
- `GET /api/v1/optimizer/result/<job_id>`: パレート解および最適パラメータ取得
- `POST /api/v1/jobs/investigate`: 飛行診断（失速・スパイラル・過剰トリム）と日本語自動助言生成ジョブ起動
- `GET /api/v1/jobs`: 全バックグラウンドジョブ一覧

### 5.6 Records & Data API (`api/routes/records.py`)
- `GET /api/v1/records/presets`: 登録済み機体プリセット（戸田拓夫氏滞空記録機、Suzanne、Dart 等）一覧
- `GET /api/v1/records/preset/<name>`: 特定プリセットの詳細パラメータ取得
- `GET /api/v1/records/guinness`: ギネス世界記録機（29.2秒滞空）のベンチマーク仕様
- `POST /api/v1/data/export`: 飛行履歴ログ（CSV / JSON）のエクスポート
- `POST /api/v1/data/import`: 外部設定・パラメータのインポート
- `GET /api/v1/records/errors`: サーバー内部エラー履歴の取得

---

## 6. セキュリティと堅牢性設計 (Security & Robustness Standards)

1. **例外トレースバックのクライアント遮断 (Information Leakage Prevention, Issue #34)**:
   - 本番 API 応答において、未処理例外発生時にファイルパスやコールスタックなどの内部実装詳細を HTTP レスポンスに含めることを厳禁とする。
   - レスポンスにはユニークな `error_id` のみを返却し、詳細トレースバックはサーバーログにのみ記録する。
2. **SSRF (Server-Side Request Forgery) 防止 (Issue #37)**:
   - 外部 URL からの 3D メッシュ取り込みにおいて、プライベート IP（`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`）、ループバック（`127.0.0.1`）、リンクローカル（`169.254.0.0/16`）、クラウドメタデータエンドポイント（AWS/GCP/Azure）、および非標準ポートへのアクセスを遮断する。
   - HTTP リダイレクト時の多段検証を徹底する。
3. **座標系の一貫性と変換の検証 (Frame Transformation Integrity, Issue #30)**:
   - 風場座標系（$+z$ 上向き）と世界 NED 座標系（$+z$ 下向き）の相互受け渡し境界で符号変換を厳密に行い、無風時および各風況における単体テストで整合性を検証する。

---

## 7. 学術引用・文献根拠レジストリ (Academic Reference Registry)

本シミュレーターで採用されている数式および物理モデルは、以下の航空工学・流体力学の学術文献に準拠している（Issue #32 / `/api/v1/meta/physics-references` で公開）:

1. **Helmbold (1942)**: 低アスペクト比直線翼の揚力傾斜経験式 ($AR \le 4$)。
2. **Polhamus (1966, 1971)**: 三角翼の前縁剥離渦による吸い出し揚力概念（Leading-Edge Suction Analogy）。
3. **Viterna & Corrigan (1982)**: 風力タービンおよび翼型の深失速・失速後空力特性モデル。
4. **Wieselsberger (1921)**: 翼幅および高度比に基づく地面効果誘導抗力低減理論。
5. **Hoerner (1965)**: 乱流境界層摩擦抗力係数・形状抗力係数の経験データ（*Fluid-Dynamic Drag*）。
6. **Sutherland (1893)**: 気体の動粘性係数の温度依存性式。
7. **AeroSandbox (Bremwand et al.)**: 6DoF 飛行力学および微小擾乱安定微係数の定式化。

---

## 8. 監査課題解決履歴 (Audit Resolutions)

プロジェクト品質監査で提起された重要課題（Issue #28〜#37）は、以下のとおりすべて解決・テスト済みである:

| Issue | 分類 | 課題内容 | 解決内容・検証テスト |
|---|---|---|---|
| **#28** | Bug | GPUバッチでケース別の `duration_s` が `landing_time` に反映されない疑い | ケースごとの上限時間を配列保持し個別タイムアウト・着地判定を実装。`tests/test_issue28_gpu_duration.py` で検証合格。 |
| **#29** | Physics | 6DoF 飛行力学モデルと体積メッシュ CFD の区別明確化 | README および AGENTS.md に AeroSandbox 準拠 6DoF + パネル法モデルであることを明記。 |
| **#30** | Physics | 風場（+z上向き）と世界NED（+z下向き）の座標変換整合性 | 鉛直風符号変換経路を厳密化。`tests/test_security_and_frames_audit.py` で上昇風・下降風・無風を検証合格。 |
| **#31** | Numerics | 固定時間刻み RK4 積分の系統的収束性の検証 | $\Delta t = 0.04 \to 0.005\,\text{s}$ の刻み細分化テストを実装し単調収束を実証。`tests/test_advanced_physics_audit.py` で検証合格。 |
| **#32** | Aerodynamics | 空力係数の理論・文献出典の独立検証と公開 | `/api/v1/meta/physics-references` エンドポイントを新設し学術引用レジストリを公開。単体テスト検証合格。 |
| **#33** | Wind Model | 3D風場の有界発散・空間相関・時間連続性の検証 | 乱流場の発散有界性、空間距離に応じた相関減衰、および時間連続性を数値評価。`tests/test_advanced_physics_audit.py` で検証合格。 |
| **#34** | Security | 予期しない例外時の内部トレースバック漏洩防止 | `api_error()` を改修し、本番環境で `error_id` のみを返却。`tests/test_security_and_frames_audit.py` で検証合格。 |
| **#35** | GPU | CPU版とGPU版の計算結果・返却スキーマの完全一致 | `gpu_batch_evaluate_cases` の CPU フォールバックを統一スキーマ化。CPU/GPU 一致テストで検証合格。 |
| **#36** | Mesh | パネル法のメッシュ解像度依存性と揚力整合性の検証 | パネル細分化に伴う揚力・抗力・モーメントの漸近収束を実証。`tests/test_advanced_physics_audit.py` で検証合格。 |
| **#37** | Security | 3Dメッシュインポート時の SSRF 防止機構 | プライベートIP・ループバック・クラウドメタデータ・非標準ポートを遮断。`tests/test_security_and_frames_audit.py` で検証合格。 |

---

## 9. ディレクトリ構成 (Directory Layout)

```
.
├── AGENTS.md                  # 本仕様書 (プロジェクト固有の仕様・アーキテクチャ・検証基準)
├── prompt.md                  # 実行プロンプト
├── requirements.txt           # Python 依存関係
├── smoke_test.py              # 全 13 ステージ統合テスト (API 連鎖・最適化ジョブ・メッシュ・ETA)
├── app.py                     # Flask エントリーポイント & Blueprint ルーター
├── .agents/                   # プロジェクト非依存 自律実行ループ OS プロトコル
│   ├── AGENTS.md              # コンテキスト最小化・テキサスの狙撃兵排除・5フェーズ自律ループ
│   ├── Project.md             # プロジェクトゴール & 現在のループ状態 (Loop State)
│   ├── Knowledge-important.md # 常駐制約・重要知見リスト
│   ├── Knowledge.md           # オンデマンド参照ナレッジベース
│   └── skills/                # 定型作業手順 (loop-handover.md など)
├── api/                       # 分割された REST API パッケージ
│   ├── __init__.py
│   ├── core.py                # Session 状態・共通ヘルパー・エラー応答 (トレースバック秘匿)
│   └── routes/                # 責務別 Blueprint ルーティング
│       ├── __init__.py
│       ├── meta.py            # メタデータ・学術引用・スキーマ・障害テスト
│       ├── physics.py         # 空気力学・大気・風場・応力解析
│       ├── simulation.py      # 6DoF 飛行積分・リセット・一括並列
│       ├── mesh.py            # 3D メッシュ取込 (SSRF防止)・選択・物理量解析
│       ├── jobs.py            # 最適化 & 自動調査バックグラウンドジョブ
│       └── records.py         # ギネス記録・プリセット・ログ出力・エラー履歴
├── physics/                   # 物理・シミュレーションコアエンジン
│   ├── __init__.py
│   ├── config_schema.py       # 全 dataclass / JSON スキーマ定義
│   ├── geometry_utils.py      # 四元数・Euler 変換・座標変換
│   ├── aero_domain.py         # Helmbold/Polhamus/Viterna/受動空力弾性
│   ├── atmosphere_domain.py   # ISA・局所大気・Sutherland動粘性
│   ├── wind_domain.py         # 3D Simplex ノイズ風場・乱流・熱上昇気流
│   ├── stress_domain.py       # 動圧・Gフォース・翼根曲げモーメント・頂点応力
│   ├── mesh_domain.py         # glTF/GLB/OBJ/STL パネル法・慣性テンソル
│   ├── flight_sim.py          # 6DoF RK4 数値積分・適応サブステッピング
│   ├── parallel_engine.py     # CuPy GPU 並列 / CPU 並列フォールバック
│   ├── optimizer_domain.py    # NSGA-II / DE / Nelder-Mead / Surrogate
│   ├── job_manager.py         # ジョブ進捗・指数移動平均 ETA 計算
│   ├── storage_domain.py      # CSV / JSON 永続化・エクスポート
│   ├── auto_investigate.py    # 自動調査・チューニング後方互換ファサード
│   └── investigate/           # 自動調査・診断・助言パッケージ
│       ├── __init__.py
│       ├── models.py          # 調査データモデル
│       ├── diagnostics.py     # 飛行診断エンジン (失速・スパイラル判定)
│       ├── advice.py          # 日本語折り方・投げ方改善アドバイス生成
│       └── tuning.py          # 1D感度スイープ & 局所チューニング
├── tests/                     # 総合テストスイート (700+ テスト)
│   ├── test_aero.py
│   ├── test_atmo_wind.py
│   ├── test_stress.py
│   ├── test_mesh.py
│   ├── test_flight.py
│   ├── test_optimizer.py
│   ├── test_jobs.py
│   ├── test_investigate.py
│   ├── test_issue28_gpu_duration.py         # GPU バッチ duration_s タイムアウト検証
│   ├── test_security_and_frames_audit.py    # 座標変換・SSRF防止・トレースバック秘匿検証
│   └── test_advanced_physics_audit.py       # 固定刻み収束性・風場相関・パネル法解像度検証
├── tools/                     # 開発・運用・検証用ツール群
│   ├── notify_discord.py      # Discord Webhook 通知
│   ├── discord_bridge.py      # Discord Bot リモートシミュレーション操作
│   ├── run_ci_tests.py        # ローカル CI 総合テスト実行
│   ├── check_runs.py          # GitHub Actions 実行状況チェッカー
│   ├── check_issues.py        # GitHub オープン Issue 点検
│   └── close_resolved_issue.py# 解決済み Issue 自動クローズ
├── templates/
│   └── index.html             # Google Clean スタイル計器コンソール
├── static/
│   ├── css/console.css        # コンソールスタイル
│   └── js/                    # Three.js 3D ビューア & UI スクリプト
│       ├── app.js             # メインエントリ
│       ├── api.js             # REST API クライアント
│       ├── viewport.js        # Three.js シーン・カメラ・レンダラー
│       ├── airframe.js        # 機体メッシュ・動的応力ヒートマップ描画
│       ├── flowfield.js       # GPGPU 風流線粒子システム
│       ├── transport.js       # 時間軸制御 (再生/停止/シーク/倍速)
│       ├── telemetry.js       # 飛行計器・リアルタイム Chart.js 描画
│       ├── panels.js          # パラメータ調整パネル
│       ├── optimizer.js       # 最適化パネル & ETA 表示
│       ├── meshpanel.js       # 3D モデルインポート UI
│       └── datapanel.js       # データ入出力 UI
└── models/                    # 取込 3D モデルのキャッシュ (.gitignore 対象)
```

---

## 10. 検証コマンドと CI/CD ガイド (Verification & CI/CD Protocol)

コード変更時は、以下の手順で段階的かつ厳密に検証を実施すること:

```powershell
# 1. 構文検証
python -m compileall physics api tools app.py smoke_test.py

# 2. 静的型検査・コード品質検査
ruff check .
mypy physics api app.py smoke_test.py

# 3. ユニットテストおよび監査テストの全実行
python -m pytest tests -q

# 4. エンドツーエンド統合テスト (API・最適化ジョブ・ETA・3Dモデル解析)
python smoke_test.py
```

### GitHub Actions および Git 運用
1. 作業開始前に必ず専用トピックブランチを作成する (`git checkout -b <type>/<description>`)。
2. 変更・修正後はローカル環境で上記全テストの通過を確認する。
3. コミット後、ブランチを push し、master にマージする。
4. GitHub Actions (Ubuntu/Windows × Python 3.10/3.11/3.12, CodeQL, pip-audit) の全ジョブがグリーンであることを確認する。
5. 解決した Issue に対応するクローズ処理および Discord への完了通知を送信する。