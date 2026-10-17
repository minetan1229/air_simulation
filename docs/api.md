# REST API リファレンス (API Specifications)

本ドキュメントでは、本システムで提供される Flask REST API の全エンドポイント、リクエスト/レスポンス形式、およびエラーハンドリングについて説明します。

---

## 1. 共通仕様・エラーハンドリング

- **ベースURL**: `http://localhost:5000`
- **データ形式**: リクエスト / レスポンスともに `application/json` (ファイルアップロードを除く)
- **バックエンド識別子**: 成功レスポンスには、実行した計算バックエンド（`"backend": "cupy"` / `"torch"` / `"numpy"`）が常に含まれます。
- **エラーレスポンス**:
  クライアント側の不正リクエスト時は `HTTP 400`、内部エラー時は `HTTP 500` と詳細メッセージが返却されます。
  ```json
  {
    "error": "Error description message",
    "status": 400,
    "timestamp": "2026-10-17T13:20:00Z"
  }
  ```

---

## 2. API エンドポイント一覧

| メソッド | エンドポイント | 概要 |
|:---|:---|:---|
| `POST` | `/api/v1/aerodynamics/calculate` | 指定条件における揚力・抗力・モーメント・失速判定の計算 |
| `POST` | `/api/v1/aerodynamics/polar` | 迎角スイープによる抗力極線（Drag Polar）データの取得 |
| `POST` | `/api/v1/environment/atmosphere` | 大気・環境パラメータの設定と大気物性値（密度・動粘性等）の取得 |
| `GET`  | `/api/v1/environment/current` | 現在の大気・環境パラメータの照会 |
| `POST` | `/api/v1/wind/field` | 指定3D空間グリッドにおける風速ベクトル場の計算（GPU/CPU） |
| `POST` | `/api/v1/stress/analysis` | 翼面動圧・Gフォースおよび頂点応力ヒートマップの算出 |
| `POST` | `/api/v1/simulation/step` | 6DoF運動方程式の1ステップ状態更新（可変 $\Delta t$ 対応） |
| `POST` | `/api/v1/simulation/reset` | シミュレーション初期化 |
| `POST` | `/api/v1/simulation/batch` | 複数パラメータスイープ・並列飛行シミュレーション実行 |
| `POST` | `/api/v1/mesh/import` | 3Dモデル（STL/OBJ/GLB）アップロードと自動物理量解析 |
| `GET`  | `/api/v1/mesh/models` | アップロード済みモデル一覧の照会 |
| `POST` | `/api/v1/mesh/select` | シミュレーションで使用する3Dモデルの選択 |
| `GET`  | `/api/v1/mesh/metrics/<model_id>` | 指定モデルの詳細物理特性（重心・慣性テンソル等）照会 |
| `POST` | `/api/v1/optimizer/run` | 最適化ジョブの非同期開始（即座に `job_id` 返却） |
| `GET`  | `/api/v1/optimizer/status/<job_id>` | 最適化ジョブの進捗率・評価速度・推定完了時刻（ETA）照会 |
| `POST` | `/api/v1/optimizer/cancel/<job_id>` | 実行中最適化ジョブのキャンセル |
| `GET`  | `/api/v1/optimizer/result/<job_id>` | 最適化ジョブの結果（パレートフロント・最適値）取得 |
| `GET`  | `/api/v1/jobs` | 登録済み全ジョブ一覧とステータス照会 |
| `POST` | `/api/v1/data/export` | 飛行ログデータの CSV / JSON 形式エクスポート |
| `POST` | `/api/v1/data/import` | プリセット設定のインポート |

---

## 3. 主要エンドポイント詳細

### 3.1 揚力・抗力計算
`POST /api/v1/aerodynamics/calculate`

**リクエスト例:**
```json
{
  "alpha_deg": 4.5,
  "velocity_mps": 6.2,
  "density": 1.225,
  "wing_span": 0.28,
  "wing_area": 0.024,
  "aspect_ratio": 3.27,
  "gsm": 80.0
}
```

**レスポンス例:**
```json
{
  "lift_N": 0.0892,
  "drag_N": 0.0124,
  "lift_coeff": 0.324,
  "drag_coeff": 0.045,
  "is_stalled": false,
  "reynolds_number": 34500,
  "ld_ratio": 7.19,
  "backend": "numpy"
}
```

---

### 3.2 3D風場ベクトル計算
`POST /api/v1/wind/field`

**リクエスト例:**
```json
{
  "bounds": {
    "x_min": -10, "x_max": 10,
    "y_min": -10, "y_max": 10,
    "z_min": 0, "z_max": 15
  },
  "resolution": [20, 20, 15],
  "mean_wind": [2.0, 0.0, 0.0],
  "turbulence_intensity": 0.15
}
```

**レスポンス例:**
```json
{
  "grid_shape": [20, 20, 15, 3],
  "vectors": [...],
  "backend": "cupy"
}
```

---

### 3.3 非同期最適化ジョブ開始
`POST /api/v1/optimizer/run`

**リクエスト例:**
```json
{
  "algorithm": "nsga2",
  "objectives": ["max_distance", "min_max_stress"],
  "population_size": 24,
  "generations": 10
}
```

**レスポンス例 (即座に返却):**
```json
{
  "job_id": "8f3b2d1c-e7a9-4a2b-923f-1d84f04c64b1",
  "status": "queued",
  "message": "Optimization job successfully queued"
}
```

---

### 3.4 最適化ジョブステータス照会
`GET /api/v1/optimizer/status/<job_id>`

**レスポンス例:**
```json
{
  "job_id": "8f3b2d1c-e7a9-4a2b-923f-1d84f04c64b1",
  "status": "running",
  "progress": 0.45,
  "evaluated_count": 108,
  "total_evaluations": 240,
  "evals_per_second": 12.4,
  "eta_seconds": 10.6,
  "estimated_completion": "2026-10-17T13:20:15Z"
}
```

---

## 4. 関連ドキュメント

- [クイックスタート (docs/getting-started.md)](./getting-started.md)
- [システムアーキテクチャ (docs/architecture.md)](./architecture.md)
- [ユーザーガイド (docs/usage.md)](./usage.md)
- [開発・テストガイド (docs/development.md)](./development.md)

---

[⬅ README に戻る](../README.md)
