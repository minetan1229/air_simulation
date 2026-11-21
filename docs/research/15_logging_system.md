# 全事象ロギング・デバッグ・エラー追跡システム設計

## 1. 概要
「すべての事象をログに残す」要件（REQ-12）を満たすために、以下を実装する:
- **バックエンド（Flask）**: 構造化ログ（JSON Lines 形式）、全 API 要求・応答、計算ステップ、エラートレース
- **フロントエンド（JavaScript）**: UI 操作、パラメータ変更、シミュレーション状態遷移の全ログ
- **ファイル分割**: 日付別ローテーション、レベル別フィルタリング（DEBUG/INFO/WARNING/ERROR/CRITICAL）

主要参考文献:
- Python `logging` モジュール公式ドキュメント
- Structured Logging Best Practices (DataDog Engineering Blog)
- OWASP Logging Cheat Sheet

---

## 2. ログカテゴリと対象事象

### 2.1 全ログ対象事象リスト

| カテゴリ | 事象 | レベル |
|---------|------|--------|
| **起動** | Flask サーバー起動・環境変数読み込み | INFO |
| **環境** | GPU/CPU バックエンド検出結果 | INFO |
| **環境** | 大気パラメータ設定変更 | INFO |
| **API** | 全エンドポイントへのリクエスト受信 | DEBUG |
| **API** | レスポンス送信（ステータスコード・処理時間） | INFO |
| **API** | バリデーションエラー（400系） | WARNING |
| **API** | サーバーエラー（500系）+ フルスタックトレース | ERROR |
| **計算開始** | シミュレーション・最適化ジョブ開始 | INFO |
| **計算経過** | 各積分ステップ（間引き: 100ステップ毎） | DEBUG |
| **計算終了** | 総計算時間・最終状態 | INFO |
| **計算エラー** | 数値発散・NaN/Inf 検出 | ERROR |
| **UI操作** | ボタンクリック・スライダー変更（JS→API） | DEBUG |
| **パラメータ** | 全パラメータセットのスナップショット | DEBUG |
| **状態遷移** | 飛行フェーズ変化（上昇→頂点→滑空→着地） | INFO |
| **ジョブ管理** | ジョブ作成・進捗・完了・キャンセル | INFO |

---

## 3. バックエンドロギング実装

### 3.1 構造化ロガー設定
```python
import logging
import logging.handlers
import json
import time
import traceback
from pathlib import Path
from datetime import datetime

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

class JSONLineFormatter(logging.Formatter):
    """全ログを JSON Lines 形式で出力するフォーマッター"""
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
        }
        # 追加フィールド（kwargs で渡された情報）
        for key in ("job_id", "endpoint", "duration_ms", "params", "result"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        # 例外情報
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

def setup_logger(name: str, level: str = "DEBUG") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level))

    # 日付別ローテーション（midnight に切替、最大30日保存）
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=LOG_DIR / f"{name}.jsonl",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONLineFormatter())
    logger.addHandler(file_handler)

    # コンソール出力（開発時）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(console_handler)

    return logger

# モジュール別ロガー
api_logger = setup_logger("api")
physics_logger = setup_logger("physics")
sim_logger = setup_logger("simulation")
opt_logger = setup_logger("optimizer")
```

### 3.2 Flask API フック（全エンドポイント自動ログ）
```python
from functools import wraps
import time

def log_api_call(f):
    """デコレータ: 全 API エンドポイントの入出力を自動ログ"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        request_data = request.get_json(silent=True) or {}
        api_logger.debug(
            f"→ {request.method} {request.path}",
            extra={"endpoint": request.path, "params": request_data}
        )
        try:
            response = f(*args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000
            api_logger.info(
                f"← {request.path} [{response.status_code}] {duration_ms:.1f}ms",
                extra={"endpoint": request.path, "duration_ms": round(duration_ms, 2)}
            )
            return response
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            api_logger.error(
                f"✗ {request.path} ERROR: {e}",
                exc_info=True,
                extra={"endpoint": request.path, "duration_ms": round(duration_ms, 2)}
            )
            raise
    return wrapper
```

