---
name: verification-pipeline
description: プロジェクト全体の品質保証、テスト実行、構文チェック、E2Eスモークテスト、CI互換性確認を行う手順。コード変更後やコミット前、PR作成前の検証指示・テスト実行要求時にトリガーする。
prerequisites:
  - Python 3.11+
  - pytest
  - tools/notify_discord.py
---

# Verification Pipeline Skill

3D紙飛行機シミュレーターにおける構文検証・単体/物理検証・E2E結合テスト・Discord通知までを網羅する標準検証ランブックです。

## 1. Prerequisites (前提条件)
- 仮想環境（Python 3.11+）が有効化されていること
- 必要な依存ライブラリ（`requirements.txt`）が導入されていること
- （任意）`.env` に `DISCORD_WEBHOOK_URL` が設定されていること

## 2. Trigger Conditions (発火条件)
- `tests/` または `physics/`, `app.py`, `static/` などのコードを変更したとき
- コミットまたはプッシュを行う前の品質ゲートチェック
- 「テストを実行して」「構文チェックをして」「スモークテストを通して」等の指示を受けたとき

## 3. Step-by-Step Workflow (実行手順)

### Step 1: 構文チェック (Syntax Compilation)
すべての Python ソースコードで文法エラーやインポート破損がないか一括検証します。
```powershell
python -m compileall physics app.py smoke_test.py tools
```
- 終了コード 0 であることを確認します。

### Step 2: 軽量スモークテスト (Fast Smoke Tests)
主要な API ルーティング、スキーマ、大気モデルの整合性を高速チェックします。
```powershell
python smoke_test.py --stage health+schemas --stage environment
```

### Step 3: フル E2E スモークテスト (Full E2E Smoke Test)
最適化ジョブ、メッシュインポート、6DoF シミュレーションを含めた全体導通を確認します。
```powershell
python smoke_test.py
```

### Step 4: 物理・ドメイン単体テスト (Domain Pytest)
必要に応じて特定のサブドメインまたは全テストを実行します。
```powershell
# 特定ドメインの高速テスト例
python -m pytest tests/test_aero.py tests/test_flight.py -q

# 全体テスト（※全体の完了には数分要するため、非同期実行または段階実行を推奨）
python -m pytest tests -q
```

### Step 5: 結果のDiscord通知 (Notification)
重大なテスト結果や作業完了ステータスを通知します。
```powershell
# 成功時
python tools/notify_discord.py end "テスト検証完了" "全構文チェックおよびE2Eスモークテストを通過しました。"

# 失敗時
python tools/notify_discord.py fail "テスト検証失敗" "特定ステージでアサーションエラーが発生しました。"
```

## 4. Constraints & Prohibitions (制約・禁止事項)
- **テストスキップの禁止**: エラーが出た際にアサーションを勝手に弱めたりコメントアウトしてパスさせてはならない。
- **Zero-Placeholder Policy**: テストやスクリプト内に `TODO` やダミー関数を残さない。
- **非同期コマンド管理**: 全体 pytest は 600 件以上の物理数値積分を含むため完了まで数分かかる場合がある。PowerShell の `WaitMsBeforeAsync` 設定およびタスク管理ツールを活用し、重複起動によるマシン負荷を避ける。
