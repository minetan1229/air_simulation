<div align="center">

# ✈️ 3D Paper Airplane Simulator

**6DoF RK4 飛行力学 × WebGL 3D × GPU 並列最適化 — 学術根拠を持つ紙飛行機シミュレーション基盤**

Flask REST API + Three.js WebGL でリアルタイム 3D 飛行軌跡・姿勢・気流を可視化し、
低レイノルズ数（$Re \sim 10^3 \sim 10^5$）特有の非線形空力（Helmbold / Polhamus / Hoerner / LSB）を
GPU バッチ計算と遺伝的アルゴリズムで探索します。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/backend-Flask%20REST%20API-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Three.js](https://img.shields.io/badge/frontend-Three.js%20%2F%20WebGL-black.svg)](https://threejs.org/)
[![Physics 6DoF](https://img.shields.io/badge/physics-6DoF%20RK4-brightgreen.svg)]()
[![CUDA Accelerator](https://img.shields.io/badge/accelerator-CUDA%20%2F%20CuPy-green.svg)]()
[![CI](https://github.com/minetan1229/air_simulation/actions/workflows/ci.yml/badge.svg)](https://github.com/minetan1229/air_simulation/actions/workflows/ci.yml)
[![CodeQL](https://github.com/minetan1229/air_simulation/actions/workflows/codeql.yml/badge.svg)](https://github.com/minetan1229/air_simulation/actions/workflows/codeql.yml)
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](./LICENSE)

[ドキュメント](https://minetan1229.github.io/air_simulation/) ·
[API リファレンス](./docs/api.md) ·
[物理モデル](./docs/PHYSICS_MODELS.md) ·
[開発ガイド](./CONTRIBUTING.md) ·
[学術引用](./CITATION.cff) ·
[English](./README.en.md)

</div>

> **デモ動画について**: 1920×1080 の軌跡・風洞ループ GIF はリポジトリ容量抑制のため
> 未同梱です。代わりに静止画で各画面を紹介し、ローカル起動でインタラクティブに
> ご確認いただけます（60 FPS 計器盤・タイムラインシーク・GPU 気流パーティクル）。
> GIF 化の手順は [docs/README.md §画面イメージ](./docs/README.md) を参照してください。

---

## ⚡ 30 秒で動かす (Quickstart)

> 前提: [Python 3.10+](https://www.python.org/downloads/) / WebGL2 対応ブラウザ（Chrome / Edge / Firefox）
> GPU（NVIDIA / CUDA 12.x）は任意。未搭載時は NumPy / `multiprocessing` へ自動フォールバックします。

=== "Docker (推奨・最速)"

    ```bash
    git clone https://github.com/minetan1229/air_simulation.git
    cd air_simulation
    docker compose up --build
    # → http://127.0.0.1:5000 をブラウザで開く
    ```

    > Docker Compose ファイルが無い場合は以下をご参照ください。
    > `docker build -t paper-sim . && docker run --rm -p 5000:5000 paper-sim`

=== "Python venv (Windows PowerShell)"

    ```powershell
    git clone https://github.com/minetan1229/air_simulation.git
    cd air_simulation
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -e ".[dev]"
    python app.py
    # → http://127.0.0.1:5000
    ```

=== "Python venv (Linux / macOS / WSL)"

    ```bash
    git clone https://github.com/minetan1229/air_simulation.git
    cd air_simulation
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ".[dev]"
    python app.py
    # → http://127.0.0.1:5000
    ```

**疎通確認**:

```bash
curl http://127.0.0.1:5000/api/v1/meta/health
curl http://127.0.0.1:5000/healthz/live
```

**インタラクティブ API ドキュメント**: サーバー起動後に <http://127.0.0.1:5000/docs>（Swagger UI） / <http://127.0.0.1:5000/api/openapi.json>（OpenAPI 3.0 仕様）

詳細な環境構築・`.env`・GPU 検証は [docs/getting-started.md](./docs/getting-started.md) を参照。

---

## ✨ 機能ハイライト (Feature Matrix)

<div align="center">

| 領域 | 技術 | 何ができるか |
| :--- | :--- | :--- |
| 🚀 **6DoF 飛行力学** | Quaternion + RK4 + 適応サブステッピング | 並進 + 回転オイラー方程式を 13 状態で実時間積分。連続衝突判定（CCD）・地表接地・翼端散逸まで一貫処理 |
| 🌬️ **空力・環境モデル** | Helmbold（1942）/ Polhamus（1966）/ Hoerner（1965）/ LSB / ISA + Sutherland / Dryden 乱流 | 低 AR 翼の揚力傾斜・前縁渦揚力・失速後平板揚力・低 Re 層流剥離泡・地面効果・3D 風場を数式として統合 |
| 🎛️ **3D インタラクティブ表示** | Three.js WebGL + GPU 気流パーティクル | 60 FPS 計器盤、飛行軌道・姿勢追跡、頂点応力ヒートマップ、仮想風洞、タイムラインシーク |
| ⚡ **GPU 並列 & 最適化** | CuPy ベクトル化 + CPU 自動フォールバック + NSGA-II / DE / CMA-ES / Surrogate | 多機同時アンサンブル（100 機）・パレート探索・ETA 付き非同期ジョブ。スキーマ等価性を維持 |
| 🧬 **GA 2.0 研究基盤** | `physics/ga2/` スケーリング・サロゲート・VRAM 管理 | 大規模進化探索の段階（Stage 0–3）・チェックポイント・ジャーナル・レジューム |
| 🔌 **REST API + OpenAPI** | Flask + Swagger UI / `openapi.json` | `POST /api/v1/simulation/launch` でヘッドレス飛行、`GET /api/v1/meta/schemas` で UI スキーマ単一正本化 |
| 🧪 **検証 (V&V)** | `pytest` + 閉形式テスト + `smoke_test.py` | ISA・誘導抗力・Sutherland・Helmbold 閉形式を $10^{-6} \sim 10^{-3}$ で検証。CI で Linux / Windows × Python 3.10–3.12 |

</div>

---

## 🖥️ スクリーンショット

| メインコンソール | 3D 飛行軌道・姿勢追跡 |
| :---: | :---: |
| ![メインコンソール](docs/images/01_main_console.png) | ![飛行軌道](docs/images/02_flight_trajectory.png) |
| **風洞モード (気流解析)** | **Workbench (最適化)** |
| ![風洞モード](docs/images/03_wind_tunnel_mode.png) | ![Optimizer](docs/images/05_workbench_optimizer.png) |

> その他の画面（Workbench 調査 / 3D メッシュ / アンサンブル 100 機）は [docs/README.md](./docs/README.md) を参照。

---

## 🧮 物理・理論アーキテクチャ (Physics & Theory Architecture)

本シミュレータは、紙飛行機の低レイノルズ数環境（$Re \sim 10^3 \sim 10^5$）特有の非線形現象と6自由度飛行力学を、学術的根拠に基づく数理モデルとして統合しています。

```mermaid
flowchart TD
    subgraph Env["環境・流体場モデル"]
        ISA["標準大気 (ISA + Sutherland)<br/>• 密度 ρ(h)<br/>• 動粘性係数 ν(T)"]
        Wind["3D風場 & 乱気流<br/>• 平均風速プロファイル<br/>• Dryden乱流モデル"]
    end

    subgraph State["機体運動状態 (Kinematics)"]
        Pos["位置 & 速度 (NED系)"]
        Att["姿勢四元数 (Quaternion q)<br/>• 角速度 ω (p, q, r)"]
        AeroAngles["対気状態計算<br/>• 迎角 α, 横滑り角 β<br/>• レイノルズ数 Re, 動圧 q_bar"]
    end

    subgraph AeroPhysics["空力理論・補正モデル (physics/aero_kernel.py)"]
        Helmbold["揚力傾斜: Helmbold (1942)<br/>低AR(AR≲4) 3次元誘導効果補正"]
        LSB["低Re層流剥離泡 (LSB) & 動的失速<br/>• ピッチ角速度 adot 遅延<br/>• f(Re)=√(10⁴/Re) スケーリング"]
        Polhamus["前縁渦揚力: Polhamus (1966)<br/>大迎角三角翼・後退角渦"]
        Stall["失速後揚力: Hoerner 平板混合<br/>α > α_stall での完全分離接続"]
        Drag["抗力極線 & Reべき乗スケーリング<br/>• 摩擦抗力 Cd0(Re) ∝ Re^(-0.5)<br/>• 誘導抗力 C_L²/(π e AR)<br/>• 剥離圧力抗力 (大迎角)"]
        Ground["地面効果 (Wieselberger / Phillips)<br/>高度低下時の誘導抗力低減 & 揚力増加"]
    end

    subgraph AeroElastic["空力弾性・変形モデル"]
        Stress["翼根曲げ応力 & 破損判定<br/>紙の破壊引張限界 (paper_allowable_stress)"]
        Morph["準定常受動変形 (Quasi-steady Morphing)<br/>動圧・揚力荷重による局所迎角低減"]
    end

    subgraph Integration["6DoF 数値積分 (physics/flight_sim.py)"]
        RK4["ルンゲ＝クッタ法 (RK4)<br/>• 並進運動方程式 (質点 m)<br/>• 回転オイラー方程式 (慣性テンソル I)<br/>• 四元数正規化 (Quat Normalize)"]
        CCD["連続衝突判定 (CCD)<br/>地面接地・反発・翼端散逸減衰"]
    end

    Env --> AeroAngles
    State --> AeroAngles
    AeroAngles --> Helmbold
    AeroAngles --> LSB
    AeroAngles --> Polhamus
    AeroAngles --> Stall
    AeroAngles --> Drag
    AeroAngles --> Ground

    Helmbold & LSB & Polhamus & Stall & Drag & Ground --> AeroElastic
    AeroElastic --> Integration
    Integration --> State
```

### 採用されている物理理論と適用

| 物理現象・理論 | 適用モデル / 方程式 | 文献根拠・学術的背景 | 正本モジュール |
|---|---|---|---|
| **低AR揚力傾斜** | Helmbold (1942) 3次元補正式 | Helmbold (1942), *Jahrbuch D. Luftfahrtforschung* | `physics/aero_kernel.py` |
| **失速後挙動** | Hoerner 平板完全分離混合式 | Hoerner (1965), *Fluid-Dynamic Drag* | `physics/aero_kernel.py` |
| **低Re層流剥離泡 (LSB)** | 動的失速ヒステリシス ($Re^{-0.5}$) | Pelletier & Mueller (2000), Sunada et al. (2002) | `physics/aero_kernel.py` |
| **前縁渦揚力** | Polhamus 先導縁吸い込みアナロジー | Polhamus (1966), NASA TN D-3767 | `physics/aero_kernel.py` |
| **6DoF実時間運動** | 四元数 + ルンゲ＝クッタ4段法 (RK4) | 剛体力学・オイラー回転方程式 | `physics/flight_sim.py` |
| **環境・乱気流** | ISA大気 + Sutherland粘性 + Dryden乱流 | US Standard Atmosphere 1976 / MIL-F-8785C | `physics/atmosphere_domain.py`, `physics/wind_domain.py` |
| **構造・空力弾性** | 翼根応力縮約 + 準定常変形 | 薄板曲げ理論 / 航空弾性一次縮約 | `physics/stress_domain.py`, `physics/aeroelastic_domain.py` |

> 数式の正本は [docs/SPECIFICATION.md](./docs/SPECIFICATION.md) / [docs/PHYSICS_MODELS.md](./docs/PHYSICS_MODELS.md)、
> 検証・妥当性確認（V&V）は [docs/VALIDATION.md](./docs/VALIDATION.md) を参照。

---

## 📁 ディレクトリ構成

```text
├── app.py               # アプリケーション起動エントリーポイント (Flask)
├── api/                 # REST API エンドポイント (Blueprint 群)
├── physics/             # 物理演算・空力モデル・最適化エンジン
├── examples/            # ヘッドレス実行サンプル (quick_simulation.py)
├── static/              # フロントエンド (Three.js 描画、UI)
├── templates/           # UI テンプレート HTML + Swagger UI
├── tests/               # 単体・統合テストスイート
├── docs/                # 詳細仕様・アーキテクチャ資料 (MkDocs ビルド対象)
└── .github/workflows/   # CI / CodeQL / Dependency Review / Docs デプロイ
```

---

## 📚 詳細ドキュメント

Web サイト版: <https://minetan1229.github.io/air_simulation/>（`mkdocs.yml` から GitHub Pages へ自動公開）

| カテゴリ | 文書 |
| :--- | :--- |
| **入門** | [docs/getting-started.md](./docs/getting-started.md) 環境構築・起動、`[docs/usage.md](./docs/usage.md)` 画面操作 |
| **物理・理論** | [docs/SPECIFICATION.md](./docs/SPECIFICATION.md) 技術仕様正本、`[docs/PHYSICS_MODELS.md](./docs/PHYSICS_MODELS.md)` 数式詳解、`[docs/VALIDATION.md](./docs/VALIDATION.md)` V&V |
| **API** | [docs/api.md](./docs/api.md) REST リファレンス、`GET /api/openapi.json` OpenAPI 3.0、`GET /docs` Swagger UI |
| **開発・運用** | [docs/architecture.md](./docs/architecture.md)、`[docs/development.md](./docs/development.md)`、`[CONTRIBUTING.md](./CONTRIBUTING.md)` |

---

## 👥 開発・コントリビューション

- **Issue / PR**: [CONTRIBUTING.md](./CONTRIBUTING.md) の規約（`feat(scope):` / `fix(scope):` 等）に従い、**物理モデル/issues テンプレート**で起票してください。
- **Zero-Placeholder 政策**: `TODO`・空関数・ダミープレースホルダーは禁止。実動コードのみ。
- **ブランチ隔離**: `genetic-project` フェーズでは `master` への直接コミット禁止。すべてのミッションは `mission/<slug>` ブランチで実施。
- **セキュリティ**: 脆弱性の報告は [SECURITY.md](./SECURITY.md) を参照。
- **行動規範**: [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)

---

## 📖 学術引用 (Citation)

本ソフトウェアを論文・報告で引用する場合は以下をお使いください（詳細は [CITATION.cff](./CITATION.cff)）:

```bibtex
@software{air_simulation_2026,
  title   = {3D Paper Airplane Simulator: 6DoF RK4 Flight Dynamics, Low-Reynolds Aerodynamics and GPU Ensemble Optimisation},
  author  = {minetan1229},
  year    = {2026},
  url     = {https://github.com/minetan1229/air_simulation},
  version = {3.0.0}
}
```

---

## 📄 ライセンス

本ソフトウェアはプロプライエタリ（非公開）ライセンスです。詳細は [LICENSE](./LICENSE) を参照してください。
