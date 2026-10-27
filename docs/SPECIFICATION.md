# 3D Paper Airplane High-Performance 6DoF Flight Simulator & Aero-Structural Engine Specification

本ドキュメントは、航空力学・構造力学に基づいた「3D紙飛行機シミュレーター」の理論物理モデル、GPUアクセラレーション・並列アーキテクチャ、REST API仕様、セキュリティ基準、および学術的典拠を網羅した包括的技術仕様書である。

---

## 1. 物理・航空工学的理論モデル (Physics & Theoretical Models)

本プロジェクトは、体積メッシュ上で連続の式・ナビエ–ストークス方程式を離散化して解く「体積メッシュ Navier-Stokes CFD ソルバー」ではなく、**AeroSandbox 準拠の 6自由度 (6DoF) 飛行力学方程式、Helmbold/Polhamus 三角翼・低アスペクト比経験空力モデル、Viterna 失速遷移、および静的空力パネル法を組み合わせた高精度・高速シミュレーター**である（Issue #29 明記）。

### 1.1 空気力学モデル (Aerodynamics)
- **基礎空気力**:
  $$L = \frac{1}{2} \rho v^2 S C_L(\alpha), \quad D = \frac{1}{2} \rho v^2 S C_D(\alpha)$$
- **揚力傾斜と三角翼ボルテックスリフト (Helmbold & Polhamus)**:
  低アスペクト比（$AR \le 4$）の紙飛行機に対し、線形付着流揚力に前縁剥離渦（Leading-edge vortex）による非線形吸い出し揚力を重畳:
  $$C_{L\alpha} = \frac{2\pi AR}{2 + \sqrt{AR^2 (1 + \lambda) + 4AR/\pi}}$$
  （Helmbold 1942 の三角翼式をテーパ翼へ拡張した形。**本式が単一の実装**（`physics.aero_domain.helmbold_cl_alpha`）であり、6DoF 経路・GPU アンサンブル経路・GPU 最適化バッチ経路・構造モジュールの揚力傾斜はすべてこの値を参照する。$4AR/\pi$ を定数 $4$ に置換した旧 GPU 式（+11.3 %）は廃止済み。）
  $$C_L(\alpha) = K_p \sin\alpha \cos^2\alpha + K_v \sin^2\alpha \cos\alpha$$
  （Polhamus 1966/1971 の公開式。$K_p$ を明示指定する場合は `polhamus_kp`、未指定（既定 0）の場合は機体自身の 3 次元 Helmbold 傾斜が $K_p$ を担い、$K_v$ の渦吸い出し増分のみ重畳される。）
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
- **全幅後縁折れの空力増分 (Trailing-Edge Fold Effects)**:
  全幅後縁の折れ角 $\phi$ = `fold_trailing_deg`（正 = 後縁を下へ折る = キャンバー増）に対し、空力係数へ次の増分を加える:
  $$\Delta C_L = 0.015\,\phi, \quad \Delta C_m = -0.012\,\phi, \quad \Delta C_D = 0.035\,\sin^2\phi$$
  （$\phi$ は度。正の折れは揚力を増やし機首下げモーメントを生む。負の折れ（リフレックス）は揚力を減らし機首上げトリムを生む。$\Delta C_L$ と $\Delta C_m$ は $|\Delta C_L| \le C_{L,\max}$ を満たすよう**同時に**スケールされ（その比 $\Delta C_m/\Delta C_L$ = 後縁における増分作用点）、$\Delta C_D$ は独立に $\sin^2\phi$ で増加する。実装は `physics/aero_kernel.py::trailing_edge_fold_effects`（ベクトル化カーネル）で、6DoF 正規経路（`physics/aero_domain.py`）、GPU 最適化バッチ経路（`physics/gpu_dynamics.py`）、Drag Polar / Trim 経路のすべてが**同一の共有カーネル `evaluate_coefficients` 経由**で同じ係数を使う。**係数の値は §5.2 のとおり据え置き**（出典未取得）。）

### 1.2 大気・環境モデル (Atmosphere & Fluid Dynamics)
- **大気モデル**:
  - ISA（国際標準大気）: $T = T_0 - L h$、$p = p_0 (T/T_0)^{g/(L R)}$、$\rho = p/(R T)$。
  - 局所大気: 気温・気圧・相対湿度から水蒸気圧を算出し、乾燥空気と水蒸気の混合比により実効気体定数と密度を算出。
  - Sutherland の式による動粘性係数 $\mu(T)$ 計算およびレイノルズ数 $Re = \rho V c / \mu$ の算出。
