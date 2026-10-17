# 3D Paper Airplane High-Performance CFD Simulator

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/backend-Flask%20REST%20API-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Three.js](https://img.shields.io/badge/frontend-Three.js%20%2F%20WebGL-black.svg)](https://threejs.org/)
[![Physics 6DoF](https://img.shields.io/badge/physics-6DoF%20RK4-brightgreen.svg)]()
[![Tests](https://img.shields.io/badge/tests-672%20passed-success.svg)]()

流体力学および航空工学理論に基づく、高精度・リアルタイムな**3D紙飛行機フライトシミュレーター**です。  
Flask REST API による物理演算バックエンドと、Three.js による 3D 計器コンソール・気流可視化フロントエンドを統合しています。

---

## ⚡ クイックスタート (30秒で起動)

### 1. 必要要件
- **Python**: 3.10 以上 (Python 3.10 / 3.11 推奨)
- **WebGL 2.0 対応ブラウザ**: Google Chrome, Microsoft Edge, Firefox, Safari

### 2. インストール & 起動
```powershell
# 依存ライブラリのインストール
pip install -r requirements.txt

# サーバーの起動
python app.py
```

ブラウザで 👉 **[http://localhost:5000](http://localhost:5000)** を開くと、Google Clean スタイルの計器コンソールが起動します。

---

## 🌟 主な機能・特徴

- **航空力学・空力計算 (Aero & Atmosphere)**:
  ISA標準大気、Sutherland動粘性、Viterna非線形失速モデル、地面効果、受動空力弾性モーフィング、抗力極線（Drag Polar）の自動算出。
- **6自由度 (6DoF) 飛行力学 & 時間制御**:
  4次ルンゲ＝クッタ法（RK4）および四元数姿勢表現。適応サブステッピングによる発散防止。再生・一時停止・倍速・巻き戻しシーク対応のリングバッファ構造。
- **3D風場・乱気流 (Fluid Dynamics)**:
  3D空間ベクトル場、Simplex/Perlin乱流、熱上昇気流（GPU / CPU両対応）。
- **構造負荷・応力解析 (Structural Stress)**:
  動圧・Gフォースによる翼根曲げモーメント算出と、頂点応力ヒートマップ（青〜赤）変形描画。
- **3Dメッシュインポート & パネル法 (Mesh Domain)**:
  STL（バイナリ/ASCII）、OBJ、glTF 2.0 / GLB の取込。表面積、重心、慣性テンソル自動算出と静的パネル法。
- **多目的最適化エンジン (Optimization Engine)**:
  Nelder-Mead、Differential Evolution、NSGA-II（パレートフロント）、Surrogateモデル搭載。リアルタイム進捗率・ETA算出。
- **データ永続化 & エクスポート**:
  飛行ログの CSV 即時ダウンロード、機体・環境プリセットの JSON インポート/エクスポート。

---

## 📚 ドキュメント一覧 (Documentation)

目的に応じて以下の詳細ドキュメントをご参照ください：

| ドキュメント | 内容・用途 |
|:---|:---|
| 🚀 **[環境構築・インストール (docs/getting-started.md)](./docs/getting-started.md)** | 詳細なセットアップ手順、GPU（CUDA/CuPy）環境構築、.env設定 |
| 🏛️ **[アーキテクチャ・物理理論 (docs/architecture.md)](./docs/architecture.md)** | 全体設計、ディレクトリ構造、航空工学数式・理論モデルの解説 |
| 📖 **[ユーザーガイド・操作方法 (docs/usage.md)](./docs/usage.md)** | 画面の見方、パラメータ調整、3Dモデル取込、最適化ジョブの使い方 |
| 📡 **[REST API 仕様書 (docs/api.md)](./docs/api.md)** | 全RESTエンドポイント仕様、リクエスト/レスポンス例、ステータスコード |
| 🛠️ **[開発・テストガイド (docs/development.md)](./docs/development.md)** | pytest単体テスト・総合スモークテストの実行方法、Git運用規約 |
| ❓ **[トラブルシューティング (docs/troubleshooting.md)](./docs/troubleshooting.md)** | よくあるエラー・不具合の対処法とFAQ |

---

## 🧪 テスト・検証

```powershell
# 構文チェック
python -m compileall physics app.py

# 単体テスト (pytest)
python -m pytest tests -q

# 統合スモークテスト (API疎通・最適化ジョブ・メッシュ解析)
python smoke_test.py
```

---

## 📁 ディレクトリ概要

```text
.
├── app.py                     # Flask REST API エンドポイント
├── requirements.txt           # 依存パッケージ定義
├── smoke_test.py              # API導通・最適化・メッシュ統合検証スクリプト
├── physics/                   # 物理・シミュレーション演算コア
├── static/                    # Three.js 3D描画 & UI制御スクリプト
├── templates/                 # 計器コンソールUI (Google Clean Standard)
├── tests/                     # 単体テスト群 (pytest)
├── docs/                      # 各種詳細ドキュメント (Getting Started, API, etc.)
└── models/                    # アップロードされた3Dモデルキャッシュ
```
詳細は [アーキテクチャ解説 (docs/architecture.md)](./docs/architecture.md) をご覧ください。

---

## 📄 ライセンス

本プロジェクトは研究・教育・個人利用向けに開発されています。
