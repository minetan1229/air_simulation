# Skill: Verification & CI/CD Protocol (段階的検証とCI/CD手順)
## Trigger (読み込み条件)
- コードの追加・変更を行った後、またはコミット・プッシュ前の品質検証フェーズ (Phase 4: Verify)。

## Procedure (最小手順)

### 1. 段階的ローカル検証
以下の順序で実行し、すべてのパスを確認する：
```powershell
# 1. 構文検証
python -m compileall physics api tools app.py smoke_test.py

# 2. 静的解析 & コード品質検査
ruff check .
mypy physics api app.py smoke_test.py

# 3. 単体テスト & 監査テスト
python -m pytest tests -q

# 4. E2E統合テスト (API連鎖・最適化ジョブ・ETA・3D解析)
python smoke_test.py
```

### 2. Git ブランチ & コミット運用
- **ブランチ作成**: `git checkout -b <type>/<description>`
- **コミットメッセージ規約**:
  - `feat(scope): message` : 新機能
  - `fix(scope): message` : バグ修正
  - `refactor(scope): message` : 構造改善
  - `docs(scope): message` : ドキュメント
  - `test(scope): message` : テスト追加・更新
  - `chore(scope): message` : 雑務・設定
- **マージ & プッシュ**: ローカル検証を全て通過した実動コードのみを master にマージし push する。
- **CI/CD確認**: GitHub Actions の実行状況を点検し、グリーンを確認する。

## Verification Criteria (事前合否基準の型)
- 成功条件: 構文エラーなし、Ruff/Mypy 指摘 0 件、pytest 全パス、smoke_test.py 正常終了（Exit code 0）。
- 失敗条件: いずれかのコマンドでエラー・失敗が検知された場合。直ちに変更をロールバックまたは原因特定・修正を行うこと。
