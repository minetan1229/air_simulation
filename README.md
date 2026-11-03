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

## CFD の実装状況

`physics/cfd/` には、構造格子上で圧縮性 Euler 方程式を解く 2D・3D 有限体積ソルバの試作実装があります。3D 翼面は slip wall を使い、現時点で物理精度を保証する Mach/Reynolds 範囲はありません。粘性 Navier–Stokes/RANS、境界層・遷移、熱連成、移動壁による 6DoF 連成は未実装です。このコードは通常の飛行シミュレーションや GA 評価経路には統合されていません。詳細と検証範囲は [CFD ロードマップ](docs/CFD_ROADMAP.md) を参照してください。

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
