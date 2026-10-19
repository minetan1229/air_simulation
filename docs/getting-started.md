# 環境構築および導入手順 (Setup Guide)

本ドキュメントは、「3D Paper Airplane High-Performance CFD Simulator」の動作要件、環境構築、および検証手順を規定する。

---

## 1. 動作環境・必要要件 (Prerequisites)

- **オペレーティングシステム**: Windows 10/11 (64-bit), Linux (Ubuntu 20.04 LTS 以上推奨), macOS 12+
- **Python**: 3.10 以上 (3.11.x 推奨)
- **アクセラレータ (GPU)**: NVIDIA GPU (CUDA 12.x / Compute Capability 5.0 以上)
- **必須パッケージ**: `cupy-cuda12x>=13.0.0`
  - GPU が明示的に指定された場合、またはバッチ並列計算において GPU が利用不能な場合は即座に `RuntimeError` を送出して処理を停止する（フェイルファスト原則）。CPU への暗黙的フォールバックは禁止されている。
- **表示環境**: WebGL 2.0 対応ブラウザ (Google Chrome, Microsoft Edge, Firefox, Safari)

---

## 2. 自動セットアップ手順 (Automated Setup)

リポジトリルートに配置された自動化スクリプトにより、仮想環境の作成から依存パッケージの導入、CUDA デバイス検証までを単一コマンドで実行可能である。

### 2.1 Windows (PowerShell)
```powershell
.\setup.ps1
```

### 2.2 Linux / macOS / WSL (Bash)
```bash
chmod +x setup.sh
./setup.sh
```

---

## 3. 手動セットアップ手順 (Manual Setup)

### 3.1 仮想環境の作成と有効化

**Windows:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3.2 パッケージのインストール
ビルドツールおよび依存関係をインストールする。

```bash
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

高速パッケージインストーラ `uv` を使用する場合:
```bash
uv pip install -e ".[dev]"
```

---

## 4. 環境設定 (.env)

外部連携（Discord Webhook 等）を利用する場合、`.env.example` を複製して `.env` を作成する。

```bash
cp .env.example .env
```

`.env` 内の設定項目:
```env
# Discord Webhook 通知用エンドポイント (未設定時は通知処理をスキップ)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## 5. ハードウェアアクセラレーション検証

CUDA GPU が正常に認識されているかを以下のコマンドで確認する:

```bash
python -c "import cupy as cp; print('Device Count:', cp.cuda.runtime.getDeviceCount()); print('Current Device:', cp.cuda.runtime.getDevice())"
```

正常時出力例:
```text
Device Count: 1
Current Device: 0
```

---

## 6. アプリケーションの起動と稼働確認

サーバーを起動する:
```bash
python app.py
```

ブラウザで `http://127.0.0.1:5000` を開き、計器コンソールが表示されることを確認する。
ヘルスチェックエンドポイントによる確認:
```bash
curl http://127.0.0.1:5000/api/v1/meta/health
```
