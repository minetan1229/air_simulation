# プロジェクト前提知識（コンテキスト定義）
<!--
【使い方】
本ファイルは各プロジェクト固有の前提・制約を記述する「穴埋めテンプレート」です。
AIエージェントは作業着手前に必ず本ファイルを参照し、プロジェクトの文脈と制約を把握します。
別プロジェクトに流用する際は、本ファイルの内容を各プロジェクト向けに書き換えてください。
-->

## 1. プロジェクト基本情報
- **プロジェクト名**: paper_airplanes / 6DoF Flight Simulator v3
- **概要・目的**: 遺伝的アルゴリズム（GA）による最適化と、物理法則に即した6自由度（6DoF）紙飛行機フライトシミュレーション基盤の構築
- **ターゲット・用途**: シミュレーションAPI、Web 3Dビジュアライザ、自動設計最適化パイプライン
- **主要言語・ランタイム**: Python 3.10+, PowerShell, Node.js (Webフロントエンド)

## 2. ディレクトリ・主要構成
- `physics/`: 6DoF物理演算エンジン、空気力学計算、風場モデル、GA最適化モジュール
- `api/`: シミュレーション実行・データ送出を行うバックエンドAPI
- `tools/`: 運用保守、ブランチ監視、プロセス衛生管理、Discord連携スクリプト群
- `app.py`: アプリケーション起動エントリポイント
- `scripts/smoke_test.py`: 統合スモークテスト実行スクリプト
- `docs/SPECIFICATION.md`: 詳細物理モデル・API仕様書（設計の正本）

## 3. プロジェクト固有のガードレール & 地雷
- **ブランチ隔離（Branch Isolation）**:
  - `genetic-project` 開発フェーズ中は、`master` / `main` への直接コミット・マージ・PR作成を禁止（`tools/branch_guard.py` が自動判定）。
- **物理・座標系ルール**:
  - 風場（+z上向き）と世界NED座標系（+z下向き）の符号変換規約（$w_{NED} = -w_{wind}$）。
  - 風は地表面からの高度（$z_{up} = -z_{ned}$）でサンプリング。
  - 水平距離100km制限、地中即死判定（`min_alt < -0.05`）、CCD補間、翼端散逸上限の維持。
- **評価規約**:
  - 失敗した評価を `timeout` や `crashed` 等へ勝手にリラベルしない（報酬ハッキング防止）。
  - GA既定値は `physics/ga_config.py` へ集約。

## 4. 検証・実行コマンド早見
- **構文・静的型チェック**: `python -m compileall physics api tools app.py scripts/smoke_test.py` / `ruff check .` / `mypy physics api app.py scripts/smoke_test.py`
- **単体・統合テスト**: `python -m pytest tests -q` / `python scripts/smoke_test.py`
- **プロセス管理**: `python tools/process_hygiene.py --list` / `--apply`
