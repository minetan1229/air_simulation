# Skill: Verification & CI/CD Protocol (段階的検証とCI/CD手順)
## Trigger (読み込み条件)
- コード変更後、コミット・プッシュ前の品質検証フェーズ (Phase 4, 6)。

## Procedure (最小手順)
### 1. 段階的ローカル検証（順次全件パス確認）
```bash
python -m compileall physics api tools app.py smoke_test.py
ruff check .
mypy physics api app.py smoke_test.py
python -m pytest tests -q
python smoke_test.py
```
### 2. Git & CI 運用
- ブランチ: `git checkout -b <type>/<desc>` (`feat/`, `fix/`, `docs/`, `test/`等)
- マージ&プッシュ: ローカル全通過後、実動コードのみ master マージ → `git push`
- CI/Issue確認: `python tools/check_runs.py` でCI完了確認 → `python tools/check_issues.py` & `python tools/close_resolved_issue.py` でIssueクローズ。

## Verification Criteria (事前合否基準)
- 成功: 構文エラーなし、Ruff/Mypy指摘0件、pytest全パス、smoke_test正常終了(Exit 0)。
- 失敗: いずれかでエラー検知時。直ちにロールバックまたは原因特定。
