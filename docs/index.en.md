# 3D Paper Airplane Simulator — Documentation

**6DoF RK4 flight dynamics × WebGL 3D × GPU parallel optimisation** — the official documentation for a paper-airplane simulation platform.

This site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and reuses the Markdown assets in `docs/` directly.

> **Japanese version**: [Japanese docs](/air_simulation/) · [README (JP)](https://github.com/minetan1229/air_simulation/blob/master/README.md)

## Quickstart

```bash
# Virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -e ".[dev]"
pip install mkdocs-material pymdown-extensions mkdocstrings

# Start the server (separate terminal recommended)
python app.py
# → http://127.0.0.1:5000 (simulator)
# → http://127.0.0.1:5000/docs (Swagger UI / OpenAPI)

# Start the documentation site
mkdocs serve
# → http://127.0.0.1:8000
```

## Documentation map

- **Getting Started**: `getting-started.md` → `usage.md` → `troubleshooting.md`
- **Physics & Theory**: `SPECIFICATION.md` (canonical equations) → `PHYSICS_MODELS.md` (canonical coefficients) → `VALIDATION.md` (V&V) → `architecture.md`
- **API**: `api.md` (human-readable reference) + runtime `GET /api/openapi.json` / `GET /docs` (Swagger UI)
- **Development & Ops**: `development.md` / `GA_V2_ARCHITECTURE.md` / `CFD_ROADMAP.md` / `PAPER_REGISTRY_PIPELINE.md` / `discord-bridge.md` / `REMOTE_COMPUTE_ENVIRONMENT.md`

## Deployment

Pushes to `docs/` trigger `.github/workflows/docs.yml`, which auto-publishes to GitHub Pages.
Published URL: https://minetan1229.github.io/air_simulation/
