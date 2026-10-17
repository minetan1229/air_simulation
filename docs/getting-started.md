# クイックスタートガイド (Getting Started)

本ドキュメントでは、「3D Paper Airplane High-Performance CFD Simulator」の動作環境の準備、インストール、およびアプリケーションの起動手順を解説します。

---

## 1. 動作環境・必要要件

本プロジェクトを実行するには以下の環境が必要です：

- **OS**: Windows, macOS, Linux
- **Python**: 3.10 以上 (Python 3.10 / 3.11 を推奨)
- **仮想環境**: `venv` または `conda`
- **モダンブラウザ**: Google Chrome, Microsoft Edge, Firefox, Safari (WebGL 2.0 / Three.js 対応ブラウザ)
- **(任意) GPU環境**: NVIDIA CUDA 12.x 対応GPU (CuPy / PyTorch による風場・並列バッチ計算の高速化に利用)

---

## 2. インストール手順

### ステップ 1: リポジトリの取得
```bash
git clone <repository_url>
cd air_simulation
```

### ステップ 2: 仮想環境の作成と有効化
Python 3.10 以上の環境で仮想環境を作成します。

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux (Bash):**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### ステップ 3: 依存ライブラリのインストール
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. 環境設定 (.env)

Discord への通知機能や環境変数を設定する場合、`.env.example` をコピーして `.env` を作成します。

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

### 設定項目
```env
# Discord Webhook Notification URL (任意)
# 設定しない場合、通知スクリプトは自動でスキップされます
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
```

---

## 4. (任意) GPU アクセラレーションの有効化

CUDA 対応 GPU をお持ちの場合、CuPy または PyTorch を導入することで 3D 風場グリッド計算や並列最適化を GPU で実行できます。

> [!NOTE]
> GPU パッケージがインストールされていない、または利用不可の場合は、自動的に NumPy による CPU 計算へ安全にフォールバックします。

```powershell
# CUDA 12.x 向け CuPy のインストール
pip install cupy-cuda12x

# または PyTorch (CUDA対応版) のインストール
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

---

## 5. アプリケーションの起動

Flask サーバーを起動します：

```powershell
python app.py
```

デフォルトではポート `5000` でリッスンします。起動後、ブラウザで以下のURLを開いてください：

**URL:** [http://localhost:5000](http://localhost:5000)

コンソール画面（Google Clean / Material スタイルのダッシュボード）が表示され、3Dビューアと各種計器パネルが操作可能になります。

---

## 6. 次のステップ

- [アーキテクチャ・技術スタック (docs/architecture.md)](./architecture.md): システム構成と物理理論モデルを把握する
- [ユーザーガイド・使い方 (docs/usage.md)](./usage.md): UI操作、3Dモデル取込、最適化の実行方法
- [REST API仕様書 (docs/api.md)](./api.md): バックエンド API エンドポイント詳細
- [開発・テストガイド (docs/development.md)](./development.md): テスト実行やコード規約について調べる

---

[README に戻る](../README.md)