- **3D 風場モデル (Wind Field & Turbulence)**:
  - 3D 空間 Simplex ノイズによる空間相関乱流、高度境界層プロファイル（対数則 / べき乗則）、熱上昇気流（Thermal コラム）。
  - **座標系変換の厳密性**: 風場ドメインは鉛直軸を $+z$ 上向き、水平コンパス軸を north=$+y$ / east=$+x$（`physics/wind_domain.py` が正本）として定義し、`wind_direction_deg` は気象庁風向（北から時計回りの「吹いてくる」方位；流れベクトルはその逆を指す）とする。飛行力学ドメイン（世界 NED 座標系: $+x$ 北/前, $+y$ 東/右, $+z$ 下向き）への受け渡し時に $w_{\text{NED}} = -w_{\text{wind}}$ の符号変換を厳密に実行（Issue #30 整合性検証済み）。

### 1.3 構造応力モデル (Structural Stress)
- 動圧 $q$、迎角 $\alpha$、および作用加速度（Gフォース）から翼根曲げモーメントを算出。
- 翼面各頂点に対する動的応力評価値（0.0: 安全 〜 1.0: 破断限界）を算出し、3D ヒートマップとして可視化。
- **応力インデックスの正規化尺度は単一**: `stress_index = sigma / sigma_allow` であり、分母は機体固有の許容折り応力 `paper_allowable_stress(gsm, stiffness)`（既定 100 g/m²・剛性 0.35 で 2.2 kPa）である。6DoF 経路・ヒートマップ・GPU 最適化バッチ・構造破壊判定はすべてこの尺度を共有する（旧来の 25 kPa / 120 kPa / 2.0e4·gsm の 3 系統は廃止）。荷重分布の min-max 形状は `stress_shape` チャンネルで別途公開する。
- **応力較正係数 `KAPPA_SIGMA`**: 翼根曲げモーメント $M$ と矩形板の断面係数 $Z = b\,t^2/6$（$b$ = 局所弦長、$t$ = 展開シート厚 = `gsm/800`）から応力を $\sigma = \texttt{KAPPA\_SIGMA}\,M/Z$ として算出する。`physics/stress_domain.py::KAPPA_SIGMA = 9.0e-6` は **Beam 理論値へ掛ける無次元経験的較正係数**であり、報告応力を Beam 理論値の約 1/111000 に縮める（`stress_field` / `section_modulus_batch` の双方）。寸法的な意味は無く、§1.3 の `stress_index` の分子側を規定する。**値は §5.2 のとおり据え置き**（出典未取得）。

### 1.4 6DoF 運動方程式積分 (Flight Dynamics Engine)
- 四元数（Quaternion）表現による特異点フリーの機体姿勢更新。
- 4次ルンゲ＝クッタ法 (RK4) による数値積分。
- 急激な挙動変化に対応する可変時間刻み適応サブステッピング（Adaptive Sub-stepping）および固定刻み解析の系統的収束性保証（Issue #31 収束性検証済み）。

### 1.5 折り紙ジオメトリモデル (Procedural Origami Geometry)
`physics/origami_domain.py` は A4 用紙（縦横比 1:√2, 210 × 297 mm）から紙飛行機の
3D モデルを**折り工程そのもののシミュレーション**として生成する。解析的な板ポリゴン
の組合せではなく、展開図（Crease Pattern）を切り折りして得られる多層ソリッドである。

**座標系**: 平面は正規化単位正方形 `(u, v)`（`u` = 短辺方向、`v` = 長辺方向、機首は `v = 1`）。
三次元への写像は `x = (v - 0.5) * L`（前後、+x が機首）、`y = 0`（上）、`z = (u - 0.5) * W`
（左右、+z が右翼）。この枠は右手系であり NED シミュレーション枠とは異成因
（描画側は `static/js/viewport.js` の `Rx(+90°)` で変換する）。

**折り工程（ヒンジ列）**:
1. `body`: 中央縦の基線（対称折り、$\pm(180°-3°)/2$）。左右_body_ は約 177° 開いた
   V 字のキール（中心线に微小な V ギャップ `keel_beta_deg`）を形成する。
2. `corner_l/r`: 左右上角を中央線へ 45° で折り下げる谷折り（機首第 1 層の重なり）。
   折り目は実寸の紙面（長辺 297 mm / 短辺 210 mm）上で定義するため、角は必ず中央線へ
   正確に着地する。
3. `collar_l/r`: 折れ上がった角の斜辺と中央線の二等分線に沿った折り目。前縁の厚い
   折り返しと鋭い機首（嘴）を作り、機首は片側 3 層・両側最大 6 層に重なる。
4. `wing_l/r`: 胴体キールを残して主翼を水平（微小上反角付き）に折り下げる。
5. `winglet_l/r`, `elevon_l/r`, `trailing`: 翼端のウイングレットと後縁のエレボン反り。

折り目の順序は物理的な折り順と一致し、各折り目は**その時の創位置（現在位置）**を軸に
回転する（`T' = F ∘ T`）。同一折り目が跨ぐ層は同一の三次元直線を共有するため、
折り目は重なりとして連続して折れる。

