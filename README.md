# 3D Paper Airplane High-Performance CFD Simulator

流体力学および航空工学理論に基づく、高精度・リアルタイムな「3D紙飛行機フライトシミュレーター」です。  
Flask REST APIによる物理演算バックエンドと、Three.jsによる3D計器コンソール・気流可視化フロントエンドを統合しています。

---

## 🌟 主な機能・特徴

1. **高度な航空力学・空力計算 (Aero & Atmosphere)**
   - ISA標準大気・局所大気圧/湿度/温度に基づく空気密度・動粘性（Sutherland式）計算。
   - 非線形失速モデル（Viterna法相当）、誘導抗力、地面効果（Ground Effect）、エレボン・トリム角の動的補正。
   - 抗力極線（Drag Polar）の自動算出と最大揚抗比（$L/D_{\max}$）評価。

2. **6自由度 (6DoF) 運動方程式 & 数値積分**
   - 4次ルンゲ＝クッタ法（RK4）およびクォータニオン姿勢表現による滑らかで安定した挙動。
   - 動的適応サブステッピング（Adaptive Sub-stepping）による高速・急旋回時の発散防止。
   - タイムラインシーク・巻き戻し・再生倍速（0.25x〜10x）制御対応のリングバッファ構造。

3. **3D風場・乱気流 (Fluid Dynamics)**
   - 3D空間ベクトル場とSimplex/Perlinノイズによる乱流、熱上昇気流（サーマル）モデリング。
   - GPU加速（CuPy / PyTorch）およびNumPyによるCPUフォールバック。

4. **構造負荷・応力解析 (Structural Stress)**
   - 動圧とGフォースから翼根（Wing Root）曲げモーメントを算出。
   - メッシュ頂点ごとのリアルタイム応力ヒートマップ（青〜赤）変形表示。

5. **3Dメッシュインポート & パネル法解析 (Mesh Domain)**
   - STL（バイナリ/ASCII）、OBJ、glTF 2.0 / GLB 形式の機体モデルインポートに対応。
   - 表面積、スパン、平均翼弦、アスペクト比、重心位置、慣性テンソルを自動算出。
   - パネル分割による局所迎角・揚力・回転揚力の静的パネル法推定。

6. **多目的最適化エンジン (Optimization Engine)**
   - Nelder-Mead、Differential Evolution、NSGA-II（パレートフロント抽出）、サロゲートモデル（Surrogate）を搭載。
   - 非同期ジョブ管理（`JobManager`）によるリアルタイム進捗率・完了予定時刻（ETA）算出。

7. **データ永続化 & エクスポート**
   - 飛行履歴ログ（位置、速度、迎角、Gフォース、応力等）のCSV即時ダウンロード。
   - 機体・大気プリセット設定のJSONインポート/エクスポート。

---

## 📁 ディレクトリ構成

```text
.
├── app.py                     # Flask REST API エンドポイント
├── requirements.txt           # 依存パッケージ定義
├── smoke_test.py              # API導通・最適化・メッシュ統合検証スクリプト
├── physics/                   # 物理・シミュレーション演算コア
│   ├── config_schema.py       # データクラス / パラメータ定義
│   ├── geometry_utils.py      # クォータニオン・回転・ベクトル計算
│   ├── aero_domain.py         # 揚力・抗力・失速・Drag Polar
│   ├── atmosphere_domain.py   # 大気圧・気温・湿度・動粘性
│   ├── wind_domain.py         # 3D風場・乱流生成（GPU/CPU）
│   ├── stress_domain.py       # 応力・曲げモーメント・ヒートマップ
│   ├── mesh_domain.py         # 3Dモデル解析・パネル法
│   ├── flight_sim.py          # 6DoF RK4積分・軌道シミュレーション
│   ├── parallel_engine.py     # 並列バッチ評価エンジン
│   ├── optimizer_domain.py    # NSGA-II / DE / Nelder-Mead 最適化
│   ├── job_manager.py         # 非同期ジョブ進捗・ETA管理
│   └── storage_domain.py      # CSV / JSON インポート・エクスポート
├── templates/
│   └── index.html             # 計器コンソールUI
├── static/
│   ├── css/console.css        # スタイルシート（Google Clean Standard）
│   └── js/                    # Three.js 3D描画 & UI制御スクリプト
├── tests/                     # pytest 単体テスト群
└── models/                    # インポートした3Dモデルのキャッシュ
```

---

## 🚀 クイックスタート

### 1. 必要要件
- Python 3.8 以上 (Python 3.10 / 3.11 推奨)
- 仮想環境（venv / conda）

### 2. インストール
```powershell
# 依存ライブラリのインストール
pip install -r requirements.txt
```

*(任意) GPU加速を有効にする場合:*
```powershell
pip install cupy-cuda12x  # または環境に応じた PyTorch / CUDA
```

### 3. アプリケーションの起動
```powershell
python app.py
```
起動後、ブラウザで [http://localhost:5000](http://localhost:5000) にアクセスしてください。

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

## 📡 主要 REST API 一覧

| メソッド | エンドポイント | 説明 |
|---|---|---|
| `POST` | `/api/v1/aerodynamics/calculate` | 揚力・抗力・モーメント・失速状態の算出 |
| `POST` | `/api/v1/aerodynamics/polar` | 迎角スイープによる抗力極線（Drag Polar）の取得 |
| `POST` | `/api/v1/environment/atmosphere` | 大気・環境パラメータの設定と大気物性値の取得 |
| `POST` | `/api/v1/wind/field` | 指定3Dグリッドの風速ベクトル場取得 |
| `POST` | `/api/v1/stress/analysis` | 翼面動圧・Gフォース・応力ヒートマップデータ取得 |
| `POST` | `/api/v1/simulation/step` | 6DoF 1ステップ状態更新 |
| `POST` | `/api/v1/mesh/import` | 3Dモデル（STL/OBJ/GLB）アップロード & 物理特性自動解析 |
| `POST` | `/api/v1/optimizer/run` | 最適化ジョブの非同期開始（即座に `job_id` 返却） |
| `GET`  | `/api/v1/optimizer/status/<job_id>` | 最適化進捗・残り時間（ETA）の照会 |
| `POST` | `/api/v1/data/export` | 飛行ログデータの CSV / JSON ダウンロード |