### 3.3 数値発散検出
```python
import numpy as np

def check_state_validity(state: np.ndarray, step: int, logger) -> bool:
    """NaN/Inf 検出とクランプ処理"""
    if np.any(np.isnan(state)) or np.any(np.isinf(state)):
        logger.error(
            f"数値発散検出: step={step}, state={state}",
            extra={"params": {"step": step, "state": state.tolist()}}
        )
        return False
    return True
```

---

## 4. フロントエンドロギング実装

### 4.1 JS ロガークラス
```javascript
class AppLogger {
    #buffer = [];
    #maxBuffer = 10000;

    constructor(prefix = 'APP') {
        this.prefix = prefix;
    }

    #log(level, category, message, data = null) {
        const entry = {
            ts: new Date().toISOString(),
            level,
            category,
            message,
            data,
        };
        this.#buffer.push(entry);
        if (this.#buffer.length > this.#maxBuffer) this.#buffer.shift();

        const color = { DEBUG:'#999', INFO:'#1A73E8', WARN:'#FBBC04', ERROR:'#EA4335' }[level];
        console.log(`%c[${level}][${category}] ${message}`, `color:${color}`, data ?? '');
    }

    debug(cat, msg, data) { this.#log('DEBUG', cat, msg, data); }
    info(cat, msg, data)  { this.#log('INFO',  cat, msg, data); }
    warn(cat, msg, data)  { this.#log('WARN',  cat, msg, data); }
    error(cat, msg, data) { this.#log('ERROR', cat, msg, data); }

    // UIイベント専用ヘルパー
    uiAction(elementId, action, value = null) {
        this.info('UI', `${action}: ${elementId}`, { elementId, action, value });
    }

    // パラメータ変更ログ
    paramChange(paramName, oldVal, newVal) {
        this.debug('PARAM', `${paramName}: ${oldVal} → ${newVal}`, { paramName, oldVal, newVal });
    }

    // ログをバックエンドに送信（バッファフラッシュ）
    async flush() {
        if (this.#buffer.length === 0) return;
        const logs = [...this.#buffer];
        this.#buffer = [];
        try {
            await fetch('/api/v1/logs/frontend', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ logs }),
            });
        } catch (e) { /* サイレント失敗 */ }
    }
}

export const logger = new AppLogger('PAPER_PLANE');
```

### 4.2 ログ活用例（UI操作）
```javascript
// スライダー変更
document.getElementById('slider-aoa').addEventListener('input', e => {
    const newVal = parseFloat(e.target.value);
    logger.paramChange('angle_of_attack_deg', state.aoa, newVal);
    state.aoa = newVal;
    state.dirty = true;
});

// シミュレーション開始ボタン
document.getElementById('btn-start').addEventListener('click', () => {
    logger.uiAction('btn-start', 'CLICK');
    simController.startSimulation();
});
```

---

## 5. ログファイル構成

```
logs/
├── api.jsonl              # 現在日の API ログ（JSON Lines）
├── api.jsonl.2026-10-15   # 前日分
├── physics.jsonl          # 物理計算ログ
├── simulation.jsonl       # シミュレーションステップログ
├── optimizer.jsonl        # 最適化ジョブログ
└── frontend/
    └── frontend.jsonl     # フロントエンドから送信されたUIログ
```

---

## 6. フライトデータのリアルタイムエクスポート

### CSV ヘッダーとフィールド定義
```
time_s, x_m, y_m, z_m, speed_m_s, alpha_deg, beta_deg,
gamma_deg, theta_deg, phi_deg, CL, CD, L_N, D_N,
G_factor, stress_max, phase
```

各フィールドが「全事象ロギング」要件の一部を構成し、
シミュレーション完了後に `/api/v1/data/export` で即座にダウンロード可能とする。
