# アーキテクチャ設計 (Architecture)

本シミュレーターは、ドメイン駆動設計（DDD）の思想に基づき、物理モデル・風場・構造解析・飛行力学・最適化の責務をモジュール分割した Flask REST API バックエンドと、WebGL / Three.js を用いた高パフォーマンスなフロントエンドで構成されています。

---

## 1. システム全体データフロー

ユーザーが計器コンソールでパラメータを操作、または再生ボタンを押下した際のリクエスト・演算・レンダリングの流れです。

```mermaid
flowchart TD
    subgraph Frontend["フロントエンド (Three.js / WebGL / Browser)"]
        UI["Google Clean UI コンソール<br/>(パラメータ入力 / 時間軸シーク)"]
        Renderer["3D Viewport レンダラー<br/>(Three.js / Custom Shaders)"]
        Stream["60FPS アニメーションループ<br/>(適応補間 / リングバッファ)"]
    end

    subgraph Backend["バックエンド (Flask REST API: app.py)"]
        API["REST API エンドポイント群<br/>(/api/v1/...)"]
        JobMgr["ジョブ管理 (JobManager)<br/>(非同期スレッド / ETA計算)"]
    end

    subgraph PhysicsCore["物理演算エンジン (physics/)"]
        Atmo["大気環境 (atmosphere_domain)<br/>ISA / Sutherland / 密度"]
        Aero["空力ドメイン (aero_domain)<br/>Viterna失速 / トリム / 地面効果"]
        Wind["風場ドメイン (wind_domain)<br/>3D乱流 / GPU-CuPy / NumPy"]
        Stress["構造応力 (stress_domain)<br/>動圧 / Gフォース / 翼根曲げ"]
        Flight["6DoFフライト (flight_sim)<br/>RK4数値積分 / 四元数姿勢"]
        Mesh["3Dメッシュ解析 (mesh_domain)<br/>STL/OBJ/GLB / パネル法"]
        Opt["最適化エンジン (optimizer_domain)<br/>NSGA-II / DE / Nelder-Mead"]
    end

    UI -->|パラメータ変更 / ステップ実行| API
    API --> Atmo & Aero & Wind & Stress
    API --> Flight
    Flight -->|局所気流・動圧| Aero
    Flight -->|風速ベクトル| Wind
    Flight -->|加速度・荷重| Stress
    API --> JobMgr
    JobMgr --> Opt
    API --> Mesh

    Flight -->|6DoF軌跡・姿勢・テレメトリ| API
    API -->|JSONレスポンス| Stream
    Stream --> Renderer
    Renderer --> UI
```

---

## 2. ディレクトリ構成の解説

```text
paper_airplanes/simulation/v3/
├── app.py                      # Flask REST API エントリポイント (全20種のエンドポイント統合)
├── requirements.txt            # 必須・任意パッケージ依存関係定義
├── smoke_test.py               # E2E・API疎通・物理演算・最適化検証用テストスクリプト
├── conftest.py / pytest.ini    # pytest 設定ファイル
├── ruff.toml / mypy.ini        # 静的解析・型検査設定
├── .env.example                # 環境変数テンプレート
├── physics/                    # 物理演算コアライブラリ (ドメイン別)
│   ├── __init__.py
│   ├── config_schema.py        # 物理パラメータの dataclass 定義・バリデーション
│   ├── geometry_utils.py       # 四元数・オイラー角・座標変換ユーティリティ
│   ├── aero_domain.py          # 揚力・抗力・失速遷移・トリム・空力弾性モーフィング
│   ├── atmosphere_domain.py    # ISA標準大気・Sutherland動粘性・局所大気圧計算
│   ├── wind_domain.py          # 3D風場・Simplex/Perlin乱流・GPU (CuPy) / CPU並列
│   ├── stress_domain.py        # 動圧・Gフォース・翼根曲げモーメント・頂点応力分布
│   ├── flight_sim.py           # 6自由度 RK4 積分・適応サブステップ・リングバッファ
│   ├── mesh_domain.py          # STL/OBJ/GLBインポート・重心/慣性テンソル算出・パネル法
│   ├── optimizer_domain.py     # NSGA-II・差分進化・Nelder-Mead・Surrogate 最適化
│   ├── parallel_engine.py      # マルチプロセッシング・パラメータスイープ実行基盤
│   ├── job_manager.py          # 長時間最適化ジョブ管理・進捗率・ETA平滑化
│   └── storage_domain.py       # CSV / JSON テレメトリ出力・設定シリアライズ
├── static/                     # クライアントサイド Three.js & UI スクリプト
│   ├── css/console.css         # Google Clean スタイルシート
│   └── js/
│       ├── app.js              # フロントエンド統合エントリ
│       ├── api.js              # Flask API 通信ラッパー
│       ├── viewport.js         # Three.js シーン・カメラ・レンダラー制御
│       ├── airframe.js         # 機体 3D モデル描画・応力ヒートマップシェーダー
│       ├── flowfield.js        # GPGPU / InstancedMesh 風流線パーティクル
│       ├── transport.js        # 再生・一時停止・巻き戻しシーク制御
│       ├── telemetry.js        # 計器・HUD・リアルタイムグラフ表示
│       ├── panels.js           # パラメータ調整 UI パネル制御
│       ├── optimizer.js        # 最適化ジョブ設定・リアルタイム ETA 表示
│       ├── meshpanel.js        # 3Dモデルファイルドラッグ＆ドロップ取込
│       └── datapanel.js        # CSV/JSON エクスポート・インポート
├── templates/
│   └── index.html              # 計器コンソール HTML テンプレート
├── tests/                      # pytest ユニットテスト群 (700+ テストケース)
│   ├── test_aero.py            # 空力・失速・Drag Polar 検証
│   ├── test_atmo_wind.py       # 大気・粘性・風場・GPUフォールバック検証
│   ├── test_stress.py          # 動圧・翼根応力計算検証
│   ├── test_mesh.py            # 3Dモデル幾何抽出・パネル法検証
│   ├── test_flight.py          # 6DoF RK4・四元数・エネルギー保存検証
│   ├── test_optimizer.py       # 最適化アルゴリズム・パレート解検証
│   └── test_jobs.py            # ジョブマネージャー・ETA 計算式検証
└── models/                     # アップロードされた 3D モデルキャッシュ (キャッシュ用)
```

