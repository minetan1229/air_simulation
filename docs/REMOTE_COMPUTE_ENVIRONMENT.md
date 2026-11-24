# リモート計算資源（GPU環境）構築・保守手順書
> [← README に戻る](../README.md) / [docs 索引](./README.md)

> **この文書の役割:** リモート GPU 計算機の構築・保守・連携手順の記録。
> **認証情報はリポジトリ直下の `.env`（gitignore 済み）にある `REMOTE_SSH_HOST` / `REMOTE_SSH_USER` / `REMOTE_SSH_PASSWORD` から読みます。`.env.example` にテンプレートあり。本書に平文を残さないこと。**


**接続先情報**
- **ホストIP / ユーザー名**: `.env` の `REMOTE_SSH_HOST` / `REMOTE_SSH_USER` を参照（値は本書に残さない）
- **認証**: `.env` の `REMOTE_SSH_PASSWORD` から読む。`tools/remote_*.py` 等は `python-dotenv` 経由で自動ロードする
- **ハードウェア**:
  - **CPU**: Intel 13th/14th Gen Raptor Lake (20コア)
  - **RAM**: 32 GB
  - **ストレージ**: ADATA NVMe SSD 582 GB (空き約546 GB)
  - **GPU**: NVIDIA GeForce RTX 4060 Mobile / Max-Q (AD107M, 8GB VRAM)
  - **OS**: Debian GNU/Linux 13 (trixie) x86_64 / Kernel 6.12.115+deb13.1-amd64

---

## 1. 実施した環境構築内容（ログ・記録）

### 1.1 システムパッケージ & 開発環境
以下のパッケージを `apt` よりインストール完了済み：
- `build-essential`, `dkms` (カーネルモジュールビルド用)
- `python3-dev`, `python3-venv`, `python3-pip`, `python3.13-dev`
- `git`, `rsync`, `htop`, `curl`

### 1.2 NVIDIA GPU ドライバー
- パッケージ: `nvidia-driver` (バージョン 550.163.01)
- DKMSにより以下のカーネルモジュールを自動ビルド・配置済み：
  - `nvidia.ko`
  - `nvidia-modeset.ko`
  - `nvidia-drm.ko`
  - `nvidia-uvm.ko` (CUDA統合メモリモジュール)
  - `nvidia-peermem.ko`
- `update-initramfs` を実行し、オープンソース競合ドライバ `nouveau` のブラックリスト化設定済み。

---

## 2. 隔離環境（Worker Isolated Environment）設計

リモートホストのシステムPythonや他サービスを汚染・競合させないため、以下の完全隔離構成をとる。

### ディレクトリ構成
```
/home/minetan1229/paper_airplanes_worker/
├── venv/                      # 独立したPython 3.13仮想環境
│   ├── bin/
│   │   ├── python
│   │   ├── pip
│   │   └── pytest
│   └── lib/python3.13/site-packages/
├── workspace/                 # プロジェクトコード・シミュレーション実行領域
│   ├── physics/
│   ├── api/
│   ├── requirements.txt
│   └── pyproject.toml
├── setup_env.sh               # 環境ワンクリック再構築スクリプト
├── run_worker.sh              # 隔離環境でのタスク・並列シミュレーション起動スクリプト
├── verify_gpu.sh              # GPU・CUDA・CuPy動作検証スクリプト
├── cleanup.sh                 # 隔離環境の完全リセット・アンインストールスクリプト
└── README.md                  # リモート上での保守・トラブルシューティング説明書
```

---

## 3. 保守・復旧・変更手順（後で直す・再構築する方法）

### 3.1 隔離環境の再構築（クリーンインストール）
何らかの依存破損や設定変更があった場合、リモート側で以下を実行するだけで初期状態に復元可能：
```bash
ssh minetan1229@192.168.100.2
cd ~/paper_airplanes_worker
./setup_env.sh
```

### 3.2 隔離環境の完全削除（クリーンアップ）
作業ディレクトリおよび仮想環境を完全に撤去する場合：
```bash
ssh minetan1229@192.168.100.2
cd ~/paper_airplanes_worker
./cleanup.sh
```

### 3.3 GPU状態の確認
```bash
nvidia-smi
# または隔離環境のPythonからCUDA確認
~/paper_airplanes_worker/verify_gpu.sh
```

### 3.4 万が一NVIDIAドライバを再インストール／削除する場合
```bash
sudo apt-get purge -y "*nvidia*"
sudo update-initramfs -u
sudo reboot
```
再インストール時：
```bash
sudo apt-get update
sudo apt-get install -y nvidia-driver
sudo reboot
```

---

## 4. ローカル（メインPC）からの連携管理

> **導入（#159）:** リモート連携ツールは `paramiko` を **任意の `remote` extra** として宣言しており、通常の `pip install -e .` では入りません。SSH 操作に必要な場合のみ以下を実行してください。
> ```bash
> pip install -e ".[remote]"   # または: pip install paramiko
> ```
> クリーン venv（remote extra 未インストール状態）でも、共通モジュール `tools/remote_auth.py` と各スクリプトの** import 自体は遅延 import のため成功**します。実際に SSH 接続しようとした時点でのみ、導入手順付きのエラーが出ます。

> **ホスト鍵検証（#160）:** `tools/remote_auth.py` の `build_ssh_client()` は `load_system_host_keys()` 後に **`RejectPolicy`** を設定します。登録済み鍵のないホストへの接続は**拒否**されます（従来の `AutoAddPolicy` による自動登録は廃止）。必要に応じて `~/.ssh/known_hosts` へ事前に登録してください。パスワードはログに出力されません。

> **sudo パスワード（#161）:** sudo 実行は `sudo -S -p ''` + 接続先 stdin への送信とし、**パスワードをコマンド文字列へ埋め込みません**。特殊文字を含むパスワードでもそのまま伝達され、`ps` 表示・ログ・シェル履歴への漏出を防ぎます。

ローカルリポジトリ側の `tools/remote_compute_manager.py` を用いて、
ローカルからワンコマンドで以下の操作が可能：
- リモートホストの状態監視（CPU/GPU使用率、メモリ）
- コードの自動同期 (`rsync` / SFTP)
- リモートワーカーでの分散/並列計算ジョブ実行