**レイヤーオフセット（Z-Fighting 完全排除の証明）**:
- 紙厚 $t$ = `VISUAL_THICKNESS_M` = 0.30 mm（実紙 0.10 mm の 3 倍の視覚強調）。
- 空気隙 `LAYER_AIR_GAP_M` = 0.12 mm、レイヤーピッチ `LAYER_PITCH_M` = $t$ + gap = 0.42 mm。
- 互いに平行かつ投影が重なる面のみを同一**スタック**に集群化し、スタック法線方向の
  符号付き深さ `d` で順序付け、**重なり関係 DAG の最長路**を層インデックス $k_f$ とする。
- 重なっている任意の 2 面の層差 satisfies $k_B \ge k_A + 1$（最長路の定義より）であり、
  変位後の中間面間隔は $\ge (k_B - k_A) \cdot \mathrm{PITCH} \ge \mathrm{PITCH} > t$。
  厚さは各面が中間面の $\pm t/2$ しか占有しないため、**2 つの層は交わらず同一面にも
  ならない**（Z-Fighting は構造的に発生し得ない）。機首では片側 3 層・両側合計
  5〜6 層が接触する（実装上の最深部スタック層インデックスは 8）。
- オフセット量は折り工程 $\alpha$ に比例して 0 → 1 で増加し、
  $\alpha = 0$（平たい用紙）では全層が同一面になる。

**ソリッド生成**: 各面は上スキム・下スキム・側壁（カットエッジ）の**閉じたスラブ**として
出力され、紙の断面は面積を持つ矩形になる。折り目は**ロール折り**（3 次ベジエ）で
閉じられ、接線連続な normals 勾配（折れ目のハイライトとキャビティ陰影）を与える。
折り目は 2 分割され、`crease_weight` 属性として頂点に焼き込まれる。

**頂点属性**（`BufferGeometry` カスタム属性）:
| 属性 | 意味 |
|---|---|
| `aLayer` | その位置の紙層数（1 = 単層、4〜6 = 機首パケット） |
| `aCrease` | 折り目への近接度 0..1（キャビティ陰影・ハイライト） |
| `aElastic` | 翼端方向の重み（受動空力弾性曲げ用、胴体キールは 0） |
| `aTwist` | 翼弦方向の重み（受動空力弾性ねじれ用） |
| `aSide` | 表示面が表(+1)/裏(-1) のどちらか（透過光減衰に使用） |

**受動空力弾性**: 飛行中、`aeroelastic_dihedral_deg` / `aeroelastic_twist_deg` を
uniform として紙シェーダに渡し、翼端で $\mathrm{elastic}^2$ に比例した曲げと
`aTwist` に比例したねじれを頂点変位として与える（キールは剛体）。

**3 機体アーキタイプ**:
| key | 名称 | 特徴 |
|---|---|---|
| `classic_dart` | Classic Dart / やり飛行機 | 細長いデルタ翼、機首 4〜6 層、鋭い嘴 |
| `nakamura_lock` | Nakamura Lock / へそ飛行機 | 機首を内側に折り込む belly 折り込み、前縁が厚い、翼面積が広い |
| `sky_king` | Sky King / 競技用滞空機 | 鈍頭ノーズ（水平な折り返し）、明確なウイングレット、高アスペクト比 |

---

## 2. GPU・並列計算アーキテクチャ (Parallel Engine & GPU Acceleration)

### 2.1 バックエンド並列・GPU加速
- **CuPy / CUDA による一括並列シミュレーション**:
  - 3D 風場グリッド配列の一括ベクトル計算。
  - 複数飛行ケース（モンテカルロ解析、感度解析、最適化母集団）の一括並列評価。
  - **ケース別 duration_s の個別反映**: バッチシミュレーション内においてケースごとに異なる打ち切り時間上限 `duration_s` を正確に保持・判定し、タイムアウトと着地時刻を個別算出（Issue #28 解決済み）。
