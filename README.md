# 3D Paper Airplane Simulator

6自由度（6DoF）の飛行力学シミュレーションをWebGLで可視化するプロジェクトです。紙飛行機特有の低レイノルズ数領域（Re ~ 10³〜10⁵）における非線形空力現象を、学術的な根拠に基づくモデルで再現しています。

FlaskのREST APIとThree.jsを組み合わせ、ブラウザ上でリアルタイムに飛行軌跡・姿勢・気流を表示できます。GPU並列計算（CuPy）と遺伝的アルゴリズムによる最適化探索にも対応しています。

## 特徴

- **6DoF飛行力学**: 四元数とRK4積分による並進・回転運動の実時間シミュレーション。連続衝突判定（CCD）による地表接地・翼端散逸まで処理
- **低Re空力モデル**: Helmbold（低AR揚力傾斜）、Polhamus（前縁渦揚力）、Hoerner（失速後平板揚力）、LSB（層流剥離泡・動的失速）を統合
- **環境モデル**: ISA標準大気、Sutherlandの粘性公式、Dryden乱流モデル、3D風場
- **3Dインタラクティブ表示**: Three.js/WebGLによる60FPS計器盤、軌道・姿勢追跡、頂点応力ヒートマップ、仮想風洞、タイムラインシーク
- **GPU並列・最適化**: CuPyベクトル化計算＋CPU自動フォールバック、NSGA-II/DE/CMA-ES/サロゲートによる多機同時アンサンブル（100機規模）とパレート探索
- **REST API**: Flask + Swagger UI/OpenAPI 3.0。ヘッドレス飛行実行からUIスキーマ取得まで

## 物理モデルの根拠

| 現象 | 採用モデル | 文献 |
|------|------------|------|
| 低AR揚力傾斜 | Helmbold (1942) 3次元補正 | Jahrbuch D. Luftfahrtforschung |
| 失速後挙動 | Hoerner 平板混合式 | Fluid-Dynamic Drag (1965) |
| 低Re層流剥離泡 | 動的失速ヒステリシス | Pelletier & Mueller (2000) |
| 前縁渦揚力 | Polhamus 先導縁吸い込み | NASA TN D-3767 (1966) |
| 6DoF運動 | 四元数 + RK4 | 剛体力学・オイラー方程式 |
| 環境・乱気流 | ISA + Sutherland + Dryden | US Std Atmosphere 1976 / MIL-F-8785C |

詳細は [docs/SPECIFICATION.md](docs/SPECIFICATION.md) と [docs/PHYSICS_MODELS.md](docs/PHYSICS_MODELS.md) を参照してください。

## クイックスタート

### ローカル環境（Python）

```bash
git clone https://github.com/minetan1229/air_simulation.git
cd air_simulation
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python app.py
# → http://127.0.0.1:5000 をブラウザで開く
```

### Docker

```bash
git clone https://github.com/minetan1229/air_simulation.git
cd air_simulation
docker compose up --build
# → http://127.0.0.1:5000
```

### Google Colab（推奨・環境構築不要）

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/minetan1229/air_simulation/blob/main/notebooks/quickstart.ipynb)

Colabノートブックでは以下がワンクリックで実行できます：
- 依存関係のインストール（CuPy自動検出・CPUフォールバック込み）
- シミュレーション実行と3D可視化の表示
- パラメータ変更による飛行特性の比較
- 最適化探索（GA）の実行と結果プロット

**Colabで試す手順：**
1. 上のバッジをクリックしてColabでノートブックを開く
2. 「ランタイム」→「すべてのセルを実行」を選択
3. 数分待つと3Dビューアが出力セルに表示されます

> **注意**: Colabの無料枠ではGPUメモリに制限があります。大規模アンサンブル（100機以上）や長時間シミュレーションはローカル環境またはDockerを推奨します。

## ディレクトリ構成

```
├── app.py               # Flask起動エントリーポイント
├── api/                 # REST APIエンドポイント
├── physics/             # 物理演算・空力モデル・最適化エンジン
├── examples/            # ヘッドレス実行サンプル
├── static/              # Three.jsフロントエンド
├── templates/           # HTMLテンプレート + Swagger UI
├── tests/               # 単体・統合テスト
├── docs/                # 詳細仕様・アーキテクチャ資料
├── notebooks/           # Colab/Jupyterノートブック
└── .github/workflows/   # CI / CodeQL / Docsデプロイ
```

## ドキュメント

- **入門**: [docs/getting-started.md](docs/getting-started.md) - 環境構築・起動詳細
- **物理・理論**: [docs/SPECIFICATION.md](docs/SPECIFICATION.md) - 技術仕様正本
- **API**: [docs/api.md](docs/api.md) - RESTリファレンス / Swagger UI: `http://localhost:5000/docs`
- **検証**: [docs/VALIDATION.md](docs/VALIDATION.md) - V&Vテスト結果

## 開発・コントリビューション

Issue/PRは [CONTRIBUTING.md](CONTRIBUTING.md) の規約に従ってください。物理モデル変更時は専用テンプレートを使用してください。

- Zero-Placeholder方針: `TODO`・空関数・ダミー実装は禁止
- ブランチ運用: `mission/<slug>` ブランチで作業し、完了時にmasterへマージ
- セキュリティ報告: [SECURITY.md](SECURITY.md)

## ライセンス

プロプライエタリ（非公開）ライセンスです。詳細は [LICENSE](LICENSE) を参照してください。

## 引用

論文・報告で使用する場合は以下をご利用ください（詳細は [CITATION.cff](CITATION.cff)）:

```bibtex
@software{air_simulation_2026,
  title   = {3D Paper Airplane Simulator: 6DoF RK4 Flight Dynamics, Low-Reynolds Aerodynamics and GPU Ensemble Optimisation},
  author  = {minetan1229},
  year    = 2026,
  url     = {https://github.com/minetan1229/air_simulation},
  version = {3.0.0}
}
```