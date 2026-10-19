# 3D Paper Airplane High-Performance CFD Simulator

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/backend-Flask%20REST%20API-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Three.js](https://img.shields.io/badge/frontend-Three.js%20%2F%20WebGL-black.svg)](https://threejs.org/)
[![Physics 6DoF](https://img.shields.io/badge/physics-6DoF%20RK4-brightgreen.svg)]()
[![CUDA Accelerator](https://img.shields.io/badge/accelerator-CUDA%20%2F%20CuPy-green.svg)]()

流体力学、航空力学、および飛行力学の理論モデルに基づく 3D 紙飛行機 6自由度 (6DoF) 数値シミュレーション環境。
Flask REST API による物理演算バックエンドと、Three.js を用いた 3D 計器コンソールおよび気流可視化フロントエンドで構成される。

---

## 1. 物理・力学モデルおよび学術的根拠 (Physical Models & References)

本シミュレーターの物理演算コアは、以下の学術論文、NASA テクニカルレポート、および航空力学オープンソーススイート `AeroSandbox` の定式化に準拠する。

| 対象領域 | 物理モデル / 支配方程式 | 引用・学術的根拠 | AeroSandbox リファレンス |
|:---|:---|:---|:---|
| **揚力傾斜** | $C_{L\alpha} = \frac{2\pi AR}{2 + \sqrt{AR^2(1 + \lambda) + \frac{4 AR}{\pi}}}$ | Helmbold, H. B. (1942). *Der unverwundene Dreiecksflügel als Trägerfläche höchsten Auftriebs*, Jahrbuch 1942 der deutschen Luftfahrtforschung, I, 111-113. | `aerosandbox.aerodynamics.buildup` |
| **渦揚力** | $C_L = K_p \sin\alpha \cos^2\alpha + K_v \sin^2\alpha \cos\alpha$ | Polhamus, E. C. (1966). *A Concept of the Vortex Lift of Sharp-Edge Delta Wings Based on a Leading-Edge-Suction Analogy*, NASA TN D-3767. | `aerosandbox.aerodynamics.buildup` |
| **失速後特性** | Viterna-Corrigan 非線形外挿法 ($C_L, C_D$) | Viterna, L. A., & Corrigan, R. D. (1982). *Fixed Pitch Rotor Performance of Large Horizontal Axis Wind Turbines*, NASA TM-83021. | `aerosandbox.aerodynamics.airfoil` |
| **直交剥離抗力** | $C_N = 2.0 \sin\alpha \cos^2\alpha$ | Hoerner, S. F. (1965). *Fluid-Dynamic Drag: Practical Information on Aerodynamic Drag and Hydrodynamic Resistance*, Hoerner Fluid Dynamics. | `aerosandbox.aerodynamics.buildup` |
| **地面効果** | $\phi_{GE} = \frac{16(h/b)^2}{1 + 16(h/b)^2}$ (誘導抗力低減係数) | Wieselsberger, C. (1922). *Wing Resistance Near the Ground*, ZFM, Vol. 12; translated in NACA TM 77. | `aerosandbox.aerodynamics.buildup` |
| **標準大気** | 気圧・気温・密度減率モデル (0〜84km) | NOAA, NASA, USAF (1976). *U.S. Standard Atmosphere, 1976*, NASA-TM-X-74335. | `aerosandbox.atmosphere.Atmosphere` |
| **動粘性係数** | $\mu = \mu_0 \left(\frac{T}{T_0}\right)^{1.5} \frac{T_0 + S}{T + S}$ | Sutherland, W. (1893). *The Viscosity of Gases and Molecular Force*, Philosophical Magazine, 5(36), 507-531. | `aerosandbox.atmosphere.Atmosphere` |
| **圧縮性補正** | $\beta = \sqrt{1 - M^2}, \quad C_p = \frac{C_{p0}}{\beta}$ | Glauert, H. (1928). *The Effect of Compressibility on the Lift of an Aerofoil*, Proc. R. Soc. Lond. A 118(779), 113-119. | `aerosandbox.aerodynamics.buildup` |

### 1.1 体積メッシュCFDソルバーとの明確な区別 (Model Architecture)
本シミュレーターは、空間体積メッシュ上で連続の式およびナビエ・ストークス方程式（Navier-Stokes, RANS/LES/DNS）を差分法・有限体積法で直接離散化して解く体積CFDソルバー（OpenFOAM等）ではなく、**空力係数理論（AeroSandbox準拠）・境界パネル法・統計的乱流モデルに基づくリアルタイム 6自由度 (6DoF) 剛体飛行力学シミュレーター**です。  
四元数 (Quaternion) および適応サブステップ付き4次ルンゲ＝クッタ法 (RK4) による数値積分を採用することで、ブラウザ上での 60FPS〜120FPS リアルタイム対話描画、GPU を用いた 100 機同時アンサンブルシミュレーション、および多目的遺伝的最適化（NSGA-II）の高速収束を実現しています。

---

## 2. システム要件および計算環境 (System Requirements)

- **OS**: Windows 10/11, Ubuntu 20.04+, macOS 12+
- **Python**: 3.10 以上 (3.11 推奨)
- **アクセラレータ (GPU)**: NVIDIA GPU (CUDA 12.x / Compute Capability 5.0 以上)
  - 演算バックエンドとして CuPy (`cupy-cuda12x`) を使用。
  - GPU が明示的に指定された場合、またはバッチ並列計算において GPU が利用不能な場合は即座に `RuntimeError` を送出して処理を停止する（フェイルファスト原則）。CPU への暗黙的フォールバックは禁止。
- **ブラウザ**: WebGL 2.0 対応ブラウザ (Google Chrome, Microsoft Edge, Firefox, Safari)

---

## 3. セットアップ手順 (Setup)

### 3.1 自動セットアップスクリプトの実行

**Windows (PowerShell):**
```powershell
.\setup.ps1
```

**Linux / macOS (Bash):**
```bash
chmod +x setup.sh
./setup.sh
```

### 3.2 手動セットアップ (uv または pip)

**uv を使用する場合:**
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

**pip を使用する場合:**
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

---

## 4. 実行手順 (Usage)

### 4.1 サーバーの起動
```bash
python app.py
```
起動完了後、`http://127.0.0.1:5000` にアクセスして計器コンソールを表示する。

### 4.2 テストスイートの実行
```bash
# 静的解析 (Ruff / Mypy)
ruff check .
mypy physics app.py

# 単体テスト (pytest)
pytest tests -q

# 統合検証 (13 ステージ)
python smoke_test.py
```

---

## 5. アーキテクチャ概要 (Architecture)

```text
.
├── pyproject.toml             # 標準ビルド定義・依存関係・ツール設定
├── requirements.txt           # 依存パッケージ一覧
├── setup.ps1 / setup.sh       # 環境自動構築スクリプト
├── app.py                     # Flask エントリーポイント
├── smoke_test.py              # 全 13 ステージ統合検証スクリプト
├── api/                       # REST API ルーティングパッケージ
│   ├── core.py                # 共通セッション・バリデーション・エラー処理
│   └── routes/                # 責務別エンドポイント (physics, simulation, mesh, jobs, meta)
├── physics/                   # 物理演算・流体力学コア
│   ├── config_schema.py       # データクラス・定数・単位スキーマ
│   ├── aero_domain.py         # 航空力学モデル・失速・Drag Polar
│   ├── atmosphere_domain.py   # ISA 1976 標準大気・Sutherland 動粘性
│   ├── wind_domain.py         # 3D 風場・乱気流・熱上昇気流 (CUDA/GPU)
│   ├── stress_domain.py       # 動圧・Gフォース・翼面応力テンソル (CUDA/GPU)
│   ├── flight_sim.py          # 6DoF RK4 軌道積分器・四元数姿勢
│   ├── parallel_engine.py     # GPU 100機同時アンサンブル / パラメータスイープ
│   ├── optimizer_domain.py    # NSGA-II / Nelder-Mead / DE 最適化
│   └── job_manager.py         # ETA・進捗率・非同期ジョブ管理
├── static/                    # Three.js 3D レンダリング・フロントエンド
├── templates/                 # メイン計器コンソール HTML
└── docs/                      # 技術仕様・API・運用ドキュメント
```

詳細な数式および内部構造については [docs/architecture.md](./docs/architecture.md) を参照のこと。

Discord から Antigravity エージェントを操作するブリッジについては [docs/discord-bridge.md](./docs/discord-bridge.md) を参照のこと。