- **安全な CPU / NumPy フォールバック**:
  - GPU 非搭載環境や CUDA 初期化不可環境では、NumPy および `concurrent.futures` / `multiprocessing` による CPU 並列計算へ自動移行。
  - **返却スキーマの一致**（`landed`, `crashed`, `status`, `horizontal_range_m` 等）は保証する。
  - **経路ごとの `model` タグで物理モデルの違いを明示する**（Issue #35 の「完全一致」は過大主張であった）:
    - `model = "rigid_body_6dof"` … 正規 6DoF RK4 経路、および **同一の 13 状態剛体モデル（同じ空力カーネル・同じ風場・同じ構造モデル・同じ RK4 スキーム）をベクトル化して GPU 上で積分する GPU 最適化バッチ経路**（`model_detail = "gpu_batch_rk4"`）。両者の整合は `tools/check_gpu_parity.py` と `tests/test_gpu_kernel_parity.py` で定量検証する（実測: 最大応力・G ロードは完全一致、飛距離は 0.1〜0.9 % の差）。
    - `model = "reduced_surrogate"` … 縮小モデル。具体的には **GPU モンテカルロ・アンサンブル経路**（3 自由度の縮約積分、`model_detail = "gpu_ensemble_3dof"`）と **解析的滑翔サーロゲート**（姿勢動力学を持たない閉形式解、`model_detail = "analytical_glide_surrogate"`）。これらの `approximate = true` / `physics_verification_eligible = false` であり、6DoF 再検証の対象外である。
    - 旧実装の GPU パス（3 自由度点質量・陽的オイラー・姿勢クランプ）は 6DoF 経路と最大応力で 1261 倍、飛行 `status` すら相違した（`flying` と `crashed`）。この過大主張は撤去済み。

### 2.2 フロントエンド GPU レンダリング
- Three.js Custom Vertex & Fragment Shaders による翼面動的応力ヒートマップ描画。
- GPGPU / Transform Feedback 粒子システムによる 100,000+ 個の風流線パーティクルリアルタイム描画。
- Web Workers によるテレメトリバッファリングと非同期処理。

---

## 3. REST API 完全仕様 (Comprehensive REST API Specification)

全エンドポイントは Blueprint 別に分割管理され、不正入力には HTTP 400 と詳細メッセージ、内部例外には HTTP 500 と追跡用 `error_id` を返却し、スタックトレースを秘匿する（Issue #34）。

### 3.1 Meta API (`api/routes/meta.py`)
- `GET /api/v1/meta/health`: サーバー状態、GPU 利用可否、バックエンド情報
- `GET /api/v1/meta/schema`: 全設定パラメータの dataclass / JSON スキーマ定義
- `GET /api/v1/meta/physics-references`: 学術引用・理論的出典レジストリ（Helmbold, Viterna, Wieselsberger 等の適用範囲と典拠一覧、Issue #32）
- `GET /api/v1/meta/test-fault`: エラーハンドリング・トレースバック秘匿検証用フォールト注入エンドポイント（Issue #34）

### 3.2 Physics API (`api/routes/physics.py`)
- `POST /api/v1/aerodynamics/calculate`: 揚力・抗力・モーメント・失速状態・$C_{D,\min}$・$(L/D)_{\max}$
- `POST /api/v1/aerodynamics/polar`: 迎角 $\alpha$ スイープに対する Drag Polar 特性曲線
- `POST /api/v1/environment/atmosphere`: 環境大気パラメータ設定 + 密度・動粘性・音速を即時返却
- `GET /api/v1/environment/current`: 現在の環境パラメータ取得
- `POST /api/v1/wind/field`: 指定 3D 空間座標群に対する風速ベクトル場取得（GPU / CPU）
- `POST /api/v1/stress/analysis`: 翼面動圧・Gフォース応力ヒートマップデータ取得

### 3.3 Simulation API (`api/routes/simulation.py`)
- `POST /api/v1/simulation/step`: 6DoF 1 ステップ運動方程式更新（可変 $\Delta t$）
- `POST /api/v1/simulation/reset`: シミュレーション状態の初期化
- `POST /api/v1/simulation/batch`: 複数パラメータ一括並列シミュレーション（GPU / CPU フォールバック対応）

### 3.4 Mesh API (`api/routes/mesh.py`)
- `POST /api/v1/mesh/import`: 3D モデルファイル（glTF / GLB / OBJ / STL）または URL 取込（SSRF 防止機構付き、Issue #37）
- `GET /api/v1/mesh/models`: 取込済みモデル一覧
- `POST /api/v1/mesh/select`: シミュレーション対象モデルの選択
- `GET /api/v1/mesh/metrics/<model_id>`: 面積・スパン・重心・慣性テンソル・パネル分割データ取得

### 3.5 Jobs & Optimization API (`api/routes/jobs.py`)
- `POST /api/v1/optimizer/run`: 多目的最適化開始（非ブロッキング、即 `job_id` 返却）
- `GET /api/v1/optimizer/status/<job_id>`: 進捗率・ETA・推定完了時刻（指数移動平均平滑化）
- `POST /api/v1/optimizer/cancel/<job_id>`: 実行中ジョブの中断
- `GET /api/v1/optimizer/result/<job_id>`: パレート解および最適パラメータ取得
- `POST /api/v1/jobs/investigate`: 飛行診断（失速・スパイラル・過剰トリム）と日本語自動助言生成ジョブ起動
- `GET /api/v1/jobs`: 全バックグラウンドジョブ一覧

