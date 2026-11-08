# 3D Paper Airplane Simulator

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/backend-Flask%20REST%20API-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Three.js](https://img.shields.io/badge/frontend-Three.js%20%2F%20WebGL-black.svg)](https://threejs.org/)
[![Physics 6DoF](https://img.shields.io/badge/physics-6DoF%20RK4-brightgreen.svg)]()
[![CUDA Accelerator](https://img.shields.io/badge/accelerator-CUDA%20%2F%20CuPy-green.svg)]()

流体力学・飛行力学モデルに基づく、紙飛行機の 6自由度 (6DoF) 飛行シミュレータおよび自律設計最適化環境です。  
ブラウザ上でリアルタイムに飛行軌道・姿勢・気流を 3D 表示し、GPU を用いた並列アンサンブル計算や遺伝的アルゴリズム (GA) による機体探索を行えます。

---

## 主な特徴

- **6自由度 (6DoF) 飛行力学**: 四元数 (Quaternion) とルンゲ＝クッタ法 (RK4) による実時間数値積分
- **空力・環境モデル**: 揚力・抗力・地面効果・大気モデル・3D風場・乱気流を反映
- **3D インタラクティブ表示**: Three.js による 60FPS 計器盤、飛行軌道、気流・風洞可視化
- **GPU 並列計算 & GA 最適化**: CuPy による多機同時シミュレーション（CPU自動フォールバック対応）と進化型機体最適化

---

## 物理・理論アーキテクチャ (Physics & Theory Architecture)

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

---

## 画面イメージ

| メインコンソール | 3D 飛行軌道・姿勢追跡 |
| :---: | :---: |
| ![メインコンソール](docs/images/01_main_console.png) | ![飛行軌道](docs/images/02_flight_trajectory.png) |
| **風洞モード (気流解析)** | **Workbench (最適化)** |
| ![風洞モード](docs/images/03_wind_tunnel_mode.png) | ![Optimizer](docs/images/05_workbench_optimizer.png) |

---

## クイックスタート

### 動作要件
- Python 3.10 以上 (3.11 推奨)
- WebGL 2.0 対応ブラウザ (Chrome, Edge, Firefox 等)
- (任意) NVIDIA GPU / CUDA 12.x（GPUがない環境でも自動でCPU動作します）

### セットアップ

```bash
# リポジトリのクローン
git clone https://github.com/minetan1229/air_simulation.git
cd air_simulation

# 仮想環境の作成と有効化
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate

# 依存パッケージのインストール
pip install -e ".[dev]"
```
*(Windows では `.\setup.ps1`、Linux/macOS では `./setup.sh` による自動セットアップも利用可能です)*

### 起動方法

```bash
python app.py
```
起動後、ブラウザで [http://127.0.0.1:5000](http://127.0.0.1:5000) にアクセスしてください。

---

## ディレクトリ構成の概要

```text
├── app.py             # アプリケーション起動エントリーポイント
├── api/               # REST API エンドポイント
├── physics/           # 物理演算・空力モデル・最適化エンジン
├── static/            # フロントエンド (Three.js 描画、UI)
├── templates/         # UI テンプレート HTML
├── tests/             # 単体・統合テストスイート
└── docs/              # 詳細仕様・アーキテクチャ資料
```

---

## 詳細ドキュメント

詳しい数式や内部仕様、アーキテクチャについては `docs/` 配下をご参照ください。

- [docs/SPECIFICATION.md](docs/SPECIFICATION.md): 技術仕様・定数・物理法則（正本）
- [docs/architecture.md](docs/architecture.md): 全体アーキテクチャとデータフロー
- [docs/PHYSICS_MODELS.md](docs/PHYSICS_MODELS.md): 空力・運動方程式の数式詳解
- [docs/getting-started.md](docs/getting-started.md): 詳しい導入ガイド
- [docs/usage.md](docs/usage.md): 操作マニュアル・各画面の使い方