---

## 3. 技術スタック一覧

### バックエンド (Backend)
- **言語**: Python 3.10+
- **Web フレームワーク**: Flask 3.1+ (RESTful API 設計)
- **数値計算 & 物理演算**:
  - `numpy`: ベクトル演算、配列処理、行列計算
  - `scipy`: 空間補間（RBF）、Nelder-Mead、Differential Evolution
- **3D 幾何解析 (Mesh)**:
  - `trimesh` (任意): 高度なメッシュ解析。非導入時は純 NumPy 実装への自動フォールバックが機能
- **GPU アクセラレーション**:
  - `cupy` / `torch` (任意): 3D 風場グリッドのベクトル計算を CUDA GPU へオフロード (非搭載環境は CPU/NumPy へ自動フォールバック)

### フロントエンド (Frontend)
- **3D レンダリング**: Three.js (WebGL 2.0)
  - Custom GLSL Shaders: 頂点ごとの動的応力ヒートマップ変形
  - InstancedMesh / Particle System: 数万〜数十万点の風流線パーティクルリアルタイム描画
- **UI デザイン**: Google Clean / Material Design スタイル
  - 白基調 (`#F8F9FA` / `#FFFFFF`)、角丸カード型浮遊パネル、Google Blue (`#1A73E8`)
- **グラフ・計器**: Chart.js によるリアルタイムテレメトリグラフ表示

### 開発・品質保証ツール (DevOps & Testing)
- **テストフレームワーク**: `pytest` (700+ テストケース)
- **コードリンター & フォーマッター**: `ruff`
- **静的型検査**: `mypy` (厳格な型アノテーション検査)
- **CI/CD**: GitHub Actions (Ubuntu 24.04 / Python 3.11 環境での自動テスト・静的解析)

---

## 4. 物理モジュール間の関係性

```mermaid
classDiagram
    class ConfigSchema {
        +AirplaneParams
        +AtmosphereParams
        +WindParams
        +SimulationParams
    }
    class AtmosphereDomain {
        +compute_density(h)
        +compute_viscosity(T)
        +compute_reynolds(v, chord)
    }
    class AeroDomain {
        +compute_aero_forces(alpha, q, elevon)
        +compute_drag_polar()
        +compute_ground_effect(h, b)
    }
    class WindDomain {
        +get_wind_vector(x, y, z, t)
        +generate_grid_field()
    }
    class StressDomain {
        +compute_root_bending(q, nz, span)
        +compute_vertex_stress(q, nz)
    }
    class FlightSim {
        +step_rk4(dt)
        +get_state()
        +reset()
    }

    ConfigSchema <-- AtmosphereDomain
    ConfigSchema <-- AeroDomain
    ConfigSchema <-- WindDomain
    AtmosphereDomain --> AeroDomain : 動圧 q, Re
    WindDomain --> FlightSim : 相対風速
    AeroDomain --> FlightSim : 揚力 L, 抗力 D, モーメント M
    FlightSim --> StressDomain : 荷重倍数 nz, 動圧 q
```

次ページ: [[開発・運用ガイド|Development-Guide]]