### 3.6 Records & Data API (`api/routes/records.py`)
- `GET /api/v1/records/presets`: 登録済み機体プリセット（戸田拓夫氏滞空記録機、Suzanne、Dart 等）一覧
- `GET /api/v1/records/preset/<name>`: 特定プリセットの詳細パラメータ取得
- `GET /api/v1/records/guinness`: ギネス世界記録機（29.2秒滞空）のベンチマーク仕様
- `POST /api/v1/data/export`: 飛行履歴ログ（CSV / JSON）のエクスポート
- `POST /api/v1/data/import`: 外部設定・パラメータのインポート
- `GET /api/v1/records/errors`: サーバー内部エラー履歴の取得

### 3.7 Origami API (`api/routes/origami.py`)
- `GET /api/v1/origami/archetypes`: 対応する 3 機体アーキタイプ（key / label / shape_flag）と
  紙厚・レイヤーピッチ等の物理定数を返す。
- `POST /api/v1/origami/mesh`: 折り畳まれた機体メッシュを返す。
  - 入力: `archetype`（`classic_dart` / `nakamura_lock` / `sky_king`）、`alpha`（折り工程 0..1）、
    `wingspan_m`（翼幅に合わせて等スケール）、`keel_beta_deg` / `dihedral_deg` /
    `elevon_deg` / `fold_tip_deg` / `fold_trailing_deg` / `fold_leading_deg`。
  - 出力: `positions` / `normals` / `uvs` / `indices`（フラット配列）と
    `layer_counts` / `crease_weights` / `elastic_weights` / `twist_weights` / `paper_sides`、
    `paper_thickness_m` / `layer_pitch_m` / `max_layers` / `bounds` / `stats`。
  - ビューアは同一生成器（`physics/origami_domain.py`）を使用するため、
    描画結果とユニットテストのジオメトリは常に一致する。

---

## 4. セキュリティと堅牢性設計 (Security & Robustness Standards)

