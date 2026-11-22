Now I understand the issues. Let me provide the exact fixes:

## 1. Fix `mkdocs.yml` palette (Japanese site)
## 2. Fix `mkdocs.en.yml` palette (English site)
## 3. Fix `docs/overrides/main.html` with language switching
## 4. Build script for proper English site output
All fixes are complete and verified. Here's a summary:

## Changes Made

### 1. Fixed palette configuration (both `mkdocs.yml` and `mkdocs.en.yml`)
Removed `media` queries from palette entries to enable proper 2-state light/dark toggle:

```yaml
palette:
  - scheme: default
    toggle:
      icon: material/brightness-7
      name: Light / Dark (or ライト/ダーク)
  - scheme: slate
    primary: light blue
    toggle:
      icon: material/brightness-4
      name: Dark / Light (or ダーク/ライト)
```

### 2. Fixed `docs/overrides/main.html` with language switching
Added conditional hero text based on `config.theme.language`:

```html
{% if config.theme.language == 'en' %}
  <!-- English hero -->
{% else %}
  <!-- Japanese hero -->
{% endif %}
```

### 3. Created `build_docs.py` for proper multi-language build
- Japanese: builds to `site/` from `docs/` with `index.md` as homepage
- English: creates temporary `docs_en/` with `index.en.md` copied as `index.md`, builds to `site/en/`

### 4. Updated `mkdocs.en.yml`
- Changed `docs_dir: docs` → `docs_dir: docs_en`
- Changed `nav` homepage from `index.en.md` → `index.md`

## Verification
- `site/index.html` - Japanese homepage ✓
- `site/en/index.html` - English homepage with `lang="en"` ✓
- Both builds complete without errors ✓