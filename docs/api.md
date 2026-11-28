# REST API リファレンス (API Specifications)

本ドキュメントでは、本システムで提供される Flask REST API の全エンドポイント、リクエスト/レスポンス形式、およびエラーハンドリングについて説明します。

> **インタラクティブ仕様**: サーバー起動後に [Swagger UI](http://127.0.0.1:5000/docs)（`GET /docs`）で
> cURL / Python / JavaScript のリクエスト例を試演できます。機械可読仕様は
> [`GET /api/openapi.json`](http://127.0.0.1:5000/api/openapi.json)（OpenAPI 3.0.3、自動生成。生成元: `api/openapi.py`）から取得可能です。
> 本 Markdown は人間可読の要約であり、正本はランタイム仕様です。

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
    "timestamp": "2026-10-17T13:20:00Z",
    "error_id": "err_abc123def456"
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
  "speed_mps": 6.2,
  "alpha_deg": 4.5,
  "beta_deg": 0.0,
  "mach": 0.0,
  "ground_height_m": 1.0,
  "aircraft": {
    "wing_area_m2": 0.024,
    "wingspan_m": 0.28,
    "aspect_ratio": 3.27,
    "mass_kg": 0.005,
    "paper_gsm": 80.0
  },
  "environment": {
    "density": 1.225,
    "temperature_K": 288.15
  }
}
```

**レスポンス例:**
```json
{
  "cl": 0.324,
  "cd": 0.045,
  "cm": -0.012,
  "cy": 0.0,
  "lift_n": 0.0892,
  "drag_n": 0.0124,
  "side_n": 0.0,
  "pitching_moment_nm": -0.0015,
  "yawing_moment_nm": 0.0,
  "rolling_moment_nm": 0.0,
  "ld_ratio": 7.19,
  "dynamic_pressure_pa": 23.5,
  "reynolds": 34500,
  "mach": 0.018,
  "stall_margin": 0.65,
  "stalled": false,
  "w_stall": 0.0,
  "trim_alpha_deg": 4.44,
  "ground_effect_ratio": 1.02,
  "lift_left_n": 0.0446,
  "lift_right_n": 0.0446,
  "cp_x_m": 0.032,
  "backend": "numpy"
}
```

---

### 3.2 3D風場ベクトル計算
`POST /api/v1/wind/field`

**リクエスト例:**
```json
{
  "bounds_min": [-10, -10, 0],
  "bounds_max": [10, 10, 15],
  "resolution": [20, 20, 15],
  "time_s": 0.0,
  "backend": "auto",
  "environment": {
    "wind_speed": 2.0,
    "wind_direction_deg": 0.0,
    "gust_intensity": 0.15,
    "turbulence_scale": 0.35,
    "turbulence_seed": 12345
  }
}
```

**レスポンス例:**
```json
{
  "positions": [[...], [...], [...]],
  "vectors": [[...], [...], [...]],
  "speeds": [...],
  "shape": [20, 20, 15],
  "resolution": [20, 20, 15],
  "backend": "cupy",
  "backend_requested": "auto",
  "grid_point_count": 6000,
  "bounds": {"min": [-10, -10, 0], "max": [10, 10, 15]},
  "time_s": 0.0,
  "gust_factor": 1.0,
  "mean_wind_mps": 2.0,
  "max_speed": 2.8,
  "mean_speed": 2.1
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
  "max_evaluations": 200,
  "design_variables": ["wing_area_m2", "wingspan_m", "mass_kg", "paper_gsm", "elevon_deg"],
  "aircraft": {},
  "environment": {},
  "launch": {},
  "variable_bounds": {},
  "sim_duration_s": 6.0,
  "dt_max": 0.02,
  "seed": 7,
  "processes": 0
}
```

**レスポンス例 (即座に返却、ステータスは `running`):**
```json
{
  "job_id": "8f3b2d1c-e7a9-4a2b-923f-1d84f04c64b1",
  "name": "nsga2 optimizer",
  "algorithm": "nsga2",
  "status": "running",
  "progress": 0.0,
  "evaluated_count": 0,
  "total_evaluations": 200,
  "started_at": "2026-10-17T13:20:00Z",
  "updated_at": "2026-10-17T13:20:00Z",
  "finished_at": "",
  "eta_seconds": 0.0,
  "estimated_completion": "",
  "backend": "multiprocessing",
  "elapsed_seconds": 0.0,
  "evals_per_second": 0.0,
  "message": "Optimization job started",
  "objectives": ["max_distance", "min_max_stress"],
  "model_id": "",
  "result_path": ""
}
```

---

### 3.4 最適化ジョブステータス照会
`GET /api/v1/optimizer/status/<job_id>`

**レスポンス例:**
```json
{
  "job_id": "8f3b2d1c-e7a9-4a2b-923f-1d84f04c64b1",
  "name": "nsga2 optimizer",
  "algorithm": "nsga2",
  "status": "running",
  "progress": 0.45,
  "evaluated_count": 108,
  "total_evaluations": 240,
  "started_at": "2026-10-17T13:20:00Z",
  "updated_at": "2026-10-17T13:20:15Z",
  "finished_at": "",
  "eta_seconds": 10.6,
  "estimated_completion": "2026-10-17T13:20:25Z",
  "backend": "multiprocessing",
  "elapsed_seconds": 15.2,
  "evals_per_second": 12.4,
  "message": "Optimization running",
  "objectives": ["max_distance", "min_max_stress"],
  "model_id": "",
  "result_path": ""
}
```

---

## 4. ヘッドレス実行（Web UI を介さない）

```bash
# Python 単体（サーバー不要）
python examples/quick_simulation.py --duration 6.0

# REST 経路（サーバー起動後）
curl -X POST http://127.0.0.1:5000/api/v1/simulation/launch \
  -H 'Content-Type: application/json' \
  -d '{"launch":{"launch_speed":12.0,"launch_angle_deg":12.0}}'

# 認証が必要な場合（API_KEY が設定されているとき）
curl -X POST http://127.0.0.1:5000/api/v1/optimizer/run \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"algorithm":"nsga2","objectives":["max_distance"],"population_size":24,"generations":10}'
```

詳細は [examples/quick_simulation.py](https://github.com/minetan1229/air_simulation/blob/master/examples/quick_simulation.py) を参照。

---

## 5. 関連ドキュメント

- [クイックスタート (docs/getting-started.md)](./getting-started.md)
- [システムアーキテクチャ (docs/architecture.md)](./architecture.md)
- [ユーザーガイド (docs/usage.md)](./usage.md)
- [開発・テストガイド (docs/development.md)](./development.md)

---

[README に戻る](https://github.com/minetan1229/air_simulation/blob/master/README.md)