1. **例外トレースバックのクライアント遮断 (Information Leakage Prevention, Issue #34)**:
   - 本番 API 応答において、未処理例外発生時にファイルパスやコールスタックなどの内部実装詳細を HTTP レスポンスに含めることを厳禁とする。
   - レスポンスにはユニークな `error_id` のみを返却し、詳細トレースバックはサーバーログにのみ記録する。
2. **SSRF (Server-Side Request Forgery) 防止 (Issue #37)**:
   - 外部 URL からの 3D メッシュ取り込みにおいて、プライベート IP（`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`）、ループバック（`127.0.0.1`）、リンクローカル（`169.254.0.0/16`）、クラウドメタデータエンドポイント（AWS/GCP/Azure）、および非標準ポートへのアクセスを遮断する。
   - HTTP リダイレクト時の多段検証を徹底する。
3. **座標系の一貫性と変換の検証 (Frame Transformation Integrity, Issue #30)**:
   - 風場座標系（$+z$ 上向き）と世界 NED 座標系（$+z$ 下向き）の相互受け渡し境界で符号変換を厳密に行い、無風時および各風況における単体テストで整合性を検証する。

---

## 5. 学術引用・文献根拠レジストリ (Academic Reference Registry)

本シミュレーターで採用されている数式および物理モデルは、以下の航空工学・流体力学の学術文献に準拠している（Issue #32 / `/api/v1/meta/physics-references` で公開）:

1. **Helmbold (1942)**: 低アスペクト比直線翼の揚力傾斜経験式 ($AR \le 4$)。
2. **Polhamus (1966, 1971)**: 三角翼の前縁剥離渦による吸い出し揚力概念（Leading-Edge Suction Analogy）。
3. **Viterna & Corrigan (1982)**: 風力タービンおよび翼型の深失速・失速後空力特性モデル。
4. **Wieselsberger (1921)**: 翼幅および高度比に基づく地面効果誘導抗力低減理論。
5. **Hoerner (1965)**: 乱流境界層摩擦抗力係数・形状抗力係数の経験データ（*Fluid-Dynamic Drag*）。
6. **Sutherland (1893)**: 気体の動粘性係数の温度依存性式。
7. **AeroSandbox (Bremwand et al.)**: 6DoF 飛行力学および微小擾乱安定微係数の定式化。

### 5.0 モデル別の正本・出典・適用限界 (Issue #48 / #49 / #50 / #51 / #52)

上表は **引用** の一覧であり、**実装の正本** は別文書にある。
**どのモデルが実装済みか**（`/api/v1/meta/physics-references` の各エントリの `implemented` /
`canonical_module` / `applicability` がその機械可読な宣言）、**どの式が重複定義されているか**、
**レイノルズ数への依存性**、**失速後モデルの採否の根拠** は次の 2 文書に集約している:

- **`docs/PHYSICS_MODELS.md`** — モデル別の「式 → 出典 → 正本モジュール → 適用限界 → 検証テスト」。
  Issue #48（Helmbold の式と独立検証）、#49（深失速モデルと遷移の連続性）、
  #50（Re 依存性と範囲外の扱い）、#51（重複定義の解消）、#52（Polhamus 既定値の妥当性）を解く。
- **`docs/CFD_ROADMAP.md`** — Issue #47（3D Navier–Stokes ソルバの要求仕様と段階的導入計画）。
  6DoF パネル法とは **別の層** であり、探索用 fitness は 6DoF のまま維持する。

**重要な相違**: 引用 3（Viterna & Corrigan）は **未実装** である。実装されている失速後モデルは
引用 5（Hoerner 平板）であり、その採否の根拠と適用限界は `docs/PHYSICS_MODELS.md` §3 に記す。
レジストリはこの区別を `implemented` フラグで明示する（`tests/test_physics_model_registry.py` が検証）。

### 5.1 把持フィルタ閾値の根拠（G9-1、人体工学・国際規格）

physics/origami_genome.py の GripThresholds 既定（最低高さ 8 mm / 前後長 30 mm / 最小 2 層 / CG 許容 15 mm / グリップ半幅 12 mm）は、成人手指の精密つまみ把持（Two-point precision pinch: 親指・人差し指）を対象とした公的人体計測・人間工学規格に準拠している。

**規格・文献根拠**:
1. **ISO 7250-1:2017 / ISO/TR 7250-2:2010**:
   - Clause 6.3.6（示指遠位節先端幅: 女 14〜15 mm、男 15〜18 mm）および Clause 6.3.16（母指幅: 男95%タイル 23.8 mm）。グリップ半幅 12 mm（全幅 24 mm スラブ）は成人手指の把持中心線幅に合致する。
2. **産総研（AIST）人体寸法データベース / HQL 日本人の手の寸法データ集（2010）**:
   - 示指末節長（22.5〜24.8 mm）、母指末節長（28.0〜31.2 mm）、示指指腹厚（10.5〜12.0 mm）。
3. **MIL-STD-1472H (2020) Section 5.7.3**:
   - ピンチ操作タブの最小突出高さ基準 6.4〜12.7 mm。現行の最低高さ 8 mm（min_grip_height_m = 0.008）は指腹厚の過半を接触させて滑りを防止する下限安全域。
4. **NASA-STD-3000 Section 3.3**:
   - 対向ピンチ接触長 18〜25 mm。前後長 30 mm（min_grip_length_m = 0.030）は接触域を完全にカバーし、投擲時のマージンを確保。CG許容 15 mm は接触半長に一致し、リリース時のピッチ反転モーメントを防ぐ。
5. **最小層数 2 層**: 標準紙（70〜100 g/m²）の単層面外座屈防止。

すべての閾値はコンストラクタおよび API から変更可能である。

### 5.2 出典未取得の物理定数の据え置き根拠（G-17）

§5.1 と同じ扱いだが、物理モデルの**較正係数**である。いずれも**値を改定しない**。出典のない係数を新たに作らない（AGENTS.md §1.5 / §2）。**本節に記した値 = 実装値**であり、実装側の改変はしない。

| 定数 | 値 | 物理的意味 | 据え置きの理由 | 出典 |
|---|---|---|---|---|
| `KAPPA_SIGMA` | `9.0e-6`（`physics/stress_domain.py::KAPPA_SIGMA`） | 翼根曲げモーメント $M$ と矩形板の断面係数 $Z = b\,t^2/6$ から求めた Beam 理論応力 $\sigma = \texttt{KAPPA\_SIGMA}\,M/Z$ へ掛ける**無次元経験的較正係数**。$t$ は展開シート厚（`gsm/800`）。§1.3 の `stress_index = sigma/sigma_allow` の分子側を規定する | 実紙・実折面の材料定数（引裂強度・折れ面の残留断面）に対応する出典が**監査済み範囲のどの論文にも存在しない**。値を直すと全応力値と失敗判定が同時に変わるため、**根拠を得るまで数値改定を行わない**。なお `paper_allowable_stress` の基準 2200 Pa も実紙の引張強度 20〜40 MPa ではなく、`KAPPA_SIGMA` との**組み合わせでのみ**意味を持つ | `no-finish.md` §A-1（48 行） / `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §2-H-11（462〜479 行）、定数表（1248 行）、付録B H-11（1314 行） |
| `trailing_edge_fold_effects` の係数 | `0.015` / `-0.012` / `0.035`（`physics/aero_kernel.py::trailing_edge_fold_effects`） | 全幅後縁折れ角 $\phi$ に対する空力係数の増分 $\Delta C_L = 0.015\,\phi$、$\Delta C_m = -0.012\,\phi$、$\Delta C_D = 0.035\,\sin^2\phi$（§1.1）。正の折れ = 揚力増・機首下げ、負の折れ = 揚力減・機首上げ | 後縁折れによる空力変化（翼型後縁の抗力増加＋後縁渦の形成）に対応する**特定論文の式が存在しない**。パネル法の「局所幾何 → 局所揚力」枠組みで近似すると、正規経路（Helmbold / Hoerner）と混在して A-1 と同じ**出典なき係数**が増える | `no-finish.md` §A-2（55〜65 行） / `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §2-H-19（681〜699 行。「係数 0.015 / −0.012 / 0.035 は出典なし」）、付録B H-19（1322 行） |

**実装状況の注記**: `no-finish.md` §A-2 と監査 §H-19 は「`trailing_edge_fold_effects` の呼び出し元が無い」状態を記録しているが、その後縁の空力接続は `7639895 fix(physics): restore the fold-integration contract and the payload keys` で復元済みである（`no-finish.md` 363〜365 行）。**係数の出典が未取得である事実は変わらない**ため、値の据え置きは継続する（接続の有無と係数の出典は別問題）。

**再オープン条件**: 紙航空機の後縁折れ空力（後縁抗力増加・後縁渦の揚力／モーメント寄与）に関する実測論文、または紙材料の引裂強度・折れ面の残留断面形状の実測データを入手できること。入手までは**値を改定しない**。

---

## 6. 監査課題解決履歴 (Audit Resolutions)

プロジェクト品質監査で提起された重要課題（Issue #28〜#37）は、以下のとおりすべて解決・テスト済みである:

| Issue | 分類 | 課題内容 | 解決内容・検証テスト |
|---|---|---|---|
| **#28** | Bug | GPUバッチでケース別の `duration_s` が `landing_time` に反映されない疑い | ケースごとの上限時間を配列保持し個別タイムアウト・着地判定を実装。`tests/test_issue28_gpu_duration.py` で検証合格。 |
| **#29** | Physics | 6DoF 飛行力学モデルと体積メッシュ CFD の区別明確化 | README および本仕様書に AeroSandbox 準拠 6DoF + パネル法モデルであることを明記。 |
| **#30** | Physics | 風場（+z上向き）と世界NED（+z下向き）の座標変換整合性 | 鉛直風符号変換経路を厳密化。`tests/test_security_and_frames_audit.py` で上昇風・下降風・無風を検証合格。 |
| **#31** | Numerics | 固定時間刻み RK4 積分の系統的収束性の検証 | $\Delta t = 0.04 \to 0.005\,\text{s}$ の刻み細分化テストを実装し単調収束を実証。`tests/test_advanced_physics_audit.py` で検証合格。 |
| **#32** | Aerodynamics | 空力係数の理論・文献出典の独立検証と公開 | `/api/v1/meta/physics-references` エンドポイントを新設し学術引用レジストリを公開。単体テスト検証合格。 |
| **#33** | Wind Model | 3D風場の有界発散・空間相関・時間連続性の検証 | 乱流場の発散有界性、空間距離に応じた相関減衰、および時間連続性を数値評価。`tests/test_advanced_physics_audit.py` で検証合格。 |
| **#34** | Security | 予期しない例外時の内部トレースバック漏洩防止 | `api_error()` を改修し、本番環境で `error_id` のみを返却。`tests/test_security_and_frames_audit.py` で検証合格。 |
| **#35** | GPU | CPU版とGPU版の計算結果・返却スキーマの完全一致 | `gpu_batch_evaluate_cases` の CPU フォールバックを統一スキーマ化。**返却スキーマの一致とフォールバックの動作は検証済み**。「物理計算結果の完全一致」は過大主張であったため撤去し、代わりに GPU 最適化バッチ経路を **正規経路と同一の 13 状態 RK4 モデル**に書き換えて `tools/check_gpu_parity.py` / `tests/test_gpu_kernel_parity.py` で定量検証する運用に変更（§2.1 参照）。残存する縮約経路は `model` タグ（`rigid_body_6dof` / `reduced_surrogate`）と `model_detail` で明示する。 |
| **#36** | Mesh | パネル法のメッシュ解像度依存性と揚力整合性の検証 | パネル細分化に伴う揚力・抗力・モーメントの漸近収束を実証。`tests/test_advanced_physics_audit.py` で検証合格。 |
| **#37** | Security | 3Dメッシュインポート時の SSRF 防止機構 | プライベートIP・ループバック・クラウドメタデータ・非標準ポートを遮断。`tests/test_security_and_frames_audit.py` で検証合格。 |

---

## 7. 全体モジュール・ディレクトリ構成 (Module Directory Layout)

```
.
├── AGENTS.md                  # エージェント基本プロトコル OS (プロジェクト非依存)
├── .agents/                   # 自律実行ループ OS プロトコル
│   ├── AGENTS.md              # コンテキスト最小化・テキサスの狙撃兵排除・5フェーズ自律ループ
│   ├── Project.md             # プロジェクトゴール & 現在のループ状態 (Loop State)
│   ├── Knowledge-important.md # 常駐制約・重要知見リスト (50行以内)
│   ├── Knowledge.md           # オンデマンド参照ナレッジベース
│   └── skills/                # 定型作業手順 (loop-handover.md など)
├── docs/                      # 設計書・学術調査・詳細仕様
│   ├── SPECIFICATION.md       # 本仕様書 (詳細物理モデル・API・セキュリティ)
│   └── discord-bridge.md      # Discord リモートブリッジ仕様
├── api/                       # 分割された REST API パッケージ
│   ├── __init__.py
│   ├── core.py                # Session 状態・共通ヘルパー・エラー応答 (トレースバック秘匿)
│   └── routes/                # 責務別 Blueprint ルーティング (meta, physics, simulation, mesh, jobs, records)
├── physics/                   # 物理・シミュレーションコアエンジン
├── tests/                     # 総合テストスイート (700+ テスト)
├── tools/                     # 開発・運用・検証用ツール群 (notify_discord, discord_bridge 等)
├── templates/index.html       # Google Clean UI コンソール
├── static/                    # Three.js 3D ビューア & UI スクリプト群
├── requirements.txt           # 実行時依存 (CPU 既定、gpu / bridge は extras)
├── pyproject.toml             # プロジェクトメタデータ・オプション依存
├── setup.sh                   # 環境ブートストラップスクリプト
├── app.py                     # Flask アプリケーション エントリポイント
├── smoke_test.py              # エンドツーエンドスモークテスト (14 ステージ)
├── config/                    # 遺伝的調査設定 (genetic_research.yaml 等)
├── no-finish.md               # 残課題台帳
└── README.md                  # プロジェクト概要
```

---

## 8. エージェント役割分担 (Agent Roles v4)

サブエージェント委譲やモジュール別修正を行う際は、以下の担当境界を遵守する：

| # | エージェント名 | 担当ファイル | 必須実装内容・責務 |
|---|---------------|------------|------------------|
| 0 | `Manager` | `physics/config_schema.py`, `physics/geometry_utils.py`, `app.py`, `smoke_test.py`, `AGENTS.md` | 契約定義・API Blueprint統合・UI・E2Eテスト・Git制御 |
| 1 | `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | 翼型・失速・Drag Polar・Trim・受動空力弾性・低速渦リフト |
| 2 | `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | ISA・Sutherland・全可変環境大気・3D Simplex風場・座標変換整合性 |
| 3 | `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | 動圧・Gフォース・翼根曲げモーメント・頂点応力ヒートマップ |
| 4 | `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | glTF/GLB/OBJ/STL 解析・慣性テンソル・パネル法・SSRF防止 |
| 5 | `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | 6DoF RK4・四元数・適応/固定時間刻み・リングバッファ |
| 6 | `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | GPU一括評価・ケース別duration_s・CPU完全一致フォールバック・NSGA-II |
| 7 | `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | JobManager・EMA ETA・進捗管理・JSON/CSV エクスポート |
| 8 | `ui-gpu-viz` | `templates/index.html`, `static/css/console.css`, `static/js/*.js` | Google Clean UI・Three.js シェーダー・時間軸・テレメトリ |

---

## 9. 検証コマンドと CI/CD ガイド (Verification & CI/CD Protocol)

コード変更・機能追加時は、以下の段階的検証を必ずローカル環境で通過させること：

```powershell
# 1. 構文検証
python -m compileall physics api tools app.py smoke_test.py

# 2. 静的解析 & コード品質検査
ruff check .
mypy physics api app.py smoke_test.py

# 3. ユニットテストおよび監査テストの全実行
python -m pytest tests -q

# 4. エンドツーエンド統合テスト (API連鎖・最適化ジョブ・ETA・3Dモデル解析)
python smoke_test.py
```

### GitHub Actions および Git 運用
1. 作業開始前に必ず専用トピックブランチを作成する (`git checkout -b <type>/<description>`)。
2. 変更・修正後はローカル環境で上記全テストの通過を確認する。
3. コミット後、ブランチを push し、master にマージする。
4. master 統合後、リモートリポジトリへ自動プッシュ (`git push`) を実行する。
5. GitHub Actions (Ubuntu/Windows × Python 3.10/3.11/3.12, CodeQL, pip-audit) の全ジョブがグリーンであることを確認する（`tools/check_runs.py`）。
6. GitHub Issue（`tools/check_issues.py` / `tools/read_issues.py`）を確認し、残存課題の修正・解決および自動クローズ（`tools/close_resolved_issue.py`）を行って自律ループを正常終了（HALT）する。


