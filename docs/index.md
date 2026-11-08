# 3D Paper Airplane Simulator — ドキュメントサイト

**6DoF RK4 飛行力学 × WebGL 3D × GPU 並列最適化** の紙飛行機シミュレーション基盤の公式ドキュメントです。

本サイトは [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) でビルドされ、`docs/` 配下の Markdown 資産をそのまま参照します。

## クイックスタート

```bash
# 仮想環境
python3 -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1

# 依存インストール
pip install -e ".[dev]"
pip install mkdocs-material pymdown-extensions mkdocstrings

# サーバー起動（別ターミナル推奨）
python app.py
# → http://127.0.0.1:5000 （シミュレータ本体）
# → http://127.0.0.1:5000/docs （Swagger UI / OpenAPI）

# ドキュメントサイト起動
mkdocs serve
# → http://127.0.0.1:8000
```

## ドキュメント構成

- **導入ガイド**: `getting-started.md` → `usage.md` → `troubleshooting.md`
- **物理・理論**: `SPECIFICATION.md`（数式正本）→ `PHYSICS_MODELS.md`（係数正本）→ `VALIDATION.md`（V&V）→ `architecture.md`
- **API**: `api.md`（手書きリファレンス）+ ランタイム `GET /api/openapi.json` / `GET /docs`（Swagger UI）
- **開発・運用**: `development.md` / `GA_V2_ARCHITECTURE.md` / `CFD_ROADMAP.md` / `PAPER_REGISTRY_PIPELINE.md` / `discord-bridge.md` / `REMOTE_COMPUTE_ENVIRONMENT.md`

## デプロイ

`docs/` への push で `.github/workflows/docs.yml` が自動で GitHub Pages へ公開します。
公開 URL: https://minetan1229.github.io/air_simulation/
