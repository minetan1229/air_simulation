# 開発・テスト・コントリビューションガイド (Development & Testing)

本ドキュメントでは、コードベースの保守・開発ワークフロー、単体テスト・総合スモークテストの実行手順、およびコーディング規約について説明します。

---

## 1. 開発原則 (Core Principles)

本プロジェクトでは以下の厳格な開発方針を適用しています：

1. **Zero-Placeholder Policy (省略・空実装の禁止)**:
   `TODO`、`pass`、未実装の空関数、ダミープレースホルダーは一切禁止です。すべての関数・数式・APIは100%実動するコードとして実装します。
2. **Small-Step & Verification Protocol (段階的検証)**:
   コードの改修後は必ずローカル環境で構文チェック・単体テスト（`pytest`）を実施し、検証通過を確認してから次の作業に進みます。
3. **安全なフォールバック設計**:
   GPU（CUDA / CuPy / PyTorch）が利用できない環境であっても、例外停止することなくNumPy等のCPU実装へ透過的にフォールバックする構成を維持します。

---

## 2. テストの実行

### 2.1 構文チェック
Python のバイトコードコンパイルによる構文検証：

```powershell
python -m compileall physics app.py
```

### 2.2 単体テスト (pytest)
各物理モジュール（空力、大気、風場、応力、6DoF運動、メッシュ解析、最適化、ジョブ管理）の数値検証を行います。

```powershell
# クイック実行
python -m pytest tests -q

# 特定のドメインテストのみを実行する場合
python -m pytest tests/test_aero.py -q
python -m pytest tests/test_mesh.py -q
python -m pytest tests/test_jobs.py -q
```

> [!NOTE]
> 解析解が存在する領域（ISA大気、誘導抗力、Sutherland粘性、メッシュ幾何特性）については、相対誤差 $10^{-6} \sim 10^{-3}$ の厳格な数値テストを実施しています。

### 2.3 統合スモークテスト
Flask API 疎通、最適化ジョブ実行とETA算出、および3Dモデル自動解析パイプラインを一括検証します：

```powershell
python smoke_test.py
```

---

## 3. Git 運用規約

コミットメッセージは以下のプレフィックス規約に準拠します：

- `feat(scope): ...` : 新機能の追加
- `fix(scope): ...` : バグ修正
- `refactor(scope): ...` : リファクタリング（機能変更なし）
- `test(scope): ...` : テストの追加・修正
- `docs(scope): ...` : ドキュメントの整備・修正

---

## 4. Discord Webhook 通知スクリプト

作業ステータス（開始、サブエージェント稼働、完了、失敗、警告等）を Discord に通知するためのスクリプトが用意されています：

```powershell
# 使用法
python tools/notify_discord.py <start|subagent|end|fail|warning|question> <title> <summary> [extra_info]

# 例: 完了通知
python tools/notify_discord.py end "リファクタリング完了" "ドキュメント分割およびリンク検証がすべて正常に完了しました。"
```

---

## 5. 関連ドキュメント

- [クイックスタート (docs/getting-started.md)](./getting-started.md)
- [システムアーキテクチャ (docs/architecture.md)](./architecture.md)
- [REST API 仕様書 (docs/api.md)](./api.md)
- [トラブルシューティング (docs/troubleshooting.md)](./troubleshooting.md)

---

[README に戻る](../README.md)
