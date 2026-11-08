# セキュリティポリシー (Security Policy)

## サポートバージョン

| バージョン | サポート状況 |
|---|---|
| 3.0.0 (現行) | ✅ セキュリティアップデートを提供 |
| それ以前 | ❌ サポート終了 |

## 脆弱性の報告

セキュリティ上の脆弱性を発見した場合は、**公開前に** 以下のいずれかの方法でご連絡ください:

1. **GitHub Private Security Advisory**（推奨）: リポジトリの「Security」タブから非公開で報告してください。
2. **Issue での非公開報告が難しい場合**: 公開 Issue は避けてください。メンテナーへ直接ご連絡ください。

報告時に含めてほしい情報:

- 脆弱性の種別（例: 認証バイパス、インジェクション、認可不備）
- 該当エンドポイント / コンポーネント
- 再現手順（最小再現ペイロード）
- 影響の想定範囲
- （任意）修正案の提案

## 対応プロセス

1. **受理**: 受領後 48 時間以内に確認の連絡をします。
2. **調査**: 再現・影響範囲・優先度を評価します（重大度: Critical / High / Medium / Low）。
3. **修正**: 修正パッチを作成し、テストします。
4. **開示**: 修正がリリースされてから 90 日以内に、クレジット付きで開示します（Reporter の希望を尊重）。

## セキュリティの実装済み対策

本ソフトウェアは以下の対策を実装しています:

- **認証・認可**: 環境変数 `API_KEY` が設定されている場合、Bearer Token / `X-API-Key` を要求します（`app.py` の `before_request`）。health check / UI は免除されます。
- **セキュリティヘッダー**: CSP / X-Content-Type-Options / X-Frame-Options / Referrer-Policy（`app.py` の `after_request`）。
- **CORS**: ローカル Origin および `ALLOWED_ORIGINS` のみ許可。
- **入力検証**: 統合されたエラーハンドリング（`BadRequest` / `ValueError` / `OverflowError` / `ZeroDivisionError`）で、未検証入力による内部エラー露出を防止。
- **依存関係監査**: CI で `pip-audit` と CodeQL を定期実行し、Dependabot で依存関係を監視。
- **アップロード制限**: `MAX_CONTENT_LENGTH` = 64 MiB で過大ペイロードを拒否。

## 対象外

以下の行為はセキュリティ問題として扱われません:

- `--allow-protected-branch` オプションの通常使用
- `POST /api/v1/system/force-gpu-render` の loopback 制限範囲内での実行
- `GET /api/v1/meta/test-fault`（development / `TESTING=1` でのみ有効）

## 連絡先

- GitHub Issues: https://github.com/minetan1229/air_simulation/issues
- セキュリティタブ: https://github.com/minetan1229/air_simulation/security
