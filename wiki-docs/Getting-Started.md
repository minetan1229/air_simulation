# 環境構築と起動手順 (Getting Started)

本ページでは、ローカル環境で **3D Paper Airplane High-Performance CFD Simulator** をセットアップし、シミュレーターを起動するまでの手順を解説します。

---

## 1. 前提条件と必要環境

| 項目 | 要件 | 備考 |
|:---|:---|:---|
| **OS** | Windows 10/11, macOS, Linux | |
| **Python** | **Python 3.10 以上** (3.11 推奨) | 64-bit 版必須 |
| **Web ブラウザ** | WebGL 2.0 対応モダンブラウザ | Google Chrome, Edge, Firefox, Safari |
| **GPU (任意)** | NVIDIA GPU (CUDA 12.x 対応) | CuPy / PyTorch による風場計算の並列アクセラレーション用 (CPU フォールバック搭載) |

---

## 2. リポジトリのクローンと仮想環境作成

```powershell
# リポジトリのクローン
git clone https://github.com/minetan1229/air_simulation.git
cd air_simulation

# Python仮想環境の作成
python -m venv .venv

# 仮想環境の有効化 (Windows PowerShell)
.venv\Scripts\Activate.ps1

# (macOS / Linux の場合: source .venv/bin/activate)
```

---

## 3. 依存パッケージのインストール

`requirements.txt` から必要なパッケージをインストールします。

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 開発・検証用ツールのインストール (任意)
テストやリンター・型検査を行う場合は以下の開発用ツールも追加インストールします。
```powershell
pip install ruff mypy pytest
```

### GPU アクセラレーションの有効化 (任意)
CUDA 12.x 環境をお持ちの場合、CuPy または PyTorch を追加することで風場計算を GPU にオフロードできます。
```powershell
# CuPy (CUDA 12.x 向け)
pip install cupy-cuda12x>=13.0

# または PyTorch
pip install torch
```
> ※ GPU パッケージがインストールされていない場合、自動的に最適化された NumPy / CPU マルチスレッド実装で動作します。

---

## 4. 環境変数の設定 (`.env`)

通知やリモート連携用の環境変数を設定します（未設定でもシミュレーター本体は起動可能です）。  
`.env.example` をコピーして `.env` を作成します。

```powershell
Copy-Item .env.example .env
```

### 主な設定項目 (`.env`)
```env
# Discord Webhook 通知URL (シミュレーションやテスト結果の通知)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN

# Discord リモート実行用ボットトークン (利用する場合のみ)
DISCORD_BOT_TOKEN=your_bot_token_here
ALLOWED_USER_IDS=
ALLOWED_CHANNEL_IDS=
DEFAULT_MODEL=gemini-3.8-flash-low
```
> [!WARNING]
> `.env` ファイルには機密情報が含まれるため、Git コミット対象外（`.gitignore` に登録済み）となっています。絶対に公開リポジトリへコミットしないでください。

---

## 5. シミュレーターの起動

Flask サーバーを起動します。

```powershell
python app.py
```

起動が完了すると、コンソールに以下のように表示されます。

```text
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000 (Press CTRL+C to quit)
```

ブラウザで **[http://localhost:5000](http://localhost:5000)** にアクセスしてください。Google Clean スタイルの 3D 計器コンソールが表示されます。

---

## 6. 初期動作確認 (スモークテスト)

サーバーが正常に動作しているか、API および物理演算モジュールの結合確認用スクリプトを実行して検証できます。

```powershell
python smoke_test.py
```

全項目が PASS すれば、ローカル環境のセットアップは完了です。  
詳細なアーキテクチャやコード解説については [[アーキテクチャ設計|Architecture]] を参照してください。
