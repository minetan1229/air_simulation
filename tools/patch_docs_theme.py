"""Patch script for documentation palette, overrides, and workflows."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 1. Update mkdocs.yml
mkdocs_ja_path = ROOT / "mkdocs.yml"
content = mkdocs_ja_path.read_text(encoding="utf-8")
palette_pattern = r"  palette:\n(?:    .*\n)+?(?=  features:)"
new_palette_ja = """  palette:
    - scheme: default
      primary: indigo
      accent: indigo
      toggle:
        icon: material/brightness-7
        name: ダークモードに切り替え
    - scheme: slate
      primary: light blue
      accent: indigo
      toggle:
        icon: material/brightness-4
        name: ライトモードに切り替え
"""
content = re.sub(palette_pattern, new_palette_ja, content)
mkdocs_ja_path.write_text(content, encoding="utf-8", newline="\n")
print("[OK] Updated mkdocs.yml")

# 2. Update mkdocs.en.yml
mkdocs_en_path = ROOT / "mkdocs.en.yml"
content_en = mkdocs_en_path.read_text(encoding="utf-8")
new_palette_en = """  palette:
    - scheme: default
      primary: indigo
      accent: indigo
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    - scheme: slate
      primary: light blue
      accent: indigo
      toggle:
        icon: material/brightness-4
        name: Switch to light mode
"""
content_en = re.sub(palette_pattern, new_palette_en, content_en)
content_en = content_en.replace("docs_dir: docs", "docs_dir: docs_en")
content_en = content_en.replace("  - Home: index.en.md", "  - Home: index.md")
mkdocs_en_path.write_text(content_en, encoding="utf-8", newline="\n")
print("[OK] Updated mkdocs.en.yml")

# 3. Update docs/overrides/main.html
main_html_path = ROOT / "docs" / "overrides" / "main.html"
main_html = main_html_path.read_text(encoding="utf-8")
old_hero = (
    "  {% if page and page.is_homepage %}\n"
    "    <div class=\"hero\">\n"
    "      <h1>\u2708\ufe0f {{ config.site_name }}</h1>\n"
    "      <p><strong>6DoF RK4 \u98db\u884c\u529b\u5b66 \xd7 WebGL 3D \xd7 GPU \u4e26\u5217\u6700\u9069\u5316</strong></p>\n"
    "      <p>\u5b66\u8853\u6839\u62e0\u3092\u6301\u3064\u7d19\u98db\u884c\u6a5f\u30b7\u30df\u30e5\u30ec\u30fc\u30b7\u30e7\u30f3\u57fa\u76e4\u3002\u30d6\u30e9\u30a6\u30b6\u3067\u30ea\u30a2\u30eb\u30bf\u30a4\u30e0 3D \u98db\u884c\u8ecc\u8de1\u30fb\u59ff\u52e2\u30fb\u6c17\u6d41\u3092\u53ef\u8996\u5316\u3057\u307e\u3059\u3002</p>"
)
new_hero = (
    "  {% if page and page.is_homepage %}\n"
    "    <div class=\"hero\">\n"
    "      <h1>\u2708\ufe0f {{ config.site_name }}</h1>\n"
    "      {% if config.theme.language == 'en' %}\n"
    "        <p><strong>6DoF RK4 Flight Dynamics \xd7 WebGL 3D \xd7 GPU Parallel Optimization</strong></p>\n"
    "        <p>Academic-grade paper airplane simulation platform. Real-time 3D flight trajectory, attitude, and airflow visualization in the browser.</p>\n"
    "      {% else %}\n"
    "        <p><strong>6DoF RK4 \u98db\u884c\u529b\u5b66 \xd7 WebGL 3D \xd7 GPU \u4e26\u5217\u6700\u9069\u5316</strong></p>\n"
    "        <p>\u5b66\u8853\u6839\u62e0\u3092\u6301\u3064\u7d19\u98db\u884c\u6a5f\u30b7\u30df\u30e5\u30ec\u30fc\u30b7\u30e7\u30f3\u57fa\u76e4\u3002\u30d6\u30e9\u30a6\u30b6\u3067\u30ea\u30a2\u30eb\u30bf\u30a4\u30e0 3D \u98db\u884c\u8ecc\u8de1\u30fb\u59ff\u52e2\u30fb\u6c17\u6d41\u3092\u53ef\u8996\u5316\u3057\u307e\u3059\u3002</p>\n"
    "      {% endif %}"
)
if old_hero in main_html:
    main_html = main_html.replace(old_hero, new_hero)
    main_html_path.write_text(main_html, encoding="utf-8", newline="\n")
    print("[OK] Updated docs/overrides/main.html")
else:
    print("[OK] docs/overrides/main.html already has new hero")

# 4. Update .github/workflows/docs.yml
docs_yml_path = ROOT / ".github" / "workflows" / "docs.yml"
docs_yml = docs_yml_path.read_text(encoding="utf-8")
old_build_steps = (
    "      - name: Build Japanese documentation\n"
    "        env:\n"
    "          NO_MKDOCS_2_WARNING: \"1\"\n"
    "        run: mkdocs build --config-file mkdocs.yml --site-dir site\n\n"
    "      - name: Build English documentation\n"
    "        env:\n"
    "          NO_MKDOCS_2_WARNING: \"1\"\n"
    "        run: mkdocs build --config-file mkdocs.en.yml --site-dir site/en"
)
new_build_steps = (
    "      - name: Build documentation (Multi-language)\n"
    "        env:\n"
    "          NO_MKDOCS_2_WARNING: \"1\"\n"
    "        run: python scripts/build_docs.py"
)
if old_build_steps in docs_yml:
    docs_yml = docs_yml.replace(old_build_steps, new_build_steps)
    docs_yml_path.write_text(docs_yml, encoding="utf-8", newline="\n")
    print("[OK] Updated .github/workflows/docs.yml")
else:
    print("[OK] .github/workflows/docs.yml already updated")

# 5. Update .gitignore
gitignore_path = ROOT / ".gitignore"
gitignore = gitignore_path.read_text(encoding="utf-8")
if "docs_en/" not in gitignore:
    gitignore = gitignore.replace("site/\n", "site/\ndocs_en/\n")
    gitignore_path.write_text(gitignore, encoding="utf-8", newline="\n")
    print("[OK] Updated .gitignore")
